#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repackage.py — 直装(免root) APK 注入重打包脚本

功能:
  1. 用 apktool 解包游戏 APK
  2. 将 libCube.so 注入到 lib/arm64-v8a/
  3. 将 Font.ttf 注入到 assets/
  4. 修改 smali 代码, 在游戏启动时自动加载 libCube.so
  5. 重新打包并签名

依赖工具:
  - apktool (反编译/重编译 APK)
  - apksigner (签名)
  - keytool (生成签名密钥, JRE 自带)

用法:
  python repackage.py <游戏APK路径> [libCube.so路径]

示例:
  python repackage.py game.apk
  python repackage.py game.apk libs/arm64-v8a/libCube.so

注意:
  - 需要安装 Java (JRE/JDK) 和 apktool
  - 签名后需要卸载原版游戏再安装修改版
  - 游戏可能有签名校验/完整性检测, 需要额外绕过
"""

import os
import sys
import shutil
import subprocess
import re
import tempfile

# ============================================================
#  工具函数
# ============================================================

def run(cmd, check=True):
    """执行命令并打印"""
    print(f"  > {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    result = subprocess.run(cmd, shell=isinstance(cmd, str),
                          capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  错误: {result.stderr}")
        sys.exit(1)
    return result

def find_tool(name):
    """查找工具是否在 PATH 中"""
    return shutil.which(name) is not None

# ============================================================
#  Smali 注入: 在游戏启动时加载 libCube.so
# ============================================================

def find_application_class(manifest_path):
    """从 AndroidManifest.xml 中解析 Application 类名"""
    with open(manifest_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 查找 android:name="xxx" (在 <application 标签内)
    match = re.search(r'<application[^>]*android:name="([^"]+)"', content)
    if match:
        cls = match.group(1)
        if cls.startswith('.'):
            # 相对路径, 需要加上包名
            pkg_match = re.search(r'package="([^"]+)"', content)
            if pkg_match:
                cls = pkg_match.group(1) + cls
        return cls

    # 没有自定义 Application, 使用默认的 android.app.Application
    return 'android.app.Application'

def find_main_activity(manifest_path):
    """从 AndroidManifest.xml 中解析主 Activity 类名"""
    with open(manifest_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 查找带有 MAIN + LAUNCHER intent-filter 的 activity
    # 简化匹配: 找 <activity ... android:name="xxx" ...> 后面跟着 MAIN
    activities = re.findall(r'<activity[^>]*android:name="([^"]+)"', content)
    if activities:
        cls = activities[0]
        if cls.startswith('.'):
            pkg_match = re.search(r'package="([^"]+)"', content)
            if pkg_match:
                cls = pkg_match.group(1) + cls
        return cls
    return None

def class_to_smali_path(class_name):
    """将类名转为 smali 文件路径, 例如 com.epicgames.ue4.GameApplication -> com/epicgames/ue4/GameApplication.smali"""
    return class_name.replace('.', '/') + '.smali'

def inject_load_library(smali_dir, class_name):
    """
    在指定类的 onCreate 方法开头注入 System.loadLibrary("Cube")
    如果没有 onCreate, 则注入到 <clinit> (静态初始化块)
    """
    smali_path = os.path.join(smali_dir, class_to_smali_path(class_name))

    # 在 smali 目录中搜索 (可能在多个子目录)
    if not os.path.exists(smali_path):
        # 搜索所有 smali_* 目录
        found = False
        for root, dirs, files in os.walk(smali_dir):
            rel_path = class_to_smali_path(class_name)
            test_path = os.path.join(root, os.path.basename(rel_path))
            if os.path.basename(rel_path) in files:
                smali_path = os.path.join(root, os.path.basename(rel_path))
                # 验证是否是正确的类
                with open(smali_path, 'r', encoding='utf-8') as f:
                    if f'.class' in f.read() and class_name.split('.')[-1] in smali_path:
                        found = True
                        break
        if not found:
            # 最后尝试直接文件名搜索
            for root, dirs, files in os.walk(smali_dir):
                basename = os.path.basename(class_to_smali_path(class_name))
                if basename in files:
                    smali_path = os.path.join(root, basename)
                    found = True
                    break
        if not found:
            print(f"  警告: 未找到 {class_name} 的 smali 文件")
            return False

    print(f"  找到 smali: {smali_path}")

    with open(smali_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 要注入的 smali 代码
    load_code = '''
    # ---- 直装注入: 加载修改器 .so ----
    const-string v0, "Cube"
    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V
    # ---- 注入结束 ----
'''

    # 优先注入到 onCreate 方法的 .locals 之后
    # 匹配 .method ... onCreate(...) ... .locals N
    oncreate_pattern = r'(\.method\s+public\s+(onCreate|attachBaseContext)\s*\([^)]*\)[^\n]*\n\s*\.locals\s+\d+)'
    match = re.search(oncreate_pattern, content)

    if match:
        # 在 .locals 行之后插入
        insert_pos = match.end()
        content = content[:insert_pos] + '\n' + load_code + content[insert_pos:]
        print(f"  注入到 {match.group(2)}() 方法")
    else:
        # 注入到 <clinit> (静态初始化块)
        clinit_pattern = r'(\.method\s+(static\s+)?constructor\s+<clinit>\(\)[^\n]*\n\s*\.locals\s+\d+)'
        match = re.search(clinit_pattern, content)

        if match:
            insert_pos = match.end()
            content = content[:insert_pos] + '\n' + load_code + content[insert_pos:]
            print("  注入到 <clinit>() 方法")
        else:
            # 创建一个新的 <clinit> 方法
            clinit_method = '''
.method static constructor <clinit>()V
    .locals 1
%s
    return-void
.end method
''' % load_code
            # 插入在 # virtual methods 或 # direct methods 之前
            if '# virtual methods' in content:
                content = content.replace('# virtual methods', clinit_method + '\n# virtual methods')
            elif '# direct methods' in content:
                content = content.replace('# direct methods', clinit_method + '\n# direct methods')
            else:
                content += clinit_method
            print("  创建 <clinit>() 方法并注入")

    with open(smali_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return True

# ============================================================
#  签名
# ============================================================

def generate_keystore(keystore_path):
    """生成调试签名密钥"""
    if os.path.exists(keystore_path):
        return

    print("\n[5] 生成签名密钥...")
    run([
        'keytool', '-genkeypair',
        '-keystore', keystore_path,
        '-storepass', 'android',
        '-alias', 'androiddebugkey',
        '-keypass', 'android',
        '-keyalg', 'RSA',
        '-keysize', '2048',
        '-validity', '10000',
        '-dname', 'CN=Android Debug,O=Android,C=US'
    ])
    print(f"  密钥已生成: {keystore_path}")

def sign_apk(apk_path, keystore_path):
    """签名 APK"""
    print(f"\n[6] 签名 APK...")

    if find_tool('apksigner'):
        run([
            'apksigner', 'sign',
            '--ks', keystore_path,
            '--ks-pass', 'pass:android',
            '--ks-key-alias', 'androiddebugkey',
            '--key-pass', 'pass:android',
            '--out', apk_path.replace('.apk', '_signed.apk'),
            apk_path
        ])
        signed_path = apk_path.replace('.apk', '_signed.apk')
        print(f"  签名完成: {signed_path}")
        return signed_path
    elif find_tool('jarsigner'):
        run([
            'jarsigner',
            '-keystore', keystore_path,
            '-storepass', 'android',
            '-keypass', 'android',
            '-signedjar', apk_path.replace('.apk', '_signed.apk'),
            apk_path,
            'androiddebugkey'
        ])
        # 需要 zipalign
        if find_tool('zipalign'):
            aligned = apk_path.replace('.apk', '_aligned.apk')
            run(['zipalign', '-f', '4', apk_path.replace('.apk', '_signed.apk'), aligned])
            os.replace(aligned, apk_path.replace('.apk', '_signed.apk'))
        signed_path = apk_path.replace('.apk', '_signed.apk')
        print(f"  签名完成: {signed_path}")
        return signed_path
    else:
        print("  警告: 未找到 apksigner 或 jarsigner, 请手动签名")
        return apk_path

# ============================================================
#  主流程
# ============================================================

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    game_apk = sys.argv[1]
    cube_so = sys.argv[2] if len(sys.argv) > 2 else None

    # 查找 libCube.so
    if cube_so is None:
        default_paths = [
            'app/src/main/jniLibs/arm64-v8a/libCube.so',
            'app/src/main/obj/local/arm64-v8a/libCube.so',
            'libs/arm64-v8a/libCube.so',
        ]
        for p in default_paths:
            if os.path.exists(p):
                cube_so = p
                break

    if not cube_so or not os.path.exists(cube_so):
        print("错误: 未找到 libCube.so")
        print("请先用 ndk-build DIRECT_INSTALL=true 编译, 然后指定路径:")
        print("  python repackage.py game.apk path/to/libCube.so")
        sys.exit(1)

    if not os.path.exists(game_apk):
        print(f"错误: 游戏 APK 不存在: {game_apk}")
        sys.exit(1)

    # 检查工具
    if not find_tool('apktool'):
        print("错误: 未找到 apktool, 请先安装")
        print("  下载: https://apktool.org/")
        sys.exit(1)

    font_path = 'app/src/main/assets/Font.ttf'
    if not os.path.exists(font_path):
        font_path = None
        print("警告: 未找到 Font.ttf, 中文字体将不可用")

    work_dir = tempfile.mkdtemp(prefix='repackage_')
    keystore_path = os.path.join(work_dir, 'debug.keystore')

    print(f"工作目录: {work_dir}")
    print(f"游戏 APK: {game_apk}")
    print(f"libCube.so: {cube_so}")
    print(f"字体: {font_path or '(无)'}")

    # ---- 1. 反编译 APK ----
    print("\n[1] 反编译游戏 APK...")
    decompiled_dir = os.path.join(work_dir, 'decompiled')
    run(['apktool', 'd', '-f', '-o', decompiled_dir, game_apk])

    # ---- 2. 注入 libCube.so ----
    print("\n[2] 注入 libCube.so...")
    lib_dir = os.path.join(decompiled_dir, 'lib', 'arm64-v8a')
    os.makedirs(lib_dir, exist_ok=True)
    shutil.copy2(cube_so, os.path.join(lib_dir, 'libCube.so'))
    print(f"  已复制: {lib_dir}/libCube.so")

    # 删除其他架构的 lib (避免架构不匹配)
    for arch in ['armeabi-v7a', 'x86', 'x86_64']:
        arch_dir = os.path.join(decompiled_dir, 'lib', arch)
        if os.path.exists(arch_dir):
            shutil.rmtree(arch_dir)
            print(f"  删除架构: {arch}")

    # ---- 3. 注入字体文件 ----
    if font_path:
        print("\n[3] 注入字体文件...")
        assets_dir = os.path.join(decompiled_dir, 'assets')
        os.makedirs(assets_dir, exist_ok=True)
        shutil.copy2(font_path, os.path.join(assets_dir, 'Font.ttf'))
        print(f"  已复制: assets/Font.ttf")

    # ---- 4. 修改 smali 代码 ----
    print("\n[4] 修改 smali (注入 loadLibrary)...")

    manifest = os.path.join(decompiled_dir, 'AndroidManifest.xml')

    # 尝试在 Application 类中注入
    app_class = find_application_class(manifest)
    print(f"  Application 类: {app_class}")

    # 在 smali 目录中查找 (可能是 smali, smali_classes2 等)
    smali_dirs = [d for d in os.listdir(decompiled_dir) if d.startswith('smali')]
    if not smali_dirs:
        smali_dirs = ['smali']

    injected = False
    for sd in smali_dirs:
        sd_path = os.path.join(decompiled_dir, sd)
        if os.path.isdir(sd_path):
            if inject_load_library(sd_path, app_class):
                injected = True
                break

    if not injected:
        # 尝试主 Activity
        main_activity = find_main_activity(manifest)
        if main_activity:
            print(f"  尝试主 Activity: {main_activity}")
            for sd in smali_dirs:
                sd_path = os.path.join(decompiled_dir, sd)
                if os.path.isdir(sd_path):
                    if inject_load_library(sd_path, main_activity):
                        injected = True
                        break

    if not injected:
        print("  警告: 未能自动注入, 请手动在游戏启动类中添加:")
        print('    const-string v0, "Cube"')
        print('    invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V')

    # ---- 5. 重新打包 ----
    print("\n[5] 重新打包 APK...")
    unsigned_apk = os.path.join(work_dir, 'game_unsigned.apk')
    run(['apktool', 'b', '-o', unsigned_apk, decompiled_dir])

    # ---- 6. 签名 ----
    generate_keystore(keystore_path)
    signed_apk = sign_apk(unsigned_apk, keystore_path)

    # ---- 7. 复制到当前目录 ----
    output_name = os.path.splitext(os.path.basename(game_apk))[0] + '_直装.apk'
    output_path = os.path.join(os.getcwd(), output_name)
    shutil.copy2(signed_apk, output_path)

    print(f"\n{'='*50}")
    print(f"完成! 直装 APK: {output_name}")
    print(f"路径: {output_path}")
    print(f"{'='*50}")
    print(f"\n安装步骤:")
    print(f"  1. 卸载原版游戏")
    print(f"  2. 安装 {output_name}")
    print(f"  3. 打开游戏, 修改器菜单自动显示")
    print(f"  4. 按音量下键切换菜单显示/隐藏")

    # 清理
    shutil.rmtree(work_dir, ignore_errors=True)

if __name__ == '__main__':
    main()
