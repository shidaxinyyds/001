/**
 * imgui_menu.cpp - Native ImGui menu implementation for GLSurfaceView
 * 
 * This provides the JNI bridge for ImGuiGLSurface.kt, handling:
 * - ImGui initialization with Android backend
 * - Menu rendering with settings controls
 * - Touch event processing
 */

#include <jni.h>
#include <android/asset_manager.h>
#include <android/asset_manager_jni.h>
#include <android/native_window.h>
#include <android/native_window_jni.h>
#include <android/configuration.h>
#include <GLES3/gl3.h>
#include <algorithm>
#include <atomic>
#include <cfloat>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <mutex>
#include <thread>
#include <vector>

#include "imgui/imgui.h"
#include "imgui/imgui_impl_android.h"
#include "imgui/imgui_impl_opengl3.h"
#include "settings.h"
#include "utils/logger.h"
#include "utils/vector2.h"
#include "utils/imgui_helper.h"
#include "renderer/esp_renderer.h"
#include "utils/aimbot_types.h"
#include "utils/detection_zone.h"
#include "renderer/box_smoothing.h"
#include "detector/yolo_detector.h"
#include "utils/i18n.h"

using aimbuddy::i18n::T;
using aimbuddy::i18n::Key;

// Forward declaration for shared config access (defined in esp_jni.cpp)
extern "C" ESP::RenderConfig* GetRenderConfig();
extern "C" bool GetLatestResultSnapshot(ESP::DetectionResult* out);
extern "C" void UpdateScreenSize(int width, int height);
extern "C" void GetCaptureSize(int* outWidth, int* outHeight);
extern UnifiedSettings g_settings;

// Global state for ImGui menu
static ANativeWindow* g_menuWindow = nullptr;
static bool g_imguiInitialized = false;
static int g_screenWidth = 0;
static int g_screenHeight = 0;
static std::atomic<bool> g_menuVisible{false};

// ---------------------------------------------------------------------------
// Touch input plumbing
//
// nativeMotionEvent() runs on the Android UI thread while nativeTick() renders
// on the GLSurfaceView render thread. ImGui's input event queue is NOT thread
// safe: pushing events from the UI thread races with NewFrame() consuming and
// clearing the queue on the render thread, so taps were regularly dropped and
// the menu felt "stuck" / extremely laggy.
//
// We now only enqueue raw touch samples here and translate them into ImGui
// input events from the render thread, right before NewFrame(). This also lets
// us implement swipe-to-scroll safely.
// ---------------------------------------------------------------------------
struct PendingTouch {
    int action;   // 0 = ACTION_DOWN, 1 = ACTION_UP, 2 = ACTION_MOVE
    float x;
    float y;
};
static std::mutex g_touchMutex;
static std::vector<PendingTouch> g_touchQueue;

// Render-thread-only drag state used for swipe-to-scroll inside the menu.
static float g_touchDownX = 0.0f;
static float g_touchDownY = 0.0f;
static float g_lastTouchY = 0.0f;
static bool  g_touchScrolling = false;
static int   g_framesSinceTouchDown = 0;
static float g_pendingScrollPx = 0.0f;
static bool  g_anyItemActiveLastFrame = false;
static std::atomic<bool> g_rootAvailable{false};  // Track root status
static std::atomic<bool> g_shizukuAvailable{false};
static std::atomic<bool> g_accessibilityAvailable{false};

// Icon position synced from Kotlin layer (existing SVG icon)
static ImVec2 g_iconPos = ImVec2(60.0f, 200.0f);  // Initial default
static constexpr float ICON_RADIUS = 44.0f;  // Match Kotlin icon size (44dp)

// Box smoothing for stable, jitter-free rendering
static ESP::BoxSmoother g_boxSmoother;
static std::array<ESP::BoundingBox, Config::MAX_DETECTIONS> g_smoothedBoxes;
static int g_smoothedCount = 0;
static bool g_settingsPendingSave = false;
static double g_settingsDirtyAt = 0.0;
static constexpr double SETTINGS_SAVE_DELAY_SEC = 0.35;
static std::chrono::steady_clock::time_point g_lastOverlayTickTime{};
static float g_measuredOverlayFps = 0.0f;
static float g_measuredInferenceMs = 0.0f;
static ImVec2 g_menuSize = ImVec2(0.0f, 0.0f);
static bool g_menuWasVisible = false;
// Tri-state (-1 = never pushed) so the very first frame always syncs the real
// streamer-mode value to the Java side, even when it matches the old default.
static int g_streamerModeAppliedState = -1;

extern "C" void NotifyStreamerModeChanged(bool enabled);

static float QuantizeStep(float value, float step) {
    if (step <= 0.0f) return value;
    return std::round(value / step) * step;
}

static void ShowSettingHelp(const char* description) {
    ImGui::SameLine();
    ImGui::TextDisabled("(?)");
    if (ImGui::IsItemHovered()) {
        ImGui::BeginTooltip();
        ImGui::PushTextWrapPos(ImGui::GetFontSize() * 30.0f);
        ImGui::TextUnformatted(description);
        ImGui::PopTextWrapPos();
        ImGui::EndTooltip();
    }
}

static void ApplyRenderConfigToUnifiedSettings(const ESP::RenderConfig& settings) {
    g_settings.boxColorR = settings.boxColorR.load(std::memory_order_relaxed);
    g_settings.boxColorG = settings.boxColorG.load(std::memory_order_relaxed);
    g_settings.boxColorB = settings.boxColorB.load(std::memory_order_relaxed);
    g_settings.boxThickness = settings.boxThickness.load(std::memory_order_relaxed);
    g_settings.confidenceThreshold = settings.confidenceThreshold.load(std::memory_order_relaxed);
    g_settings.fovRadius = settings.fovRadius.load(std::memory_order_relaxed);
    g_settings.showFPS = settings.showFPS.load(std::memory_order_relaxed);
    g_settings.showDetectionCount = settings.showDetectionCount.load(std::memory_order_relaxed);
    g_settings.showLabels = settings.showLabels.load(std::memory_order_relaxed);
    g_settings.drawLine = settings.drawLine.load(std::memory_order_relaxed);
    g_settings.drawDot = settings.drawDot.load(std::memory_order_relaxed);
    g_settings.enableSmoothing = settings.enableSmoothing.load(std::memory_order_relaxed);
    g_settings.smoothingFactor = settings.smoothingFactor.load(std::memory_order_relaxed);
    g_settings.aimbotEnabled = settings.aimbotEnabled.load(std::memory_order_relaxed);
    g_settings.headOffset = settings.headOffset.load(std::memory_order_relaxed);

    g_settings.screenWidth = g_screenWidth;
    g_settings.screenHeight = g_screenHeight;
    if (g_screenWidth > 0 && g_screenHeight > 0) {
        float ratioX = settings.touchCenterX.load(std::memory_order_relaxed);
        float ratioY = settings.touchCenterY.load(std::memory_order_relaxed);
        g_settings.touchX = ratioX * static_cast<float>(g_screenWidth);
        g_settings.touchY = ratioY * static_cast<float>(g_screenHeight);
    }
    g_settings.touchRadius = settings.touchRadius.load(std::memory_order_relaxed);
    g_settings.aimDelay = settings.aimDelay.load(std::memory_order_relaxed);

    g_settings.validate();
}

namespace {

// Read a system file (typically /system/fonts/*) into a heap buffer that
// ImGui's atlas will own. Returns nullptr if the file is unreadable.
unsigned char* ReadFile(const char* path, size_t& outSize) {
    FILE* f = std::fopen(path, "rb");
    if (!f) return nullptr;
    std::fseek(f, 0, SEEK_END);
    long len = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    if (len <= 0) { std::fclose(f); return nullptr; }
    unsigned char* buf = static_cast<unsigned char*>(IM_ALLOC(static_cast<size_t>(len)));
    if (!buf) { std::fclose(f); return nullptr; }
    size_t got = std::fread(buf, 1, static_cast<size_t>(len), f);
    std::fclose(f);
    if (got != static_cast<size_t>(len)) { IM_FREE(buf); return nullptr; }
    outSize = static_cast<size_t>(len);
    return buf;
}

// Load CJK font. Strategy:
//   1. Try /system/fonts/ - every modern Android device ships NotoSansCJK
//      pre-installed. This works without bundling a font in the APK.
//   2. Fall back to assets/fonts/cjk.ttf (user-supplied).
// Returns true on success. Loaded as a MERGED font over the default font
// so English glyphs stay crisp and CJK glyphs slot in only where needed.
bool TryLoadCjkFont(JNIEnv* env, jobject assetManager, float pixelSize) {
    ImGuiIO& io = ImGui::GetIO();
    const ImWchar* ranges = io.Fonts->GetGlyphRangesChineseSimplifiedCommon();

    // Step 1: system font paths. Order matters; first hit wins.
    const char* systemCandidates[] = {
        "/system/fonts/NotoSansCJK-Regular.ttc",
        "/system/fonts/NotoSerifCJK-Regular.ttc",
        "/system/fonts/DroidSansFallback.ttf",
        "/system/fonts/NotoSansSC-Regular.otf",
        "/system/fonts/MiSans-Regular.ttf",          // Xiaomi
        "/system/fonts/HarmonyOS_Sans_SC.ttf",        // HarmonyOS
        "/product/fonts/NotoSansCJK-Regular.ttc",
    };
    for (const char* path : systemCandidates) {
        size_t size = 0;
        unsigned char* buf = ReadFile(path, size);
        if (!buf) continue;
        ImFontConfig cfg;
        cfg.FontDataOwnedByAtlas = true;
        cfg.MergeMode = true;  // merge into the default font
        cfg.PixelSnapH = true;
        ImFont* font = io.Fonts->AddFontFromMemoryTTF(buf, static_cast<int>(size), pixelSize, &cfg, ranges);
        if (font) {
            LOGI("Loaded CJK font from system: %s (%zu bytes)", path, size);
            return true;
        }
    }

    // Step 2: APK assets fallback for older / stripped devices.
    if (env && assetManager) {
        AAssetManager* mgr = AAssetManager_fromJava(env, assetManager);
        const char* assetCandidates[] = {
            "fonts/cjk.ttf",
            "fonts/cjk.otf",
            "fonts/NotoSansSC-Regular.ttf",
            "fonts/NotoSansCJKsc-Regular.otf",
        };
        for (const char* path : assetCandidates) {
            AAsset* asset = AAssetManager_open(mgr, path, AASSET_MODE_BUFFER);
            if (!asset) continue;
            off_t size = AAsset_getLength(asset);
            if (size <= 0) { AAsset_close(asset); continue; }
            void* buf = IM_ALLOC(static_cast<size_t>(size));
            AAsset_read(asset, buf, static_cast<size_t>(size));
            AAsset_close(asset);
            ImFontConfig cfg;
            cfg.FontDataOwnedByAtlas = true;
            cfg.MergeMode = true;
            cfg.PixelSnapH = true;
            ImFont* font = io.Fonts->AddFontFromMemoryTTF(buf, static_cast<int>(size), pixelSize, &cfg, ranges);
            if (font) {
                LOGI("Loaded CJK font from assets: %s (%ld bytes)", path, static_cast<long>(size));
                return true;
            }
        }
    }

    LOGI("No CJK font found on this device; Chinese strings will render as tofu");
    return false;
}

} // namespace

// Initialize ImGui for GLSurfaceView rendering
extern "C" JNIEXPORT void JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeInit(JNIEnv* env, jclass /* this */, jobject assetManager, jobject surface) {
    LOGI("nativeImGuiInit called");
    
    if (g_imguiInitialized) {
        LOGI("ImGui already initialized, skipping");
        return;
    }

    if (!surface) {
        LOGE("Surface is null");
        return;
    }

    // Get native window from Surface
    g_menuWindow = ANativeWindow_fromSurface(env, surface);
    if (!g_menuWindow) {
        LOGE("Failed to get ANativeWindow from Surface");
        return;
    }

    LOGI("Got ANativeWindow: %p", g_menuWindow);

    // Query dimensions
    g_screenWidth = ANativeWindow_getWidth(g_menuWindow);
    g_screenHeight = ANativeWindow_getHeight(g_menuWindow);
    LOGI("Menu window: %dx%d", g_screenWidth, g_screenHeight);


    // Create ImGui context if not already created
    ImGuiContext* existingContext = ImGui::GetCurrentContext();
    if (!existingContext) {
        LOGI("Creating new ImGui context");
        IMGUI_CHECKVERSION();
        ImGui::CreateContext();
    } else {
        LOGI("Using existing ImGui context");
    }
    
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr; // No ini file
    io.ConfigFlags |= ImGuiConfigFlags_IsTouchScreen;
    io.ConfigWindowsMoveFromTitleBarOnly = true;
    io.DisplaySize = ImVec2(static_cast<float>(g_screenWidth), static_cast<float>(g_screenHeight));
    
    // Clean, minimal dark theme tuned for mobile overlay menus.
    ImGuiStyle& style = ImGui::GetStyle();

    const ImVec4 bgDark      = ImVec4(0.045f, 0.055f, 0.075f, 0.96f);
    const ImVec4 bgPanel    = ImVec4(0.085f, 0.105f, 0.140f, 1.00f);
    const ImVec4 bgHover    = ImVec4(0.120f, 0.145f, 0.190f, 1.00f);
    const ImVec4 bgActive   = ImVec4(0.155f, 0.185f, 0.240f, 1.00f);
    const ImVec4 accent     = ImVec4(0.42f, 0.56f, 0.78f, 1.00f);
    const ImVec4 accentHover= ImVec4(0.52f, 0.66f, 0.88f, 1.00f);

    style.Colors[ImGuiCol_WindowBg]            = bgDark;
    style.Colors[ImGuiCol_ChildBg]             = ImVec4(0.06f, 0.075f, 0.10f, 0.95f);
    style.Colors[ImGuiCol_TitleBg]             = bgPanel;
    style.Colors[ImGuiCol_TitleBgActive]       = bgActive;
    style.Colors[ImGuiCol_TitleBgCollapsed]    = bgPanel;
    style.Colors[ImGuiCol_Button]              = bgPanel;
    style.Colors[ImGuiCol_ButtonHovered]       = bgHover;
    style.Colors[ImGuiCol_ButtonActive]        = bgActive;
    style.Colors[ImGuiCol_SliderGrab]          = accent;
    style.Colors[ImGuiCol_SliderGrabActive]    = accentHover;
    style.Colors[ImGuiCol_CheckMark]           = accent;
    style.Colors[ImGuiCol_Header]              = bgPanel;
    style.Colors[ImGuiCol_HeaderHovered]       = bgHover;
    style.Colors[ImGuiCol_HeaderActive]        = bgActive;
    style.Colors[ImGuiCol_FrameBg]             = ImVec4(0.065f, 0.080f, 0.110f, 1.00f);
    style.Colors[ImGuiCol_FrameBgHovered]      = ImVec4(0.095f, 0.115f, 0.155f, 1.00f);
    style.Colors[ImGuiCol_FrameBgActive]       = ImVec4(0.125f, 0.150f, 0.200f, 1.00f);
    style.Colors[ImGuiCol_Tab]                 = ImVec4(0.075f, 0.095f, 0.125f, 1.00f);
    style.Colors[ImGuiCol_TabHovered]          = bgHover;
    style.Colors[ImGuiCol_TabActive]           = bgActive;
    style.Colors[ImGuiCol_TabUnfocused]        = ImVec4(0.060f, 0.075f, 0.100f, 1.00f);
    style.Colors[ImGuiCol_TabUnfocusedActive]  = ImVec4(0.110f, 0.135f, 0.180f, 1.00f);
    style.Colors[ImGuiCol_ScrollbarBg]         = ImVec4(0.035f, 0.045f, 0.060f, 0.90f);
    style.Colors[ImGuiCol_ScrollbarGrab]       = ImVec4(0.180f, 0.220f, 0.300f, 1.00f);
    style.Colors[ImGuiCol_ScrollbarGrabHovered]= ImVec4(0.260f, 0.310f, 0.420f, 1.00f);
    style.Colors[ImGuiCol_ScrollbarGrabActive] = ImVec4(0.340f, 0.400f, 0.540f, 1.00f);
    style.Colors[ImGuiCol_Separator]           = ImVec4(0.120f, 0.145f, 0.190f, 0.50f);
    style.Colors[ImGuiCol_TextDisabled]        = ImVec4(0.45f, 0.50f, 0.58f, 1.00f);

    // Scale UI + font to the device's real DPI so the menu is readable on
    // high-resolution phones (the old fixed 18px font looked tiny).
    float densityScale = 1.0f;
    AConfiguration* aconfig = AConfiguration_new();
    if (aconfig) {
        AConfiguration_fromAssetManager(aconfig, AAssetManager_fromJava(env, assetManager));
        int32_t dpi = AConfiguration_getDensity(aconfig);
        AConfiguration_delete(aconfig);
        if (dpi > 0) {
            densityScale = std::clamp(static_cast<float>(dpi) / 160.0f, 1.0f, 3.0f);
        }
    }
    // Base font size (both Latin and CJK built at this size, then uniformly
    // scaled by densityScale so they stay the same visual size).
    const float baseFontPx = 18.0f;
    ImFontConfig defaultFontCfg;
    defaultFontCfg.SizePixels = baseFontPx;
    defaultFontCfg.PixelSnapH = true;
    io.Fonts->AddFontDefault(&defaultFontCfg);
    TryLoadCjkFont(env, assetManager, baseFontPx);

    // Uniform scale: font size via FontGlobalScale, other widgets via ScaleAllSizes
    // (partial factor so the panel does not become absurdly large on tablets).
    io.FontGlobalScale = densityScale;
    style.ScaleAllSizes(1.0f + (densityScale - 1.0f) * 0.6f);

    // Final sizes/overrides after scaling so the visual proportions stay crisp.
    style.WindowRounding    = 12.0f;
    style.FrameRounding     = 6.0f;
    style.GrabRounding      = 6.0f;
    style.ChildRounding     = 8.0f;
    style.PopupRounding     = 8.0f;
    style.TabRounding       = 6.0f;
    style.ScrollbarRounding = 8.0f;
    style.WindowPadding     = ImVec2(16.0f, 14.0f);
    style.FramePadding      = ImVec2(10.0f, 7.0f);
    style.ItemSpacing       = ImVec2(10.0f, 7.0f);
    style.ItemInnerSpacing  = ImVec2(7.0f, 5.0f);
    style.WindowBorderSize  = 1.0f;
    style.ScrollbarSize     = 14.0f;
    style.GrabMinSize       = 16.0f;
    style.TabMinWidthForCloseButton = 0.0f;

    // Apply persisted language preference so the very first frame renders
    // in the user's chosen language without a one-frame English flash.
    aimbuddy::i18n::SetLanguage(g_settings.language);

    // Initialize backends
    LOGI("Initializing ImGui Android backend");
    ImGui_ImplAndroid_Init(g_menuWindow);

    LOGI("Initializing ImGui OpenGL3 backend");
    ImGui_ImplOpenGL3_Init("#version 300 es");

    g_imguiInitialized = true;
    LOGI("ImGui menu initialized successfully");
}

    // Handle surface size changes
    extern "C" JNIEXPORT void JNICALL
    Java_com_aimbuddy_ImGuiGLSurface_nativeSurfaceChanged(JNIEnv* /* env */, jclass /* this */, jint width, jint height) {
        if (!g_imguiInitialized) {
            return;
        }

        g_screenWidth = width;
        g_screenHeight = height;
        glViewport(0, 0, width, height);

        ImGuiIO& io = ImGui::GetIO();
        io.DisplaySize = ImVec2(static_cast<float>(width), static_cast<float>(height));

        UpdateScreenSize(width, height);
    }

// Translate queued touch samples into ImGui input events.
// MUST be called on the render thread, after the backend NewFrame() helpers and
// immediately before ImGui::NewFrame() (which drains the ImGui event queue).
static void ProcessPendingTouchEvents() {
    std::vector<PendingTouch> events;
    {
        std::lock_guard<std::mutex> lock(g_touchMutex);
        events.swap(g_touchQueue);
    }

    if (!g_menuVisible.load(std::memory_order_relaxed)) {
        // Menu closed between enqueue and drain: drop everything and reset.
        g_touchScrolling = false;
        g_pendingScrollPx = 0.0f;
        g_framesSinceTouchDown = 0;
        return;
    }

    ImGuiIO& io = ImGui::GetIO();

    // A vertical swipe longer than this (and dominant over the horizontal
    // component) is treated as a scroll gesture instead of a widget drag.
    //
    // Two thresholds: when the press did NOT land on a widget we engage very
    // early (10px) so blank space / label text scrolls like a native list.
    // When a widget DID grab the press we require a clearly deliberate swipe
    // (24px) before stealing the gesture, so checkboxes and sliders still work.
    // The previous build only allowed the first case, which is why dragging on
    // the text/checkbox rows - i.e. most of the menu - refused to scroll.
    constexpr float kScrollTriggerPx       = 10.0f;
    constexpr float kScrollTriggerActivePx = 24.0f;

    for (const PendingTouch& e : events) {
        switch (e.action) {
            case 0: { // ACTION_DOWN
                g_touchDownX = e.x;
                g_touchDownY = e.y;
                g_lastTouchY = e.y;
                g_touchScrolling = false;
                g_framesSinceTouchDown = 0;
                io.AddMousePosEvent(e.x, e.y);
                io.AddMouseButtonEvent(0, true);
                break;
            }

            case 2: { // ACTION_MOVE
                const float dx = e.x - g_touchDownX;
                const float dy = e.y - g_touchDownY;

                if (!g_touchScrolling) {
                    // Decide whether this gesture is a vertical scroll. A widget
                    // that grabbed the press gets a larger grace distance, but it
                    // can no longer veto scrolling outright.
                    const float trigger = g_anyItemActiveLastFrame
                                              ? kScrollTriggerActivePx
                                              : kScrollTriggerPx;
                    if (std::fabs(dy) > trigger &&
                        std::fabs(dy) > std::fabs(dx) * 1.5f) {
                        g_touchScrolling = true;
                        g_lastTouchY = e.y;
                        // Park the cursor outside the window BEFORE releasing so
                        // the pending mouse-up lands on nothing. Without this, a
                        // swipe that started on a checkbox would still toggle it
                        // (ImGui trickles press/release across frames).
                        io.AddMousePosEvent(-FLT_MAX, -FLT_MAX);
                        io.AddMouseButtonEvent(0, false);
                        break;
                    }
                }

                if (g_touchScrolling) {
                    // Keep the gesture alive even if the finger drifts horizontally,
                    // as long as it stays mostly vertical since scroll started.
                    const float moveDy = e.y - g_lastTouchY;
                    g_pendingScrollPx += moveDy;
                    g_lastTouchY = e.y;
                } else {
                    io.AddMousePosEvent(e.x, e.y);
                }
                break;
            }

            case 1: { // ACTION_UP
                if (g_touchScrolling) {
                    g_pendingScrollPx += (e.y - g_lastTouchY);
                    g_touchScrolling = false;
                } else {
                    io.AddMousePosEvent(e.x, e.y);
                    io.AddMouseButtonEvent(0, false);
                }
                g_lastTouchY = e.y;
                break;
            }

            default:
                break;
        }
    }

    ++g_framesSinceTouchDown;
}

// Render ImGui (menu + ESP)
extern "C" JNIEXPORT void JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeTick(JNIEnv* /* env */, jclass /* this */) {
    if (!g_imguiInitialized || !g_menuWindow) {
        return;
    }

    try {
        // Cap the *background* overlay (menu hidden) to ~60 FPS to avoid
        // burning GPU on high-refresh panels. When the menu is open we render
        // uncapped (vsync-limited) so taps and tab switches feel instant.
        constexpr int kBaseFpsCap = 60;
        const int fpsCap = g_menuVisible ? 0 : kBaseFpsCap;
        if (fpsCap > 0) {
            const auto minFrameTime = std::chrono::nanoseconds(1'000'000'000LL / fpsCap);
            if (g_lastOverlayTickTime.time_since_epoch().count() != 0) {
                const auto elapsed = std::chrono::steady_clock::now() - g_lastOverlayTickTime;
                if (elapsed < minFrameTime) {
                    std::this_thread::sleep_for(minFrameTime - elapsed);
                }
            }
        }
        const auto nowTick = std::chrono::steady_clock::now();
        if (g_lastOverlayTickTime.time_since_epoch().count() != 0) {
            const float dtSeconds = std::chrono::duration<float>(nowTick - g_lastOverlayTickTime).count();
            if (dtSeconds > 0.0f && dtSeconds <= 0.25f) {
                const float instantFps = 1.0f / dtSeconds;
                g_measuredOverlayFps = (g_measuredOverlayFps > 0.0f)
                    ? (g_measuredOverlayFps * 0.90f + instantFps * 0.10f)
                    : instantFps;
            }
        }
        g_lastOverlayTickTime = nowTick;

        // Start ImGui frame
        ImGui_ImplOpenGL3_NewFrame();
        ImGui_ImplAndroid_NewFrame();
        // Feed queued touches (thread-safe hand-off from the UI thread) before
        // NewFrame() consumes the ImGui input queue.
        ProcessPendingTouchEvents();
        ImGui::NewFrame();

        // Access global settings
        ESP::RenderConfig* settings = GetRenderConfig();
        if (!settings) {
            ImGui::EndFrame();
            ImGui::Render();
            glClearColor(0.0f, 0.0f, 0.0f, 0.0f);
            glClear(GL_COLOR_BUFFER_BIT);
            ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
            return;
        }


        ImVec2 displaySize = ImGui::GetIO().DisplaySize;
        float displayW = displaySize.x;
        float displayH = displaySize.y;

        // Always-visible center crosshair
        {
            ImDrawList* drawList = ImGui::GetBackgroundDrawList();
            const float centerX = displayW * 0.5f;
            const float centerY = displayH * 0.5f;
            const float crossArm = 11.5f;
            const ImU32 crossColor = IM_COL32(58, 156, 255, 240);
            drawList->AddLine(ImVec2(centerX - crossArm, centerY), ImVec2(centerX + crossArm, centerY), crossColor, 3.0f);
            drawList->AddLine(ImVec2(centerX, centerY - crossArm), ImVec2(centerX, centerY + crossArm), crossColor, 3.0f);
        }

        // Get detection results and apply smoothing if enabled
        ESP::DetectionResult latest;
        bool hasDetections = GetLatestResultSnapshot(&latest);
        if (latest.inferenceTimeMs > 0.01f && std::isfinite(latest.inferenceTimeMs)) {
            g_measuredInferenceMs = (g_measuredInferenceMs > 0.0f)
                ? (g_measuredInferenceMs * 0.88f + latest.inferenceTimeMs * 0.12f)
                : latest.inferenceTimeMs;
        }
        
        // Apply smoothing if enabled
        bool useSmoothing = settings->enableSmoothing.load(std::memory_order_relaxed);
        const ESP::BoundingBox* boxesToRender = nullptr;
        int boxCount = 0;
        
        // Temp buffer for BoxSmoother adaptation (FixedArray -> std::array)
        std::array<ESP::BoundingBox, Config::MAX_DETECTIONS> tempInputs;
        
        if (hasDetections) {
            if (useSmoothing) {
                float alpha = settings->smoothingFactor.load(std::memory_order_relaxed);
                // Copy to std::array for BoxSmoother signature compatibility
                std::copy(latest.boxes.begin(), latest.boxes.end(), tempInputs.begin());
                g_boxSmoother.update(tempInputs, latest.boxes.size(), g_smoothedBoxes, g_smoothedCount, alpha);
                boxesToRender = g_smoothedBoxes.data();
                boxCount = g_smoothedCount;
            } else {
                boxesToRender = latest.boxes.data();
                boxCount = latest.boxes.size();
            }
        } else if (useSmoothing) {
            // Clear immediately when detector has no boxes to avoid lingering ghosts.
            g_boxSmoother.clear();
            g_smoothedCount = 0;
            boxesToRender = g_smoothedBoxes.data();
            boxCount = g_smoothedCount;
        }
        
        if (boxCount > 0 && boxesToRender) {
            ImDrawList* drawList = ImGui::GetBackgroundDrawList();
            float r = settings->boxColorR.load(std::memory_order_relaxed);
            float g = settings->boxColorG.load(std::memory_order_relaxed);
            float b = settings->boxColorB.load(std::memory_order_relaxed);
            int thickness = settings->boxThickness.load(std::memory_order_relaxed);
            float threshold = settings->confidenceThreshold.load(std::memory_order_relaxed);
            bool showLabels = settings->showLabels.load(std::memory_order_relaxed);
            bool drawLine = settings->drawLine.load(std::memory_order_relaxed);
            bool drawDot = settings->drawDot.load(std::memory_order_relaxed);
            float headOffset = settings->headOffset.load(std::memory_order_relaxed);
            const float espFovRadius = settings->fovRadius.load(std::memory_order_relaxed);

            thickness = std::max(1, std::min(thickness, 5));
            ImU32 boxColor = ImGui::ColorConvertFloat4ToU32(ImVec4(r, g, b, 1.0f));
            ImU32 shadowColor = IM_COL32(0, 0, 0, 150);
            
            // Track closest enemy for snap line
            int closestEnemyIdx = -1;
            float closestDistSq = espFovRadius * espFovRadius;
            float centerX = displayW * 0.5f;
            float centerY = displayH * 0.5f;

            for (int i = 0; i < boxCount; ++i) {
                const ESP::BoundingBox& box = boxesToRender[i];
                if (box.confidence < threshold || box.width <= 0.0f || box.height <= 0.0f) {
                    continue;
                }

                // Apply user box-scale (expand/contract around the box center).
                const float boxScale = settings->boxScale.load(std::memory_order_relaxed);
                const float cx = box.x + box.width * 0.5f;
                const float cy = box.y + box.height * 0.5f;
                const float halfW = (box.width * 0.5f) * boxScale;
                const float halfH = (box.height * 0.5f) * boxScale;
                float left = cx - halfW;
                float top = cy - halfH;
                float right = cx + halfW;
                float bottom = cy + halfH;

                // Clamp to screen bounds
                if (left < 0.0f) left = 0.0f;
                if (top < 0.0f) top = 0.0f;
                if (right > displayW) right = displayW;
                if (bottom > displayH) bottom = displayH;

                // Final screen-space sanity filter: drop degenerate/ tiny boxes
                // and boxes that cover almost the entire screen (common in lobby
                // false positives caused by UI elements / full-screen panels).
                const float boxWpx = right - left;
                const float boxHpx = bottom - top;
                if (boxWpx < 14.0f || boxHpx < 14.0f) continue;
                if (boxWpx > displayW * 0.85f && boxHpx > displayH * 0.85f) continue;

                // Draw box with shadow for depth
                ESP::ImGuiHelper::DrawBox3D(
                    drawList,
                    ImVec2(left, top),
                    ImVec2(right, bottom),
                    boxColor,
                    static_cast<float>(thickness),
                    shadowColor
                );
                
                // Calculate head position and box center
                float boxCenterX = left + (right - left) * 0.5f;
                float headY = top + (bottom - top) * headOffset;
                
                // Draw head dot if enabled
                if (drawDot) {
                    drawList->AddCircleFilled(
                        ImVec2(boxCenterX, headY),
                        5.0f + thickness * 0.5f,
                        boxColor,
                        12
                    );
                }
                
                // Track closest enemy for snap line (within FOV)
                float dx = boxCenterX - centerX;
                float dy = headY - centerY;
                float distSq = dx * dx + dy * dy;
                if (distSq < closestDistSq) {
                    closestDistSq = distSq;
                    closestEnemyIdx = i;
                }

                if (showLabels) {
                    // Top-center label: "Enemy" with shadow
                    const char* enemyLabel = "敌人";
                    ImVec2 enemySize = ImGui::CalcTextSize(enemyLabel);
                    ImVec2 enemyPos(
                        left + (right - left - enemySize.x) * 0.5f,
                        top - enemySize.y - 4.0f
                    );
                    if (enemyPos.y < 0.0f) enemyPos.y = top + 2.0f;
                    ESP::ImGuiHelper::DrawTextWithShadow(drawList, enemyPos, boxColor, enemyLabel);

                    // Bottom-center accuracy with shadow
                    char accLabel[32];
                    snprintf(accLabel, sizeof(accLabel), "%.0f%%", box.confidence * 100.0f);
                    ImVec2 accSize = ImGui::CalcTextSize(accLabel);
                    ImVec2 accPos(
                        left + (right - left - accSize.x) * 0.5f,
                        bottom + 2.0f
                    );
                    if (accPos.y + accSize.y > displayH) accPos.y = bottom - accSize.y - 2.0f;
                    ESP::ImGuiHelper::DrawTextWithShadow(drawList, accPos, boxColor, accLabel);
                }
            }
            
            // Draw snap line to closest enemy
            if (drawLine && closestEnemyIdx >= 0) {
                const ESP::BoundingBox& enemyBox = boxesToRender[closestEnemyIdx];
                float boxCenterX = enemyBox.x + enemyBox.width * 0.5f;
                float headY = enemyBox.y + enemyBox.height * headOffset;
                
                ImU32 snapLineColor = IM_COL32(255, 100, 50, 255);
                drawList->AddLine(
                    ImVec2(centerX, centerY),
                    ImVec2(boxCenterX, headY),
                    snapLineColor,
                    2.5f
                );
            }
        }

        // Draw FOV overlays (ESP=blue box, Aimbot=red circle)
        {
            ImDrawList* drawList = ImGui::GetBackgroundDrawList();
            const bool aimbotEnabled = settings->aimbotEnabled.load(std::memory_order_relaxed);
            const float espFovRadius = settings->fovRadius.load(std::memory_order_relaxed);
            const float aimFovRadius = std::min(
                (g_settings.aimFovRadius > 0.0f) ? g_settings.aimFovRadius : espFovRadius,
                espFovRadius
            );

            if (espFovRadius > 0.0f) {
                const float centerX = displayW * 0.5f;
                const float centerY = displayH * 0.5f;

                int captureWidth = Config::CAPTURE_WIDTH;
                int captureHeight = Config::CAPTURE_HEIGHT;
                GetCaptureSize(&captureWidth, &captureHeight);
                const ESP::DetectionZoneMetrics zone = ESP::ComputeDetectionZoneMetrics(
                    espFovRadius,
                    g_screenWidth,
                    displayW,
                    displayH,
                    captureWidth,
                    captureHeight
                );

                const ImU32 espFovColor = IM_COL32(40, 140, 255, 220);
                const ImVec2 tl(centerX - zone.halfWidthPx, centerY - zone.halfHeightPx);
                const ImVec2 br(centerX + zone.halfWidthPx, centerY + zone.halfHeightPx);
                drawList->AddRect(tl, br, espFovColor, 0.0f, 0, 2.2f);

                if (aimbotEnabled && aimFovRadius > 0.0f) {
                    drawList->AddCircle(ImVec2(centerX, centerY), aimFovRadius, IM_COL32(255, 60, 60, 230), 64, 2.3f);
                }
            }
        }

        // Detection count overlay (red, centered at top with margin)
        int detCount = latest.boxes.size();
        if (settings->showDetectionCount.load(std::memory_order_relaxed) && detCount > 0) {
            ImDrawList* drawList = ImGui::GetBackgroundDrawList();
            ImU32 redColor = IM_COL32(255, 50, 50, 255);
            ImFont* font = ImGui::GetFont();
            if (drawList != nullptr && font != nullptr) {
                float largeSize = ImGui::GetFontSize() * 2.0f;
                float topMargin = 40.0f;  // Proper margin to avoid clipping

                // Format: "X enemy" or "X enemies"
                char countText[64];
                const char* label = "敌人";
                snprintf(countText, sizeof(countText), "%d %s", detCount, label);

                ImVec2 textSize = font->CalcTextSizeA(largeSize, FLT_MAX, 0.0f, countText);
                ImVec2 textPos((displayW - textSize.x) * 0.5f, topMargin);
                drawList->AddText(font, largeSize, textPos, redColor, countText);
            }
        }

        // Menu window  -  display-proportional size with scrollable tab layout
        g_menuVisible = settings->menuVisible.load(std::memory_order_relaxed);
        if (g_menuVisible) {
            bool settingsDirty = false;
            const float defaultMenuWidth  = std::max(460.0f, std::min(displayW * 0.48f, 860.0f));
            const float defaultMenuHeight = std::max(560.0f, std::min(displayH * 0.92f, 1060.0f));
            if (!g_menuWasVisible || g_menuSize.x <= 0.0f || g_menuSize.y <= 0.0f) {
                g_menuSize = ImVec2(defaultMenuWidth, defaultMenuHeight);
            }

            const float menuWidth = g_menuSize.x;
            const float menuHeight = g_menuSize.y;
            const float iconPad = 52.0f;
            float menuX = g_iconPos.x + ICON_RADIUS + iconPad;
            float menuY = g_iconPos.y - menuHeight * 0.5f;
            if (menuX + menuWidth > displayW)
                menuX = g_iconPos.x - ICON_RADIUS - iconPad - menuWidth;
            menuX = std::max(4.0f, std::min(menuX, displayW - menuWidth - 4.0f));
            menuY = std::max(4.0f, std::min(menuY, displayH - menuHeight - 4.0f));

            ImGui::SetNextWindowPos(ImVec2(menuX, menuY), ImGuiCond_Always);
            ImGui::SetNextWindowSize(g_menuSize, ImGuiCond_Appearing);
            ImGui::SetNextWindowSizeConstraints(ImVec2(420.0f, 460.0f), ImVec2(displayW - 8.0f, displayH - 8.0f));

            ImGuiWindowFlags windowFlags = ImGuiWindowFlags_NoCollapse
                                         | ImGuiWindowFlags_NoTitleBar
                                         | ImGuiWindowFlags_NoResize;
            // Keep the language in sync every frame so a toggle takes
            // effect immediately, even before the next save.
            aimbuddy::i18n::SetLanguage(g_settings.language);

            if (ImGui::Begin(T(Key::AppTitle), nullptr, windowFlags)) {
                g_menuSize = ImGui::GetWindowSize();
                const bool rootAvailable = g_rootAvailable.load(std::memory_order_relaxed);
                const bool shizukuAvailable = g_shizukuAvailable.load(std::memory_order_relaxed);
                if (!rootAvailable && !shizukuAvailable) {
                    ImGui::TextColored(ImVec4(1.0f, 0.35f, 0.35f, 1.0f), "%s", T(Key::BannerRootMissing));
                    // Only separate when there is actually something above it -
                    // the title bar is gone, so an unconditional rule would just
                    // draw a stray line at the very top of the panel.
                    ImGui::Separator();
                }

                ImGui::BeginChild("##MenuScroll", ImVec2(0, -ImGui::GetFrameHeightWithSpacing() - 6.0f), false, ImGuiWindowFlags_AlwaysVerticalScrollbar);

                // Apply swipe-to-scroll accumulated from touch input. ImGui has
                // no native touch scrolling, so a finger drag over the content
                // area is turned into a direct, pixel-accurate scroll here.
                if (g_pendingScrollPx != 0.0f) {
                    ImGui::SetScrollY(ImGui::GetScrollY() - g_pendingScrollPx);
                    g_pendingScrollPx = 0.0f;
                }

                if (ImGui::BeginTabBar("##MenuTabs", ImGuiTabBarFlags_FittingPolicyResizeDown)) {
                    if (ImGui::BeginTabItem(T(Key::TabEsp))) {
                        bool showLabels = settings->showLabels.load(std::memory_order_relaxed);
                        bool drawLine = settings->drawLine.load(std::memory_order_relaxed);
                        bool drawDot = settings->drawDot.load(std::memory_order_relaxed);
                        bool countOn = settings->showDetectionCount.load(std::memory_order_relaxed);
                        bool smoothOn = settings->enableSmoothing.load(std::memory_order_relaxed);

                        if (ImGui::Checkbox(T(Key::EspLabels), &showLabels)) { settings->showLabels.store(showLabels, std::memory_order_relaxed); settingsDirty = true; }
                        ShowSettingHelp("在每个检测到的目标上显示标签文字。");
                        if (ImGui::Checkbox(T(Key::EspSnapLine), &drawLine)) { settings->drawLine.store(drawLine, std::memory_order_relaxed); settingsDirty = true; }
                        ShowSettingHelp("从屏幕中心到目标框绘制一条连线。");
                        if (ImGui::Checkbox(T(Key::EspHeadDot), &drawDot)) { settings->drawDot.store(drawDot, std::memory_order_relaxed); settingsDirty = true; }
                        ShowSettingHelp("标记每个检测结果预估的头部位置。");
                        if (ImGui::Checkbox(T(Key::EspDetectionCount), &countOn)) { settings->showDetectionCount.store(countOn, std::memory_order_relaxed); settingsDirty = true; }
                        ShowSettingHelp("显示当前检测到的目标数量。");
                        ImGui::Separator();

                        if (ImGui::Checkbox(T(Key::EspEnableSmoothing), &smoothOn)) { settings->enableSmoothing.store(smoothOn, std::memory_order_relaxed); settingsDirty = true; }
                        ShowSettingHelp("稳定目标框的移动，减少帧间抖动。");
                        if (smoothOn) {
                            float smooth = settings->smoothingFactor.load(std::memory_order_relaxed);
                            if (ImGui::SliderFloat(T(Key::EspSmoothingAmount), &smooth, 0.10f, 1.0f, "%.2f")) {
                                settings->smoothingFactor.store(smooth, std::memory_order_relaxed);
                                settingsDirty = true;
                            }
                            ShowSettingHelp("数值越低反应越快；数值越高越平滑，但延迟更明显。");
                        }

                        ImGui::Separator();

                        float boxColor[4] = {
                            settings->boxColorR.load(std::memory_order_relaxed),
                            settings->boxColorG.load(std::memory_order_relaxed),
                            settings->boxColorB.load(std::memory_order_relaxed),
                            1.0f
                        };
                        if (ImGui::ColorEdit4(T(Key::EspBoxColor), boxColor, ImGuiColorEditFlags_NoInputs)) {
                            settings->boxColorR.store(boxColor[0], std::memory_order_relaxed);
                            settings->boxColorG.store(boxColor[1], std::memory_order_relaxed);
                            settings->boxColorB.store(boxColor[2], std::memory_order_relaxed);
                            settingsDirty = true;
                        }
                        ShowSettingHelp("更改 ESP 框颜色以提升可见性。");

                        float thickness = static_cast<float>(settings->boxThickness.load(std::memory_order_relaxed));
                        if (ImGui::SliderFloat(T(Key::EspBoxThickness), &thickness, 1.0f, 5.0f, "%.0f")) {
                            settings->boxThickness.store(static_cast<int>(thickness), std::memory_order_relaxed);
                            settingsDirty = true;
                        }
                        float boxScale = settings->boxScale.load(std::memory_order_relaxed);
                        if (ImGui::SliderFloat("识别框缩放", &boxScale, 0.5f, 3.0f, "%.2f")) {
                            settings->boxScale.store(boxScale, std::memory_order_relaxed);
                            settingsDirty = true;
                        }
                        ShowSettingHelp("放大或缩小识别框的显示尺寸（不影响检测范围，检测已覆盖全屏）。");
                        ShowSettingHelp("数值越高越容易看清；数值越低越简洁。");

                        ImGui::Separator();

                        float conf = settings->confidenceThreshold.load(std::memory_order_relaxed);
                        if (ImGui::SliderFloat(T(Key::EspConfidence), &conf, 0.1f, 0.95f, "%.2f")) {
                            settings->confidenceThreshold.store(conf, std::memory_order_relaxed);
                            settingsDirty = true;
                        }
                        ShowSettingHelp("最小检测置信度。数值越高，误报越少。");

                        float detFov = settings->fovRadius.load(std::memory_order_relaxed);
                        if (ImGui::SliderFloat(T(Key::EspDetectionZone), &detFov, 100.0f, 650.0f, "%.0f px")) {
                            settings->fovRadius.store(detFov, std::memory_order_relaxed);
                            if (g_settings.aimFovRadius > detFov) {
                                g_settings.aimFovRadius = detFov;
                            }
                            settingsDirty = true;
                        }
                        ShowSettingHelp("ESP 检测与瞄准生效的范围（以屏幕中心为圆心的半径）。数值越小越只关注屏幕中心区域。");

                        bool showTouchZone = g_settings.showTouchZone;
                        if (ImGui::Checkbox(T(Key::EspTouchZoneOverlay), &showTouchZone)) {
                            g_settings.showTouchZone = showTouchZone;
                            settingsDirty = true;
                        }
                        ShowSettingHelp("显示辅助移动所使用的触摸输入区域。");
                        if (g_settings.showTouchZone) {
                            float alpha = g_settings.touchZoneAlpha;
                            if (ImGui::SliderFloat(T(Key::EspTouchZoneOpacity), &alpha, 0.10f, 1.0f, "%.2f")) {
                                g_settings.touchZoneAlpha = QuantizeStep(alpha, 0.01f);
                                settingsDirty = true;
                            }
                            ShowSettingHelp("不透明度越低越不干扰；越高越容易定位。");
                        }

                        ImGui::Separator();
                        bool streamer = g_settings.streamerMode;
                        if (ImGui::Checkbox(T(Key::EspStreamerMode), &streamer)) {
                            g_settings.streamerMode = streamer;
                            settingsDirty = true;
                        }
                        ShowSettingHelp("将覆盖层标记为安全窗口。覆盖层仍显示在你的屏幕上，但不会被录屏、截图和屏幕镜像捕获。");

                        // Language picker. Items come from i18n::LanguageDisplayName.
                        const char* languageItems[2] = {
                            aimbuddy::i18n::LanguageDisplayName(0),
                            aimbuddy::i18n::LanguageDisplayName(1),
                        };
                        int langIndex = g_settings.language;
                        if (ImGui::Combo(T(Key::EspLanguage), &langIndex, languageItems, 2)) {
                            g_settings.language = langIndex;
                            aimbuddy::i18n::SetLanguage(langIndex);
                            settingsDirty = true;
                        }
                        ShowSettingHelp("界面语言。将 cjk.ttf 放入 assets/fonts/ 即可显示中文。");
                        ImGui::EndTabItem();
                    }

                    if (ImGui::BeginTabItem(T(Key::TabAim))) {
                        int touchBackend = g_settings.touchBackend;
                        const char* touchBackends[] = { "uinput（需 Root）", "Shizuku（免 Root）", "无障碍服务（免 Root）" };
                        if (ImGui::Combo(T(Key::AimTouchBackend), &touchBackend, touchBackends, 3)) {
                            g_settings.touchBackend = touchBackend;
                            settingsDirty = true;
                        }
                        if (touchBackend == 0) {
                            ImGui::TextDisabled("%s", rootAvailable ? T(Key::AimBackendStatusReady) : T(Key::AimBackendStatusMissingRoot));
                        } else if (touchBackend == 1) {
                            ImGui::TextDisabled("%s", shizukuAvailable ? T(Key::AimBackendStatusReady) : T(Key::AimBackendStatusMissingShizuku));
                        } else {
                            ImGui::TextDisabled("%s", g_accessibilityAvailable.load(std::memory_order_relaxed) ? T(Key::AimBackendStatusReady) : T(Key::AimBackendStatusMissingAccessibility));
                        }
                        ImGui::Separator();

                        const bool backendReady = (touchBackend == 0) ? rootAvailable
                                                  : (touchBackend == 1) ? shizukuAvailable
                                                  : g_accessibilityAvailable.load(std::memory_order_relaxed);
                        if (!backendReady) {
                            ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(1.0f, 0.35f, 0.35f, 1.0f));
                            if (touchBackend == 0) {
                                ImGui::TextUnformatted(T(Key::AimRootRequired));
                            } else if (touchBackend == 1) {
                                ImGui::TextUnformatted(T(Key::AimShizukuRequired));
                            } else {
                                ImGui::TextUnformatted(T(Key::AimAccessibilityRequired));
                            }
                            ImGui::PopStyleColor();
                        } else {
                        bool enabled = settings->aimbotEnabled.load(std::memory_order_relaxed);
                        if (ImGui::Checkbox(T(Key::AimEnable), &enabled)) {
                            settings->aimbotEnabled.store(enabled, std::memory_order_relaxed);
                            settingsDirty = true;
                            // Auto-close the menu once aim assist is enabled so the
                            // game underneath stays touchable (screen clickable/slidable).
                            if (enabled) {
                                settings->menuVisible.store(false, std::memory_order_relaxed);
                                g_menuVisible = false;
                            }
                        }

                            if (enabled) {
                                ImGui::Spacing();
                                if (ImGui::Button(T(Key::AimPresetDefault), ImVec2(150, 0))) {
                                    g_settings.aimMode = 0; g_settings.aimSpeed = 0.48f;
                                    g_settings.smoothness = 0.78f; g_settings.filterType = 1;
                                    g_settings.emaAlpha = 0.30f; g_settings.pdDerivativeGain = 0.030f;
                                    g_settings.velocityLeadFactor = 0.85f; g_settings.velocityLeadClamp = 60.0f;
                                    g_settings.enableConvergenceDamping = true; g_settings.convergenceRadius = 30.0f;
                                    g_settings.maxLockMissFrames = 2; g_settings.targetSwitchDelayFrames = 8;
                                    g_settings.recoilCompensationEnabled = false;
                                    g_settings.aimFovRadius = 240.0f;
                                    settings->headOffset.store(0.18f, std::memory_order_relaxed);
                                    settingsDirty = true;
                                }
                                ImGui::SameLine();
                                if (ImGui::Button(T(Key::AimPresetCompetitive), ImVec2(150, 0))) {
                                    g_settings.aimMode = 1; g_settings.aimSpeed = 0.72f;
                                    g_settings.smoothness = 0.45f; g_settings.filterType = 0;
                                    g_settings.emaAlpha = 0.30f; g_settings.pdDerivativeGain = 0.020f;
                                    g_settings.velocityLeadFactor = 1.10f; g_settings.velocityLeadClamp = 80.0f;
                                    g_settings.enableConvergenceDamping = true; g_settings.convergenceRadius = 22.0f;
                                    g_settings.maxLockMissFrames = 2; g_settings.targetSwitchDelayFrames = 5;
                                    g_settings.recoilCompensationEnabled = false;
                                    g_settings.aimFovRadius = 220.0f;
                                    settings->headOffset.store(0.15f, std::memory_order_relaxed);
                                    settingsDirty = true;
                                }
                                if (ImGui::Button(T(Key::AimPresetBalanced), ImVec2(150, 0))) {
                                    g_settings.aimMode = 0; g_settings.aimSpeed = 0.52f;
                                    g_settings.smoothness = 0.80f; g_settings.filterType = 1;
                                    g_settings.emaAlpha = 0.28f; g_settings.pdDerivativeGain = 0.032f;
                                    g_settings.velocityLeadFactor = 0.90f; g_settings.velocityLeadClamp = 65.0f;
                                    g_settings.enableConvergenceDamping = true; g_settings.convergenceRadius = 32.0f;
                                    g_settings.maxLockMissFrames = 2; g_settings.targetSwitchDelayFrames = 9;
                                    g_settings.recoilCompensationEnabled = false;
                                    g_settings.aimFovRadius = 260.0f;
                                    settings->headOffset.store(0.18f, std::memory_order_relaxed);
                                    settingsDirty = true;
                                }
                                ImGui::SameLine();
                                if (ImGui::Button(T(Key::AimPresetPrecision), ImVec2(150, 0))) {
                                    g_settings.aimMode = 2; g_settings.aimSpeed = 0.58f;
                                    g_settings.smoothness = 0.88f; g_settings.filterType = 2;
                                    g_settings.kalmanProcessNoise = 0.8f; g_settings.kalmanMeasurementNoise = 5.0f;
                                    g_settings.pdDerivativeGain = 0.025f; g_settings.velocityLeadFactor = 0.70f;
                                    g_settings.velocityLeadClamp = 50.0f;
                                    g_settings.enableConvergenceDamping = true; g_settings.convergenceRadius = 40.0f;
                                    g_settings.maxLockMissFrames = 2; g_settings.targetSwitchDelayFrames = 12;
                                    g_settings.recoilCompensationEnabled = false;
                                    g_settings.aimFovRadius = 300.0f;
                                    settings->headOffset.store(0.17f, std::memory_order_relaxed);
                                    settingsDirty = true;
                                }

                                ImGui::Separator();

                                float offset = settings->headOffset.load(std::memory_order_relaxed);
                                if (ImGui::SliderFloat(T(Key::AimHeadOffset), &offset, 0.0f, 0.5f, "%.2f")) {
                                    settings->headOffset.store(offset, std::memory_order_relaxed);
                                    settingsDirty = true;
                                }
                                ShowSettingHelp("调整框内垂直瞄准点。增大可瞄准更高位置。");

                                int priority = static_cast<int>(g_settings.targetPriority);
                                const char* priorities[] = { "最近", "最大", "置信度" };
                                if (ImGui::Combo(T(Key::AimTargetPriority), &priority, priorities, 3)) {
                                    g_settings.targetPriority = priority;
                                    settingsDirty = true;
                                }

                                ImGui::Separator();

                                if (g_settings.aimFovRadius > settings->fovRadius.load(std::memory_order_relaxed)) {
                                    g_settings.aimFovRadius = settings->fovRadius.load(std::memory_order_relaxed);
                                }

                                const char* aimModes[] = { "平滑", "瞬移", "磁吸" };
                                int aimMode = static_cast<int>(g_settings.aimMode);
                                if (ImGui::Combo(T(Key::AimMode), &aimMode, aimModes, 3)) { g_settings.aimMode = aimMode; settingsDirty = true; }

                                float aimSpeed = g_settings.aimSpeed;
                                if (ImGui::SliderFloat(T(Key::AimSpeed), &aimSpeed, 0.1f, 1.0f, "%.2f")) { g_settings.aimSpeed = QuantizeStep(aimSpeed, 0.01f); settingsDirty = true; }
                                ShowSettingHelp("数值越高移动越快；越低越慢越平滑。");

                                float smoothness = g_settings.smoothness;
                                if (ImGui::SliderFloat(T(Key::AimSmoothness), &smoothness, 0.0f, 1.0f, "%.2f")) { g_settings.smoothness = QuantizeStep(smoothness, 0.01f); settingsDirty = true; }
                                ShowSettingHelp("平滑度越高越自然，但反应越慢。");

                                float aimFov = g_settings.aimFovRadius;
                                if (ImGui::SliderFloat(T(Key::AimFovRadius), &aimFov, 50.0f, 600.0f, "%.0f px")) {
                                    g_settings.aimFovRadius = QuantizeStep(aimFov, 1.0f);
                                    if (g_settings.aimFovRadius > settings->fovRadius.load(std::memory_order_relaxed)) {
                                        g_settings.aimFovRadius = settings->fovRadius.load(std::memory_order_relaxed);
                                    }
                                    settingsDirty = true;
                                }
                                ShowSettingHelp("只有该半径范围内的目标才会被选中。");

                                float maxDist = g_settings.maxAimDistance;
                                if (ImGui::SliderFloat(T(Key::AimMaxDistance), &maxDist, 100.0f, 1000.0f, "%.0f px")) {
                                    g_settings.maxAimDistance = QuantizeStep(maxDist, 1.0f);
                                    settingsDirty = true;
                                }

                                int fps = static_cast<int>(g_settings.aimbotFps);
                                if (ImGui::SliderInt(T(Key::AimFps), &fps, 30, 120)) {
                                    g_settings.aimbotFps = static_cast<uint32_t>(fps);
                                    settingsDirty = true;
                                }
                                ShowSettingHelp("辅助逻辑刷新频率。越高响应越快，但更耗性能。");

                                ImGui::Separator();

                                const char* filterTypes[] = { "无", "EMA", "Kalman" };
                                int filterType = static_cast<int>(g_settings.filterType);
                                if (ImGui::Combo(T(Key::AimFilter), &filterType, filterTypes, 3)) {
                                    g_settings.filterType = filterType;
                                    settingsDirty = true;
                                }
                                if (g_settings.filterType == 1) {
                                    float ema = g_settings.emaAlpha;
                                    if (ImGui::SliderFloat("EMA 系数", &ema, 0.1f, 0.9f, "%.2f")) { g_settings.emaAlpha = QuantizeStep(ema, 0.01f); settingsDirty = true; }
                                } else if (g_settings.filterType == 2) {
                                    float pn = g_settings.kalmanProcessNoise;
                                    if (ImGui::SliderFloat("Kalman 过程噪声", &pn, 0.1f, 5.0f, "%.1f")) { g_settings.kalmanProcessNoise = QuantizeStep(pn, 0.1f); settingsDirty = true; }
                                    float mn = g_settings.kalmanMeasurementNoise;
                                    if (ImGui::SliderFloat("Kalman 测量噪声", &mn, 1.0f, 10.0f, "%.1f")) { g_settings.kalmanMeasurementNoise = QuantizeStep(mn, 0.1f); settingsDirty = true; }
                                }

                                bool antiOvershoot = g_settings.enableConvergenceDamping;
                                if (ImGui::Checkbox("抗超调", &antiOvershoot)) { g_settings.enableConvergenceDamping = antiOvershoot; settingsDirty = true; }
                                if (g_settings.enableConvergenceDamping) {
                                    float cr = g_settings.convergenceRadius;
                                    if (ImGui::SliderFloat("阻尼半径", &cr, 10.0f, 100.0f, "%.0f px")) { g_settings.convergenceRadius = QuantizeStep(cr, 1.0f); settingsDirty = true; }
                                }

                                float pdGain = g_settings.pdDerivativeGain;
                                if (ImGui::SliderFloat("微分阻尼", &pdGain, 0.0f, 0.12f, "%.3f")) { g_settings.pdDerivativeGain = QuantizeStep(pdGain, 0.005f); settingsDirty = true; }

                                float leadFactor = g_settings.velocityLeadFactor;
                                if (ImGui::SliderFloat("速度预判", &leadFactor, 0.0f, 1.5f, "%.2f")) { g_settings.velocityLeadFactor = QuantizeStep(leadFactor, 0.05f); settingsDirty = true; }
                                ShowSettingHelp("对移动目标的提前量强度。0 = 不预判，1.0 = 全力按速度×延迟预判。");
                                float leadClamp = g_settings.velocityLeadClamp;
                                if (ImGui::SliderFloat("预判上限", &leadClamp, 1.0f, 120.0f, "%.0f px")) { g_settings.velocityLeadClamp = QuantizeStep(leadClamp, 1.0f); settingsDirty = true; }

                                bool recoilOn = g_settings.recoilCompensationEnabled;
                                if (ImGui::Checkbox("后坐力补偿", &recoilOn)) { g_settings.recoilCompensationEnabled = recoilOn; settingsDirty = true; }
                                if (g_settings.recoilCompensationEnabled) {
                                    float rs = g_settings.recoilCompensationStrength;
                                    if (ImGui::SliderFloat("后坐力强度", &rs, 0.0f, 0.35f, "%.2f")) { g_settings.recoilCompensationStrength = QuantizeStep(rs, 0.01f); settingsDirty = true; }
                                    float rm = g_settings.recoilCompensationMax;
                                    if (ImGui::SliderFloat("后坐力最大值", &rm, 2.0f, 18.0f, "%.0f px")) { g_settings.recoilCompensationMax = QuantizeStep(rm, 1.0f); settingsDirty = true; }
                                    float rd = g_settings.recoilCompensationDecay;
                                    if (ImGui::SliderFloat("后坐力衰减", &rd, 0.50f, 0.98f, "%.2f")) { g_settings.recoilCompensationDecay = QuantizeStep(rd, 0.01f); settingsDirty = true; }
                                }

                                int missFrames = g_settings.maxLockMissFrames;
                                if (ImGui::SliderInt("丢失宽容", &missFrames, 1, 12, "%d")) { g_settings.maxLockMissFrames = missFrames; settingsDirty = true; }
                                int switchDelay = g_settings.targetSwitchDelayFrames;
                                if (ImGui::SliderInt("切换延迟", &switchDelay, 0, 20, "%d")) { g_settings.targetSwitchDelayFrames = switchDelay; settingsDirty = true; }

                                ImGui::Separator();

                                float tcx = settings->touchCenterX.load(std::memory_order_relaxed);
                                if (ImGui::SliderFloat("触摸中心 X", &tcx, 0.5f, 0.95f, "%.2f")) { settings->touchCenterX.store(tcx, std::memory_order_relaxed); settingsDirty = true; }
                                float tcy = settings->touchCenterY.load(std::memory_order_relaxed);
                                if (ImGui::SliderFloat("触摸中心 Y", &tcy, 0.3f, 0.7f, "%.2f")) { settings->touchCenterY.store(tcy, std::memory_order_relaxed); settingsDirty = true; }
                                float tr = settings->touchRadius.load(std::memory_order_relaxed);
                                if (ImGui::SliderFloat("触摸半径", &tr, 50.0f, 300.0f, "%.0f px")) { settings->touchRadius.store(tr, std::memory_order_relaxed); settingsDirty = true; }
                                float ad = settings->aimDelay.load(std::memory_order_relaxed);
                                if (ImGui::SliderFloat("瞄准延迟", &ad, 0.0f, 5.0f, "%.1f ms")) { settings->aimDelay.store(ad, std::memory_order_relaxed); settingsDirty = true; }

                                if (ImGui::Button("重置触摸区", ImVec2(220.0f, 0.0f))) {
                                    settings->touchCenterX.store(0.75f, std::memory_order_relaxed);
                                    settings->touchCenterY.store(0.5f, std::memory_order_relaxed);
                                    settings->touchRadius.store(150.0f, std::memory_order_relaxed);
                                    settings->aimDelay.store(0.0f, std::memory_order_relaxed);
                                    settingsDirty = true;
                                }
                            }
                        }
                        ImGui::EndTabItem();
                    }

                    if (ImGui::BeginTabItem(T(Key::TabInfo))) {
                        ImGui::Text("%s", T(Key::InfoTitle));
                        ImGui::Text("%s", T(Key::InfoVersion));
                        ImGui::Text("%s: %.0f", T(Key::InfoOverlayFps), g_measuredOverlayFps);
                        ImGui::Text("%s: %.1f ms", T(Key::InfoInferenceMs), g_measuredInferenceMs);
                        ImGui::Text("%s: %d", T(Key::InfoDetections), static_cast<int>(latest.boxes.size()));
                        ImGui::Text("%s: %dx%d", T(Key::InfoScreen), g_screenWidth, g_screenHeight);
                        ImGui::Separator();
                        ImGui::TextWrapped("%s", T(Key::InfoTip));
                        ImGui::EndTabItem();
                    }

                    ImGui::EndTabBar();
                }

                ImGui::EndChild();

                if (ImGui::Button(T(Key::SaveNow), ImVec2(160.0f, 0.0f))) {
                    ApplyRenderConfigToUnifiedSettings(*settings);
                    g_settings.validate();
                    g_settings.save();
                    g_settingsPendingSave = false;
                }
                ImGui::SameLine();
                ImGui::TextDisabled("%s", T(Key::AutoSaveHint));
            }
            if (settingsDirty) {
                ApplyRenderConfigToUnifiedSettings(*settings);
                g_settingsPendingSave = true;
                g_settingsDirtyAt = ImGui::GetTime();
            }
            ImGui::End();
            g_menuWasVisible = true;
        } else {
            g_menuWasVisible = false;
        }

        if (g_settingsPendingSave) {
            const double now = ImGui::GetTime();
            if ((now - g_settingsDirtyAt) >= SETTINGS_SAVE_DELAY_SEC && !ImGui::IsAnyItemActive()) {
                g_settings.validate();
                g_settings.save();
                g_settingsPendingSave = false;
            }
        }

        // Push streamer-mode state across the JNI bridge whenever it changes.
        const int streamerNow = g_settings.streamerMode ? 1 : 0;
        if (streamerNow != g_streamerModeAppliedState) {
            g_streamerModeAppliedState = streamerNow;
            NotifyStreamerModeChanged(streamerNow != 0);
        }

        // Latch widget-activity for the next frame's gesture classification
        // (a swipe only scrolls when no widget grabbed the initial press).
        g_anyItemActiveLastFrame = ImGui::IsAnyItemActive();
        if (!g_menuVisible.load(std::memory_order_relaxed)) {
            g_pendingScrollPx = 0.0f;
            g_touchScrolling = false;
        }

        // Render ImGui
        ImGui::Render();
        
        // Clear to transparent
        glClearColor(0.0f, 0.0f, 0.0f, 0.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        
        // Render ImGui draw data
        ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
    } catch (const std::exception& e) {
        LOGE("Exception in nativeImGuiRender: %s", e.what());
    } catch (...) {
        LOGE("Unknown exception in nativeImGuiRender");
    }
}

// Handle touch events
extern "C" JNIEXPORT jboolean JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeMotionEvent(
    JNIEnv* /* env */, jclass /* this */,
    jint action, jfloat x, jfloat y) {
    
    if (!g_imguiInitialized) {
        return JNI_FALSE;
    }

    // Menu closed -> let the touch fall through to the game underneath.
    if (!g_menuVisible.load(std::memory_order_relaxed)) {
        return JNI_FALSE;
    }

    if (action != 0 && action != 1 && action != 2) {
        return JNI_FALSE;
    }

    // Hand the sample over to the render thread. Never touch ImGui state here:
    // its input queue is not thread safe and racing with NewFrame() silently
    // drops taps (the root cause of the "menu takes forever to react" bug).
    {
        std::lock_guard<std::mutex> lock(g_touchMutex);
        // Guard against unbounded growth if the render thread ever stalls.
        if (g_touchQueue.size() < 512) {
            g_touchQueue.push_back(PendingTouch{static_cast<int>(action), x, y});
        }
    }

    // Consume input while the menu is visible.
    return JNI_TRUE;
}

// Expose whether ImGui wants to capture touch
extern "C" JNIEXPORT jboolean JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeWantsCapture(JNIEnv* /* env */, jclass /* this */) {
    if (!g_imguiInitialized) {
        return JNI_FALSE;
    }
    // Only capture touches while the menu is actually open. Relying on
    // io.WantCaptureMouse here previously kept the full-screen overlay
    // touchable (swallowing all game input) whenever a widget briefly held
    // capture, which made the underlying screen unclickable/unslidable.
    return g_menuVisible ? JNI_TRUE : JNI_FALSE;
}

extern "C" JNIEXPORT void JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeSetMenuVisible(JNIEnv* /* env */, jclass /* this */, jboolean visible) {
    ESP::RenderConfig* settings = GetRenderConfig();
    if (settings) {
        settings->menuVisible.store(visible == JNI_TRUE, std::memory_order_relaxed);
    }
    g_menuVisible = (visible == JNI_TRUE);
}

// Expose current menu visibility to the Kotlin layer so the floating-icon
// toggle always reflects the real state (the native aim-enable path can close
// the menu directly, desyncing the Kotlin-side flag).
extern "C" JNIEXPORT jboolean JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeIsMenuVisible(JNIEnv* /* env */, jclass /* this */) {
    return g_menuVisible ? JNI_TRUE : JNI_FALSE;
}


// Shutdown ImGui
extern "C" JNIEXPORT void JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeShutdown(JNIEnv* /* env */, jclass /* this */) {
    if (!g_imguiInitialized) {
        return;
    }

    LOGI("Shutting down ImGui menu");
    
    ImGui_ImplOpenGL3_Shutdown();
    ImGui_ImplAndroid_Shutdown();
    ImGui::DestroyContext();
    
    if (g_menuWindow) {
        ANativeWindow_release(g_menuWindow);
        g_menuWindow = nullptr;
    }
    
    g_imguiInitialized = false;
    LOGI("ImGui menu shutdown complete");
}

// Set icon position from Kotlin layer (for menu positioning)
extern "C" JNIEXPORT void JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeSetIconPosition(JNIEnv* /* env */, jclass /* this */, jfloat x, jfloat y) {
    g_iconPos.x = x + (ICON_RADIUS * 0.5f);  // Adjust to center of icon
    g_iconPos.y = y + (ICON_RADIUS * 0.5f);
}

extern "C" JNIEXPORT void JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeSetRootAvailable(JNIEnv* /* env */, jclass /* this */, jboolean available) {
    g_rootAvailable.store(available == JNI_TRUE, std::memory_order_relaxed);
    LOGI("Root status updated: %s", available ? "AVAILABLE" : "NOT AVAILABLE");
}

extern "C" JNIEXPORT void JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeSetShizukuAvailable(JNIEnv* /* env */, jclass /* this */, jboolean available) {
    g_shizukuAvailable.store(available == JNI_TRUE, std::memory_order_relaxed);
    LOGI("Shizuku status updated: %s", available ? "AVAILABLE" : "NOT AVAILABLE");
}

extern "C" JNIEXPORT void JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeSetAccessibilityAvailable(JNIEnv* /* env */, jclass /* this */, jboolean available) {
    g_accessibilityAvailable.store(available == JNI_TRUE, std::memory_order_relaxed);
    LOGI("Accessibility status updated: %s", available ? "AVAILABLE" : "NOT AVAILABLE");
}

extern "C" bool IsImGuiMenuVisible() {
    return g_menuVisible;
}
