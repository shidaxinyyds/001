/**
 * touch_helper.cpp - uinput-based touch simulation
 *
 * Creates a dedicated virtual touchscreen via /dev/uinput and forwards
 * aimbot touch events through it. The user's real touchscreen is left
 * UNTOUCHED (no EVIOCGRAB, no readback thread), so user touches keep
 * working in parallel with aimbot touches.
 *
 * Uses Type B multi-touch protocol with a single dedicated slot for the
 * aim contact. Android InputDispatcher multiplexes the real and virtual
 * devices, so finger + aimbot pointer coexist naturally.
 */

#include "touch_helper.h"

#include <android/log.h>

#include <dirent.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <mutex>
#include <string>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <vector>
#include <cstring>

#define LOG_TAG "TouchHelper"
#if defined(NDEBUG)
    #define LOGE(...) ((void)0)
    #define LOGI(...) ((void)0)
#else
    #define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)
    #define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#endif

namespace {

// Dedicated tracking id for aimbot contact. High value so it cannot collide
// with whatever ids the OS hands out to physical touches on the real device.
constexpr int kAimTrackingId = 0x7000;

struct TouchScreenInfo {
    int touchXMin = 0;
    int touchXMax = 32767;
    int touchYMin = 0;
    int touchYMax = 32767;
    int absFuzzX = 0;
    int absFuzzY = 0;
    int bustype = 0;
    int vendor = 0;
    int product = 0;
    int version = 0;
    std::string phys;
    bool valid = false;
};

int g_displayWidth = 0;
int g_displayHeight = 0;

int g_uInputTouchFd = -1;
bool g_isAimDown = false;
std::mutex g_writeMutex;
TouchScreenInfo g_screenInfo;

int isCharDevice(const std::string& path) {
    struct stat st{};
    if (stat(path.c_str(), &st) == -1) {
        return 0;
    }
    return S_ISCHR(st.st_mode) ? 1 : 0;
}

bool bitIsSet(const unsigned char* bits, unsigned int idx) {
    return (bits[idx / 8] & (1u << (idx % 8))) != 0;
}

// Probe /dev/input for the primary touchscreen to copy its ABS ranges /
// physical-location string. We never open it for read/write  -  only read
// metadata via ioctl on a temporary fd.
bool probeRealTouchscreen(TouchScreenInfo& out) {
    DIR* dir = opendir("/dev/input");
    if (!dir) {
        return false;
    }
    bool found = false;
    struct dirent* entry;
    while ((entry = readdir(dir))) {
        if (!strstr(entry->d_name, "event")) continue;
        std::string path = std::string("/dev/input/") + entry->d_name;
        if (!isCharDevice(path)) continue;

        int fd = open(path.c_str(), O_RDONLY);
        if (fd < 0) continue;

        unsigned char absBits[ABS_MAX / 8 + 1] = {0};
        unsigned char propBits[INPUT_PROP_MAX / 8 + 1] = {0};
        unsigned char keyBits[KEY_MAX / 8 + 1] = {0};

        if (ioctl(fd, EVIOCGBIT(EV_ABS, sizeof(absBits)), &absBits) < 0 ||
            ioctl(fd, EVIOCGPROP(sizeof(propBits)), &propBits) < 0 ||
            ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(keyBits)), &keyBits) < 0) {
            close(fd);
            continue;
        }

        const bool hasMtSlot   = bitIsSet(absBits, ABS_MT_SLOT);
        const bool hasMtPosX   = bitIsSet(absBits, ABS_MT_POSITION_X);
        const bool hasMtPosY   = bitIsSet(absBits, ABS_MT_POSITION_Y);
        const bool hasDirect   = bitIsSet(propBits, INPUT_PROP_DIRECT);
        const bool hasBtnTouch = bitIsSet(keyBits, BTN_TOUCH);

        if (!hasMtSlot || !hasMtPosX || !hasMtPosY || !hasDirect || !hasBtnTouch) {
            close(fd);
            continue;
        }

        struct input_absinfo absX{};
        struct input_absinfo absY{};
        if (ioctl(fd, EVIOCGABS(ABS_MT_POSITION_X), &absX) < 0 ||
            ioctl(fd, EVIOCGABS(ABS_MT_POSITION_Y), &absY) < 0) {
            close(fd);
            continue;
        }

        struct input_id iid{};
        ioctl(fd, EVIOCGID, &iid);
        char phys[256] = {0};
        ioctl(fd, EVIOCGPHYS(sizeof(phys)), phys);

        out.touchXMin = absX.minimum;
        out.touchXMax = absX.maximum;
        out.touchYMin = absY.minimum;
        out.touchYMax = absY.maximum;
        out.absFuzzX = absX.fuzz;
        out.absFuzzY = absY.fuzz;
        out.bustype = iid.bustype;
        out.vendor = iid.vendor;
        out.product = iid.product;
        out.version = iid.version;
        out.phys = phys;
        out.valid = true;
        found = true;

        close(fd);
        break;
    }
    closedir(dir);
    return found;
}

int createUInputAim(const TouchScreenInfo& info) {
    int ufd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
    if (ufd < 0) {
        LOGE("Unable to open /dev/uinput");
        return -1;
    }

    // Event types we'll emit
    ioctl(ufd, UI_SET_EVBIT, EV_SYN);
    ioctl(ufd, UI_SET_EVBIT, EV_KEY);
    ioctl(ufd, UI_SET_EVBIT, EV_ABS);

    ioctl(ufd, UI_SET_KEYBIT, BTN_TOUCH);

    ioctl(ufd, UI_SET_ABSBIT, ABS_MT_SLOT);
    ioctl(ufd, UI_SET_ABSBIT, ABS_MT_TRACKING_ID);
    ioctl(ufd, UI_SET_ABSBIT, ABS_MT_POSITION_X);
    ioctl(ufd, UI_SET_ABSBIT, ABS_MT_POSITION_Y);
    ioctl(ufd, UI_SET_ABSBIT, ABS_MT_TOUCH_MAJOR);
    ioctl(ufd, UI_SET_ABSBIT, ABS_MT_PRESSURE);

    ioctl(ufd, UI_SET_PROPBIT, INPUT_PROP_DIRECT);

    struct uinput_user_dev uidev{};
    memset(&uidev, 0, sizeof(uidev));
    // Use a distinctive name (not a copy of the real screen) so we don't
    // confuse logging or input mapping.
    strncpy(uidev.name, "aimbuddy-virtual-touch", UINPUT_MAX_NAME_SIZE - 1);
    uidev.id.bustype = info.bustype ? info.bustype : BUS_VIRTUAL;
    uidev.id.vendor  = 0x1209;  // generic
    uidev.id.product = 0xA1B0;
    uidev.id.version = 1;

    // Single slot is enough  -  the user's real touchscreen handles their fingers.
    uidev.absmin[ABS_MT_SLOT] = 0;
    uidev.absmax[ABS_MT_SLOT] = 0;

    uidev.absmin[ABS_MT_TRACKING_ID] = 0;
    uidev.absmax[ABS_MT_TRACKING_ID] = 0xFFFF;

    uidev.absmin[ABS_MT_POSITION_X] = info.touchXMin;
    uidev.absmax[ABS_MT_POSITION_X] = info.touchXMax;
    uidev.absfuzz[ABS_MT_POSITION_X] = info.absFuzzX;

    uidev.absmin[ABS_MT_POSITION_Y] = info.touchYMin;
    uidev.absmax[ABS_MT_POSITION_Y] = info.touchYMax;
    uidev.absfuzz[ABS_MT_POSITION_Y] = info.absFuzzY;

    uidev.absmin[ABS_MT_TOUCH_MAJOR] = 0;
    uidev.absmax[ABS_MT_TOUCH_MAJOR] = 32;

    uidev.absmin[ABS_MT_PRESSURE] = 0;
    uidev.absmax[ABS_MT_PRESSURE] = 255;

    if (write(ufd, &uidev, sizeof(uidev)) != sizeof(uidev)) {
        LOGE("uinput write(uidev) failed");
        close(ufd);
        return -1;
    }

    if (ioctl(ufd, UI_DEV_CREATE) < 0) {
        LOGE("UI_DEV_CREATE failed");
        close(ufd);
        return -1;
    }

    // Brief settle so InputReader picks up the new device before first event.
    std::this_thread::sleep_for(std::chrono::milliseconds(80));
    return ufd;
}

inline void writeEvent(int fd, uint16_t type, uint16_t code, int32_t value) {
    struct input_event ev{};
    ev.type = type;
    ev.code = code;
    ev.value = value;
    (void)write(fd, &ev, sizeof(ev));
}

void mapToDeviceCoords(int screenX, int screenY, int& outX, int& outY) {
    // The capture/aim pipeline produces coordinates in the orientation of
    // the OVERLAY (landscape: long edge = X, short edge = Y). The physical
    // touchscreen panel is reported in portrait native orientation, so we
    // rotate 90deg CCW when the device is portrait-native.
    const int devWidthRange  = std::max(1, g_screenInfo.touchXMax - g_screenInfo.touchXMin);
    const int devHeightRange = std::max(1, g_screenInfo.touchYMax - g_screenInfo.touchYMin);

    if (g_displayWidth <= 0 || g_displayHeight <= 0) {
        outX = screenX;
        outY = screenY;
        return;
    }

    // Long edge of the overlay maps to the long edge of the panel.
    const bool screenIsLandscape = g_displayWidth >= g_displayHeight;
    const bool panelIsPortrait   = devHeightRange >= devWidthRange;

    if (screenIsLandscape && panelIsPortrait) {
        // Rotate 90deg CCW
        long deviceY = static_cast<long>(screenX) * devHeightRange / std::max(1, g_displayWidth);
        long deviceX = static_cast<long>(screenY) * devWidthRange  / std::max(1, g_displayHeight);
        // Invert Y so screen-right maps to panel-bottom (consistent with most OEMs).
        deviceY = devHeightRange - deviceY;
        outX = static_cast<int>(deviceX) + g_screenInfo.touchXMin;
        outY = static_cast<int>(deviceY) + g_screenInfo.touchYMin;
    } else {
        long deviceX = static_cast<long>(screenX) * devWidthRange  / std::max(1, g_displayWidth);
        long deviceY = static_cast<long>(screenY) * devHeightRange / std::max(1, g_displayHeight);
        outX = static_cast<int>(deviceX) + g_screenInfo.touchXMin;
        outY = static_cast<int>(deviceY) + g_screenInfo.touchYMin;
    }

    if (outX < g_screenInfo.touchXMin) outX = g_screenInfo.touchXMin;
    if (outX > g_screenInfo.touchXMax) outX = g_screenInfo.touchXMax;
    if (outY < g_screenInfo.touchYMin) outY = g_screenInfo.touchYMin;
    if (outY > g_screenInfo.touchYMax) outY = g_screenInfo.touchYMax;
}

void sendAimDownLocked(int devX, int devY) {
    // Type B multi-touch sequence for a fresh contact on slot 0.
    writeEvent(g_uInputTouchFd, EV_ABS, ABS_MT_SLOT, 0);
    writeEvent(g_uInputTouchFd, EV_ABS, ABS_MT_TRACKING_ID, kAimTrackingId);
    writeEvent(g_uInputTouchFd, EV_ABS, ABS_MT_POSITION_X, devX);
    writeEvent(g_uInputTouchFd, EV_ABS, ABS_MT_POSITION_Y, devY);
    writeEvent(g_uInputTouchFd, EV_ABS, ABS_MT_TOUCH_MAJOR, 6);
    writeEvent(g_uInputTouchFd, EV_ABS, ABS_MT_PRESSURE, 64);
    writeEvent(g_uInputTouchFd, EV_KEY, BTN_TOUCH, 1);
    writeEvent(g_uInputTouchFd, EV_SYN, SYN_REPORT, 0);
}

void sendAimMoveLocked(int devX, int devY) {
    writeEvent(g_uInputTouchFd, EV_ABS, ABS_MT_SLOT, 0);
    writeEvent(g_uInputTouchFd, EV_ABS, ABS_MT_POSITION_X, devX);
    writeEvent(g_uInputTouchFd, EV_ABS, ABS_MT_POSITION_Y, devY);
    writeEvent(g_uInputTouchFd, EV_SYN, SYN_REPORT, 0);
}

void sendAimUpLocked() {
    writeEvent(g_uInputTouchFd, EV_ABS, ABS_MT_SLOT, 0);
    writeEvent(g_uInputTouchFd, EV_ABS, ABS_MT_TRACKING_ID, -1);
    writeEvent(g_uInputTouchFd, EV_KEY, BTN_TOUCH, 0);
    writeEvent(g_uInputTouchFd, EV_SYN, SYN_REPORT, 0);
}

void sendTouchDownOrMove(int screenX, int screenY, bool forceDown) {
    if (g_uInputTouchFd < 0) return;
    int devX = 0, devY = 0;
    mapToDeviceCoords(screenX, screenY, devX, devY);

    std::lock_guard<std::mutex> lock(g_writeMutex);
    if (!g_isAimDown || forceDown) {
        sendAimDownLocked(devX, devY);
        g_isAimDown = true;
    } else {
        sendAimMoveLocked(devX, devY);
    }
}

void sendTouchUp() {
    if (g_uInputTouchFd < 0) return;
    std::lock_guard<std::mutex> lock(g_writeMutex);
    if (!g_isAimDown) return;
    sendAimUpLocked();
    g_isAimDown = false;
}

void touchInputStart() {
    if (g_uInputTouchFd >= 0) {
        return;
    }
    if (!probeRealTouchscreen(g_screenInfo)) {
        LOGE("Could not probe real touchscreen; using default ABS ranges");
        g_screenInfo = TouchScreenInfo{};
        g_screenInfo.valid = true;
    }
    g_uInputTouchFd = createUInputAim(g_screenInfo);
    if (g_uInputTouchFd < 0) {
        LOGE("Failed to create virtual touch device");
    } else {
        LOGI("Virtual aim touch device created (no EVIOCGRAB on real screen)");
    }
    g_isAimDown = false;
}

void touchInputStop() {
    if (g_uInputTouchFd < 0) return;
    sendTouchUp();
    ioctl(g_uInputTouchFd, UI_DEV_DESTROY);
    close(g_uInputTouchFd);
    g_uInputTouchFd = -1;
}

void updateRes(int x, int y) {
    g_displayWidth = x;
    g_displayHeight = y;
}

} // namespace

TouchHelper::TouchHelper()
    : backend_(TouchBackend::UINPUT)
    , initialized_(false)
    , shizukuBridgeAvailable_(false)
    , accessibilityBridgeAvailable_(false)
    , javaVm_(nullptr)
    , activityClassGlobal_(nullptr)
    , shizukuMoveMethod_(nullptr)
    , shizukuUpMethod_(nullptr)
    , accessibilityMoveMethod_(nullptr)
    , accessibilityUpMethod_(nullptr) {}

TouchHelper::~TouchHelper() {
    if (javaVm_ && activityClassGlobal_) {
        JNIEnv* env = nullptr;
        if (javaVm_->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) == JNI_OK && env) {
            env->DeleteGlobalRef(activityClassGlobal_);
        }
    }
    shutdown();
}

void TouchHelper::setBackend(TouchBackend backend) {
    if (backend_ == backend) {
        return;
    }
    releaseActiveTouch();
    shutdownUinput();
    initialized_ = false;
    backend_ = backend;
}

void TouchHelper::setJniBridge(JavaVM* vm, JNIEnv* env, jobject activityInstance) {
    javaVm_ = vm;
    if (!env || !activityInstance) {
        return;
    }

    if (activityClassGlobal_) {
        env->DeleteGlobalRef(activityClassGlobal_);
        activityClassGlobal_ = nullptr;
    }

    jclass localClass = env->GetObjectClass(activityInstance);
    if (!localClass) {
        return;
    }
    activityClassGlobal_ = static_cast<jclass>(env->NewGlobalRef(localClass));
    env->DeleteLocalRef(localClass);

    shizukuMoveMethod_ = nullptr;
    shizukuUpMethod_ = nullptr;
}

void TouchHelper::setShizukuBridgeAvailable(bool available) {
    shizukuBridgeAvailable_ = available;
    if (!available && backend_ == TouchBackend::SHIZUKU) {
        shutdown();
    }
}

void TouchHelper::setAccessibilityBridgeAvailable(bool available) {
    accessibilityBridgeAvailable_ = available;
    if (!available && backend_ == TouchBackend::ACCESSIBILITY) {
        shutdown();
    }
}

bool TouchHelper::init() {
    if (initialized_) {
        return true;
    }

    if (backend_ == TouchBackend::SHIZUKU) {
        initialized_ = initShizuku();
        return initialized_;
    }

    if (backend_ == TouchBackend::ACCESSIBILITY) {
        initialized_ = initAccessibility();
        return initialized_;
    }

    initialized_ = initUinput();
    return initialized_;
}

bool TouchHelper::initUinput() {
    touchInputStart();
    return g_uInputTouchFd >= 0;
}

bool TouchHelper::initShizuku() {
    return shizukuBridgeAvailable_;
}

bool TouchHelper::initAccessibility() {
    return accessibilityBridgeAvailable_;
}

void TouchHelper::releaseActiveTouch() {
    if (!initialized_) {
        return;
    }

    if (backend_ == TouchBackend::SHIZUKU) {
        callShizukuUp();
        return;
    }

    if (backend_ == TouchBackend::ACCESSIBILITY) {
        callAccessibilityUp();
        return;
    }

    sendTouchUp();
}

void TouchHelper::setScreenSize(int width, int height) {
    updateRes(width, height);
}

void TouchHelper::touchDown(int slot, float x, float y) {
    (void)slot;
    if (!initialized_ && !init()) {
        return;
    }

    if (backend_ == TouchBackend::SHIZUKU) {
        if (!callShizukuMove(x, y, true)) {
            initialized_ = false;
        }
        return;
    }

    if (backend_ == TouchBackend::ACCESSIBILITY) {
        if (!callAccessibilityMove(x, y, true)) {
            initialized_ = false;
        }
        return;
    }

    sendTouchDownOrMove(static_cast<int>(x), static_cast<int>(y), true);
}

void TouchHelper::touchMove(int slot, float x, float y) {
    (void)slot;
    if (!initialized_ && !init()) {
        return;
    }

    if (backend_ == TouchBackend::SHIZUKU) {
        if (!callShizukuMove(x, y, false)) {
            initialized_ = false;
        }
        return;
    }

    if (backend_ == TouchBackend::ACCESSIBILITY) {
        if (!callAccessibilityMove(x, y, false)) {
            initialized_ = false;
        }
        return;
    }

    sendTouchDownOrMove(static_cast<int>(x), static_cast<int>(y), false);
}

void TouchHelper::touchUp(int slot) {
    (void)slot;
    if (!initialized_) {
        return;
    }

    if (backend_ == TouchBackend::SHIZUKU) {
        if (!callShizukuUp()) {
            initialized_ = false;
        }
        return;
    }

    if (backend_ == TouchBackend::ACCESSIBILITY) {
        if (!callAccessibilityUp()) {
            initialized_ = false;
        }
        return;
    }

    sendTouchUp();
}

void TouchHelper::shutdown() {
    releaseActiveTouch();
    shutdownUinput();
    initialized_ = false;
}

bool TouchHelper::isInitialized() const {
    return initialized_;
}

void TouchHelper::shutdownUinput() {
    touchInputStop();
}

bool TouchHelper::ensureJniMethods(JNIEnv* env) {
    if (!env || !activityClassGlobal_) {
        return false;
    }

    if (!shizukuMoveMethod_) {
        shizukuMoveMethod_ = env->GetStaticMethodID(
            activityClassGlobal_,
            "nativeInjectShizukuAimMove",
            "(FFZ)Z"
        );
    }
    if (!shizukuUpMethod_) {
        shizukuUpMethod_ = env->GetStaticMethodID(
            activityClassGlobal_,
            "nativeInjectShizukuAimUp",
            "()Z"
        );
    }

    return shizukuMoveMethod_ && shizukuUpMethod_;
}

bool TouchHelper::callShizukuMove(float x, float y, bool isFirst) {
    if (!javaVm_ || !shizukuBridgeAvailable_) {
        return false;
    }

    JNIEnv* env = nullptr;
    bool attached = false;
    if (javaVm_->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK) {
        if (javaVm_->AttachCurrentThread(&env, nullptr) != JNI_OK) {
            return false;
        }
        attached = true;
    }

    bool ok = false;
    if (ensureJniMethods(env)) {
        const jboolean result = env->CallStaticBooleanMethod(
            activityClassGlobal_,
            shizukuMoveMethod_,
            static_cast<jfloat>(x),
            static_cast<jfloat>(y),
            static_cast<jboolean>(isFirst)
        );
        ok = (result == JNI_TRUE) && !env->ExceptionCheck();
        if (env->ExceptionCheck()) {
            env->ExceptionClear();
        }
    }

    if (attached) {
        javaVm_->DetachCurrentThread();
    }
    return ok;
}

bool TouchHelper::callShizukuUp() {
    if (!javaVm_ || !shizukuBridgeAvailable_) {
        return false;
    }

    JNIEnv* env = nullptr;
    bool attached = false;
    if (javaVm_->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK) {
        if (javaVm_->AttachCurrentThread(&env, nullptr) != JNI_OK) {
            return false;
        }
        attached = true;
    }

    bool ok = false;
    if (ensureJniMethods(env)) {
        const jboolean result = env->CallStaticBooleanMethod(activityClassGlobal_, shizukuUpMethod_);
        ok = (result == JNI_TRUE) && !env->ExceptionCheck();
        if (env->ExceptionCheck()) {
            env->ExceptionClear();
        }
    }

    if (attached) {
        javaVm_->DetachCurrentThread();
    }
    return ok;
}

bool TouchHelper::ensureAccessibilityJniMethods(JNIEnv* env) {
    if (!env || !activityClassGlobal_) {
        return false;
    }

    if (!accessibilityMoveMethod_) {
        accessibilityMoveMethod_ = env->GetStaticMethodID(
            activityClassGlobal_,
            "nativeInjectAccessibilityAimMove",
            "(FFZ)Z"
        );
    }
    if (!accessibilityUpMethod_) {
        accessibilityUpMethod_ = env->GetStaticMethodID(
            activityClassGlobal_,
            "nativeInjectAccessibilityAimUp",
            "()Z"
        );
    }

    return accessibilityMoveMethod_ && accessibilityUpMethod_;
}

bool TouchHelper::callAccessibilityMove(float x, float y, bool isFirst) {
    if (!javaVm_ || !accessibilityBridgeAvailable_) {
        return false;
    }

    JNIEnv* env = nullptr;
    bool attached = false;
    if (javaVm_->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK) {
        if (javaVm_->AttachCurrentThread(&env, nullptr) != JNI_OK) {
            return false;
        }
        attached = true;
    }

    bool ok = false;
    if (ensureAccessibilityJniMethods(env)) {
        const jboolean result = env->CallStaticBooleanMethod(
            activityClassGlobal_,
            accessibilityMoveMethod_,
            static_cast<jfloat>(x),
            static_cast<jfloat>(y),
            static_cast<jboolean>(isFirst)
        );
        ok = (result == JNI_TRUE) && !env->ExceptionCheck();
        if (env->ExceptionCheck()) {
            env->ExceptionClear();
        }
    }

    if (attached) {
        javaVm_->DetachCurrentThread();
    }
    return ok;
}

bool TouchHelper::callAccessibilityUp() {
    if (!javaVm_ || !accessibilityBridgeAvailable_) {
        return false;
    }

    JNIEnv* env = nullptr;
    bool attached = false;
    if (javaVm_->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK) {
        if (javaVm_->AttachCurrentThread(&env, nullptr) != JNI_OK) {
            return false;
        }
        attached = true;
    }

    bool ok = false;
    if (ensureAccessibilityJniMethods(env)) {
        const jboolean result = env->CallStaticBooleanMethod(activityClassGlobal_, accessibilityUpMethod_);
        ok = (result == JNI_TRUE) && !env->ExceptionCheck();
        if (env->ExceptionCheck()) {
            env->ExceptionClear();
        }
    }

    if (attached) {
        javaVm_->DetachCurrentThread();
    }
    return ok;
}
