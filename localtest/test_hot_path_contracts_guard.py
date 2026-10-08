# -*- coding: utf-8 -*-
"""热路径契约守卫（B2 + B3）：把"实时性"钉成源码级硬约束。

为什么是源码契约而不是行为测试：这两处都在 Dart/Java，本机没有 Android 工具链、
也跑不了 flutter test 打到的这条链路（悬浮窗 State 私有），而它们的失效方式全是
**"某天有人把门又加回帧数/把深扫塞回采集线程"** —— 这种回归不会让任何精度测试变红。
于是直接把源码形态当断言对象，改回坏写法必须变红。

锁的条目：

Dart `lib/overlays/mahjong_overlay.dart`（B2）
  D1 清空去抖不得再带帧数门（`_pendingClearRun` 整个删掉）。帧数门把"多久"隐含成
     "多少个采集周期"：单帧实测 p50 415ms 时「≥4 帧」= ≈1.66s，画面早就离开牌桌了
     面板还死播上一局。只有时间门是与帧率无关的物理量。
  D2 清空必须仍由时间门（≥450ms）把关，且 `!showingGood` 仍能立即清空。
  D3 `'waiting'` 必须仍在 `_kClearStatuses` 里 —— B1 把"非牌桌首帧播旧牌"改成
     报 waiting 之后，防闪全靠这一层；把它从清空集合里摘掉就等于把闪烁放回去。
  D4 有数据帧必须保持前沿 0ms 上屏（不许有人为了"防抖"再插一个固定延时）。

Java（B3）
  J1 `SecurityGuard.isSafe` 体内不得出现 `performDeepScan(`：深扫含逐行读
     /proc/self/maps + 两次 socket.connect(15ms)，旧写法把它嵌在每帧都调的
     isSafe 里，等于每 8 秒有一帧在采集线程上被安全扫描堵住。
  J2 深扫必须仍有人跑：`ImageProcessor` 的心跳任务调 `runScheduledDeepScan`。
  J3 采集热路径的卡密核验必须用 `verifyLicenseThrottled`（每帧 binder IPC +
     SHA-256 + 正则的旧账），启动闸门保持强校验 `verifyLicense`。
  J4 `verifyLicenseThrottled` 只许缓存"好结果"，坏结果必须回写 `sLicenseOkAt = 0`
     —— 否则授权过期/被拉黑后最多还能再跑 2s。
  J5 `computeDeviceId` 必须走进程内缓存。
  J6 `MainActivity.startProcessing` 必须在 `isSafe` 之前显式做一次深扫
     （否则启动这一环的门比改前更弱）。
  J7/J8 采集线程与主线程共享的 static 字段（配置开关、ROI、方向覆盖、计数器、
     Context、深扫时间戳）必须 `volatile` —— 本文件 62-68 行的旧注释早就写过
     "非 volatile 的 static boolean 可能长期读到旧值（开关不生效）"，本轮把
     同一族剩下的字段补齐，这里防止再被拆回去。

运行：
  py -3.10 -X utf8 localtest/test_hot_path_contracts_guard.py          # 主守卫
  py -3.10 -X utf8 localtest/test_hot_path_contracts_guard.py --mutate  # 变异对照
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MUTATE = "--mutate" in sys.argv

DART = os.path.join(REPO, "lib", "overlays", "mahjong_overlay.dart")
SEC_JAVA = os.path.join(REPO, "android", "app", "src", "main", "java",
                        "com", "example", "auto_vision", "SecurityGuard.java")
PROC_JAVA = os.path.join(REPO, "android", "app", "src", "main", "java",
                         "com", "example", "auto_vision", "ImageProcessor.java")
MAIN_JAVA = os.path.join(REPO, "android", "app", "src", "main", "java",
                         "com", "example", "auto_vision", "MainActivity.java")


def read(p: str) -> str:
    with open(p, encoding="utf-8") as fp:
        return fp.read()


def java_body(src: str, sig: str) -> str:
    """按花括号配对取方法体（sig 形如 `public static boolean isSafe(`）。"""
    i = src.index(sig)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    raise AssertionError(f"方法体不闭合：{sig}")


# ===== 断言函数：输入源码文本，返回违规列表（空 = 合规）=====

def check_dart(src: str):
    bad = []
    if "_pendingClearRun" in src:
        bad.append("D1: 清空去抖又出现帧数门 `_pendingClearRun`（帧数≠时间，慢帧下会拖成秒级陈旧）")
    if "inMilliseconds >= 450" not in src:
        bad.append("D2: 清空帧的 450ms 时间门不见了")
    if "if (!showingGood || sustained) {" not in src:
        bad.append("D2: 清空判据不再是「屏上没好数据 或 已持续 450ms」")
    m = re.search(r"_kClearStatuses\s*=\s*\{[^}]*\}", src, re.S)
    if not m or "'waiting'" not in m.group(0):
        bad.append("D3: 'waiting' 不在 _kClearStatuses —— B1 之后防闪全靠这层，摘掉就变闪烁")
    if "_applyPendingResult(); // 前沿：0ms 立即上屏响应！" not in src:
        bad.append("D4: 有数据帧的前沿 0ms 上屏被改掉（实时性回退）")
    return bad


def check_security_guard(src: str):
    bad = []
    if "performDeepScan(" in java_body(src, "public static boolean isSafe("):
        bad.append("J1: isSafe() 里又内嵌深扫（会在调用线程做文件读 + socket 连接）")
    if "runScheduledDeepScan" not in src:
        bad.append("J2: runScheduledDeepScan 不存在（深扫没人调度，安全门被整体摘掉）")
    thr = java_body(src, "public static boolean verifyLicenseThrottled(")
    if "verifyLicense(context)" not in thr:
        bad.append("J3: verifyLicenseThrottled 不再真核验卡密")
    if "sLicenseOkAt = 0;" not in thr:
        bad.append("J4: 坏结果也被缓存了（过期/拉黑最多还能再跑一个 TTL）")
    dev = java_body(src, "public static String computeDeviceId(")
    if "String cached = sDeviceIdCache;" not in dev:
        # 写回缓存不算：必须看见「先读缓存、命中就返」，否则每调仍在 binder + SHA-256。
        bad.append("J5: computeDeviceId 没有读进程内缓存（每调现算设备指纹）")
    if "private static volatile long sLastDeepScanTime" not in src:
        bad.append("J7: sLastDeepScanTime 非 volatile（心跳线程与主线程各自看副本，节流失效）")
    return bad


def check_processor(src: str):
    bad = []
    hb = java_body(src, "heartbeatTimer.schedule(new TimerTask() {")
    if "SecurityGuard.runScheduledDeepScan(" not in hb:
        bad.append("J2: 心跳线程没调 runScheduledDeepScan —— 深扫被移出采集线程后必须有人接盘")
    cap = java_body(src, "public void processCapturedImage(Image image) {")
    if "SecurityGuard.verifyLicense(sContext)" in cap:
        bad.append("J3: 采集热路径仍在每帧做完整卡密核验（应使用 verifyLicenseThrottled）")
    if "verifyLicenseThrottled(sContext)" not in cap:
        bad.append("J3: 采集热路径没有做卡密核验")
    # J8：跨线程共享的字段必须 volatile（static 与实例字段都有）
    for name in ("roiTop", "roiBottom", "roiDirty", "orientOverride", "orientDirty",
                 "dingqueOverride", "dingqueDirty", "cfgAntiBan", "cfgAntiDetect",
                 "cfgAutoOrient", "cfgBootstrap", "cfgStrict", "configDirty",
                 "cfgDumpFrames", "cfgCollectRiver", "cfgYoloRiver", "sContext",
                 "framesDumped", "framesAcquired", "framesProcessed", "sendFailures",
                 "lastCaptureTickAt", "consecutiveSkips", "sLimitLogged"):
        m = re.search(r"private\s+(?:static\s+)?[^\n]*?\b%s\b[^\n]*;" % name, src)
        if m is None:
            bad.append(f"J8: 找不到字段 {name} 的声明（改名了？volatile 断言会空转）")
        elif "volatile" not in m.group(0):
            bad.append(f"J8: {name} 非 volatile —— 主线程写、采集线程读，可能长期读到旧值")
    return bad


def check_main_activity(src: str):
    bad = []
    i = src.index('call.method.equals("startProcessing")')
    j = src.index("prepareStream", i) if "prepareStream" in src[i:i + 6000] else i + 6000
    seg = src[i:j]
    k_scan = seg.find("SecurityGuard.performDeepScan(")
    k_safe = seg.find("SecurityGuard.isSafe(")
    if k_scan < 0:
        bad.append("J6: 启动闸门不再做深扫（深扫移出 isSafe 后必须在这里补回一次）")
    elif k_safe >= 0 and k_scan > k_safe:
        bad.append("J6: 深扫排在 isSafe 之后 —— 启动时门还没合就先把启动判出去了")
    return bad


FILES = {"dart": (DART, check_dart),
         "security": (SEC_JAVA, check_security_guard),
         "processor": (PROC_JAVA, check_processor),
         "activity": (MAIN_JAVA, check_main_activity)}


class TestHotPathContracts(unittest.TestCase):
    def test_dart_frame_gates(self):
        self.assertFalse(check_dart(read(DART)), "\n".join(check_dart(read(DART))))

    def test_security_guard_hot_path_offload(self):
        self.assertFalse(check_security_guard(read(SEC_JAVA)),
                         "\n".join(check_security_guard(read(SEC_JAVA))))

    def test_image_processor_wiring(self):
        self.assertFalse(check_processor(read(PROC_JAVA)),
                         "\n".join(check_processor(read(PROC_JAVA))))

    def test_main_activity_startup_gate(self):
        self.assertFalse(check_main_activity(read(MAIN_JAVA)),
                         "\n".join(check_main_activity(read(MAIN_JAVA))))


# ===== 变异：把坏写法塞回文本，断言对应检查确实变红 =====

MUTANTS = {
    "dart": [
        ("D1/D2 帧数门回归",
         lambda s: s.replace("if (!showingGood || sustained) {",
                             "if (!showingGood || (_pendingClearRun >= 4 && sustained)) {")),
        ("D3 waiting 被摘出清空集合",
         lambda s: s.replace("_kClearStatuses = {\n    'waiting', ",
                             "_kClearStatuses = {\n    ")),
        ("D4 前沿上屏插进固定延时",
         lambda s: s.replace("_applyPendingResult(); // 前沿：0ms 立即上屏响应！",
                             "Future.delayed(const Duration(milliseconds: 300), _applyPendingResult);")),
    ],
    "security": [
        ("J1 深扫塞回 isSafe",
         lambda s: s.replace("        return true;\n    }\n\n    /**\n     * 按节流推进一次深层安全扫描",
                             "        performDeepScan(context);\n        return true;\n    }\n\n    /**\n     * 按节流推进一次深层安全扫描")),
        ("J4 坏结果也缓存",
         lambda s: s.replace("        } else {\n            sLicenseOkAt = 0;\n        }", "        }")),
        ("J5 设备号每调现算",
         lambda s: s.replace("        String cached = sDeviceIdCache;", "        String cached = null;")),
        ("J7 深扫时间戳丢 volatile",
         lambda s: s.replace("private static volatile long sLastDeepScanTime",
                             "private static long sLastDeepScanTime")),
    ],
    "processor": [
        ("J2 心跳不接盘深扫",
         lambda s: s.replace("SecurityGuard.runScheduledDeepScan(sContext);", "")),
        ("J3 热路径回到完整核验",
         lambda s: s.replace("SecurityGuard.verifyLicenseThrottled(sContext)",
                             "SecurityGuard.verifyLicense(sContext)")),
        ("J8 ROI 脏标志丢 volatile",
         lambda s: s.replace("private static volatile boolean roiDirty",
                             "private static boolean roiDirty")),
        ("J8 计数器丢 volatile（long 撕裂）",
         lambda s: s.replace("private volatile long framesProcessed",
                             "private long framesProcessed")),
    ],
    "activity": [
        ("J6 启动闸门不做深扫",
         lambda s: s.replace("SecurityGuard.performDeepScan(getApplicationContext());", "")),
        ("J6 深扫排到 isSafe 之后",
         lambda s: s.replace(
             "        SecurityGuard.performDeepScan(getApplicationContext());\n"
             "        if (!SecurityGuard.isSafe(getApplicationContext())) {",
             "        if (!SecurityGuard.isSafe(getApplicationContext())) {\n"
             "          SecurityGuard.performDeepScan(getApplicationContext());")),
    ],
}


class TestMutationControls(unittest.TestCase):
    def setUp(self) -> None:
        if not MUTATE:
            self.skipTest("加 --mutate 才跑变异对照")

    def test_mutants_are_caught(self):
        missed = []
        for key, cases in MUTANTS.items():
            src, check = FILES[key][0], FILES[key][1]
            text = read(src)
            for label, fn in cases:
                mutated = fn(text)
                if mutated == text:
                    missed.append(f"{key}/{label}: 变异没改到任何字节（模板过期）")
                    continue
                if not check(mutated):
                    missed.append(f"{key}/{label}: 坏写法通过了守卫（守卫空转）")
        self.assertFalse(missed, "\n".join(missed))


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestHotPathContracts))
    if MUTATE:
        suite.addTests(loader.loadTestsFromTestCase(TestMutationControls))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
