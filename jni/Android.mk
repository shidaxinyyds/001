LOCAL_PATH := $(call my-dir)
include $(CLEAR_VARS)


LOCAL_MODULE := m
LOCAL_CFLAGS := -w -s -Wno-error=format-security -fvisibility=hidden -fpermissive -fexceptions
LOCAL_LDFLAGS += -Wl,--gc-sections,--strip-all,-llog
LOCAL_CPPFLAGS += -w -s -Wno-error=format-security -fvisibility=hidden -Werror -std=c++17
LOCAL_CPPFLAGS += -Wno-error=c++11-narrowing -fpermissive -Wall -fexceptions
LOCAL_CFLAGS := -std=c++17

LOCAL_C_INCLUDES += $(LOCAL_PATH)/include
LOCAL_C_INCLUDES += $(LOCAL_PATH)/include/ImGui
LOCAL_C_INCLUDES += $(LOCAL_PATH)/include/My_Utils
LOCAL_C_INCLUDES += $(LOCAL_PATH)/include/curl
LOCAL_C_INCLUDES += $(LOCAL_PATH)/include/CPUaffinity
LOCAL_C_INCLUDES += $(LOCAL_PATH)/include/drivers
LOCAL_C_INCLUDES += $(LOCAL_PATH)/src

FILE_LIST += $(wildcard $(LOCAL_PATH)/src/*.c*)
FILE_LIST += $(wildcard $(LOCAL_PATH)/src/drivers/*.c*)
FILE_LIST += $(wildcard $(LOCAL_PATH)/src/ImGui/*.c*)
LOCAL_SRC_FILES := $(FILE_LIST:$(LOCAL_PATH)/%=%)

LOCAL_LDLIBS := -llog -landroid -lEGL -lGLESv1_CM -lGLESv2 -lGLESv3
LOCAL_LDLIBS += -lz

# 正确设置静态库路径
LOCAL_LDFLAGS := -L$(LOCAL_PATH)/src/CPUaffinity
LOCAL_STATIC_LIBRARIES := imgui_static curl_static

include $(BUILD_EXECUTABLE)

# 预编译静态库 imgui_static
include $(CLEAR_VARS)
LOCAL_MODULE := imgui_static
LOCAL_SRC_FILES := src/CPUaffinity/libPikachu.a
include $(PREBUILT_STATIC_LIBRARY)

# 预编译静态库 libcurl
include $(CLEAR_VARS)
LOCAL_MODULE := curl_static
LOCAL_SRC_FILES := include/lib/libcurl.a
  # 确保 libcurl.a 存在
include $(PREBUILT_STATIC_LIBRARY)
