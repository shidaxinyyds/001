#ifndef NATIVESURFACE_DRAW_H
#define NATIVESURFACE_DRAW_H
// System libs
#include <iostream>
#include <thread>
#include <chrono>
#include <dlfcn.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <cstdio>
#include <cstring>
#include <cerrno>
#include <android/native_window.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES/gl.h>
#include <GLES3/gl3platform.h>
#include <GLES3/gl3ext.h>
#include <GLES3/gl32.h>

// User libs
#include "ImGui/ANativeWindowCreator.h"
#include "ImGui/imgui.h"
#include "ImGui/imgui_internal.h"
#include "ImGui/imgui_impl_android.h"
#include "ImGui/TouchHelperA.h"

/*
#include "ImGui/imgui_impl_vulkan.h"
#include "Vulkan/VulkanUtils.h"
*/
#include "ImGui/imgui_impl_opengl3.h"

// namespace
using namespace std;
using namespace std::chrono_literals;

// 屏幕信息
extern android::ANativeWindowCreator::DisplayInfo displayInfo;

extern bool g_Initialized;
extern int Orientation;

extern ImGuiWindow *g_window;
// window创建的 大小
extern int native_window_screen_x, native_window_screen_y;
// 绝对屏幕X _ Y
extern int abs_ScreenX, abs_ScreenY;
// Func
bool init_egl(uint32_t _screen_x, uint32_t _screen_y, bool log = false);

bool initGUI_draw(uint32_t _screen_x, uint32_t _screen_y, bool log = false);

bool ImGui_init();

void screen_config();

void drawBegin();

void drawEnd();

void shutdown();

int 音量();

#endif //NATIVESURFACE_DRAW_H
