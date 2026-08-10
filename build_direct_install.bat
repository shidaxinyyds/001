@echo off
chcp 65001 >nul
REM ============================================================
REM  build_direct_install.bat — 直装(免root)模式编译脚本
REM
REM  用法: 双击运行 或 在命令行执行
REM  需要: Android NDK (ndk-build 在 PATH 中)
REM ============================================================

echo ============================================
echo   直装(免root)模式编译 libCube.so
echo ============================================
echo.

REM 检查 ndk-build
where ndk-build >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未找到 ndk-build, 请确保 Android NDK 已安装并在 PATH 中
    echo   下载: https://developer.android.com/ndk/downloads
    pause
    exit /b 1
)

REM 编译直装模式
echo [1/2] 编译 libCube.so (DIRECT_INSTALL=true)...
cd /d "%~dp0\app\src\main\jni"
call ndk-build DIRECT_INSTALL=true
if %errorlevel% neq 0 (
    echo [错误] 编译失败
    pause
    exit /b 1
)

echo.
echo [2/2] 编译完成!
echo   输出: app\src\main\jniLibs\arm64-v8a\libCube.so
echo   输出: app\src\main\obj\local\arm64-v8a\libCube.so
echo.
echo 下一步:
echo   python repackage.py 游戏APK路径.apk
echo.
pause
