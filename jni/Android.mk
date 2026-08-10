LOCAL_PATH := $(call my-dir)

# Prebuilt static libraries (define before main module)
include $(CLEAR_VARS)
LOCAL_MODULE := imgui_static
LOCAL_SRC_FILES := src/CPUaffinity/libPikachu.a
include $(PREBUILT_STATIC_LIBRARY)

include $(CLEAR_VARS)
LOCAL_MODULE := curl_static
LOCAL_SRC_FILES := include/lib/libcurl.a
include $(PREBUILT_STATIC_LIBRARY)

# Main module
include $(CLEAR_VARS)

LOCAL_MODULE := berry
LOCAL_CFLAGS := -w -s -Wno-error=format-security -fvisibility=hidden -fpermissive -fexceptions
LOCAL_CPPFLAGS += -w -s -Wno-error=format-security -fvisibility=hidden -Wno-error=c++11-narrowing -fpermissive -Wall -fexceptions -std=c++17
LOCAL_LDFLAGS += -Wl,--gc-sections,--strip-all
LOCAL_LDFLAGS += -L$(LOCAL_PATH)/src/CPUaffinity

LOCAL_C_INCLUDES += $(LOCAL_PATH)/include
LOCAL_C_INCLUDES += $(LOCAL_PATH)/include/ImGui
LOCAL_C_INCLUDES += $(LOCAL_PATH)/include/My_Utils
LOCAL_C_INCLUDES += $(LOCAL_PATH)/include/curl
LOCAL_C_INCLUDES += $(LOCAL_PATH)/src/CPUaffinity
LOCAL_C_INCLUDES += $(LOCAL_PATH)/include/drivers
LOCAL_C_INCLUDES += $(LOCAL_PATH)/src

FILE_LIST += $(wildcard $(LOCAL_PATH)/src/*.c*)
FILE_LIST += $(wildcard $(LOCAL_PATH)/src/drivers/*.c*)
FILE_LIST += $(wildcard $(LOCAL_PATH)/src/ImGui/*.c*)
LOCAL_SRC_FILES := $(FILE_LIST:$(LOCAL_PATH)/%=%)

LOCAL_LDLIBS := -llog -landroid -lEGL -lGLESv1_CM -lGLESv2 -lGLESv3
LOCAL_LDLIBS += -lz

LOCAL_STATIC_LIBRARIES := imgui_static curl_static

include $(BUILD_SHARED_LIBRARY)
