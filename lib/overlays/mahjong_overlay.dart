import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:auto_vision/mode_store.dart';
import 'package:auto_vision/channel.dart';
import 'package:auto_vision/license/license_service.dart';
import 'package:auto_vision/license/license_status.dart';
import 'package:auto_vision/overlays/tile_labels.dart';
import 'package:auto_vision/server.dart';

// 解析原生层发来的分析结果：前 10('\n') 之前为 JSON，之后为 PNG 预览图字节。
// 预览图当前不在界面上展示（仅保留字节以备扩展），因此这里不做解码，
// 避免每帧在 UI 线程上解码图片造成卡顿。
Map<String, dynamic>? parseEngineResult(List<int> b) {
  final int sepIndex = b.indexOf(10); // 对应 '\n'
  if (sepIndex <= 0) {
    return null;
  }
  try {
    return jsonDecode(utf8.decode(b.sublist(0, sepIndex)))
        as Map<String, dynamic>;
  } catch (e) {
    print('解析分析结果失败：$e');
    return null;
  }
}

/// 单张麻将牌的小卡片。横排展示用，整体不依赖任何游戏资源。
class TileChip extends StatelessWidget {
  final String tile; // mpsz 形式，如 "5m" "7p" "1z"
  final double size;
  final bool dim;
  // 绝张：手牌 + 牌河累计该牌型已出 ≥4 张。该牌在凑牌上已"死"，可放心打
  // 且别人也几乎不可能拿它和牌 —— App 自动算出来的、肉眼看不出来的高价值信号。
  // 配色：灰底 + 青绿描边 + 「绝」白底青字标，绝对不用红/橙/琥珀。
  final bool dead;
  final bool isDrawing;
  final String? defenseLevel;

  const TileChip({
    super.key,
    required this.tile,
    this.size = 26,
    this.dim = false,
    this.dead = false,
    this.isDrawing = false,
    this.defenseLevel,
  });


  @override
  Widget build(BuildContext context) {
    final cn = tileToChinese(tile);
    final suit = tile.endsWith('m')
        ? 'm'
        : tile.endsWith('p')
            ? 'p'
            : tile.endsWith('s')
                ? 's'
                : 'z';
    final Color charColor;
    if (suit == 'm' || suit == 'p') {
      // 万/筒：蓝绿色（中国主流牌面配色）
      charColor = const Color(0xFF1E6B7A);
    } else if (suit == 's') {
      // 条：草绿色
      charColor = const Color(0xFF2E7D32);
    } else {
      // 字牌：所有字牌统一深灰
      charColor = const Color(0xFF202124);
    }

    // 绝张的牌面底色换成中性灰，并加一道青绿描边把它从普通牌里顶出来 —— 一眼可见。
    final Color tileBg = dead ? const Color(0xFFBDBDBD) : const Color(0xFFF7F3E8);
    final Color tileBorder = isDrawing
        ? const Color(0xFFFFD54F)
        : (dead ? const Color(0xFF00695C) : const Color(0xFFB7A98F));
    final double tileBorderW = (dead || isDrawing) ? 1.2 : 0.6;

    // 角标独立透明层：badge 行放在牌面正上方（固定高、透明底），
    // 彻底告别旧版 Positioned(-4,-4) 溢出压住邻牌牌面的遮挡问题。
    // 左槽：摸（优先）或 危；右槽：绝。无角标时占空位，保证整行牌顶对齐。
    final String? leftBadge = isDrawing ? '摸' : (
        defenseLevel == 'DANGER' && !dead ? '危' : null);

    return Opacity(
      opacity: dim ? 0.45 : 1.0,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: size,
            height: size * 0.40,
            child: Padding(
              padding: const EdgeInsets.only(right: 2),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  if (leftBadge != null)
                    _TileBadge(
                      text: leftBadge,
                      bg: isDrawing ? const Color(0xFF2E7D32) : const Color(0xFFB71C1C),
                      fg: Colors.white,
                      border: isDrawing
                          ? const Color(0xFFFFD54F)
                          : const Color(0xFFFF8A80),
                    )
                  else
                    const SizedBox.shrink(),
                  if (dead)
                    _TileBadge(
                      text: '绝',
                      bg: const Color(0xFFE0F2F1), // 青绿浅底，与主色统一
                      fg: const Color(0xFF00695C),
                      border: const Color(0xFF00695C),
                    )
                  else
                    const SizedBox.shrink(),
                ],
              ),
            ),
          ),
          Container(
            width: size,
            height: size * 1.18,
            decoration: BoxDecoration(
              color: tileBg,
              borderRadius: BorderRadius.circular(3),
              border: Border.all(color: tileBorder, width: tileBorderW),
              boxShadow: [
                BoxShadow(
                  color: isDrawing ? const Color(0x66FFD54F) : Colors.black.withAlpha(40),
                  blurRadius: isDrawing ? 3 : 1,
                  offset: const Offset(0, 0.5),
                ),
              ],
            ),
            alignment: Alignment.center,
            child: FittedBox(
              fit: BoxFit.scaleDown,
              child: Text(
                cn,
                style: TextStyle(
                  color: charColor,
                  fontWeight: FontWeight.w800,
                  fontSize: size * 0.62,
                  height: 1.0,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// TileChip 顶部的悬浮角标（摸/危/绝），画在牌面之外的透明层上，不遮牌。
class _TileBadge extends StatelessWidget {
  final String text;
  final Color bg;
  final Color fg;
  final Color border;

  const _TileBadge({
    required this.text,
    required this.bg,
    required this.fg,
    required this.border,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 2.5, vertical: 0.5),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(2.5),
        border: Border.all(color: border, width: 0.5),
      ),
      child: Text(
        text,
        style: TextStyle(
          color: fg,
          fontSize: 6.5,
          fontWeight: FontWeight.bold,
          height: 1.0,
        ),
      ),
    );
  }
}

/// 一排手牌 chip（最多 14 张）。多余空间自动 wrap。
/// 可选地接收一个 deadTiles 集合（mpsz 形式），集合内的牌型会被自动标上「绝」标。
class HandChipRow extends StatelessWidget {
  final String hand; // mpsz 形式
  final double chipSize;
  final Set<String>? deadTiles;
  final String? drawingTile;
  final Map<String, dynamic>? defenseMap;

  const HandChipRow({
    super.key,
    required this.hand,
    this.chipSize = 24,
    this.deadTiles,
    this.drawingTile,
    this.defenseMap,
  });

  @override
  Widget build(BuildContext context) {
    final tiles = _mpszToTiles(hand);
    if (tiles.isEmpty) {
      return const SizedBox.shrink();
    }
    final drawingIndex = drawingTile != null ? tiles.lastIndexOf(drawingTile!) : -1;
    return Wrap(
      // TileChip 自身不再有 margin，间距在此统一控制
      spacing: 3,
      runSpacing: 3,
      children: tiles.asMap().entries
          .map((entry) => TileChip(
                tile: entry.value,
                size: chipSize,
                dead: deadTiles?.contains(entry.value) ?? false,
                isDrawing: entry.key == drawingIndex,
                defenseLevel: defenseMap?[entry.value]?.toString(),
              ))
          .toList(),
    );
  }
}


/// 把 "1m2m3p4p5z" 拆成 ["1m","2m","3p","4p","5z"]。
List<String> _mpszToTiles(String mpsz) {
  final out = <String>[];
  var buf = StringBuffer();
  for (final ch in mpsz.split('')) {
    if (RegExp(r'[0-9]').hasMatch(ch)) {
      buf.write(ch);
    } else if ('mpsz'.contains(ch)) {
      if (buf.isNotEmpty) {
        out.add('${buf.toString()}$ch');
        buf.clear();
      }
    }
  }
  return out;
}



/// 危险牌预警小标：防点炮 / 防杠 的等级提示。
/// 配色遵守全局约束（禁红 / 橙 / 琥珀）：安全 = 青绿、中等 = 蓝灰、危险 = 深蓝灰 + 白字描边。
Widget _dangerTag(String label, String level) {
  final Color bg;
  final String text;
  if (level == 'safe') {
    bg = const Color(0xFF00695C);
    text = '$label·安全';
  } else if (level == 'mid') {
    bg = const Color(0xFF546E7A);
    text = '$label·中';
  } else {
    // risky：深蓝灰底 + 白字加粗 + 一道白描边，足够醒目但不触碰红/橙/琥珀。
    bg = const Color(0xFF263238);
    text = '$label·危险';
  }
  return Container(
    margin: const EdgeInsets.only(top: 2),
    padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
    decoration: BoxDecoration(
      color: bg,
      borderRadius: BorderRadius.circular(3),
      border: level == 'risky'
          ? Border.all(color: Colors.white, width: 0.6)
          : null,
    ),
    child: Text(
      text,
      style: const TextStyle(
        color: Colors.white,
        fontSize: 8.5,
        fontWeight: FontWeight.bold,
        height: 1.0,
      ),
    ),
  );
}

/// "推荐打这张"卡片。打 [牌] → 进张 N 张。
class AdviceCard extends StatelessWidget {
  final String tile;
  final int ukeire;
  final bool best;
  // 危险牌预警（防点炮 / 防杠）：仅当调试页对应开关开启、且引擎算出该字段时非空。
  // 值：deal_in ∈ {"safe","risky"}；pon_kong ∈ {"safe","mid","risky"}。
  final String? dealIn;
  final String? ponKong;
  final String? reason;
  final bool isDingque;

  const AdviceCard({
    super.key,
    required this.tile,
    required this.ukeire,
    this.best = false,
    this.dealIn,
    this.ponKong,
    this.reason,
    this.isDingque = false,
  });

  @override
  Widget build(BuildContext context) {
    final bg = best
        ? const Color(0x331B5E20)
        : const Color(0x22FFFFFF);
    final border = best
        ? const Color(0xFF66BB6A)
        : const Color(0x33FFFFFF);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: border, width: 0.6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            '打',
            style: const TextStyle(
              color: Colors.white70,
              fontSize: 11,
            ),
          ),
          const SizedBox(width: 4),
          TileChip(tile: tile, size: 22),
          const SizedBox(width: 6),
          Text.rich(
            TextSpan(
              style: const TextStyle(decoration: TextDecoration.none),
              children: [
                const TextSpan(
                  text: '进张 ',
                  style: TextStyle(color: Colors.white70, fontSize: 11, decoration: TextDecoration.none),
                ),
                TextSpan(
                  text: '$ukeire',
                  style: TextStyle(
                    color: best ? Colors.lightGreenAccent : Colors.lightBlueAccent,
                    fontSize: 14,
                    fontWeight: FontWeight.bold,
                    decoration: TextDecoration.none,
                  ),
                ),
                const TextSpan(
                  text: ' 张',
                  style: TextStyle(color: Colors.white70, fontSize: 11, decoration: TextDecoration.none),
                ),
              ],
            ),
          ),
          if (dealIn != null || ponKong != null) ...[
            const SizedBox(width: 6),
            Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (dealIn != null) _dangerTag('防点炮', dealIn!),
                if (ponKong != null) _dangerTag('防杠', ponKong!),
              ],
            ),
          ],
          if (isDingque) ...[
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
              decoration: BoxDecoration(
                color: const Color(0xFF00695C),
                borderRadius: BorderRadius.circular(3),
              ),
              child: const Text(
                '定缺',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 9,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
          ],
          if (best) ...[
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
              decoration: BoxDecoration(
                color: const Color(0xFF2E7D32),
                borderRadius: BorderRadius.circular(3),
              ),
              child: const Text(
                '最优',
                style: TextStyle(color: Colors.white, fontSize: 9),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class MahjongOverlay extends StatefulWidget {
  const MahjongOverlay({super.key});

  @override
  State<MahjongOverlay> createState() => _MahjongOverlayState();
}

class _MahjongOverlayState extends State<MahjongOverlay> {
  // 最近一帧的识别结果
  Map<String, dynamic>? result;
  bool ready = false;

  // 收起态 = 悬浮按钮；展开态 = 分析面板（可自由缩放）
  bool panelVisible = false;

  // 当前玩法：悬浮窗写入共享文件，Python 引擎每帧读取。默认川麻。
  String selectedMode = 'sc';

  List<dynamic> _shownAdvice = const [];
  String _shownBest = '';

  static const double collapsed = 56;
  // 胶囊微缩模式：收起态下在屏幕边缘显示小巧横条，展示听牌/最优打法
  bool _capsuleMode = true;
  static const double _kCapsuleW = 210;
  static const double _kCapsuleH = 38;
  // 9x3 剩余牌矩阵面板折叠态：默认折叠，弹窗小巧简约不眼花
  bool _matrixExpanded = false;
  // 默认小巧面板：宽度 220dp，高度 210dp
  double panelW = 220;
  double panelH = 210;

  // 展开态独立垂直滚动控制器
  final ScrollController _panelScrollController = ScrollController();

  // ── 自由缩放临界点常量 ──
  static const double minPanelW = 190;
  static const double maxPanelW = 420;
  static const double minPanelH = 120;
  static const double maxPanelH = 520;

  // 缩放中：此期间关闭原生拖动，避免拖把手时整窗跟着位移
  bool _draggingResize = false;
  bool _resizeInFlight = false;
  bool _isResizing = false;
  bool _hitLimitFeedback = false;

  // ── 授权自守护（悬浮窗子引擎用 device_id 直连服务器心跳，到期即自锁）──
  bool _licenseAllows = true; // 乐观默认，首次异步核验后纠正
  int _licenseDays = 0;
  Timer? _licenseTimer;
  Timer? _licenseExpiryTimer;

  // ── socket 服务生命周期：旧实现 Server 建完即丢引用，dispose 不关监听，
  //    关窗重开后旧 State 的监听与新监听共存抢连接丢帧（server.dart 已去 shared）。
  Server? _server;

  // ── 假活治理：区分"数据帧到了 / 仅 Java 心跳 / 彻底断流 / 采集已停止"。
  //    旧实现状态条恒显"实时"，采集断流后 UI 永远假活。Java 侧每 2s 必发
  //    流水线心跳帧，据此把"画面静止"与"链路死亡"分开呈现。
  DateTime? _lastFrameAt;
  DateTime? _lastPipelineAt;
  bool _signalLost = false;
  bool _projectionStopped = false;
  Timer? _signalWatchdog;

  // ── 版本徽章：读真实构建版本，取代硬编码 "PRO v1.4.5"（版本漂移误导用户）。
  String _appVersion = '';

  // ── 清空型帧去抖：waiting/no_tiles/错误态等"清空帧"需连续 ≥2 帧或持续 ~400ms
  //    才采纳，ok 帧立即上屏；杜绝引擎瞬态坏帧造成文案闪烁。
  static const Set<String> _kClearStatuses = {
    'waiting', 'no_tiles', 'py_error', 'decode_error', 'animation',
  };
  // Java 采集层流水线状态帧（同一 schema，只当信标，不参与画面数据替换）
  static const Set<String> _kPipelineStatuses = {
    'capturing', 'paused_foreground', 'send_error', 'capture_error',
    'engine_ready', 'start_failed', 'java_error', 'projection_stopped',
    'pipeline_stalled', 'stopped',
  };
  int _pendingClearRun = 0;
  DateTime? _firstPendingClearAt;
  Map<String, dynamic>? _pendingClearJson;

  @override
  void initState() {
    super.initState();

    // 加载用户自定义记忆弹窗尺寸
    _loadSavedSize();

    // 监听原生层通过本地 socket 发来的每帧分析结果（端口 12345 与 ImageProcessor 发送端一致）。
    // 即便 socket 启动失败也不能让悬浮窗引擎崩溃（否则按钮永远不渲染），因此整体 try/catch 兜底。
    try {
      _server = Server(
        callback: (data) {
          final json = parseEngineResult(data);
          if (json == null) return;
          _ingestEngineResult(json);
          _maybeShareStatus(json);
        },
        host: "127.0.0.1",
        port: 12345,
      );
    } catch (e) {
      print("悬浮窗分析服务初始化失败（不影响按钮显示）：$e");
    }

    // 假活看门狗：每 500ms 检查数据帧/心跳到达间隔；Java 固定 2s 心跳下，
    // 引擎死但采集活 → 心跳仍在（不算中断，避免误报）；彻底断流 >2s → 信号中断。
    _signalWatchdog =
        Timer.periodic(const Duration(milliseconds: 500), _checkSignalLost);

    // 版本徽章：从原生 BuildConfig 读真实版本号（失败静默，徽章不渲染）。
    _loadAppVersion();

    // 插件 showOverlay 时把 width/height 当作物理像素使用（未做 dp 转换），
    // 56dp 的按钮在 3 倍密度屏上会被画成 56 像素（约 7mm，几乎看不见）。
    // 因此这里由悬浮窗自身按 dp 重新设定一次尺寸。
    // 注意：resizeOverlay 走的是悬浮窗引擎的通道，只有悬浮窗自己调用才生效。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final double w = _capsuleMode ? _kCapsuleW : collapsed;
      final double h = _capsuleMode ? _kCapsuleH : collapsed;
      _ensureSize(w, h);
    });

    // 授权核验：异步服务器心跳，之后每 60 秒静默复核（单行只读、成本极低）。
    // 取 60s 而非旧 30min：主 isolate 被厂商省电冻结时，子引擎仍能 ≤~1 分钟内自锁。
    _refreshLicense();
    _licenseTimer =
        Timer.periodic(const Duration(seconds: 60), (_) => _refreshLicense());

    // 玩法文件已改由主页通过 Java MethodChannel 写入；这里不再读 dart:io 文件。
    // （注：本 Flutter 端的 selectedMode 仍保留，仅用于把当前模式透传给主界面。）
  }

  @override
  void dispose() {
    _licenseTimer?.cancel();
    _licenseExpiryTimer?.cancel();
    _signalWatchdog?.cancel();
    // 必须关闭 socket 监听：旧实现泄漏 Server，旧 State 继续抢接 Java 短连接丢帧。
    _server?.close();
    _server = null;
    _panelScrollController.dispose();
    super.dispose();
  }

  Future<void> _loadAppVersion() async {
    try {
      final v = await const MethodChannel(CHANNEL_NAME).invokeMethod<String>('getVersion');
      if (v != null && v.isNotEmpty && mounted) {
        setState(() => _appVersion = v);
      }
    } catch (_) {
      // 通道不可用时徽章不渲染，绝不再显示假版本号。
    }
  }

  void _checkSignalLost(Timer t) {
    if (!mounted) return;
    final now = DateTime.now();
    bool lost;
    if (_projectionStopped) {
      lost = true;
    } else if (_lastFrameAt == null) {
      lost = false; // 尚未收到过任何帧：保持原"等待/实时"文案，不误报
    } else {
      final frameGap = now.difference(_lastFrameAt!).inMilliseconds;
      final statusGap = _lastPipelineAt == null
          ? 1 << 40
          : now.difference(_lastPipelineAt!).inMilliseconds;
      lost = frameGap > 2000 && statusGap > 2000;
    }
    if (lost != _signalLost) setState(() => _signalLost = lost);
  }

  Future<void> _refreshLicense() async {
    try {
      // 子引擎用 device_id 直连服务器心跳（不依赖 shared_preferences）。服务器明确
      // 到期/拉黑/换设备 → 硬锁；网络失败且本引擎读不到本地券 → 保持 fail-open，
      // 避免断网误伤正常用户（真到期由服务器回包 valid:false 或主闸门收窗兜底）。
      final st = await LicenseService.instance.heartbeat();
      if (!mounted) return;
      final denied = st.status == LicenseStatus.licenseExpired ||
          st.status == LicenseStatus.refused;
      final allows = !denied;
      final days = st.remainingDays;
      if (allows != _licenseAllows || days != _licenseDays) {
        setState(() {
          _licenseAllows = allows;
          _licenseDays = days;
        });
      }
      _scheduleLicenseExpiryCheck(st, allows);
    } catch (_) {
      // 核验异常绝不让悬浮窗引擎崩溃；保持当前状态。
    }
  }

  /// 若总到期在下一个 60s 轮询之前，排一个到期精确 one-shot，到期即时锁。
  void _scheduleLicenseExpiryCheck(LicenseState st, bool allows) {
    _licenseExpiryTimer?.cancel();
    final exp = st.expiresAt;
    if (exp == null || !allows) return;
    final serverNow = DateTime.fromMillisecondsSinceEpoch(
        LicenseService.instance.serverNowSec * 1000);
    final remainMs = exp.difference(serverNow).inMilliseconds;
    final d = remainMs <= 0 ? Duration.zero : Duration(milliseconds: remainMs + 1000);
    _licenseExpiryTimer = Timer(d, _refreshLicense);
  }

  // ===== 渲染节流：前沿立即 + 尾部合并（降低 UI 重建频率，不增加反馈延迟）=====
  // 引擎每 25~80ms 推一帧，逐帧 setState 会让整棵大型 widget 树以 40Hz 重建，
  // UI 线程被重建本身占满，反而拖慢"最新结果"的上屏。策略：
  // 新数据到达时**第一帧立即应用**（零额外感知延迟），120ms 窗口内的后续帧
  // 合并为一次，窗口收尾时应用最新一帧（保证尾部数据不丢）。
  Map<String, dynamic>? _pendingJson;
  bool _renderScheduled = false;

  void _ingestEngineResult(Map<String, dynamic> json) {
    final status = json['status'];
    // Java 采集层流水线帧（capturing 心跳等）：只当信标更新，绝不拿它的
    // 空 hand/advice 覆盖当前画面数据（旧行为会让心跳把建议清空闪现）。
    if (status is String && _kPipelineStatuses.contains(status)) {
      _lastPipelineAt = DateTime.now();
      if (_signalLost && mounted) setState(() => _signalLost = false);
      if (status == 'projection_stopped' || status == 'stopped' || status == 'start_failed') {
        if (!_projectionStopped && mounted) {
          setState(() => _projectionStopped = true);
        }
      } else if (_projectionStopped) {
        if (mounted) setState(() => _projectionStopped = false);
      }
      return;
    }
    final isClear = status is String && _kClearStatuses.contains(status);
    if (!isClear) {
      // ok/partial/dingque/swap/pick 等有数据帧：前沿立即应用，并打断待确认的清空帧
      _pendingClearRun = 0;
      _firstPendingClearAt = null;
      _pendingClearJson = null;
      _lastFrameAt = DateTime.now();
      if (_signalLost && mounted) setState(() => _signalLost = false);
      _pendingJson = json;
      if (_renderScheduled) return;
      _applyPendingResult(); // 前沿：立即上屏
      _renderScheduled = true;
      Future<void>.delayed(const Duration(milliseconds: 120), () {
        _renderScheduled = false;
        if (!mounted) return;
        if (_pendingJson != null) _applyPendingResult(); // 尾部：应用窗口内最新一帧
      });
      return;
    }
    // 清空帧去抖：当前正显示好数据 → 首帧仍立即采纳（真离开对局要快速响应），
    // 但后续同类清空帧必须连续 ≥2 帧或持续 ~400ms 才再次替换画面，
    // 瞬态坏帧（引擎单帧 waiting/no_tiles）永远到不了屏幕。
    _pendingClearRun++;
    _firstPendingClearAt ??= DateTime.now();
    final last = _pendingJson;
    final showingGood =
        last != null && !_kClearStatuses.contains(last['status'] as String? ?? '');
    final sustained = DateTime.now()
            .difference(_firstPendingClearAt!)
            .inMilliseconds >= 400;
    if (showingGood || _pendingClearRun >= 2 || sustained) {
      _pendingClearRun = 0;
      _firstPendingClearAt = null;
      _pendingClearJson = null;
      _lastFrameAt = DateTime.now();
      _pendingJson = json;
      _applyPendingResult();
    } else {
      // 未达采纳条件：暂存，到期由看门狗兜底提交（持续清空说明确实该清）
      _pendingClearJson = json;
      _scheduleClearCommit();
    }
  }

  bool _clearCommitScheduled = false;

  void _scheduleClearCommit() {
    if (_clearCommitScheduled) return;
    _clearCommitScheduled = true;
    Future<void>.delayed(const Duration(milliseconds: 420), () {
      _clearCommitScheduled = false;
      if (!mounted || _pendingClearJson == null) return;
      _lastFrameAt = DateTime.now();
      _pendingJson = _pendingClearJson;
      _pendingClearJson = null;
      _pendingClearRun = 0;
      _firstPendingClearAt = null;
      _applyPendingResult();
    });
  }

  void _applyPendingResult() {
    final json = _pendingJson;
    if (json == null || !mounted) return;
    _pendingJson = null;
    setState(() {
      result = json;
      if (json['status'] == 'waiting') {
        _shownAdvice = const [];
        _shownBest = '';
      } else {
        _shownAdvice = (json['advice'] ?? const []) as List<dynamic>;
        _shownBest = (json['best'] ?? '') as String;
      }
      ready = true;
      // 引擎已读到玩法文件并回传，与本地选择不一致时以回传为准，保持两端同步。
      // 引擎回传的 mode 与本地一致即可，不再校验 kModeOptions。
      final m = json['mode'];
      if (m is String && m != selectedMode) {
        selectedMode = m;
      }
    });
  }

  // 授权到期/被停用时的悬浮占位胶囊：不给出任何牌建议。
  Widget _licenseDisabled() {
    return Center(
      child: Container(
        margin: const EdgeInsets.all(8),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: BoxDecoration(
          color: const Color(0xF51A1D24),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: const Color(0xFFFF8A80), width: 1),
        ),
        child: const Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.lock_outline, size: 15, color: Color(0xFFFF8A80)),
            SizedBox(width: 6),
            Text(
              '授权已到期，请续费',
              style: TextStyle(
                color: Colors.white,
                fontSize: 12.5,
                fontWeight: FontWeight.w700,
                decoration: TextDecoration.none,
              ),
            ),
          ],
        ),
      ),
    );
  }

  // 只在识别内容真正变化时回传一次摘要给主 App，
  // 让主界面也能确认"后端确实在识别"，而不是每帧刷屏。
  String _lastSharedKey = '';

  // 弹窗顶部状态与假活治理：不再恒显"实时"。数据帧与 Java 流水线心跳都断 >2s
  // → 「信号中断」并灰化数据区；采集被系统/用户停止 → 「采集已停止」。
  // 旧实现把状态条固定成"实时"只是掩盖断流观感，用户永远不知道链路已死。



  // 诊断行：恒定显示识别链路关键指标，便于"识别不出来"时一眼定位断在哪：
  //   状态  引擎最终状态（ok / no_tiles / 各种错误）
  //   切牌  本帧结构识别器切出的牌总数（0 = 根本没找到牌，多半是朝向/画面问题）
  //   方向  当前锁定的旋转角度
  //   张数  已建立稳定手牌的张数
  // 全部做空安全处理，任何字段缺失都不渲染、绝不抛错。


  void _maybeShareStatus(Map<String, dynamic> json) {
    final key =
        "${json['hand']}|${json['shanten']}|${json['status']}|${json['count']}|${json['best']}";
    if (key == _lastSharedKey) return;
    _lastSharedKey = key;
    FlutterOverlayWindow.shareData({
      'type': 'status',
      'hand': json['hand'] ?? '',
      'count': json['count'] ?? 0,
      'status': json['status'] ?? '',
      'shanten': json['shanten'],
      'mode': json['mode'] ?? selectedMode,
      'top_score': json['top_score'] ?? 0,
      'screen': (json['screen'] as List?)?.join('x') ?? '',
      'message': json['message'] ?? '',
      'best': json['best'] ?? '',
      'advice': json['advice'] ?? const [],
    }).catchError((_) {});
  }

  /// 一键「新局重置」：向原生与 Python 发送重置信号，同时界面瞬间恢复 108 张活牌满额
  void _requestResetMatch() {
    FlutterOverlayWindow.shareData({'type': 'reset_match'}).catchError((_) {});
    if (mounted) {
      setState(() {
        final resetMatrix = {
          'm': List.filled(9, 4),
          'p': List.filled(9, 4),
          's': List.filled(9, 4),
          'z': List.filled(7, 4),
        };
        if (result != null) {
          result = Map<String, dynamic>.from(result!)
            ..['remaining_matrix'] = resetMatrix
            ..['hand'] = ''
            ..['count'] = 0
            ..['discards'] = ''
            ..['discard_count'] = 0
            ..['advice'] = []
            ..['best'] = ''
            ..['status'] = 'waiting'
            ..['message'] = '已重置新对局';
        }
        _shownAdvice = const [];
        _shownBest = '';
      });
    }
  }

  /// 持续重试直到窗口尺寸设置成功（首次显示、展开/收起时用）
  Future<void> _ensureSize(double w, double h, {bool drag = true}) async {
    for (int i = 0; i < 40; i++) {
      try {
        final ok =
            await FlutterOverlayWindow.resizeOverlay(w.toInt(), h.toInt(), drag);
        if (ok == true) return;
      } catch (_) {}
      await Future<void>.delayed(const Duration(milliseconds: 100));
    }
    print('悬浮窗尺寸校正失败（w=$w, h=$h）');
  }

  double? _pendingW;
  double? _pendingH;
  DateTime? _lastResizeCallTime;

  /// 读取用户在本地记忆的悬浮窗自定义尺寸
  Future<void> _loadSavedSize() async {
    try {
      final sp = await SharedPreferences.getInstance();
      final double? sw = sp.getDouble('overlay_panel_w');
      final double? sh = sp.getDouble('overlay_panel_h');
      if (sw != null && sh != null && mounted) {
        setState(() {
          panelW = sw.clamp(minPanelW, maxPanelW);
          panelH = sh.clamp(minPanelH, maxPanelH);
        });
      }
    } catch (_) {}
  }

  /// 持久化保存用户自定义悬浮窗尺寸
  Future<void> _savePanelSize(double w, double h) async {
    try {
      final sp = await SharedPreferences.getInstance();
      await sp.setDouble('overlay_panel_w', w);
      await sp.setDouble('overlay_panel_h', h);
    } catch (_) {}
  }

  /// 拖动缩放把手时实时平滑更新原生窗口物理尺寸。
  ///
  /// 关键要点：
  /// 1. 传 false 彻底屏蔽原生拖动，防止原生 onTouch 将缩放手势识别为整窗位移。
  /// 2. 35ms 极速节流，既保证 60Hz 视觉丝滑缩放，又绝不造成 Android 消息通道阻塞。
  /// 3. 生命期守卫：手指一旦松开（_isResizing == false），立即废弃后续延时任务，
  ///    杜绝延时任务覆盖原生拖动状态！
  void _resizeLive(double w, double h) {
    if (!_isResizing) return;
    _pendingW = w;
    _pendingH = h;
    if (_resizeInFlight) return;

    final now = DateTime.now();
    final elapsed = _lastResizeCallTime == null
        ? 999
        : now.difference(_lastResizeCallTime!).inMilliseconds;
    if (elapsed < 35) {
      Future.delayed(Duration(milliseconds: 35 - elapsed), () {
        if (!_isResizing) return;
        if (_pendingW != null && _pendingH != null && !_resizeInFlight && mounted) {
          final nw = _pendingW!;
          final nh = _pendingH!;
          _pendingW = null;
          _pendingH = null;
          _resizeLive(nw, nh);
        }
      });
      return;
    }

    _resizeInFlight = true;
    _lastResizeCallTime = now;
    FlutterOverlayWindow.resizeOverlay(w.toInt(), h.toInt(), false)
        .catchError((Object _) => null)
        .whenComplete(() {
      _resizeInFlight = false;
      if (!_isResizing) return;
      final double? nw = _pendingW;
      final double? nh = _pendingH;
      if (nw != null && nh != null) {
        _pendingW = null;
        _pendingH = null;
        _resizeLive(nw, nh);
      }
    });
  }

  // 悬浮按钮点击：在"仅按钮/胶囊"与"分析面板"之间切换（窗口始终常驻在屏幕上）
  Future<void> _togglePanel() async {
    if (!mounted) return;
    final next = !panelVisible;
    setState(() {
      panelVisible = next;
    });
    if (next) {
      // 展开分析面板时保持原生拖动开启（enableDrag = true）。
      // 原生 OverlayService 保证：顶部 50dp 区域触发 120Hz 极速原生拖动位移；
      // 50dp 以下内容区域完全透传给 Flutter SingleChildScrollView 自由顺畅滚动。
      panelH = panelH.clamp(minPanelH, maxPanelH);
      await _ensureSize(panelW, panelH, drag: true);
    } else {
      final double w = _capsuleMode ? _kCapsuleW : collapsed;
      final double h = _capsuleMode ? _kCapsuleH : collapsed;
      // 收起态整块开启原生拖动，方便随时拖动胶囊/按钮到屏幕任意边缘
      await _ensureSize(w, h, drag: true);
    }
  }







  // 自由缩放把手：右下角，支持手指任意平滑拖动，实时自由缩放，带有边界临界点与触感反馈
  Widget _resizeHandle() {
    return Positioned(
      right: 0,
      bottom: 0,
      child: Listener(
        behavior: HitTestBehavior.opaque,
        onPointerDown: (e) {
          _isResizing = true;
          _hitLimitFeedback = false;
          setState(() => _draggingResize = true);
          // 立即向原生层关闭顶栏拖动，防止手势被原生拦截位移
          FlutterOverlayWindow.resizeOverlay(panelW.toInt(), panelH.toInt(), false);
        },
        onPointerMove: (e) {
          if (!_isResizing) return;
          final double prevW = panelW;
          final double prevH = panelH;
          final double nw = (panelW + e.delta.dx).clamp(minPanelW, maxPanelW);
          final double nh = (panelH + e.delta.dy).clamp(minPanelH, maxPanelH);

          // 达到最大或最小临界点（临界点控制）
          final bool atLimit = (nw == minPanelW || nw == maxPanelW || nh == minPanelH || nh == maxPanelH);
          if (atLimit && !_hitLimitFeedback) {
            _hitLimitFeedback = true;
            HapticFeedback.selectionClick();
          } else if (!atLimit) {
            _hitLimitFeedback = false;
          }

          if (nw != prevW || nh != prevH) {
            setState(() {
              panelW = nw;
              panelH = nh;
            });
            _resizeLive(panelW, panelH);
          }
        },
        onPointerUp: (e) async {
          if (!_isResizing) return;
          _isResizing = false;
          _pendingW = null;
          _pendingH = null;
          setState(() => _draggingResize = false);
          // 释放手指，恢复原生顶栏拖动并固定精确尺寸
          await _ensureSize(panelW, panelH, drag: true);
          _savePanelSize(panelW, panelH);
        },
        onPointerCancel: (e) async {
          if (!_isResizing) return;
          _isResizing = false;
          _pendingW = null;
          _pendingH = null;
          setState(() => _draggingResize = false);
          await _ensureSize(panelW, panelH, drag: true);
          _savePanelSize(panelW, panelH);
        },
        child: Container(
          width: 46,
          height: 46,
          alignment: Alignment.bottomRight,
          padding: const EdgeInsets.only(right: 3, bottom: 3),
          child: Stack(
            clipBehavior: Clip.none,
            alignment: Alignment.bottomRight,
            children: [
              CustomPaint(
                size: const Size(22, 22),
                painter: _GripPainter(
                  color: _draggingResize
                      ? const Color(0xFF64FFDA)
                      : Colors.white.withAlpha(150),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  // 收起态：屏幕上只保留一个圆形悬浮按钮（内含麻将牌图标）
  Widget _floatingButton({double size = collapsed}) {
    final shanten = result?['shanten'];
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: _togglePanel,
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          // 用墨绿替代橙红 —— 不再出现橙色/红色视觉信号。
          // 深色背景下墨绿悬浮按钮更显沉稳，避免与"错误/警告"语义混淆。
          color: const Color(0xFF1B5E20),
          shape: BoxShape.circle,
          boxShadow: [
            BoxShadow(
              color: Colors.black.withAlpha(89),
              blurRadius: 6,
              spreadRadius: 1,
            ),
          ],
        ),
        child: Stack(
          alignment: Alignment.center,
          children: [
            if (!ready)
              SizedBox(
                width: size * 0.78,
                height: size * 0.78,
                child: const CircularProgressIndicator(
                  strokeWidth: 2,
                  color: Colors.white70,
                ),
              ),
            Padding(
              padding: EdgeInsets.all(size * 0.19),
              child: const MahjongTileIcon(),
            ),
            if (shanten != null)
              Positioned(
                right: 0,
                bottom: 0,
                child: _ShantenBadge(shanten: shanten as int, size: size),
              ),
          ],
        ),
      ),
    );
  }

  // 收起态：胶囊微缩模式（横向微缩条，不遮挡牌局，实时展示听牌/最优打法）
  Widget _miniCapsule() {
    final bool signalLost = _signalLost || _projectionStopped;
    final shanten = result?['shanten'];
    final int count = ((result?['count'] as num?)?.toInt() ?? 0);
    final String status = (result?['status'] as String?) ?? '';
    final bool isDingquePhase = (result?['dingque_phase'] == true || status == 'dingque');
    final bool isSwapPhase = (result?['swap_phase'] == true || status == 'swap');
    final bool isPickPhase = (result?['pick_phase'] == true || status == 'pick');
    final bool inMatch = status != 'waiting' && status != 'no_tiles' && count >= 4;
    final adviceList = ((inMatch || isDingquePhase || isPickPhase) && _shownAdvice.isNotEmpty)
        ? _shownAdvice
        : ((inMatch || isDingquePhase || isPickPhase) ? (result?['advice'] as List<dynamic>? ?? const []) : const []);
    final topAdvice = adviceList.isNotEmpty ? adviceList[0] as Map<dynamic, dynamic>? : null;
    final String bestTile = inMatch ? (_shownBest.isNotEmpty ? _shownBest : (result?['best'] ?? '')) : '';
    final String tileStr = (topAdvice != null && topAdvice['tile'] != null) ? topAdvice['tile'] as String : bestTile;
    final int ukeire = (topAdvice != null && topAdvice['ukeire'] is int) ? topAdvice['ukeire'] as int : 0;
    final String? reason = (topAdvice != null && topAdvice['reason'] is String) ? topAdvice['reason'] as String : null;

    // 换牌/选牌阶段的顶部建议
    final swapData = isSwapPhase ? (result?['swap_advice'] as Map<String, dynamic>?) : null;
    final pickAdvice = isPickPhase
        ? ((result?['advice'] as List<dynamic>?)?.isNotEmpty == true
            ? (result!['advice'] as List<dynamic>)[0] as Map<dynamic, dynamic>?
            : null)
        : null;

    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: _togglePanel,
      child: Container(
        height: _kCapsuleH,
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: const Color(0xEE1E232A), // 深色微透磨砂底，不突兀
          borderRadius: BorderRadius.circular(19),
          border: Border.all(color: Colors.white.withAlpha(45), width: 1.0),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withAlpha(160),
              blurRadius: 8,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            if (shanten != null && shanten is int)
              _ShantenBadge(shanten: shanten, size: 28)
            else
              const MahjongTileIcon(size: 17),
            const SizedBox(width: 5),
            const Text(
              '就绪',
              style: TextStyle(
                color: Colors.white,
                fontSize: 11,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.2,
                decoration: TextDecoration.none,
              ),
            ),
            const SizedBox(width: 5),
            if (isSwapPhase && swapData != null && swapData['viable'] == true) ...[
              // 1. 换牌阶段：显示换出的3张牌
              const Text('换出', style: TextStyle(color: Colors.white70, fontSize: 10.5, decoration: TextDecoration.none)),
              const SizedBox(width: 2),
              ...((swapData['tiles'] as List<dynamic>? ?? []).take(3).map((t) =>
                Row(children: [TileChip(tile: t as String, size: 19), const SizedBox(width: 1)])
              )),
            ] else if (isDingquePhase) ...[
              // 2. 定缺阶段：优先展示定缺建议
              const Text('定缺', style: TextStyle(color: Color(0xFFFFD54F), fontSize: 10.5, fontWeight: FontWeight.bold, decoration: TextDecoration.none)),
              const SizedBox(width: 3),
              if (tileStr.isNotEmpty) ...[
                TileChip(tile: tileStr, size: 19),
                const SizedBox(width: 3),
              ],
              Flexible(
                child: Text(
                  result?['message'] ?? '推演中…',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Color(0xFFFFF9C4), fontSize: 10, fontWeight: FontWeight.bold, decoration: TextDecoration.none),
                ),
              ),
            ] else if (isPickPhase) ...[
              // 3. 选牌阶段：优先展示推荐选择的牌
              const Text('选', style: TextStyle(color: Color(0xFFFFCC80), fontSize: 10.5, fontWeight: FontWeight.bold, decoration: TextDecoration.none)),
              const SizedBox(width: 2),
              if (pickAdvice != null && (pickAdvice['tile'] ?? '').isNotEmpty) ...[
                TileChip(tile: pickAdvice['tile'] as String, size: 19),
                const SizedBox(width: 3),
                const Flexible(child: Text('最优', overflow: TextOverflow.ellipsis, style: TextStyle(color: Color(0xFFFFD54F), fontSize: 10, fontWeight: FontWeight.bold, decoration: TextDecoration.none))),
              ] else ...[
                Flexible(child: Text(result?['message'] ?? '选牌中…', overflow: TextOverflow.ellipsis, style: const TextStyle(color: Color(0xFFFFCC80), fontSize: 10, fontWeight: FontWeight.bold, decoration: TextDecoration.none))),
              ],
            ] else if (tileStr.isNotEmpty) ...[
              // 4. 正常摸打阶段：显示建议打出牌及进张
              const Text(
                '打',
                style: TextStyle(
                  color: Colors.white70,
                  fontSize: 10.5,
                  decoration: TextDecoration.none,
                ),
              ),
              const SizedBox(width: 2),
              TileChip(tile: tileStr, size: 19),
              const SizedBox(width: 3),
              Flexible(
                child: Text(
                  ukeire > 0 ? '进$ukeire张' : (reason ?? '最优'),
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: Color(0xFF69F0AE),
                    fontSize: 10.5,
                    fontWeight: FontWeight.bold,
                    decoration: TextDecoration.none,
                  ),
                ),
              ),
            ] else if (isSwapPhase) ...[
              Flexible(
                child: Text(
                  result?['message'] ?? '换牌建议中…',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Color(0xFF80CBC4), fontSize: 10, fontWeight: FontWeight.bold, decoration: TextDecoration.none),
                ),
              ),
            ] else ...[
              Flexible(
                child: Text(
                  _projectionStopped
                      ? '采集已停止，请重新开始识别'
                      : (signalLost
                          ? '信号中断，等待画面…'
                          : (result?['status'] == 'waiting' ? '等待对局…' : '实时分析…')),
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: signalLost ? Colors.white24 : Colors.white38,
                    fontSize: 9.5,
                    decoration: TextDecoration.none,
                  ),
                ),
              ),
            ],
            const SizedBox(width: 3),
            const Icon(Icons.arrow_drop_down, color: Colors.white38, size: 16),
          ],
        ),
      ),
    );
  }

  // 全场记牌器面板（万/筒/条 各 9 种牌及字牌/红中当前牌池/手牌扣除后的剩余存活数 0~4）
  Widget _remainingMatrixSection(Map<String, dynamic>? matrix) {
    if (matrix == null) return const SizedBox.shrink();
    final List<dynamic>? m = matrix['m'] as List<dynamic>?;
    final List<dynamic>? p = matrix['p'] as List<dynamic>?;
    final List<dynamic>? s = matrix['s'] as List<dynamic>?;
    final List<dynamic>? z = matrix['z'] as List<dynamic>?;
    if (m == null || p == null || s == null) return const SizedBox.shrink();

    Widget buildRow(String suitName, Color labelColor, List<dynamic> counts) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 1.0),
        child: Row(
          children: [
            SizedBox(
              width: 14,
              child: Text(
                suitName,
                style: TextStyle(
                  color: labelColor,
                  fontSize: 9.5,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
            const SizedBox(width: 4),
            Expanded(
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: List.generate(9, (idx) {
                  final int cnt = (counts.length > idx && counts[idx] is int) ? counts[idx] as int : 0;
                  final Color numColor;
                  final Color cellBg;
                  final Color borderColor;
                  if (cnt == 0) {
                    numColor = Colors.white24;
                    cellBg = const Color(0xFF181B20);
                    borderColor = Colors.white10;
                  } else if (cnt == 1) {
                    numColor = const Color(0xFFFFB74D); // 明亮金橙（仅剩1张）
                    cellBg = const Color(0xFF2D2013);   // 纯正暖深琥珀底
                    borderColor = const Color(0xFFFF9800);
                  } else if (cnt == 2) {
                    numColor = const Color(0xFF81C784); // 翡翠绿（2张）
                    cellBg = const Color(0xFF142416);   // 纯正墨绿底
                    borderColor = const Color(0xFF2E7D32);
                  } else {
                    numColor = const Color(0xFF00E676); // 活跃热张（3~4张存活，荧光高亮）
                    cellBg = const Color(0xFF0D331A);
                    borderColor = const Color(0xFF00E676);
                  }

                  return Container(
                    width: 23,
                    height: 22,
                    decoration: BoxDecoration(
                      color: cellBg,
                      borderRadius: BorderRadius.circular(3),
                      border: Border.all(
                        color: borderColor,
                        width: 0.7,
                      ),
                    ),
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Text(
                          '${idx + 1}',
                          style: TextStyle(
                            color: labelColor.withAlpha(cnt == 0 ? 70 : 220),
                            fontSize: 7.5,
                            fontWeight: FontWeight.w600,
                            height: 1.0,
                          ),
                        ),
                        const SizedBox(height: 1),
                        Text(
                          '$cnt',
                          style: TextStyle(
                            color: numColor,
                            fontSize: 9.5,
                            fontWeight: FontWeight.bold,
                            height: 1.0,
                          ),
                        ),
                      ],
                    ),
                  );
                }),
              ),
            ),
          ],
        ),
      );
    }

    Widget buildZRow(String suitName, Color labelColor, List<dynamic> counts) {
      if (counts.isEmpty) return const SizedBox.shrink();
      // 若只有 1 张字牌（血流红中），专门呈现醒目的【中】
      final bool isOnlyHongZhong = counts.length == 1;
      final List<String> zNames = isOnlyHongZhong ? const ['中'] : const ['东', '南', '西', '北', '白', '发', '中'];
      final int displayCount = isOnlyHongZhong ? 1 : (counts.length >= 7 ? 7 : counts.length);

      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 1.0),
        child: Row(
          children: [
            SizedBox(
              width: 14,
              child: Text(
                isOnlyHongZhong ? '中' : suitName,
                style: TextStyle(
                  color: isOnlyHongZhong ? const Color(0xFFFF5252) : labelColor,
                  fontSize: 9.5,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
            const SizedBox(width: 4),
            Expanded(
              child: Row(
                mainAxisAlignment: MainAxisAlignment.start,
                children: List.generate(displayCount, (idx) {
                  final int cnt = (counts.length > idx && counts[idx] is int) ? counts[idx] as int : 0;
                  final Color numColor;
                  final Color cellBg;
                  final Color borderColor;
                  if (cnt == 0) {
                    numColor = Colors.white24;
                    cellBg = const Color(0xFF181B20);
                    borderColor = Colors.white10;
                  } else if (cnt == 1) {
                    numColor = const Color(0xFFFFB74D);
                    cellBg = const Color(0xFF2D2013);
                    borderColor = const Color(0xFFFF9800);
                  } else if (cnt == 2) {
                    numColor = const Color(0xFF81C784);
                    cellBg = const Color(0xFF142416);
                    borderColor = const Color(0xFF2E7D32);
                  } else {
                    numColor = const Color(0xFF00E676);
                    cellBg = const Color(0xFF0D331A);
                    borderColor = const Color(0xFF00E676);
                  }

                  return Padding(
                    padding: const EdgeInsets.only(right: 3.5),
                    child: Container(
                      width: isOnlyHongZhong ? 34 : 23,
                      height: 22,
                      decoration: BoxDecoration(
                        color: cellBg,
                        borderRadius: BorderRadius.circular(3),
                        border: Border.all(
                          color: borderColor,
                          width: 0.7,
                        ),
                      ),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Text(
                            zNames[idx],
                            style: TextStyle(
                              color: (isOnlyHongZhong ? const Color(0xFFFF5252) : labelColor).withAlpha(cnt == 0 ? 70 : 220),
                              fontSize: 7.5,
                              fontWeight: FontWeight.bold,
                              height: 1.0,
                            ),
                          ),
                          const SizedBox(height: 1),
                          Text(
                            '$cnt',
                            style: TextStyle(
                              color: numColor,
                              fontSize: 9.5,
                              fontWeight: FontWeight.bold,
                              height: 1.0,
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                }),
              ),
            ),
          ],
        ),
      );
    }

    return Container(
      margin: const EdgeInsets.only(bottom: 4),
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.white.withAlpha(8),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Text(
                '全场记牌器 (剩余活牌)',
                style: TextStyle(
                  color: Colors.white70,
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 0.3,
                ),
              ),
              const Spacer(),
              GestureDetector(
                onTap: _requestResetMatch,
                behavior: HitTestBehavior.opaque,
                child: Container(
                  margin: const EdgeInsets.only(right: 6),
                  padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1.5),
                  decoration: BoxDecoration(
                    color: const Color(0xFFE65100).withAlpha(160),
                    borderRadius: BorderRadius.circular(3),
                    border: Border.all(color: const Color(0xFFFFB74D), width: 0.6),
                  ),
                  child: const Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.refresh_rounded, color: Colors.white, size: 9.5),
                      SizedBox(width: 2),
                      Text(
                        '新局',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 8.5,
                          fontWeight: FontWeight.bold,
                          decoration: TextDecoration.none,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              GestureDetector(
                onTap: () => setState(() => _matrixExpanded = !_matrixExpanded),
                behavior: HitTestBehavior.opaque,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                  child: Text(
                    _matrixExpanded ? '收起 ▾' : '展开 ▸',
                    style: const TextStyle(
                      color: Color(0xFF80CBC4),
                      fontSize: 9.5,
                    ),
                  ),
                ),
              ),
            ],
          ),
          if (_matrixExpanded) ...[
            const SizedBox(height: 3),
            Padding(
              padding: const EdgeInsets.only(bottom: 4, top: 1, left: 10),
              child: Row(
                children: [
                  _legendDot(const Color(0xFF00E676), '多(3-4)'),
                  const SizedBox(width: 7),
                  _legendDot(const Color(0xFF81C784), '充裕(2)'),
                  const SizedBox(width: 7),
                  _legendDot(const Color(0xFFFFB74D), '仅1张'),
                  const SizedBox(width: 7),
                  _legendDot(Colors.white38, '绝张(0)'),
                ],
              ),
            ),
            buildRow('万', const Color(0xFF1E6B7A), m),
            buildRow('筒', const Color(0xFF1E6B7A), p),
            buildRow('条', const Color(0xFF66BB6A), s),
            if (z != null && z.isNotEmpty)
              buildZRow('字', const Color(0xFFB0BEC5), z),
          ],
        ],
      ),
    );
  }

  Widget _legendDot(Color color, String label) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 5.5,
          height: 5.5,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 3),
        Text(
          label,
          style: const TextStyle(
            color: Colors.white60,
            fontSize: 8.5,
            decoration: TextDecoration.none,
          ),
        ),
      ],
    );
  }

  // ---------- 内容区小部件 ----------

  Widget _handSection(String hand, int count) {
    if (hand.isEmpty || count == 0) {
      return const SizedBox.shrink();
    }
    // 合法麻将立牌张数：1/2 (碰4次), 4/5 (碰3次), 7/8 (碰2次), 10/11 (碰1次), 13/14 (门清)
    // 仅在非标准张数（如 3, 6, 9, 12 张）时才提示可能被遮挡
    final bool isLegalStanding = const {1, 2, 4, 5, 7, 8, 10, 11, 13, 14}.contains(count);
    final bool partial = count > 0 && !isLegalStanding;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (partial)
            Container(
              margin: const EdgeInsets.only(bottom: 4),
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
              decoration: BoxDecoration(
                color: const Color(0xFFE65100).withAlpha(40),
                borderRadius: BorderRadius.circular(4),
                border: Border.all(color: const Color(0xFFFFB74D), width: 0.6),
              ),
              child: Row(
                children: [
                  const Icon(Icons.warning_amber_rounded, color: Color(0xFFFFB74D), size: 12),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      '手牌仅 $count 张：若被悬浮窗压住，请上移避免遮挡！',
                      style: const TextStyle(
                        color: Color(0xFFFFD54F),
                        fontSize: 8.5,
                        fontWeight: FontWeight.w600,
                        decoration: TextDecoration.none,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          HandChipRow(
            hand: hand,
            chipSize: 20,
            drawingTile: result?['is_drawing'] == true
                ? (result?['drawing_tile'] as String?)
                : null,
            defenseMap: result?['defense_map'] as Map<String, dynamic>?,
          ),

        ],
      ),
    );
  }

  Widget _buildSwapAdviceWidget(Map<String, dynamic> swap) {
    final tiles = (swap['tiles'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? [];
    final reason = swap['reason'] as String? ?? '开局最优换三张';
    final suit = swap['suit'] as String? ?? '';
    return Container(
      margin: const EdgeInsets.only(bottom: 5),
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 6),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xFF281547), Color(0xFF4A154B)],
        ),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: const Color(0xFFCE93D8), width: 0.8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.swap_horiz_rounded, color: Color(0xFFEA80FC), size: 14),
              const SizedBox(width: 4),
              const Text(
                '【换三张博弈】',
                style: TextStyle(
                  color: Color(0xFFEA80FC),
                  fontSize: 10,
                  fontWeight: FontWeight.bold,
                  decoration: TextDecoration.none,
                ),
              ),
              const Spacer(),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                decoration: BoxDecoration(
                  color: Colors.white.withAlpha(20),
                  borderRadius: BorderRadius.circular(3),
                ),
                child: Text(
                  '换【$suit】',
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 8.5,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Row(
            children: [
              const Text(
                '首选换出: ',
                style: TextStyle(color: Colors.white70, fontSize: 9.5),
              ),
              for (final t in tiles) ...[
                TileChip(tile: t, size: 19),
                const SizedBox(width: 3),
              ],
            ],
          ),
          const SizedBox(height: 2),
          Text(
            reason,
            style: const TextStyle(
              color: Color(0xFFE1BEE7),
              fontSize: 8.5,
              height: 1.1,
              decoration: TextDecoration.none,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTenpaiAlertWidget(Map<String, dynamic> alert) {
    final isHuazhu = alert['type'] == 'huazhu';
    final title = alert['title'] as String? ?? '预警';
    final msg = alert['message'] as String? ?? '';
    final mustDiscards = (alert['must_discard'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? [];

    return Container(
      margin: const EdgeInsets.only(bottom: 5),
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 5),
      decoration: BoxDecoration(
        color: isHuazhu ? const Color(0xFF880E4F).withAlpha(190) : const Color(0xFFBF360C).withAlpha(190),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(
          color: isHuazhu ? const Color(0xFFFF4081) : const Color(0xFFFF6D00),
          width: 1.0,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                isHuazhu ? Icons.dangerous_rounded : Icons.warning_amber_rounded,
                color: isHuazhu ? const Color(0xFFFF80AB) : const Color(0xFFFFD180),
                size: 13,
              ),
              const SizedBox(width: 4),
              Expanded(
                child: Text(
                  title,
                  style: TextStyle(
                    color: isHuazhu ? const Color(0xFFFFEBEE) : const Color(0xFFFFF8E1),
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    decoration: TextDecoration.none,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 2),
          Text(
            msg,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 8.5,
              height: 1.15,
              decoration: TextDecoration.none,
            ),
          ),
          if (mustDiscards.isNotEmpty) ...[
            const SizedBox(height: 3),
            Row(
              children: [
                Text(
                  isHuazhu ? '绝不能留: ' : '下叫必打: ',
                  style: const TextStyle(color: Colors.white70, fontSize: 8.5),
                ),
                for (final t in mustDiscards) ...[
                  TileChip(tile: t, size: 17),
                  const SizedBox(width: 3),
                ],
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildTingRadarWidget(List<dynamic> tingDetails, int? shanten) {
    if (tingDetails.isEmpty) return const SizedBox.shrink();

    int totalRemaining = 0;
    for (final t in tingDetails) {
      if (t is Map) {
        totalRemaining += (t['remaining'] as num? ?? 0).toInt();
      }
    }
    final bool hasDead = tingDetails.any((t) => (t is Map && t['is_dead'] == true));

    return Container(
      margin: const EdgeInsets.only(bottom: 5),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: hasDead
              ? [const Color(0xFF2A1015), const Color(0xFF3B151E)]
              : [const Color(0xFF082216), const Color(0xFF0C3824)],
        ),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(
          color: hasDead ? const Color(0xFFFF5252) : const Color(0xFF00E676),
          width: 0.9,
        ),
        boxShadow: [
          BoxShadow(
            color: (hasDead ? Colors.redAccent : Colors.greenAccent).withAlpha(40),
            blurRadius: 4,
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Icon(
                Icons.radar_rounded,
                color: hasDead ? const Color(0xFFFF5252) : const Color(0xFF00E676),
                size: 14,
              ),
              const SizedBox(width: 4),
              Text(
                '【🎯 听牌·胡牌绝张雷达】',
                style: TextStyle(
                  color: hasDead ? const Color(0xFFFF8A80) : const Color(0xFF69F0AE),
                  fontSize: 10.5,
                  fontWeight: FontWeight.bold,
                  decoration: TextDecoration.none,
                ),
              ),
              const Spacer(),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                decoration: BoxDecoration(
                  color: hasDead ? const Color(0xFFD32F2F) : const Color(0xFF2E7D32),
                  borderRadius: BorderRadius.circular(3),
                ),
                child: Text(
                  hasDead ? '含绝张警报' : '余 $totalRemaining 张机会',
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 8.5,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 5),
          Wrap(
            spacing: 6,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              for (final item in tingDetails)
                if (item is Map)
                  Builder(builder: (context) {
                    final tMpsz = (item['tile'] ?? '') as String;
                    final rem = (item['remaining'] ?? 0) as int;
                    final isDead = item['is_dead'] == true || rem == 0;
                    final fan = item['fan'] as int?;

                    return Container(
                      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                      decoration: BoxDecoration(
                        color: isDead
                            ? const Color(0xFFB71C1C).withAlpha(120)
                            : const Color(0xFF1B5E20).withAlpha(120),
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(
                          color: isDead ? const Color(0xFFFF5252) : const Color(0xFF81C784),
                          width: 0.6,
                        ),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          TileChip(tile: tMpsz, size: 18, dead: isDead),
                          const SizedBox(width: 3),
                          if (isDead)
                            const Text(
                              '⚠️ 绝张0张·建议换叫',
                              style: TextStyle(
                                color: Color(0xFFFF8A80),
                                fontSize: 8.5,
                                fontWeight: FontWeight.bold,
                                decoration: TextDecoration.none,
                              ),
                            )
                          else
                            Text(
                              '余 $rem 张${fan != null && fan > 1 ? ' · $fan番' : ''}',
                              style: const TextStyle(
                                color: Color(0xFFA7FFEB),
                                fontSize: 9,
                                fontWeight: FontWeight.bold,
                                decoration: TextDecoration.none,
                              ),
                            ),
                        ],
                      ),
                    );
                  }),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildDualStrategyWidget(Map<String, dynamic> fast, Map<String, dynamic> big) {
    return Container(
      margin: const EdgeInsets.only(bottom: 5),
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 6),
      decoration: BoxDecoration(
        color: const Color(0xFF151922),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: const Color(0xFF424242), width: 0.7),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Row(
            children: [
              Icon(Icons.balance_rounded, color: Color(0xFFFFD54F), size: 13),
              SizedBox(width: 4),
              Text(
                '【⚖️ 双策略路线博弈对比】',
                style: TextStyle(
                  color: Color(0xFFFFD54F),
                  fontSize: 10,
                  fontWeight: FontWeight.bold,
                  decoration: TextDecoration.none,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Row(
            children: [
              // 稳胡极速流
              Expanded(
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
                  decoration: BoxDecoration(
                    color: const Color(0xFF00382F).withAlpha(150),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(color: const Color(0xFF00BFA5), width: 0.7),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Row(
                        children: [
                          Icon(Icons.shield_outlined, color: Color(0xFF64FFDA), size: 10),
                          SizedBox(width: 2),
                          Text(
                            '🛡️ 稳胡极速流',
                            style: TextStyle(
                              color: Color(0xFF64FFDA),
                              fontSize: 9,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 3),
                      Row(
                        children: [
                          const Text('打 ', style: TextStyle(color: Colors.white70, fontSize: 9.5)),
                          TileChip(tile: (fast['tile'] ?? '') as String, size: 17),
                          const SizedBox(width: 3),
                          Text(
                            '进张 ${fast['ukeire']} 张',
                            style: const TextStyle(
                              color: Colors.lightGreenAccent,
                              fontSize: 9.5,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(width: 6),
              // 大番收益流
              Expanded(
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
                  decoration: BoxDecoration(
                    color: const Color(0xFF3E2723).withAlpha(150),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(color: const Color(0xFFFFB74D), width: 0.7),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Row(
                        children: [
                          Icon(Icons.workspace_premium_rounded, color: Color(0xFFFFD54F), size: 10),
                          SizedBox(width: 2),
                          Text(
                            '👑 大番收益流',
                            style: TextStyle(
                              color: Color(0xFFFFD54F),
                              fontSize: 9,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 3),
                      Row(
                        children: [
                          const Text('打 ', style: TextStyle(color: Colors.white70, fontSize: 9.5)),
                          TileChip(tile: (big['tile'] ?? '') as String, size: 17),
                          const SizedBox(width: 3),
                          Expanded(
                            child: Text(
                              (big['desc'] ?? '') as String,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                color: Color(0xFFFFCC80),
                                fontSize: 8.5,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  /// 次选进张标签：引擎算出真实进张时恒 >0；
  /// `ukeire==0 且 shanten>=2` 表示尚未算得真实进张，显示“—”避免误导“绝张 0 张”。
  String _ukeireLabel(dynamic item) {
    final int u = (item['ukeire'] as num? ?? 0).toInt();
    final int sh = (item['shanten'] as num? ?? 0).toInt();
    if (u == 0 && sh >= 2) return '—';
    return '$u张';
  }

  Widget _adviceSection(List<dynamic> advice, String best, int count) {
    final status = result?['status'] as String? ?? '';
    final bool isDingquePhase = (result?['dingque_phase'] == true || status == 'dingque');
    final bool isSwapPhase = (result?['swap_phase'] == true || status == 'swap');
    final bool isPickPhase = (result?['pick_phase'] == true || status == 'pick');
    final bool inMatch = status != 'waiting' && status != 'no_tiles' && count >= 4;
    final List<dynamic> activeAdvice = (inMatch || isDingquePhase || isSwapPhase || isPickPhase) ? advice : const [];
    final swapData = result?['swap_advice'] as Map<String, dynamic>?;
    final alertData = result?['tenpai_alert'] as Map<String, dynamic>?;
    final fastAdvice = result?['fast_advice'] as Map<String, dynamic>?;
    final bigAdvice = result?['big_advice'] as Map<String, dynamic>?;
    final tingDetails = (result?['ting_details'] as List<dynamic>?) ?? [];
    final shanten = result?['shanten'] as int?;
    final pickCandidates = (result?['pick_candidates'] as List<dynamic>?) ?? [];

    // swapWidget 仅在真正的换牌阶段有效，严禁泄露至定缺、选牌或摸打对局
    final Widget? swapWidget = (isSwapPhase && swapData != null && swapData['viable'] == true)
        ? _buildSwapAdviceWidget(swapData)
        : null;
    final Widget? alertWidget = (alertData != null && alertData['alert'] == true)
        ? _buildTenpaiAlertWidget(alertData)
        : null;
    final Widget? dualStrategyWidget = (fastAdvice != null &&
            bigAdvice != null &&
            fastAdvice['tile'] != bigAdvice['tile'])
        ? _buildDualStrategyWidget(fastAdvice, bigAdvice)
        : null;
    final Widget? tingRadarWidget = (shanten == 0 || tingDetails.isNotEmpty)
        ? _buildTingRadarWidget(tingDetails, shanten)
        : null;

    // ===== 1. 换牌阶段专用 UI =====
    if (isSwapPhase) {
      final msg = (result?['message'] as String?) ?? '换牌建议推演中…';
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (swapWidget != null)
            swapWidget
          else
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 7),
              decoration: BoxDecoration(
                color: const Color(0x3300BFA5),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(color: const Color(0xFF00BFA5), width: 0.8),
              ),
              child: Row(
                children: [
                  const Icon(Icons.swap_horiz, color: Color(0xFF80CBC4), size: 16),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      '【换牌阶段】$msg',
                      style: const TextStyle(
                        color: Color(0xFFB2EBF2),
                        fontSize: 10.5,
                        fontWeight: FontWeight.bold,
                        decoration: TextDecoration.none,
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      );
    }

    // ===== 2. 任选一张牌阶段专用 UI =====
    if (isPickPhase) {
      final msg = (result?['message'] as String?) ?? '等待选牌弹窗识别…';
      final topPick = activeAdvice.isNotEmpty ? activeAdvice[0] as Map<dynamic, dynamic>? : null;
      final pickTile = (topPick != null && topPick['tile'] != null) ? topPick['tile'] as String : '';
      final pickReason = (topPick != null && topPick['reason'] != null) ? topPick['reason'] as String : '';
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 7),
        decoration: BoxDecoration(
          color: const Color(0x33FF6F00),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: const Color(0xFFFF8F00), width: 0.8),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.casino, color: Color(0xFFFFCC80), size: 16),
                const SizedBox(width: 6),
                const Expanded(
                  child: Text(
                    '【任选一张牌】推荐选择',
                    style: TextStyle(
                      color: Color(0xFFFFE0B2),
                      fontSize: 10.5,
                      fontWeight: FontWeight.bold,
                      decoration: TextDecoration.none,
                    ),
                  ),
                ),
              ],
            ),
            if (pickTile.isNotEmpty) ...[
              const SizedBox(height: 5),
              Row(
                children: [
                  const Text('首选用牌: ', style: TextStyle(color: Colors.white70, fontSize: 10, decoration: TextDecoration.none)),
                  TileChip(tile: pickTile, size: 22),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      pickReason,
                      style: const TextStyle(color: Color(0xFFFFD54F), fontSize: 9.5, fontWeight: FontWeight.bold, decoration: TextDecoration.none),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ] else if (pickCandidates.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(
                '候选: ${pickCandidates.join(' ')}',
                style: const TextStyle(color: Colors.white60, fontSize: 9.5, decoration: TextDecoration.none),
              ),
            ] else ...[
              const SizedBox(height: 4),
              Text(msg, style: const TextStyle(color: Colors.white54, fontSize: 9.5, decoration: TextDecoration.none)),
            ],
          ],
        ),
      );
    }

    // ===== 3. 定缺阶段专用 UI =====
    if (isDingquePhase) {
      final msg = (result?['message'] as String?) ?? '正在推演最佳断门…';
      final topDq = activeAdvice.isNotEmpty ? activeAdvice[0] as Map<dynamic, dynamic>? : null;
      final dqTile = (topDq != null && topDq['tile'] != null) ? topDq['tile'] as String : '';
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 7),
        decoration: BoxDecoration(
          color: const Color(0x33FFB300),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: const Color(0xFFFFB300), width: 0.8),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.lightbulb, color: Color(0xFFFFD54F), size: 16),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    '【定缺阶段】$msg',
                    style: const TextStyle(
                      color: Color(0xFFFFF9C4),
                      fontSize: 10.5,
                      fontWeight: FontWeight.bold,
                      decoration: TextDecoration.none,
                    ),
                  ),
                ),
              ],
            ),
            if (dqTile.isNotEmpty) ...[
              const SizedBox(height: 4),
              Row(
                children: [
                  const Text('建议打缺: ', style: TextStyle(color: Colors.white70, fontSize: 10, decoration: TextDecoration.none)),
                  TileChip(tile: dqTile, size: 20),
                ],
              ),
            ],
          ],
        ),
      );
    }

    // ===== 4. 正常摸打对局：activeAdvice 为空时的友好占位 =====
    if (activeAdvice.isEmpty) {
      if (tingRadarWidget != null) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (alertWidget != null) alertWidget,
            tingRadarWidget,
          ],
        );
      }
      final String hint;
      if (status == 'waiting') {
        hint = '等待牌局开始（进入游戏后自动识别）';
      } else if (status == 'animation' ||
          status == 'py_error' ||
          status == 'decode_error') {
        // 瞬态帧（动画突变 / 解码失败 / 引擎异常）：绝不把识别抖动归咎于用户遮挡，
        // 保持中性「识别中」，消除「不需要时却持续显示错误信息」的观感。
        hint = '画面识别中，请稍候…';
      } else if (count > 0) {
        hint = '手牌识别中，正在推演建议…';
      } else {
        hint = '未检测到手牌，请让底部手牌区完整入镜';
      }
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        decoration: BoxDecoration(
          color: Colors.white.withAlpha(6),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: Colors.white10, width: 0.5),
        ),
        child: Row(
          children: [
            const Icon(Icons.tips_and_updates_outlined, color: Colors.white38, size: 13),
            const SizedBox(width: 5),
            Expanded(
              child: Text(
                hint,
                style: const TextStyle(color: Colors.white60, fontSize: 9.5),
              ),
            ),
          ],
        ),
      );
    }

    final sorted = [...activeAdvice];
    if (sorted.isNotEmpty && best.isNotEmpty) {
      final bestIdx = sorted.indexWhere((a) => a['tile'] == best);
      if (bestIdx > 0) {
        final bItem = sorted.removeAt(bestIdx);
        sorted.insert(0, bItem);
      }
    }

    final top = sorted.first;
    final topTile = (top['tile'] ?? '') as String;
    final topUkeire = (top['ukeire'] ?? 0) as int;
    final topReason = top['reason'] as String?;
    final bool isDingque = (top['is_dingque'] ?? false) as bool;
    final String? defenseLevel = top['defense_level'] as String?;
    final String? defenseReason = top['defense_reason'] as String?;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (alertWidget != null) alertWidget,
        if (tingRadarWidget != null) tingRadarWidget,
        if (dualStrategyWidget != null) dualStrategyWidget,
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 5),
          decoration: BoxDecoration(
            color: const Color(0x33004D40), // 墨绿微底
            borderRadius: BorderRadius.circular(6),
            border: Border.all(color: const Color(0x6680CBC4), width: 0.8),
          ),

          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Text(
                        '建议打',
                        style: TextStyle(
                          color: Colors.white70,
                          fontSize: 11,
                          decoration: TextDecoration.none,
                        ),
                      ),
                      const SizedBox(width: 4),
                      TileChip(tile: topTile, size: 21),
                      if (isDingque) ...[
                        const SizedBox(width: 4),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 1),
                          decoration: BoxDecoration(
                            color: const Color(0xFF00695C),
                            borderRadius: BorderRadius.circular(3),
                          ),
                          child: const Text(
                            '定缺',
                            style: TextStyle(
                              color: Colors.white,
                              fontSize: 8,
                              fontWeight: FontWeight.bold,
                              decoration: TextDecoration.none,
                            ),
                          ),
                        ),
                      ],
                      if (defenseLevel == 'SAFE') ...[
                        const SizedBox(width: 4),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 1),
                          decoration: BoxDecoration(
                            color: const Color(0xFF1B5E20),
                            borderRadius: BorderRadius.circular(3),
                            border: Border.all(color: const Color(0xFF81C784), width: 0.5),
                          ),
                          child: const Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(Icons.shield, color: Color(0xFFC8E6C9), size: 8),
                              SizedBox(width: 1),
                              Text(
                                '安全',
                                style: TextStyle(
                                  color: Color(0xFFE8F5E9),
                                  fontSize: 8,
                                  fontWeight: FontWeight.bold,
                                  decoration: TextDecoration.none,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ] else if (defenseLevel == 'DANGER') ...[
                        const SizedBox(width: 4),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 1),
                          decoration: BoxDecoration(
                            color: const Color(0xFFB71C1C),
                            borderRadius: BorderRadius.circular(3),
                            border: Border.all(color: const Color(0xFFFF8A80), width: 0.5),
                          ),
                          child: const Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(Icons.warning, color: Color(0xFFFFCDD2), size: 8),
                              SizedBox(width: 1),
                              Text(
                                '高危',
                                style: TextStyle(
                                  color: Colors.white,
                                  fontSize: 8,
                                  fontWeight: FontWeight.bold,
                                  decoration: TextDecoration.none,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ],
                  ),
                  if (topUkeire > 0)
                    Flexible(
                      child: Text(
                        '进张 $topUkeire 张',
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: Colors.lightGreenAccent,
                          fontSize: 11,
                          fontWeight: FontWeight.bold,
                          decoration: TextDecoration.none,
                        ),
                      ),
                    ),
                ],
              ),
              if (topReason != null && topReason.isNotEmpty) ...[
                const SizedBox(height: 3),
                Text(
                  topReason,
                  style: const TextStyle(
                    color: Color(0xFF80CBC4),
                    fontSize: 9.5,
                    height: 1.15,
                    decoration: TextDecoration.none,
                  ),
                ),
              ],
              if (defenseReason != null && defenseReason.isNotEmpty && defenseLevel != 'SAFE') ...[
                const SizedBox(height: 2),
                Text(
                  '防守: $defenseReason',
                  style: TextStyle(
                    color: defenseLevel == 'DANGER' ? const Color(0xFFFF8A80) : const Color(0xFFFFCC80),
                    fontSize: 8.5,
                    height: 1.1,
                    decoration: TextDecoration.none,
                  ),
                ),
              ],
              if (sorted.length > 1) ...[
                const SizedBox(height: 4),
                Wrap(
                  spacing: 6,
                  runSpacing: 2,
                  children: [
                    for (int i = 1; i < sorted.length && i < 3; i++)
                      Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(
                            '次选:',
                            style: TextStyle(
                              color: Colors.white.withAlpha(140),
                              fontSize: 9,
                              decoration: TextDecoration.none,
                            ),
                          ),
                          const SizedBox(width: 2),
                          TileChip(tile: (sorted[i]['tile'] ?? '') as String, size: 16),
                          const SizedBox(width: 2),
                          Text(
                            _ukeireLabel(sorted[i]),
                            style: TextStyle(
                              color: Colors.white.withAlpha(160),
                              fontSize: 9,
                              decoration: TextDecoration.none,
                            ),
                          ),
                        ],
                      ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    // 授权不可用：不渲染任何识别建议，只显一个极简提示胶囊。
    if (!_licenseAllows) {
      return SizedBox.expand(child: _licenseDisabled());
    }
    final Widget current;
    if (!panelVisible) {
      current = SizedBox.expand(
        child: _capsuleMode ? _miniCapsule() : _floatingButton(),
      );
    } else {
      final String hand = (result?['hand'] ?? '') as String;
      final int count = (result?['count'] ?? 0) as int;
      final List<dynamic> advice = _shownAdvice;
      final String best = _shownBest;
      final String status = (result?['status'] as String?) ?? '';
      final bool isDingquePhase = (result?['dingque_phase'] == true || status == 'dingque');
      final bool isSwapPhase = (result?['swap_phase'] == true || status == 'swap');
      final bool isPickPhase = (result?['pick_phase'] == true || status == 'pick');
      // inMatch：对局已进行中（有手牌且不是等待状态），不再要求 remaining_matrix（swap/pick阶段无牌河）
      final bool inMatch = status != 'waiting' &&
          status != 'no_tiles' &&
          count >= 4;
      // 假活/断流信标：采集停止或信号中断，此时灰化数据区（屏上数据已非实时）。
      final bool signalLost = _signalLost || _projectionStopped;

      current = SizedBox.expand(
        child: Stack(
        children: [
          Container(
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [Color(0xF51A1D24), Color(0xF5101216)],
              ),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: Colors.white.withAlpha(24), width: 0.8),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withAlpha(120),
                  blurRadius: 10,
                  offset: const Offset(0, 3),
                ),
              ],
            ),
            padding: const EdgeInsets.fromLTRB(7, 6, 7, 6),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // 顶部控制与拖动手柄区：由 Android 原生 onTouch 在 50dp 区域执行 120Hz 极速拖动
                Container(
                  height: 44,
                  padding: const EdgeInsets.only(bottom: 2),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      // 居中拖动手柄 Pill（醒目提示按住此处即可平滑移动悬浮窗）
                      Center(
                        child: Container(
                          width: 52,
                          height: 5,
                          margin: const EdgeInsets.only(bottom: 4),
                          decoration: BoxDecoration(
                            color: Colors.white.withAlpha(90),
                            borderRadius: BorderRadius.circular(3),
                          ),
                        ),
                      ),
                      Expanded(
                        child: Row(
                          children: [
                            Expanded(
                              child: Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  const MahjongTileIcon(size: 15),
                                  const SizedBox(width: 5),
                                  Flexible(
                                    child: Text(
                                      GameMode.label(selectedMode),
                                      overflow: TextOverflow.ellipsis,
                                      style: const TextStyle(
                                        color: Colors.white,
                                        fontWeight: FontWeight.bold,
                                        fontSize: 11,
                                        letterSpacing: 0.2,
                                        decoration: TextDecoration.none,
                                      ),
                                    ),
                                  ),
                                  const SizedBox(width: 4),
                                  Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                                    decoration: BoxDecoration(
                                      gradient: const LinearGradient(
                                        colors: [Color(0xFFFFD700), Color(0xFFFFA000)],
                                      ),
                                      borderRadius: BorderRadius.circular(3),
                                    ),
                                    child: Text(
                                      _appVersion.isEmpty
                                          ? 'PRO'
                                          : 'PRO v$_appVersion',
                                      style: const TextStyle(
                                        color: Color(0xFF1E1E1E),
                                        fontSize: 7.5,
                                        fontWeight: FontWeight.w900,
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            const SizedBox(width: 5),
                            GestureDetector(
                              onTap: _requestResetMatch,
                              behavior: HitTestBehavior.opaque,
                              child: Container(
                                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2.5),
                                margin: const EdgeInsets.only(right: 5),
                                decoration: BoxDecoration(
                                  color: const Color(0xFFE65100).withAlpha(160),
                                  borderRadius: BorderRadius.circular(4),
                                  border: Border.all(color: const Color(0xFFFFB74D), width: 0.7),
                                ),
                                child: const Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    Icon(Icons.refresh_rounded, color: Colors.white, size: 10),
                                    SizedBox(width: 2),
                                    Text(
                                      '新局',
                                      style: TextStyle(
                                        color: Colors.white,
                                        fontSize: 9.5,
                                        fontWeight: FontWeight.bold,
                                        decoration: TextDecoration.none,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            ),
                            GestureDetector(
                              onTap: _togglePanel,
                              behavior: HitTestBehavior.opaque,
                              child: Container(
                                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2.5),
                                decoration: BoxDecoration(
                                  color: Colors.white.withAlpha(25),
                                  borderRadius: BorderRadius.circular(4),
                                  border: Border.all(color: Colors.white12, width: 0.6),
                                ),
                                child: const Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    Icon(Icons.unfold_less_rounded, color: Colors.white70, size: 11),
                                    SizedBox(width: 2),
                                    Text(
                                      '收起',
                                      style: TextStyle(
                                        color: Colors.white,
                                        fontSize: 10,
                                        fontWeight: FontWeight.w500,
                                        decoration: TextDecoration.none,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 5),
                // 核心卡片滚动流：全包裹于 SingleChildScrollView，彻底杜绝 RenderFlex overflow
                // 假活/断流时灰化数据区（降透明度+不可交互误导降至最低），
                // 让用户一眼看出屏上数据已不是实时。
                Expanded(
                  child: Opacity(
                    opacity: signalLost ? 0.45 : 1.0,
                    child: SingleChildScrollView(
                    controller: _panelScrollController,
                    physics: const AlwaysScrollableScrollPhysics(
                      parent: BouncingScrollPhysics(),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        // 1. 核心建议（出牌决策）
                        _adviceSection(advice, best, count),
                        const SizedBox(height: 5),
                        // 2. 当前手牌（仅在确认对局内或定缺阶段才显示，杜绝大厅与非对局干扰）
                        if (hand.isNotEmpty && count > 0 && (inMatch || isDingquePhase || isSwapPhase || isPickPhase)) ...[
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
                            decoration: BoxDecoration(
                              color: Colors.white.withAlpha(6),
                              borderRadius: BorderRadius.circular(6),
                              border: Border.all(color: Colors.white10, width: 0.5),
                            ),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: [
                                Row(
                                  children: [
                                    Text(
                                      '手牌 ($count张)',
                                      style: const TextStyle(
                                        color: Colors.white70,
                                        fontSize: 9.5,
                                        fontWeight: FontWeight.w600,
                                        letterSpacing: 0.3,
                                      ),
                                    ),
                                    if (result?['is_drawing'] == true) ...[
                                      const SizedBox(width: 6),
                                      Container(
                                        padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 0.5),
                                        decoration: BoxDecoration(
                                          color: const Color(0xFFE65100),
                                          borderRadius: BorderRadius.circular(2),
                                        ),
                                        child: const Text(
                                          '摸牌中',
                                          style: TextStyle(
                                            color: Colors.white,
                                            fontSize: 7.5,
                                            fontWeight: FontWeight.bold,
                                          ),
                                        ),
                                      ),
                                    ],
                                  ],
                                ),
                                const SizedBox(height: 2),
                                _handSection(hand, count),
                              ],
                            ),
                          ),
                          const SizedBox(height: 5),
                        ],
                        // 3. 全场记牌器（对局开始后显示，实时统揽全场 108/136 张活牌剩余数）
                        if (inMatch) ...[
                          _remainingMatrixSection(result?['remaining_matrix'] as Map<String, dynamic>?),
                        ],
                        // 底部安全留白：确保可以顺畅滑到最底部且不被右下角缩放手柄遮挡
                        const SizedBox(height: 26),
                      ],
                    ),
                    ),
                  ),
                ),
              ],
            ),
          ),
          _resizeHandle(),
        ],
      ),
    );
    }

    return Material(
      type: MaterialType.transparency,
      child: DefaultTextStyle(
        style: const TextStyle(
          decoration: TextDecoration.none,
          color: Colors.white,
          fontFamily: 'sans-serif',
        ),
        child: current,
      ),
    );
  }
}

class _ShantenBadge extends StatelessWidget {
  final int shanten;
  final double size;
  const _ShantenBadge({required this.shanten, required this.size});

  @override
  Widget build(BuildContext context) {
    final bool tenpai = shanten <= 0;
    final String text = shanten < 0 ? '和' : (shanten == 0 ? '听' : '$shanten');
    return Container(
      width: size * 0.38,
      height: size * 0.38,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        // 仅向听≤0（听牌/和牌）显绿色，其余一律中性灰 —— 不再出现深橙色
        // （深橙色 0xFFD84315 与红色在视觉上极易混淆，误触发"红字 = 异常"）。
        // 这里"非听牌不报错"，仅作为状态指示，避免误读。
        color: tenpai ? Colors.green : const Color(0xFF455A64),
        shape: BoxShape.circle,
        border: Border.all(color: Colors.white, width: 1),
      ),
      child: Text(
        text,
        style: TextStyle(
          color: Colors.white,
          fontSize: size * 0.22,
          fontWeight: FontWeight.bold,
        ),
      ),
    );
  }
}

/// 麻将牌图标：象牙色牌面 + 绿色竹节图案
class MahjongTileIcon extends StatelessWidget {
  final double size;
  const MahjongTileIcon({this.size = 28, super.key});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size * 0.78,
      height: size,
      child: CustomPaint(painter: _TilePainter()),
    );
  }
}

class _TilePainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final double w = size.width;
    final double h = size.height;
    final double r = w * 0.20;

    final RRect face =
        RRect.fromRectAndRadius(Offset.zero & size, Radius.circular(r));

    // 牌背（下缘露一点深色，做出厚度感）
    canvas.drawRRect(
      face.shift(Offset(0, h * 0.05)),
      Paint()..color = const Color(0xFFB9AE93),
    );
    // 牌面
    canvas.drawRRect(face, Paint()..color = const Color(0xFFF7F3E8));
    canvas.drawRRect(
      face,
      Paint()
        ..color = const Color(0xFFC9BFA6)
        ..style = PaintingStyle.stroke
        ..strokeWidth = w * 0.05,
    );

    // 竹节图案（索子）：一根竖条 + 两道节点
    final double barW = w * 0.26;
    final double barH = h * 0.52;
    final double left = (w - barW) / 2;
    final double top = (h - barH) / 2;

    canvas.drawRRect(
      RRect.fromRectAndRadius(
        Rect.fromLTWH(left, top, barW, barH),
        Radius.circular(barW * 0.35),
      ),
      Paint()..color = const Color(0xFF2E7D32),
    );

    final Paint nodePaint = Paint()
      ..color = const Color(0xFFF7F3E8)
      ..strokeWidth = barW * 0.16;
    canvas.drawLine(
        Offset(left, top + barH * 0.38), Offset(left + barW, top + barH * 0.38), nodePaint);
    canvas.drawLine(
        Offset(left, top + barH * 0.66), Offset(left + barW, top + barH * 0.66), nodePaint);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

/// 右下角缩放把手：三道斜线
class _GripPainter extends CustomPainter {
  final Color color;
  const _GripPainter({required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final Paint p = Paint()
      ..color = color
      ..strokeWidth = 1.6
      ..strokeCap = StrokeCap.round;
    const double gap = 5.0;
    for (int i = 0; i < 3; i++) {
      final double o = 6.0 + i * gap;
      canvas.drawLine(
        Offset(size.width - o, size.height - 4),
        Offset(size.width - 4, size.height - o),
        p,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _GripPainter oldDelegate) =>
      oldDelegate.color != color;
}
