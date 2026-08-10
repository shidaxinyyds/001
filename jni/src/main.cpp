#include <stdio.h>
#include <unistd.h>
#include <stdlib.h>
#include <fcntl.h>
#include <dirent.h>
#include <pthread.h>
#include <fstream>
#include <string.h>
#include <time.h>
#include <malloc.h>
#include <iostream>
#include <fstream>
#include "res/weiyan.h"
#include "res/cJSON.h"
#include "res/cJSON.c"
#include "res/Encrypt.h"
#include<iostream>
#include<ctime>
using namespace std;

#include <Draw.h>
#include <limits.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <random>
#include <set>
#include "辅助类.h"
#include <dirent.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <limits.h>
#include <malloc.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/inotify.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/ptrace.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <csignal>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "图片调用.h"

#include <curl/curl.h>
#include <my_str.h>
#include "wcnm.h"
//#include "My_T3verify.h"
#include "drivers/driver.h"

// JNI 支持
#include <jni.h>
#include <android/native_window_jni.h>
#include <android/log.h>
#include <thread>

#define LOG_TAG "BerryNative"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

using namespace std;

int abs_ScreenX, abs_ScreenY;

int 无后台 = 1, 自瞄选项, 漏打;

布局 布局;
绘制 绘制;

static std::thread* g_render_thread = nullptr;
static bool g_running = false;

// 声明 draw.cpp 中的设置函数
extern void set_external_window(ANativeWindow* window);
extern void set_screen_info(int w, int h, int orient);

static void berry_run()
{
	LOGI("berry_run 开始");

	绘制.防录屏 = 1;
	绘制.自瞄模式 = 0;
	绘制.无后台开关 = 1;

	// 初始化程序（使用 Java 提供的窗口）
	if (布局.初始化程序() != 0)
	{
		LOGE("初始化程序失败");
		return;
	}
	LOGI("初始化程序成功");

	加载内存图片();
	LOGI("图片加载完成");

	绘制.自瞄.预判力度 = 1.55f;
	绘制.自瞄主线程();
	绘制.GetTouch();
	绘制.按钮.自瞄选项 = true;
	绘制.读取配置();

	LOGI("开始渲染循环");
	布局.开启悬浮窗();
	LOGI("渲染循环结束");
}

extern "C" {

JNIEXPORT jboolean JNICALL
Java_com_berry_kernel_KernelService_nativeInit(
	JNIEnv* env, jobject thiz, jobject surface, jint width, jint height, jint orientation)
{
	LOGI("nativeInit 调入, width=%d, height=%d, orientation=%d", width, height, orientation);

	if (!surface)
	{
		LOGE("surface 为空");
		return JNI_FALSE;
	}

	ANativeWindow* window = ANativeWindow_fromSurface(env, surface);
	if (!window)
	{
		LOGE("ANativeWindow_fromSurface 失败");
		return JNI_FALSE;
	}

	LOGI("ANativeWindow 获取成功: %p", window);

	// 设置外部窗口和屏幕信息给 draw.cpp
	set_external_window(window);
	set_screen_info(width, height, orientation);

	if (g_running)
	{
		LOGE("已经在运行中");
		return JNI_FALSE;
	}
	g_running = true;

	// 在新线程中运行主循环
	g_render_thread = new std::thread([]() {
		berry_run();
		g_running = false;
	});

	LOGI("nativeInit 完成");
	return JNI_TRUE;
}

JNIEXPORT void JNICALL
Java_com_berry_kernel_KernelService_nativeStop(JNIEnv* env, jobject thiz)
{
	LOGI("nativeStop 调入");

	g_running = false;

	if (g_render_thread)
	{
		if (g_render_thread->joinable())
			g_render_thread->detach();
		delete g_render_thread;
		g_render_thread = nullptr;
	}

	LOGI("nativeStop 完成");
}

} // extern "C"