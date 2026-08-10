# BerryKernel

Android NDK 原生悬浮窗工具，使用 ImGui + OpenGL ES 渲染界面。

## 项目结构

```
.
├── jni/                    # NDK 原生代码 (C++)
│   ├── Android.mk          # NDK 构建脚本
│   ├── Application.mk      # NDK 构建配置 (arm64-v8a, android-21)
│   ├── include/             # 头文件 + 预编译静态库
│   └── src/                 # 源码
├── app/                    # Android 应用模块
│   ├── build.gradle        # Gradle 构建配置 (含 ndk-build 集成)
│   └── src/main/
│       ├── AndroidManifest.xml
│       ├── java/com/berry/kernel/
│       │   ├── MainActivity.java   # 主界面
│       │   └── KernelService.java  # 前台服务 (运行原生二进制)
│       └── res/            # UI 资源
├── .github/workflows/
│   └── build-apk.yml       # GitHub Actions CI (自动编译 APK)
├── build.gradle            # 项目级 Gradle
├── settings.gradle
├── gradle.properties
└── gradlew / gradlew.bat   # Gradle Wrapper
```

## GitHub Actions 自动打包

推送代码到 GitHub 后，Actions 会自动触发编译：

1. 推送到 `main` 或 `master` 分支
2. 进入仓库的 **Actions** 标签页
3. 等待 **Build APK** workflow 完成
4. 在 Artifacts 区域下载 APK

也可在 Actions 页面手动触发 (Run workflow)。

## 本地构建

### 前置条件

- JDK 17+
- Android SDK (compileSdk 34)
- Android NDK 25.2.9519653
- Gradle 8.0+

### 步骤

```bash
# 1. 创建 local.properties 指向 Android SDK
echo "sdk.dir=C:\\Users\\<用户名>\\AppData\\Local\\Android\\Sdk" > local.properties

# 2. 编译 Debug APK
gradlew assembleDebug

# 3. APK 输出路径
# app/build/outputs/apk/debug/app-debug.apk
```

## 构建流程

1. Gradle 触发 `buildNative` task → 调用 ndk-build 编译 C++ 代码
2. `copyNativeLib` task → 将编译产物重命名为 `libberry.so` 并放入 jniLibs
3. Android Gradle Plugin 打包 APK (自动解压 .so 到 nativeLibraryDir)
4. 运行时，Java 代码通过 `ProcessBuilder` 执行 `libberry.so`
