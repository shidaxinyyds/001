LOCAL_PATH := $(call my-dir)

include $(CLEAR_VARS)
LOCAL_MODULE := Cube
LOCAL_ARM_MODE := arm

LOCAL_CFLAGS := -Wno-error=format-security -w
LOCAL_CFLAGS += -fno-rtti -fno-exceptions -fpermissive
LOCAL_CPPFLAGS := -Wno-error=format-security -fpermissive -w -Werror -s -std=c++17
LOCAL_CPPFLAGS += -fno-rtti -fno-exceptions -fms-extensions -Wno-error=c++11-narrowing

# ============================================================
#  DIRECT_INSTALL (直装免root) 构建开关
#
#  用法:
#    直装模式(免root): ndk-build DIRECT_INSTALL=true
#    独立模式(需root): ndk-build
#
#  直装模式会将 libCube.so 注入到游戏 APK 中运行,
#  不需要 root 权限和 sharedUserId
# ============================================================
ifeq ($(DIRECT_INSTALL),true)
    LOCAL_CFLAGS += -DDIRECT_INSTALL
    $(info ===== 直装(免root)模式编译 =====)
else
    $(info ===== 独立悬浮窗模式编译 =====)
endif

FILE_LIST += $(wildcard $(LOCAL_PATH)/app/*.c*)
FILE_LIST += $(wildcard $(LOCAL_PATH)/imgui/*.c*)

LOCAL_SRC_FILES := $(FILE_LIST:$(LOCAL_PATH)/%=%)

LOCAL_LDLIBS := -Wl -llog -landroid -lEGL -lGLESv2

include $(BUILD_SHARED_LIBRARY)
