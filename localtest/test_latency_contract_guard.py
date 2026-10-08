# -*- coding: utf-8 -*-
"""端到端可测性 + 判据契约守卫（P1-e / B4 / B5）。

这一轮改的东西和前两轮不同：它们不是"算得快一点"，而是**把延迟从读不出来变成
读得出来**，以及**把两个靠文本巧合成立的判据换成语义判据**。这类改动失效时不会
有任何精度测试变红，所以必须源码级钉死。

本机没有 Android 工具链（无 javac、无 Android SDK，`flutter analyze` 也跑不了），
所以这里做两层尽力而为的静态检查：
  ① Java 括号/圆括号配对（含字符串与注释剥离）—— SearchReplace 造成的大括号错位
     是这一轮最现实的编译失败方式，必须先在本机抓出来；
  ② 方法体级别的契约断言（见下列条目）。

锁的条目：

Java `ImageProcessor.java`（B5：判据）
  L1 采集热路径不得再用子串判据读 `native_ready` / `frame_skipped`。
     子串判据把「序列化格式」当语义契约：有人把 json.dumps 的分隔符改紧凑
     （separators=(',',':')，正是压 payload 的常规手段）→ 文本变成
     `"native_ready":true`，子串永不命中，native 全线静默不接管、静默帧不再
     降频采集，而**没有任何精度测试会变红**；反过来任意文本字段含该字面串就误接管。
  L2 必须改为读 JSON 布尔（optBoolean），且 `native_ready` 的判定必须同时要求
     NativeEngine.isAvailable()（能力 + 可用性两个条件缺一不可）。
  L3 手牌不可映射（混进字牌/鬼牌）时必须 return，不得半覆写。

Java `ImageProcessor.java`（P1-e：可测性）
  L4 采集入口必须打 captured_at_ms 原点，并注入 encode_ms / engine_ms 两段耗时
     （三键一起注入才有"慢在哪一段"的分解能力）。
  L5 注入必须在 native 覆写之外的**统一出口**：否则非川麻玩法（native_ready=false，
     用户实际在玩的血流红中正是这类）一帧都没有读数。
  L6 解析失败必须降级为「原样转发 Python 出帧」，绝不丢帧、绝不半覆写。
  L7 解析失败日志必须节流（第一次 + 每 300 次），真实次数走心跳计数 —— 否则
     一个持续输出非法 JSON 的玩法会形成 logcat 风暴反过来拖慢热路径。
  L8 跨线程统计字段（stamp/parse 计数）必须 volatile；心跳必须把它们读出来。

Java `ImageEncoder.java`（B4：判定为"不该做"，因此锁住不做）
  L9 采集热路径必须走无 ROI 的整屏编码入口。这条不是洁癖：Java 的 roiTop/roiBottom
     是**手牌识别区域**，经 set_roi 交给 Python 在整屏图上裁；牌河/副露/对家/牌桌
     校验另用一套整屏几何。若编码时就裁，两套坐标叠加 → 牌河漏框与手牌错位，
     而省下的只有实测 4ms（7.8→6.4 编码、13.1→10.2 解码）。

Java `ImageProcessor.java` / `NetworkClient.java`（本轮改动自己引入的风险）
  L13 旧版只有 native 帧会走 JSONObject 重序列化，本轮起**每帧**都走。而 Python 的
     json.dumps 默认 allow_nan=True 会写出 NaN/Infinity 这种非标准 JSON：org.json
     把它们读成 Double.NaN 还是字符串 "NaN" 取决于 Android 版本，toString() 写回时
     要么变成非法字面量（Dart jsonDecode 直接抛 → 整帧丢），要么数字变字符串（渲染
     处 `as num` 抛 → 整帧建议消失）。所以这种帧必须退回「原样转发 Python」并计数。
  L14 采集层状态帧必须自带 java_status 标记：悬浮窗据此把它排除在帧龄样本之外。
     靠枚举 status 字符串排除必然漏（`security_alert` 就漏过一次），而漏掉的每一帧
     都会把 dropped 刷满，让真正的降级帧看不出来。心跳拼接还必须在 statusJson 不以
     '}' 结尾时原样返回 —— 拼接异常会把整帧处理连带打断。

Dart（P1-e 显示侧）
  L10 LatencyProbe 窗口必须有界；帧龄必须丢弃负值与超阈样本（墙钟回拨）；
      无样本必须返回 null（UI 才能显示"—"而不是假的 0ms）。
  L11 悬浮窗必须逐帧喂 `_latency.add(...)` 并带 encode/engine 分段；上报必须 2s
      节流（逐帧 shareData 会让主页每帧重建，正是"高峰期点什么都没反应"的病根）；
      dispose 必须取消定时器；还必须把 Java 心跳计数随同上报（否则 Java 里那句
     「让开销自己读数」就只是一句注释），且统计入口必须排除 java_status 帧。
  L12 调试页必须只接 `type=='latency'`、必须 dispose 取消订阅，且无数据时不得印 0；
     采集侧计数必须落到界面上，与帧龄的 dropped 摆在一起才能对账。

运行：
  py -3.10 -X utf8 localtest/test_latency_contract_guard.py          # 主守卫
  py -3.10 -X utf8 localtest/test_latency_contract_guard.py --mutate  # 变异对照
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from test_hot_path_contracts_guard import java_body, read  # noqa: E402

MUTATE = "--mutate" in sys.argv

JAVA_DIR = os.path.join(REPO, "android", "app", "src", "main", "java",
                        "com", "example", "auto_vision")
PROC_JAVA = os.path.join(JAVA_DIR, "ImageProcessor.java")
ENC_JAVA = os.path.join(JAVA_DIR, "ImageEncoder.java")
SEC_JAVA = os.path.join(JAVA_DIR, "SecurityGuard.java")
MAIN_JAVA = os.path.join(JAVA_DIR, "MainActivity.java")
NET_JAVA = os.path.join(JAVA_DIR, "NetworkClient.java")
STREAM_JAVA = os.path.join(JAVA_DIR, "ScreenStreamer.java")

PROBE_DART = os.path.join(REPO, "lib", "latency_probe.dart")
OVERLAY_DART = os.path.join(REPO, "lib", "overlays", "mahjong_overlay.dart")
DEBUG_DART = os.path.join(REPO, "lib", "debug_page.dart")


# ===== ① Java 结构静态检查：字符串/字符字面量/注释剥离后括号必须配对 =====

def strip_java_noise(src: str) -> str:
    """去掉字符串、字符字面量、// 与 /* */ 注释，只留代码骨架。

    必须先剥离噪音再数括号，否则 `"帧编码失败（Bitmap为空）"` 里的括号、
    `'}'` 字符字面量都会污染计数。
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            i += 1
            out.append(" ")
        elif c == "'":
            i += 1
            while i < n and src[i] != "'":
                i += 2 if src[i] == "\\" else 1
            i += 1
            out.append(" ")
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_comments(src: str) -> str:
    """去掉 // 与 /* */ 注释，但**保留字符串与字符字面量**。

    两个理由：
    1. 注释里会写被禁止的旧写法作为反面教材（本轮就是这么写理由的），不能让
       它们触发 L1 的违规；
    2. 注释里的括号会让 java_body 的花括号配对取体错位（全角中文括号不算，但
       写代码注释时很容易混进半角括号）。
    字符串里的括号仍留着 —— 它们不参与配对，但判据检查需要看到代码里的字面量。
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif c == '"':
            out.append('"')
            i += 1
            while i < n and src[i] != '"':
                if src[i] == "\\":
                    out.append(src[i:i + 2])
                    i += 2
                else:
                    out.append(src[i])
                    i += 1
            if i < n:
                out.append('"')
                i += 1
        elif c == "'":
            out.append("'")
            i += 1
            while i < n and src[i] != "'":
                out.append(src[i])
                i += 2 if src[i] == "\\" else 1
            if i < n:
                out.append("'")
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def brace_violations(path: str):
    """返回该 Java 文件的括号配对问题列表（空 = 结构闭合良好）。"""
    code = strip_java_noise(read(path))
    bad = []
    for open_c, close_c, name in (("{", "}", "大括号"), ("(", ")", "圆括号")):
        depth = 0
        for ch in code:
            if ch == open_c:
                depth += 1
            elif ch == close_c:
                depth -= 1
                if depth < 0:
                    bad.append(f"{name}出现多余闭合")
                    break
        if depth > 0:
            bad.append(f"{name}未闭合（差 {depth} 个）")
    return bad


# ===== ② 契约断言 =====

def mask_string_braces(src: str) -> str:
    """剥注释 + 把字符串/字符字面量**内部的括号**换成空格（长度与行结构不变）。

    为什么不能直接用 strip_comments：java_body 靠花括号配对取方法体，而方法体里的
    字面量常带括号 —— 心跳拼接里的 `json.endsWith("}")`、`startsWith("{")` 都会把体
    提前截断（本轮就被它坑过一次：于是 L8/L14 看到的心跳体少了一大截）。
    也不能用 strip_java_noise：它把整个字面量删掉，而契约检查需要看到
    `callAttr("to_bytes")`、`"parse_fail"` 这些字符串内容。
    折中：只把括号抹成空格，其它字符（含转义引号）原样保留。
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in src[i:j]))
            i = j
        elif c in '"\'':
            out.append(c)
            i += 1
            while i < n and src[i] != c:
                if src[i] == "\\":
                    out.append(src[i:i + 2])
                    i += 2
                    continue
                out.append(" " if src[i] in "(){}[]" else src[i])
                i += 1
            if i < n:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def if_block(code: str, cond_prefix: str):
    """在 mask_string_braces 处理过的代码里，按括号配对取 `if (cond_prefix…)` 的分支体。

    为什么要先抹括号：native 那个守卫的条件里带 `startsWith("{")`，字符串里那个
    花括号会把「找分支体起点」直接带坑里（抹过之后它已变成一个空格）。
    取不到返回 None（调用方把它当违规，而不是当通过）。
    """
    i = code.find(cond_prefix)
    if i < 0:
        return None
    j = code.find("{", i)
    if j < 0:
        return None
    depth = 0
    for k in range(j, len(code)):
        ch = code[k]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return code[j + 1:k]
    return None


def check_processor(src: str):
    bad = []
    # 所有方法体提取与字面量搜索都同一份口径：剥注释 + 抹掉字面量里的括号
    # （两份口径会漂，而且带括号的字面量会把 java_body 的配对带坑里）。
    clean = mask_string_braces(src)
    cap = java_body(clean, "public void processCapturedImage(Image image) {")
    enc = java_body(clean, "private static void applyNativeSolver(")

    # L1 子串判据不得回来
    for key in ("native_ready", "frame_skipped"):
        if re.search(r'contains\(\s*"\\??"%s' % key, cap):
            bad.append(f"L1: {key} 又用字符串子串判据（序列化格式一变就静默失效）")
    # L2 语义判据 + 两个条件
    if 'optBoolean("native_ready"' not in cap:
        bad.append("L2: native_ready 没有从 JSON 读布尔")
    if "NativeEngine.isAvailable() && " not in cap:
        bad.append("L2: native 接管丢了 isAvailable() 这一半条件（只看玩法能力会对无 native 的机器硬调）")
    if 'optBoolean("frame_skipped"' not in cap:
        bad.append("L2: frame_skipped 没有从 JSON 读布尔（静默帧降频会失效）")
    # L9 采集热路径必须走整屏编码入口（与 ImageEncoder 那条注释同一个约定，两边都钉）
    if "ImageEncoder.encodeImageToByteArray(image)" not in cap:
        bad.append("L9: 采集热路径不再走整屏编码入口")
    if re.search(r"encodeImageToByteArray\(image\s*,", cap):
        bad.append("L9: 采集热路径改用了带 ROI 参数的编码重载 —— 裁过的图再遇 set_roi 就是两套坐标叠加")
    # L3 手牌不可映射必须早退，不半覆写
    if "handMappable" not in enc:
        bad.append("L3: applyNativeSolver 里不再有手牌可映射的判定")
    blk = if_block(enc, "if (!handMappable)")
    if blk is None:
        bad.append("L3: !handMappable 的守卫块找不到了（结构变了，守卫必须同步改）")
    elif "return" not in blk:
        bad.append("L3: 手牌不可映射时没有 return —— 会继续用 native 结果半覆写 Python 的正确答案")
    blk2 = if_block(enc, "if (nativeJsonStr == null")
    if blk2 is None:
        bad.append("L3: native 无结果的守卫块找不到了")
    elif "return" not in blk2:
        bad.append("L3: native 没给出结果时没有 return（会拿空结果覆写）")
    # L4 时间戳原点 + 三键
    if "final long capturedAt = System.currentTimeMillis();" not in cap:
        bad.append("L4: 采集入口没有打端到端时间戳原点")
    for k in ("captured_at_ms", "encode_ms", "engine_ms"):
        if f'pyObj.put("{k}"' not in cap:
            bad.append(f"L4: 出帧缺 {k}（延迟无法分解）")
    # L5 注入位置：必须在统一出口（else 分支里、applyNativeSolver 调用之外）
    i_inject = cap.find('pyObj.put("captured_at_ms"')
    i_solver = cap.find("applyNativeSolver(pyObj)")
    if i_inject < 0:
        bad.append("L5: 出帧不再注入 captured_at_ms（端到端延迟又变成读不出来的数字）")
    elif i_solver >= 0 and i_solver >= i_inject:
        bad.append("L5: 时间戳注入排到了 native 覆写之前/之内 —— 非 native 玩法会没有读数")
    if 'bytes = (pyObj.toString() + "\\n")' not in cap:
        bad.append("L5: 注入后没有用统一 pyObj 出帧（两条路径必须汇到一个出口）")
    # L13 重序列化前必须拦掉 NaN/Infinity
    if "hasNonFiniteValue(pyObj)" not in cap:
        bad.append("L13: 每帧 JSONObject 重序列化前没拦 NaN/Infinity（非标准 JSON 会被改写成语义不同的帧）")
    if "noteNonFiniteFrame" not in cap:
        bad.append("L13: 非有限帧没有计数（它们没有时间戳也不走 native，不计数就看不出来）")
    if "private static boolean hasNonFiniteValue(Object v)" not in clean:
        bad.append("L13: hasNonFiniteValue 实现丢了")
    # L6 降级不丢帧
    m = re.search(r"if \(pyObj == null\) \{(.{0,400}?)\n        \} else \{", cap, re.S)
    if not m or "to_bytes" not in m.group(1):
        bad.append("L6: 解析失败没有原样转发 Python 出帧（等于丢帧）")
    # L7 日志节流
    note = java_body(clean, "private void noteParseFailure(")
    if "sParseFailures" not in note or "% 300" not in note:
        bad.append("L7: 解析失败日志没节流（持续非法 JSON 会形成 logcat 风暴，反过来拖慢热路径）")
    # L8 统计字段 volatile + 心跳读出
    for name in ("sStampMsSum", "sStampFrames", "sParseFailures", "sNonFiniteFrames"):
        m = re.search(r"private\s+static\s+[^\n]*?\b%s\b[^\n]*;" % name, clean)
        if m is None:
            bad.append(f"L8: 找不到 {name} 的声明（volatile 断言会空转）")
        elif "volatile" not in m.group(0):
            bad.append(f"L8: {name} 非 volatile —— 采集线程写、心跳线程读")
    hb = java_body(clean, "private String withHeartbeatCounters(String json) {")
    for k in ("parse_fail", "non_finite", "stamp_avg_ms"):
        if k not in hb:
            bad.append(f"L8: 心跳没上报 {k}（本轮新增的开销与故障必须自己读数）")
    # L14 拼接前先校验前置假设：statusJson 产出不是完整对象时必须原样返回，
    # 而不是抛 StringIndexOutOfBounds（心跳也在 send_error 路径上，拼接异常会打断整帧）。
    if "json.endsWith(" not in hb:
        bad.append("L14: 心跳拼接没先校验 '}' 结尾（异常会把主链路打断）")
    return bad


def check_network(src: str):
    bad = []
    clean = strip_comments(src)
    body = java_body(clean, "public static String statusJson(String status, String message) {")
    if '\\"java_status\\":true' not in body:
        bad.append("L14: 状态帧不再自带 java_status 标记（Dart 只能靠枚举 status 字符串排除，枚举必然漏）")
    if 'sb.append("}")' not in body:
        bad.append("L14: statusJson 不再以固定 '}' 收尾（心跳去尾拼接的假设会被打破）")
    return bad


def check_encoder(src: str):
    bad = []
    clean = mask_string_braces(src)
    if "public static final int DEFAULT_QUALITY" not in clean:
        bad.append("B4: JPEG 质量回到多处硬写（口径会漂）")
    flat = java_body(clean, "public static byte[] encodeImageToByteArray(Image image) {")
    if "0f, 1f" not in flat:
        bad.append("L9: 整屏入口不再固定 0f..1f —— 采集侧一旦被裁就与 Python 的 set_roi 坐标叠加")
    if "DEFAULT_QUALITY" not in flat:
        bad.append("B4: 整屏入口没用统一质量口径")
    hot = java_body(clean, "private static byte[] bitmapToByteArray(Bitmap bitmap, int quality) {")
    if "new ByteArrayOutputStream(cap)" not in hot:
        bad.append("B4: 输出流回到默认 32 字节起步（写满一帧要翻倍扩容 ≈14 次）")
    return bad


def check_probe(src: str):
    bad = []
    if "if (_v.length > capacity)" not in src:
        bad.append("L10: 样本窗口没有上界（长跑 = 内存泄漏，且历史成绩会糊住当前退化）")
    if "age < 0 || age > maxPlausibleMs" not in src:
        bad.append("L10: 没有丢弃墙钟回拨造成的负值/荒谬样本")
    if "capturedAtMs == null || capturedAtMs <= 0" not in src:
        bad.append("L10: 缺时间戳的帧没有计入 dropped（会被静默无视，读数看不出缺口）")
    if "if (n == 0) return null;" not in src:
        bad.append("L10: 无样本时没返回 null（UI 会拿 0 冒充「0ms 很快」）")
    if "'type': 'latency'" not in src:
        bad.append("L10: 上报载荷没有类型标识，主页无法与 status/roi 帧分流")
    if "'java': Map<String, int>.from(java)," not in src:
        bad.append("L14: 上报载荷不带采集侧计数（Java 自报的开销与降级帧数就永远看不到）")
    return bad


def check_overlay(src: str):
    bad = []
    if "_latency.add(" not in src:
        bad.append("L11: 悬浮窗没再喂帧龄探针（端到端又变成读不出来的数字）")
    if "(json['captured_at_ms'] as num?)?.toInt()" not in src:
        bad.append("L11: 没读 captured_at_ms")
    if "engineMs: (json['engine_ms'] as num?)?.toInt()" not in src:
        bad.append("L11: 没把 engine_ms 分段交给探针")
    # L14 统计入口必须排除 java_status 帧：靠枚举 status 字符串必然漏（security_alert 就是遗的那个）
    if "if (json['java_status'] != true) {" not in src:
        bad.append("L14: 帧龄统计没排除 java_status 帧（心跳会把 dropped 刷满，真正的降级帧看不出来）")
    # 采集侧计数必须上屏：不能因为 status 不匹配而丢，也不能被空值重置
    if "_captureJavaCounters(json);" not in src:
        bad.append("L14: 信标分支里没抓 Java 自报计数（「让开销自己读数」会退化成一句注释）")
    cap_body = src[src.find("void _captureJavaCounters("):]
    cap_body = cap_body[:cap_body.find("\n  }")] if cap_body.find("\n  }") >= 0 else ""
    if "json['stamp_avg_ms'] == null" not in cap_body:
        bad.append("L14: 计数抓取没守卫缺键帧（不带计数的心跳会把上一次真读数盖成 0）")
    if "toShare(java: _javaCounters)" not in src:
        bad.append("L14: 上报时没把采集侧计数带上")
    i = src.find("void _ingestEngineResult(")
    if i < 0:
        bad.append("L11: _ingestEngineResult 找不到了（接线点必须在这个入口里）")
    else:
        ing = src[i:]
        # 统计必须在流水线信标帧 return 之后：心跳帧没有采集时刻，混进来会污染样本
        gate = ing.find("_kPipelineStatuses.contains(status)")
        feed = ing.find("_latency.add(")
        if gate < 0:
            bad.append("L11: 心跳信标帧的早退没了（Java 心跳会被当成画面帧计入帧龄）")
        elif feed < 0:
            bad.append("L11: 入口里没看到帧龄统计")
        elif gate > feed:
            bad.append("L11: 帧龄统计排在心跳信标之前（Java 心跳帧会被当成画面帧计入）")
    if "Timer.periodic(const Duration(seconds: 2), (Timer t) {\n      " \
            "FlutterOverlayWindow.shareData(_latency.toShare(java: _javaCounters))" not in src:
        bad.append("L11: 帧龄上报没有 2s 节流（逐帧 shareData 会让主页每帧重建）")
    if "_latencyReport?.cancel();" not in src:
        bad.append("L11: dispose 没取消上报定时器（关窗后 Timer 泄漏并持续跨引擎发消息）")
    return bad


def check_debug(src: str):
    bad = []
    if "event['type'] == 'latency'" not in src:
        bad.append("L12: 调试页没接 latency 上报")
    if "_latencySub?.cancel();" not in src:
        bad.append("L12: 订阅没在 dispose 取消（页面切换累积监听器）")
    if "v == null ? '—' : '$v'" not in src:
        bad.append("L12: 无样本时没有显示占位符（会把「没数据」印成 0ms）")
    if "开始识别后这里跟着每一帧变化" not in src:
        bad.append("L12: 没有「暂无读数」的如实说明")
    # 采集侧计数必须真的落到屏上（否则 L14 那一串上报只是网络上的死数据）
    for key in ("stamp_avg_ms", "parse_fail", "non_finite", "send_fail"):
        if f"ji('{key}')" not in src:
            bad.append(f"L14: 采集侧计数 {key} 没上屏（帧龄样本的缺口就没法对账）")
    if "if (java.isNotEmpty) ...[" not in src:
        bad.append("L12: 采集侧计数没拿到时仍会印一排 0（把「没数据」冒充成「很好」）")
    return bad


CHECKS = {
    "ImageProcessor.java": (PROC_JAVA, check_processor),
    "ImageEncoder.java": (ENC_JAVA, check_encoder),
    "NetworkClient.java": (NET_JAVA, check_network),
    "latency_probe.dart": (PROBE_DART, check_probe),
    "mahjong_overlay.dart": (OVERLAY_DART, check_overlay),
    "debug_page.dart": (DEBUG_DART, check_debug),
}
JAVA_FOR_BALANCE = (PROC_JAVA, ENC_JAVA, SEC_JAVA, MAIN_JAVA, NET_JAVA, STREAM_JAVA)


class TestLatencyContracts(unittest.TestCase):
    def test_java_structure_is_balanced(self):
        """括号配对：本轮大段重写热路径，这是本机唯一能抓出结构错位的手段。"""
        bad = []
        for p in JAVA_FOR_BALANCE:
            v = brace_violations(p)
            if v:
                bad.append(f"{os.path.basename(p)}: " + "；".join(v))
        self.assertFalse(bad, "\n".join(bad))

    def test_every_file_honours_its_contract(self):
        bad = []
        for name, (path, check) in CHECKS.items():
            v = check(read(path))
            if v:
                bad.append(f"{name}: " + "；".join(v))
        self.assertFalse(bad, "\n".join(bad))


# ===== 变异：把坏写法塞回去，断言守卫真的会红（否则守卫只是空转）=====

MUTANTS = [
    ("L1 native_ready 回到子串判据", "ImageProcessor.java",
     lambda s: s.replace('if (NativeEngine.isAvailable() && pyObj.optBoolean("native_ready", false)) {',
                         'if (NativeEngine.isAvailable() && pyJsonStr.contains("\\"native_ready\\": true")) {')),
    ("L1 frame_skipped 回到子串判据", "ImageProcessor.java",
     lambda s: s.replace('frameSkipped = pyObj.optBoolean("frame_skipped", false);',
                         'frameSkipped = pyJsonStr.contains("\\"frame_skipped\\": true");')),
    ("L3 手牌不可映射时半覆写", "ImageProcessor.java",
     lambda s: s.replace('            TimedLog.e(TAG, "手牌含 native 无法映射的牌张，本帧回退 Python: " + handStr);\n            return;',
                         '            TimedLog.e(TAG, "手牌含 native 无法映射的牌张，仍继续覆写: " + handStr);')),
    ("L4/L5 时间戳只给 native 帧注入", "ImageProcessor.java",
     lambda s: s.replace('                pyObj.put("captured_at_ms", capturedAt);\n'
                         '                pyObj.put("encode_ms", encodeMs);\n'
                         '                pyObj.put("engine_ms", engineMs);\n', '')),
    ("L6 解析失败直接丢帧", "ImageProcessor.java",
     lambda s: s.replace('            bytes = engineResult.callAttr("to_bytes").toJava(byte[].class);\n        } else {\n'
                         '            frameSkipped = pyObj.optBoolean("frame_skipped", false);',
                         '            sendStatus(NetworkClient.statusJson("java_error", "结果解析失败"));\n'
                         '            return;\n        } else {\n'
                         '            frameSkipped = pyObj.optBoolean("frame_skipped", false);')),
    ("L7 解析失败每帧刷日志", "ImageProcessor.java",
     lambda s: s.replace('        if (n == 1 || n % 300 == 0) {\n', '        if (true) {\n')),
    ("L8 统计字段丢 volatile", "ImageProcessor.java",
     lambda s: s.replace("private static volatile long sStampMsSum = 0;",
                         "private static long sStampMsSum = 0;")),
    ("L9 采集热路径改成裁 ROI 编码", "ImageProcessor.java",
     lambda s: s.replace("byte[] encoded = ImageEncoder.encodeImageToByteArray(image);",
                         "byte[] encoded = ImageEncoder.encodeImageToByteArray(image, roiTop, roiBottom);")),
    ("L13 重序列化前不拦 NaN/Infinity", "ImageProcessor.java",
     lambda s: s.replace("if (pyObj != null && hasNonFiniteValue(pyObj)) {",
                         "if (false) {")),
    ("L14 心跳拼接不校验 '}' 结尾", "ImageProcessor.java",
     lambda s: s.replace('if (json == null || json.length() < 2 || !json.endsWith("}")) {',
                         'if (false) {')),
    ("L14 状态帧丢掉 java_status 标记", "NetworkClient.java",
     lambda s: s.replace('sb.append("{\\"java_status\\":true,\\"hand\\":\\"\\",',
                         'sb.append("{\\"hand\\":\\"\\",')),
    ("L13 非有限帧不计数", "ImageProcessor.java",
     lambda s: s.replace("            noteNonFiniteFrame();\n", "")),
    ("B4 输出流回到默认小容量", "ImageEncoder.java",
     lambda s: s.replace("ByteArrayOutputStream stream = new ByteArrayOutputStream(cap);",
                         "ByteArrayOutputStream stream = new ByteArrayOutputStream();")),
    ("L10 窗口无上界", "latency_probe.dart",
     lambda s: s.replace("    if (_v.length > capacity) {\n      _v.removeRange(0, _v.length - capacity);\n    }\n", "")),
    ("L10 不过滤负值样本", "latency_probe.dart",
     lambda s: s.replace("    if (age < 0 || age > maxPlausibleMs) {\n      _dropped++;\n      return null;\n    }\n", "")),
    ("L10 无样本返回 0", "latency_probe.dart",
     lambda s: s.replace("    if (n == 0) return null;", "    if (n == 0) return 0;")),
    ("L11 探针不再收帧", "mahjong_overlay.dart",
     lambda s: s.replace("      _latency.add(\n          (json['captured_at_ms'] as num?)?.toInt(),",
                         "      _latencyDisabled = (\n          (json['captured_at_ms'] as num?)?.toInt(),")),
    ("L11 上报改成逐帧 shareData", "mahjong_overlay.dart",
     lambda s: s.replace("Timer.periodic(const Duration(seconds: 2), (Timer t) {\n      FlutterOverlayWindow.shareData(_latency.toShare(java: _javaCounters))",
                         "Timer.periodic(const Duration(milliseconds: 16), (Timer t) {\n      FlutterOverlayWindow.shareData(_latency.toShare(java: _javaCounters))")),
    ("L11 dispose 不取消定时器", "mahjong_overlay.dart",
     lambda s: s.replace("    _latencyReport?.cancel();", "")),
    ("L14 帧龄统计不再排除状态帧", "mahjong_overlay.dart",
     lambda s: s.replace("if (json['java_status'] != true) {", "if (true) {")),
    ("L14 不去重抓采集侧计数", "mahjong_overlay.dart",
     lambda s: s.replace("      _captureJavaCounters(json);\n", "")),
    ("L14 计数缺键时也覆盖上一次真读数", "mahjong_overlay.dart",
     lambda s: s.replace("    if (json['stamp_avg_ms'] == null) return;\n", "")),
    ("L14 上报载荷不带采集侧计数", "latency_probe.dart",
     lambda s: s.replace("      'java': Map<String, int>.from(java),\n", "")),
    ("L12 采集侧解析开销不上屏", "debug_page.dart",
     lambda s: s.replace("${ji('stamp_avg_ms')}ms", "?").replace("ji('stamp_avg_ms')", "0")),
    ("L12 无样本印 0ms", "debug_page.dart",
     lambda s: s.replace("String ms(int? v) => v == null ? '—' : '$v';",
                         "String ms(int? v) => '${v ?? 0}';")),
]

_REG = {k: (p, c) for k, (p, c) in CHECKS.items()}


class TestMutationControls(unittest.TestCase):
    def setUp(self) -> None:
        if not MUTATE:
            self.skipTest("加 --mutate 才跑变异对照")

    def test_mutants_are_caught(self):
        missed = []
        for label, key, fn in MUTANTS:
            path, check = _REG[key]
            text = read(path)
            mutated = fn(text)
            if mutated == text:
                missed.append(f"{label}: 变异没改到任何字节（模板过期）")
                continue
            if not check(mutated):
                missed.append(f"{label}: 坏写法通过了守卫（守卫空转）")
        self.assertFalse(missed, "\n".join(missed))

    def test_mutation_still_keeps_java_balanced(self):
        """L6 那条变异会把整段换成 return 形态，顺带确认它没破坏括号配对 —— 否则
        「结构检查」和「契约检查」会在变异里互相掩盖。"""
        path = _REG["ImageProcessor.java"][0]
        text = read(path)
        for label, key, fn in MUTANTS:
            if key != "ImageProcessor.java":
                continue
            m = fn(text)
            if m != text:
                self.assertFalse(brace_violations_from_string(m), f"{label}: 变异文本自身不闭合")


def brace_violations_from_string(src: str):
    code = strip_java_noise(src)
    bad = []
    for open_c, close_c, name in (("{", "}", "大括号"), ("(", ")", "圆括号")):
        depth = 0
        for ch in code:
            if ch == open_c:
                depth += 1
            elif ch == close_c:
                depth -= 1
                if depth < 0:
                    bad.append(name)
                    break
        if depth > 0:
            bad.append(name)
    return bad


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestLatencyContracts))
    if MUTATE:
        suite.addTests(loader.loadTestsFromTestCase(TestMutationControls))
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
