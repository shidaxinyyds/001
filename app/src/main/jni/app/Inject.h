/**
 * Inject.h — 直装(免root)注入引导模块
 *
 * 核心原理:
 *   1. libCube.so 被注入到游戏 APK 内, 游戏启动时 System.loadLibrary("Cube") 加载本 .so
 *   2. JNI_OnLoad 自动执行: Hook eglSwapBuffers + 启动触摸轮询线程
 *   3. Hook 的 eglSwapBuffers 在每帧渲染 ImGui 菜单 (复用 main.cpp 的 BeginDraw/DrawPlayer/EndDraw)
 *   4. 内存读写: pid = getpid(), process_vm_readv 对自身进程不需要 root 权限
 *
 * 优势:
 *   - 不需要 root
 *   - 不需要 sharedUserId
 *   - 不需要单独的悬浮窗 App
 *   - 安装一个游戏 APK 即可使用 (开箱即用)
 *
 * 编译开关: -DDIRECT_INSTALL (在 Android.mk 中通过 DIRECT_INSTALL:=true 启用)
 */

#pragma once

#ifdef DIRECT_INSTALL

#include <jni.h>
#include <dlfcn.h>
#include <EGL/egl.h>
#include <pthread.h>
#include <poll.h>
#include <fcntl.h>
#include <linux/input.h>
#include <sys/ioctl.h>
#include <android/asset_manager.h>
#include <android/asset_manager_jni.h>
#include "And64InlineHook.h"

#define INJECT_TAG "DirectInstall"
#define INJECT_LOGI(...) __android_log_print(ANDROID_LOG_INFO,  INJECT_TAG, __VA_ARGS__)
#define INJECT_LOGE(...) __android_log_print(ANDROID_LOG_ERROR, INJECT_TAG, __VA_ARGS__)

// ---- 引用 main.cpp / Memory.h 中的全局变量和函数 ----
extern int screenWidth, screenHeight, ScreenX, ScreenY;
extern int pid;
extern bool g_Initialized;
extern ImFont *font;
extern float px, py;

// main.cpp 中定义的渲染函数
extern void BeginDraw();
extern void DrawPlayer(ImDrawList *Draw);
extern void EndDraw();
extern void 颜色初始化();

// 注意: font_awesome_data 和 font_awesome_size 是 static 变量, 定义在 Iconcpp.h 中
// 由于 Inject.h 在 main.cpp 末尾引入 (同一编译单元), 可直接使用, 无需 extern

// ---- 模块内部状态 ----
static JavaVM *g_vm = nullptr;
static EGLBoolean (*orig_eglSwapBuffers)(EGLDisplay, EGLSurface) = nullptr;
static pthread_t g_touch_tid;
static bool g_menu_visible = true;  // 菜单是否显示, 可通过音量键切换

// ============================================================
//  字体加载: 从游戏 assets 读取 Font.ttf
// ============================================================
static void LoadFontFromAssets()
{
    if (!g_vm) return;
    JNIEnv *env;
    if (g_vm->AttachCurrentThread(&env, nullptr) != JNI_OK) return;

    // ActivityThread.currentApplication() → 获取当前 Application 对象
    jclass cls_AT = env->FindClass("android/app/ActivityThread");
    if (!cls_AT) { g_vm->DetachCurrentThread(); return; }
    jmethodID mid_curr = env->GetStaticMethodID(
        cls_AT, "currentApplication", "()Landroid/app/Application;");
    jobject app = env->CallStaticObjectMethod(cls_AT, mid_curr);
    if (!app) { g_vm->DetachCurrentThread(); return; }

    // app.getAssets() → 获取 AssetManager
    jmethodID mid_assets = env->GetMethodID(
        env->GetObjectClass(app), "getAssets",
        "()Landroid/content/res/AssetManager;");
    jobject java_am = env->CallObjectMethod(app, mid_assets);

    // 转为 native AAssetManager
    AAssetManager *mgr = AAssetManager_fromJava(env, java_am);
    if (mgr) {
        // 尝试打开 Font.ttf (打包时放入游戏 assets)
        AAsset *asset = AAssetManager_open(mgr, "Font.ttf", AASSET_MODE_BUFFER);
        if (asset) {
            size_t size = AAsset_getLength(asset);
            const void *buf = AAsset_getBuffer(asset);
            if (buf && size > 0) {
                // 复制到堆内存 (AAsset 释放后缓冲区失效)
                void *font_buf = malloc(size);
                memcpy(font_buf, buf, size);
                ImGuiIO &io = ImGui::GetIO();
                font = io.Fonts->AddFontFromMemoryTTF(
                    font_buf, size, 45.0f, nullptr,
                    io.Fonts->GetGlyphRangesChineseFull());
                INJECT_LOGI("字体加载成功, 大小=%zu", size);
            }
            AAsset_close(asset);
        } else {
            INJECT_LOGE("assets/Font.ttf 未找到, 使用默认字体");
        }
    }

    env->DeleteLocalRef(java_am);
    env->DeleteLocalRef(app);
    g_vm->DetachCurrentThread();
}

// ============================================================
//  ImGui 初始化 (直装模式, 不依赖 Java 层 GLSurfaceView)
// ============================================================
static void DirectInstallInit()
{
    if (g_Initialized) return;

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO &io = ImGui::GetIO();
    io.IniFilename = nullptr;  // 不保存 ini

    ImGui::StyleColorsYellow();

    // 初始化 ImGui 后端 (Android 平台 + OpenGL3 渲染器)
    ImGui_ImplAndroid_Init();
    ImGui_ImplOpenGL3_Init("#version 300 es");

    // 加载中文字体
    LoadFontFromAssets();

    // 加载图标字体 (Font Awesome, 内嵌在 Icon.h 中)
    static const ImWchar icons_ranges[] = {0xf000, 0xf3ff, 0};
    ImFontConfig icons_config;
    icons_config.MergeMode = true;
    icons_config.PixelSnapH = true;
    io.Fonts->AddFontFromMemoryCompressedTTF(
        (const void *)font_awesome_data, font_awesome_size, 60.0f,
        &icons_config, icons_ranges);

    // 应用样式 (与原始 init() 保持一致)
    ImGui::GetStyle().ScaleAllSizes(2.0f);
    ImGui::SetNextWindowBgAlpha(5.0);
    ImGuiStyle &style = ImGui::GetStyle();
    style.ScaleAllSizes(1.8f);
    style.WindowMenuButtonPosition = 0;
    style.WindowRounding = 10.0f;
    style.FrameRounding = 1.0f;
    style.FrameBorderSize = 0.3f;
    style.ScrollbarRounding = 3.0f;
    style.ScrollbarSize = 60.0f;
    style.GrabRounding = 8.0f;
    style.GrabMinSize = 20.0f;

    g_Initialized = true;
    INJECT_LOGI("ImGui 直装初始化完成");
}

// ============================================================
//  eglSwapBuffers Hook: 每帧渲染 ImGui
// ============================================================
static EGLBoolean hook_eglSwapBuffers(EGLDisplay dpy, EGLSurface surface)
{
    // 获取当前 EGL Surface 尺寸 (即游戏画面分辨率)
    EGLint width = 0, height = 0;
    eglQuerySurface(dpy, surface, EGL_WIDTH, &width);
    eglQuerySurface(dpy, surface, EGL_HEIGHT, &height);
    if (width > 0 && height > 0) {
        screenWidth = width;
        screenHeight = height;
        ScreenX = width;
        ScreenY = height;
    }

    // 首帧初始化 ImGui
    if (!g_Initialized) {
        DirectInstallInit();
    }

    // 渲染 ImGui 菜单
    if (g_Initialized) {
        ImGuiIO &io = ImGui::GetIO();
        io.DisplaySize = ImVec2((float)screenWidth, (float)screenHeight);

        ImGui_ImplOpenGL3_NewFrame();
        ImGui_ImplAndroid_NewFrame(screenWidth, screenHeight);
        ImGui::NewFrame();

        if (g_menu_visible) {
            BeginDraw();
            颜色初始化();
        }

        // ESP 绘制始终执行 (透视等功能不依赖菜单是否展开)
        DrawPlayer(ImGui::GetBackgroundDrawList());

        if (g_menu_visible) {
            EndDraw();
        }

        ImGui::Render();
        // 注意: 不调用 glClear, 否则游戏画面会被清除
        ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
    }

    // 调用原始 eglSwapBuffers, 将画面呈现到屏幕
    return orig_eglSwapBuffers(dpy, surface);
}

// ============================================================
//  触摸输入轮询线程: 读取 /dev/input/event* 触摸事件
//  原理: 游戏进程属于 input 组, 有权限读取触摸设备
//  事件不会被消费, 游戏仍能正常收到触摸 (只读模式)
// ============================================================
static void *TouchPollThread(void *)
{
    // ---- 1. 查找触摸设备 ----
    int fd = -1;
    for (int i = 0; i < 16; i++) {
        char path[32];
        snprintf(path, sizeof(path), "/dev/input/event%d", i);
        int tmp = open(path, O_RDONLY);
        if (tmp < 0) continue;

        // 检查是否支持绝对坐标 (触摸屏特征)
        unsigned long absbits = 0;
        if (ioctl(tmp, EVIOCGBIT(EV_ABS, sizeof(absbits)), &absbits) >= 0) {
            if (absbits & (1UL << ABS_MT_POSITION_X)) {
                fd = tmp;
                INJECT_LOGI("找到触摸设备: %s", path);
                break;
            }
        }
        close(tmp);
    }
    if (fd < 0) {
        INJECT_LOGE("未找到触摸设备");
        return nullptr;
    }

    // ---- 2. 获取触摸坐标范围 (用于坐标转换) ----
    struct input_absinfo xinfo, yinfo;
    memset(&xinfo, 0, sizeof(xinfo));
    memset(&yinfo, 0, sizeof(yinfo));
    ioctl(fd, EVIOCGABS(ABS_MT_POSITION_X), &xinfo);
    ioctl(fd, EVIOCGABS(ABS_MT_POSITION_Y), &yinfo);
    int xrange = xinfo.maximum - xinfo.minimum;
    int yrange = yinfo.maximum - yinfo.minimum;
    if (xrange <= 0) xrange = 1;
    if (yrange <= 0) yrange = 1;

    // ---- 3. 轮询触摸事件 ----
    int touching = 0;
    int raw_x = 0, raw_y = 0;
    struct input_event ev;

    while (true) {
        ssize_t n = read(fd, &ev, sizeof(ev));
        if (n != (ssize_t)sizeof(ev)) continue;

        switch (ev.type) {
        case EV_ABS:
            if (ev.code == ABS_MT_POSITION_X)
                raw_x = ev.value;
            else if (ev.code == ABS_MT_POSITION_Y)
                raw_y = ev.value;
            break;

        case EV_KEY:
            if (ev.code == BTN_TOUCH) {
                touching = ev.value;
                if (g_Initialized) {
                    ImGuiIO &io = ImGui::GetIO();
                    io.MouseDown[0] = touching;
                }
            }
            // 音量下键: 切换菜单显示/隐藏
            if (ev.code == KEY_VOLUMEDOWN && ev.value == 1) {
                g_menu_visible = !g_menu_visible;
                INJECT_LOGI("菜单: %s", g_menu_visible ? "显示" : "隐藏");
            }
            break;

        case EV_SYN:
            if (ev.code == SYN_REPORT && g_Initialized && touching) {
                // 将触摸原始坐标转换为屏幕像素坐标
                float sx = (float)(raw_x - xinfo.minimum) / xrange * screenWidth;
                float sy = (float)(raw_y - yinfo.minimum) / yrange * screenHeight;
                ImGuiIO &io = ImGui::GetIO();
                io.MousePos = ImVec2(sx, sy);
            }
            break;
        }
    }
    close(fd);
    return nullptr;
}

// ============================================================
//  等待目标库加载 (libUE4.so) 后再安装 Hook
//  避免在游戏库尚未加载时就尝试读取内存
// ============================================================
static void *WaitAndHookThread(void *)
{
    // 等待 libUE4.so 加载 (通过检测 /proc/self/maps)
    int wait_count = 0;
    while (true) {
        FILE *fp = fopen("/proc/self/maps", "r");
        if (fp) {
            char line[1024];
            bool found = false;
            while (fgets(line, sizeof(line), fp)) {
                if (strstr(line, "libUE4.so")) {
                    found = true;
                    break;
                }
            }
            fclose(fp);
            if (found) {
                INJECT_LOGI("libUE4.so 已加载 (等待 %d 次)", wait_count);
                break;
            }
        }
        wait_count++;
        if (wait_count > 300) {  // 最多等待 ~30 秒
            INJECT_LOGE("等待 libUE4.so 超时, 继续 Hook");
            break;
        }
        usleep(100000);  // 100ms
    }

    // Hook eglSwapBuffers
    void *egl_swap = dlsym(RTLD_DEFAULT, "eglSwapBuffers");
    if (egl_swap) {
        bool ok = A64HookFunction(egl_swap, (void *)hook_eglSwapBuffers,
                                  (void **)&orig_eglSwapBuffers);
        if (ok) {
            INJECT_LOGI("eglSwapBuffers Hook 成功");
        } else {
            INJECT_LOGE("eglSwapBuffers Hook 失败");
        }
    } else {
        INJECT_LOGE("dlsym eglSwapBuffers 失败");
    }

    return nullptr;
}

// ============================================================
//  JNI_OnLoad — .so 被游戏加载时的入口点
//  这是整个直装模式的启动入口, 无需 Java 层手动调用
// ============================================================
extern "C" JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM *vm, void *reserved)
{
    g_vm = vm;

    // 关键: 设置 pid 为自身进程! 我们运行在游戏进程内
    // process_vm_readv / process_vm_writev 对自身进程不需要 root 权限
    pid = getpid();
    INJECT_LOGI("直装模式启动, pid=%d (游戏进程内)", pid);

    // 启动等待线程: 等 libUE4.so 加载后 Hook eglSwapBuffers
    pthread_t hook_tid;
    pthread_create(&hook_tid, nullptr, WaitAndHookThread, nullptr);
    pthread_detach(hook_tid);

    // 启动触摸输入轮询线程
    pthread_create(&g_touch_tid, nullptr, TouchPollThread, nullptr);
    pthread_detach(g_touch_tid);

    INJECT_LOGI("直装注入完成, 等待游戏渲染...");
    return JNI_VERSION_1_6;
}

#endif // DIRECT_INSTALL
