import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:auto_vision/mode_store.dart';
import 'package:auto_vision/channel.dart';
import 'package:auto_vision/license/license_service.dart';
import 'package:auto_vision/license/license_status.dart';
import 'package:auto_vision/overlays/tile_labels.dart';
import 'package:auto_vision/latency_probe.dart';
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
    if (suit == 'm') {
      // 万：经典传统朱砂红（真实骨牌质感）
      charColor = const Color(0xFFC62828);
    } else if (suit == 's') {
      // 条：纯正竹叶绿/翡翠绿
      charColor = const Color(0xFF2E7D32);
    } else if (suit == 'p') {
      // 筒：传统深宝蓝/青黛
      charColor = const Color(0xFF1565C0);
    } else {
      // 字牌：中为正红，发为青翠，其余为深墨黑
      if (tile == '7z') {
        charColor = const Color(0xFFC62828);
      } else if (tile == '6z') {
        charColor = const Color(0xFF2E7D32);
      } else {
        charColor = const Color(0xFF263238);
      }
    }

    // 牌面渐变与边框：模拟真实高档象牙白/亚克力麻将牌微曲面与边缘高光
    final Gradient tileGradient = dead
        ? const LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [Color(0xFFD8D4CD), Color(0xFFC4C0B7)],
          )
        : (isDrawing
            ? const LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [Color(0xFFFFFDF5), Color(0xFFFFF3D6)],
              )
            : const LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [Color(0xFFFCFAF5), Color(0xFFEDE6DA)],
              ));

    final Color tileBorder = isDrawing
        ? const Color(0xFFFFB300)
        : (dead ? const Color(0xFF00796B) : const Color(0xFFC8BCA8));
    final double tileBorderW = (dead || isDrawing) ? 1.2 : 0.7;

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
              gradient: tileGradient,
              borderRadius: BorderRadius.circular(3.5),
              border: Border.all(color: tileBorder, width: tileBorderW),
              boxShadow: [
                BoxShadow(
                  color: isDrawing
                      ? const Color(0x66FFC107)
                      : Colors.black.withAlpha(55),
                  blurRadius: isDrawing ? 3.5 : 2,
                  offset: const Offset(0, 1),
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
                  fontWeight: FontWeight.w900,
                  fontSize: size * 0.62,
                  height: 1.0,
                  shadows: [
                    Shadow(
                      color: Colors.white.withAlpha(160),
                      offset: const Offset(0, 0.5),
                      blurRadius: 0.5,
                    ),
                  ],
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

  // 当前玩法：悬浮窗写入共享文件，Python 引擎每帧读取。默认值只从 GameMode.defaultMode
  // 取（与 Python modes.DEFAULT_MODE 同一条）：这里曾写死 'sc'，而 'sc' 经别名解析是
  // sc_xz（108 张无字），与真默认 sc_hz（112 张带红中）不同，导致首帧数据到达前
  // 面板按 wall=108 显示剩余牌数、标题显示另一个玩法。
  String selectedMode = GameMode.defaultMode;

  List<dynamic> _shownAdvice = const [];
  String _shownBest = '';

  // ── 关键事件瞬时胶囊（开局/碰/杠/听牌/阶段切换/本局结束）───────────────────
  // 悬浮窗是叠在真实牌局上的覆盖层：这里**不新增任何常驻控件**。这颗胶囊只挂在
  // Stack 的 Positioned 上（不进 Column、不占布局高度），~2.5s 自动消失，并且用
  // IgnorePointer 不吃手势（否则它会挡住顶部拖动区）。完整明细走主页调试页。
  //
  // 只播“刚刚落定”的事件（draw/discard/discard_other 每一巡都在发生，播了就是刷屏），
  // 其中 start/swap_in/swap_done/dingque_in/dingque/irregular 是阶段切换：玩家问的
  // “现在到哪儿了”恰好都在这几个瞬间，用瞬时一句话回答，而不是养一行常驻文字。
  static const Set<String> _kFlashKinds = {
    'start', 'pong', 'kong', 'added_kong',
    'swap_in', 'swap_done', 'dingque_in', 'dingque',
    'tenpai', 'irregular', 'over',
  };
  // 副露种类 → 中文。引擎只会给 pong/kong/added_kong；出现别的值（包括 'unknown'）
  // 就是接线坏了，宁可退回「副露」也不能替它编一个具体动作 ——
  // 把碰报成杠会被玩家当场戳穿。
  static const Map<String, String> _kMeldKindCn = {
    'pong': '碰',
    'kong': '杠',
    'added_kong': '加杠',
  };
  Map<String, dynamic>? _eventFlash;
  Timer? _eventFlashTimer;
  int _lastFlashAtMs = 0; // 水位：隔帧重发同一份缓存 payload 时不得重复闪

  static const double collapsed = 56;
  // 胶囊微缩模式：收起态下在屏幕边缘显示小巧横条，展示听牌/最优打法
  bool _capsuleMode = true;
  static const double _kCapsuleW = 210;
  static const double _kCapsuleH = 38;
  // 9x3 剩余牌矩阵面板折叠态：默认折叠，弹窗小巧简约不眼花
  bool _matrixExpanded = false;
  // 悬浮窗内玩法切换菜单展开态
  bool _showModeSelector = false;

  void _selectMode(String key) {
    final norm = GameMode.normalizeKey(key);
    setState(() {
      selectedMode = norm;
      _showModeSelector = false;
    });
    // 1. 调用 GameMode.set (经 MethodChannel 通知 Java/Python 即时生效)
    GameMode.set(norm).catchError((_) => false);
    // 2. 双保险：直接通过外部私有文件通道写入 mahjong_mode.json，主应用后台挂起时引擎依然能毫秒级动态感知
    try {
      final f = File('/storage/emulated/0/Android/data/com.example.auto_vision/files/mahjong_mode.json');
      if (f.parent.existsSync()) {
        f.writeAsStringSync(jsonEncode({'mode': norm}));
      }
    } catch (_) {}
    // 3. 广播给 Flutter 主程序更新 UI
    FlutterOverlayWindow.shareData({'type': 'set_mode', 'mode': norm}).catchError((_) {});
    _requestResetMatch();
  }

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
  // 默认放行：悬浮窗只由已放行激活的主界面拉起，经权威校验发现确实到期/停用后再硬锁
  bool _licenseAllows = true;
  // 是否已拿到过一次权威结论。用来区分「校验失败（终态）」与「还没校验完（瞬态）」：
  // 不区分的话，冷启动一次网络抖动就会被当成失权硬锁，而用户完全无法手动解锁。
  bool _licenseVerified = false;
  int _licenseDays = 0;
  // 锁定胶囊文案：只在真到期/被拒时显示对应原因，绝不把一切失权都写成"到期"。
  String _licenseDenyText = '授权校验中…';
  Timer? _licenseTimer;
  Timer? _licenseExpiryTimer;
  // 首次核验失败后的快速重试定时器（不等满 60s 轮询）。
  Timer? _licenseRetryTimer;
  // 锁定态是否已把窗口撑到胶囊尺寸（避免每帧重复 resize）。
  bool _lockSized = false;

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

  // ── 端到端帧龄探针（采集→本侧收到）。只用于诊断，不参与任何牌面展示。
  final LatencyProbe _latency = LatencyProbe();
  Timer? _latencyReport;

  // ── Java 采集层在心跳帧里自报的计数（本帧解析+注入开销、降级帧数、发送失败数）。
  // 只存最近一次。这些数必须跟着上报：本轮为了语义判据改成每帧解析一次 JSON，
  // 它的真实代价如果不落到界面上，「让开销自己读数」就只是一句注释。
  Map<String, int> _javaCounters = const <String, int>{};

  /// 心跳帧里自报的计数字段（与 ImageProcessor.withHeartbeatCounters 一一对应）。
  static const List<String> _kJavaCounterKeys = <String>[
    'frames', 'proc', 'send_fail', 'parse_fail', 'non_finite', 'stamp_avg_ms',
  ];

  void _captureJavaCounters(Map<String, dynamic> json) {
    if (json['java_status'] != true) return;
    // 只有心跳帧带计数（sendStatus(java_error…) 那一类不带）；缺关键字就整组不更新，
    // 绝不用 0 去盖掉上一次的真读数。
    if (json['stamp_avg_ms'] == null) return;
    final Map<String, int> out = <String, int>{};
    for (final String k in _kJavaCounterKeys) {
      final int? v = (json[k] as num?)?.toInt();
      if (v != null) out[k] = v;
    }
    if (out.isNotEmpty) _javaCounters = out;
  }

  // ── 版本徽章：读真实构建版本，取代硬编码 "PRO v1.4.5"（版本漂移误导用户）。
  String _appVersion = '';

  // ── 清空型帧去抖：waiting/no_tiles/错误态等"清空帧"需**持续 ≥450ms**
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

    // 帧龄读数按 2s 上报给主 App 的调试页（与 Java 心跳同频）。刻意不逐帧上报：
    // shareData 是跨引擎发消息，逐帧发会让主页每帧重建 —— 那正是「识别高峰期
    // 点什么都没反应」的病根（见 home_page 里 _RecognitionStatusView 的局部订阅注释）。
    _latencyReport = Timer.periodic(const Duration(seconds: 2), (Timer t) {
      // 局况摘要搭同一班车上报（2s 一次，不逐帧）：调试页要能回答“这局到底在
      // 干嘛、播过哪些事件、脏读被挡了几次”，而那些读数只有悬浮窗引擎里有。
      final Map<String, dynamic> share =
          _latency.toShare(java: _javaCounters);
      share['match_phase'] = _phaseDigestForShare();
      FlutterOverlayWindow.shareData(share).catchError((_) {});
    });

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
    _licenseRetryTimer?.cancel();
    _signalWatchdog?.cancel();
    _latencyReport?.cancel();
    _eventFlashTimer?.cancel();
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
      await LicenseService.instance.init(reload: true);
      // 1. 本地极速验券（毫秒级）：已激活且未过期立即保持放行，杜绝网络心跳期间将已激活用户误锁
      final localSt = LicenseService.instance.ensureLocalFast();
      if (localSt.allowsUsage && mounted) {
        if (!_licenseAllows || !_licenseVerified) {
          setState(() {
            _licenseAllows = true;
            _licenseVerified = true;
            _licenseDays = localSt.remainingDays;
            _lockSized = false;
          });
        }
      }

      // 2. 权威云端核验：获取服务器与本地双重校验状态
      final st = await LicenseService.instance.heartbeat();
      // 核心铁律：只要本地凭证合法且未被拉黑，绝不因为心跳网络抖动/未查找到而误锁
      final bool allows = st.allowsUsage || (localSt.allowsUsage && !st.isRevoked);
      _licenseVerified = true; // 拿到权威结论，从此才允许下终态文案
      final days = st.allowsUsage ? st.remainingDays : localSt.remainingDays;
      final String denyText;
      if (st.isRevoked) {
        denyText = '授权已被停用，请联系客服';
      } else if (st.status == LicenseStatus.licenseExpired && !localSt.allowsUsage) {
        denyText = '卡密授权已到期，请重新激活';
      } else if (st.status == LicenseStatus.notActivated && !localSt.allowsUsage) {
        denyText = '未激活有效卡密，请先激活';
      } else if (st.message?.isNotEmpty ?? false) {
        denyText = st.message!;
      } else {
        denyText = '授权未生效，请重新激活';
      }
      if (allows != _licenseAllows ||
          days != _licenseDays ||
          denyText != _licenseDenyText) {
        final bool wasAllows = _licenseAllows;
        setState(() {
          _licenseAllows = allows;
          _licenseDays = days;
          _licenseDenyText = denyText;
          // 恢复放行后把尺寸标记交回正常路径（initState / _togglePanel）管理。
          if (allows) _lockSized = false;
        });
        // 从锁定态恢复时，窗口还停在锁定胶囊的尺寸上；不主动收回就会出现
        // “透明区域比球大”的空档（点了没反应但挡住了牌桌），所以按当前收起模式重设。
        if (allows && !wasAllows) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (!mounted) return;
            _ensureSize(
              _capsuleMode ? _kCapsuleW : collapsed,
              _capsuleMode ? _kCapsuleH : collapsed,
            );
          });
        }
      }
      _scheduleLicenseExpiryCheck(st, allows);
    } catch (_) {
      // 网络或子系统抖动时：如果本地已证明可用，坚决保持放行，绝不误锁！
      final localSt = LicenseService.instance.ensureLocalFast();
      if (localSt.allowsUsage) {
        if (mounted && (!_licenseAllows || !_licenseVerified)) {
          setState(() {
            _licenseAllows = true;
            _licenseVerified = true;
            _licenseDays = localSt.remainingDays;
            _lockSized = false;
          });
        }
        return;
      }
      // 核验异常绝不让悬浮窗引擎崩溃。但“保持当前状态”不等于“什么都不做”：
      // 从未拿到权威结论时一路保持 false，用户就会对着一个点不动的锁等满 60s，
      // 所以文案给「校验中」并 4s 后重试一次。已放行过的不因单次抖动回锁。
      if (!_licenseVerified) {
        if (mounted && _licenseDenyText != '授权校验中，点此重试') {
          setState(() => _licenseDenyText = '授权校验中，点此重试');
        }
        _licenseRetryTimer?.cancel();
        _licenseRetryTimer =
            Timer(const Duration(seconds: 4), _refreshLicense);
      }
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

  // ===== 渲染节流与响应加速：前沿即时上屏 + 尾部合并防风暴（0ms 响应，告别卡顿与延迟）=====
  // 1. 首帧或关键变动到达：0ms 前沿立即上屏应用；
  // 2. 密集高频帧（15~30Hz）：在 36ms 窗口内合并最新一帧，绝不挤占 UI 线程引发掉帧。
  Map<String, dynamic>? _pendingJson;
  bool _renderScheduled = false;

  void _ingestEngineResult(Map<String, dynamic> json) {
    final status = json['status'];
    // Java 采集层流水线帧（capturing 心跳等）：只当信标更新，绝不拿它的
    // 空 hand/advice 覆盖当前画面数据（旧行为会让心跳把建议清空闪现）。
    if (status is String && _kPipelineStatuses.contains(status)) {
      _lastPipelineAt = DateTime.now();
      _captureJavaCounters(json);
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
    // 端到端帧龄（采集→本侧收到）：只有这里能同时拿到两端时钟。统计与上屏解耦
    // ——即便本帧因去抖被暂存，它的帧龄仍是真实链路样本，不该因为没上屏就丢掉。
    // 同时带上 Java 分段耗时，否则「慢在哪一段」只能靠猜。
    //
    // java_status 帧（采集层心跳/报错）不参与样本，也不计入 dropped：它们根本不带
    // 采集时刻，混进来只会把「缺时间戳」的计数刷满，让真正的降级帧看不出来。
    // 判据用产出方随身携带的显式字段，而不在此枚举 status 字符串（枚举会漏）。
    if (json['java_status'] != true) {
      _latency.add(
          (json['captured_at_ms'] as num?)?.toInt(),
          DateTime.now().millisecondsSinceEpoch,
          encodeMs: (json['encode_ms'] as num?)?.toInt(),
          engineMs: (json['engine_ms'] as num?)?.toInt());
    }
    final isClear = status is String && _kClearStatuses.contains(status);
    if (!isClear) {
      // ok/partial/dingque/swap/pick 等有数据帧：前沿立即应用，并打断待确认的清空帧
      _firstPendingClearAt = null;
      _pendingClearJson = null;
      _lastFrameAt = DateTime.now();
      if (_signalLost && mounted) setState(() => _signalLost = false);
      _pendingJson = json;

      if (!_renderScheduled) {
        _renderScheduled = true;
        _applyPendingResult(); // 前沿：0ms 立即上屏响应！
        Future<void>.delayed(const Duration(milliseconds: 36), () {
          _renderScheduled = false;
          if (!mounted) return;
          if (_pendingJson != null) {
            _applyPendingResult(); // 尾部：窗口收尾时刷新最新数据
          }
        });
      }
      return;
    }
    // 清空帧去抖：**纯时间门**（自第一个清空帧起持续 ≥450ms）。
    // 旧写法是「≥ 4 帧 且 ≥450ms」的帧数×时间双门 —— 帧数门把"多久"隐含成
    // "多少个 15ms 采集周期"，而单帧实测已到 400ms+（引擎侧 p50 415ms），
    // 4 帧就是 ≈1.66s：画面早离开牌桌了，面板还死播上一局的手牌与建议。
    // 只有时间门是与帧率无关的物理量：瞬态遮挡（<450ms）依旧被挡住，
    // 真离开牌桌则在 450ms（或 _scheduleClearCommit 的 460ms 兜底）内清空。
    _firstPendingClearAt ??= DateTime.now();
    final showingGood = result != null &&
        !_kClearStatuses.contains(result?['status'] as String? ?? '') &&
        ((result?['hand'] as String? ?? '').isNotEmpty ||
            (result?['count'] as num? ?? 0) > 0);
    final sustained = DateTime.now()
            .difference(_firstPendingClearAt!)
            .inMilliseconds >= 450;
    if (!showingGood || sustained) {
      _firstPendingClearAt = null;
      _pendingClearJson = null;
      _lastFrameAt = DateTime.now();
      _pendingJson = json;
      _applyPendingResult();
    } else {
      // 局中瞬态丢帧：暂存保护，不打断屏幕已有建议，到期仍无好数据再由看门狗兜底提交
      _pendingClearJson = json;
      _scheduleClearCommit();
    }
  }

  bool _clearCommitScheduled = false;

  void _scheduleClearCommit() {
    if (_clearCommitScheduled) return;
    _clearCommitScheduled = true;
    Future<void>.delayed(const Duration(milliseconds: 460), () {
      _clearCommitScheduled = false;
      if (!mounted || _pendingClearJson == null) return;
      _lastFrameAt = DateTime.now();
      _pendingJson = _pendingClearJson;
      _pendingClearJson = null;
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
      final st = json['status'];
      final c = (json['count'] as num?)?.toInt() ?? 0;
      if (st == 'waiting' || st == 'no_tiles' || c == 0) {
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
    // 局况关键事件（碰/杠/听牌/换牌完成/结束）闪一颗瞬时胶囊。放在 setState 之后、
    // 用它自己的 setState：事件到达与面板重建解耦，错一帧也不会抖到建议区。
    _pulseKeyEvent(json);
  }

  /// 局况事实层（引擎 `match_phase`）只服务两处：瞬时胶囊与调试页摘要。
  /// 取不到就返回 null：宁可那一行整块收起，也不能拿上一帧的读数冒充“现在轮到谁”。
  static Map<String, dynamic>? _phaseOf(Map<String, dynamic>? res) {
    final mp = res?['match_phase'];
    return (mp is Map) ? mp.cast<String, dynamic>() : null;
  }

  /// 从 match_phase.feed 尾部取“刚刚落定的关键事件”，闪一个 ~2.5s 自动消失的
  /// 极小胶囊。**只用 at_ms 水位判重**：隔帧重发同一份缓存 payload 时，feed 尾
  /// 还是同一条，不去重会让同一句“对家 杠 5万”反复顶新，看着像播了三遍。
  void _pulseKeyEvent(Map<String, dynamic>? json) {
    final mp = _phaseOf(json);
    if (mp == null) return;
    final feed = mp['feed'];
    if (feed is! List || feed.isEmpty) return;
    final last = feed[feed.length - 1];
    if (last is! Map) return;
    final kind = last['kind'] as String? ?? '';
    final text = (last['text'] as String? ?? '').trim();
    final at = (last['at_ms'] as num?)?.toInt() ?? 0;
    if (!_kFlashKinds.contains(kind) || text.isEmpty) return;
    if (at <= _lastFlashAtMs) return;
    _lastFlashAtMs = at;
    // 必须走 setState：只改字段不标脏，胶囊得等下一帧才可能出现 ——
    // 而“最后一帧恰好就是碰的那一帧”时引擎已经安静下来，这一句就永远不会露脸。
    if (!mounted) return;
    setState(() => _eventFlash = Map<String, dynamic>.from(last));
    _eventFlashTimer?.cancel();
    _eventFlashTimer = Timer(const Duration(milliseconds: 2500), () {
      if (mounted) setState(() => _eventFlash = null);
    });
  }

  /// 调试页用的局况摘要：只带文本与计数，不带 34 维数组（跨引擎消息要小）。
  Map<String, dynamic> _phaseDigestForShare() {
    final mp = _phaseOf(result);
    if (mp == null) return const {};
    final feed = (mp['feed'] as List?) ?? const [];
    final melds = (mp['melds'] as List?) ?? const [];
    final ev = (mp['evidence'] as Map?)?.cast<String, dynamic>() ?? const {};
    return {
      'phase': mp['phase'] ?? '',
      'label': mp['label'] ?? '',
      'hint': mp['hint'] ?? '',
      'turn_basis': mp['turn_basis'] ?? '',
      'hand': (mp['hand'] as Map?)?.cast<String, dynamic>() ?? const {},
      'melds': melds
          .map((m) => m is Map
              ? '${m['seat_name'] ?? ''} '
                  '${_kMeldKindCn[m['kind'] as String? ?? ''] ?? '副露'} '
                  '${m['tile_cn'] ?? ''}'
              : '')
          .where((s) => (s as String).trim().isNotEmpty)
          .toList(),
      'feed': feed
          .map((e) => e is Map ? '${e['text'] ?? ''}' : '')
          .where((s) => (s as String).isNotEmpty)
          .toList(),
      'river': ev['river'] ?? const {},
      'rejected': ev['rejected'] ?? const {},
      'confirm_ms': ev['confirm_ms'],
      'over_ms': ev['over_ms'],
      'seq': mp['seq'],
      'updated_at_ms': mp['updated_at_ms'],
    };
  }

  /// 瞬时胶囊的配色：拿战术条同一族低饱和色，不引入红橙“错误/警告”语义。
  /// 阶段切换类（start/swap_in/dingque_in/irregular）走青灰系：它们是“现在到哪儿了”
  /// 的告知，不是故障报警 —— 拿警告色会把正常开局渲染成出错。
  static const Map<String, Color> _kFlashColor = {
    'start': Color(0xFF4DB6AC),
    'pong': Color(0xFF26A69A),
    'kong': Color(0xFF9CCC65),
    'added_kong': Color(0xFF9CCC65),
    'swap_in': Color(0xFF4DD0E1),
    'swap_done': Color(0xFF4DD0E1),
    'dingque_in': Color(0xFF7986CB),
    'dingque': Color(0xFF7986CB),
    'tenpai': Color(0xFFFFD54F),
    'irregular': Color(0xFF90A4AE),
    'over': Color(0xFFB0BEC5),
  };

  /// 碰/杠/听牌/阶段切换这类“刚刚落定”的事件，用一颗 ~2.5s 自动消失的极小胶囊回个话。
  /// 它只挂在 Stack 的 Positioned 上：不进 Column、不占布局高度，到时就从树上摘掉；
  /// IgnorePointer 让它不吃手势，完整局况明细（各家牌河张数、副露一览、被挡下的
  /// 脏读计数）一律放主页调试页，悬浮窗不新增任何常驻。
  Widget _eventFlashCapsule() {
    final Map<String, dynamic>? ev = _eventFlash;
    final String text = (ev?['text'] as String? ?? '').trim();
    if (text.isEmpty) return const SizedBox.shrink();
    final Color c =
        _kFlashColor[ev?['kind'] as String? ?? ''] ?? const Color(0xFF4DB6AC);
    return Positioned(
      left: 8,
      bottom: 20,
      child: IgnorePointer(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 140),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1.5),
            decoration: BoxDecoration(
              color: const Color(0xF00A1412),
              borderRadius: BorderRadius.circular(3),
              border: Border.all(color: c.withAlpha(190), width: 0.6),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 3,
                  height: 3,
                  decoration: BoxDecoration(color: c, shape: BoxShape.circle),
                ),
                const SizedBox(width: 3),
                Flexible(
                  child: Text(
                    text,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: Color(0xFFE0F2F1),
                      fontSize: 7.5,
                      fontWeight: FontWeight.w600,
                      decoration: TextDecoration.none,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // 授权到期/被停用时的悬浮占位胶囊：不给出任何牌建议。
  //
  // 为什么必须可点：旧版这里没有任何手势，而 _licenseAllows 是悲观默认 false，
  // 于是冷启动撞上一次心跳异常（弱网、子引擎凭证未就绪）就会卡在锁上最长 60s；
  // 用户看到的是“屏幕共享中”“识别中”都在，但球就是点不开。
  Widget _licenseDisabled() {
    if (!_lockSized) {
      // 锁定态可能出现在窗口仍是 56x56 球型尺寸时，胶囊文案会被裁得只剩一个
      // 锁图标（现象就是“一个红圈球，看不出为什么不可用”）。这里撑到胶囊尺寸。
      _lockSized = true;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && !_licenseAllows) _ensureSize(_kCapsuleW, _kCapsuleH);
      });
    }
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: _retryLicenseNow,
      child: Center(
        child: Container(
          margin: const EdgeInsets.all(8),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
          decoration: BoxDecoration(
            color: const Color(0xF51A1D24),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: const Color(0xFFFF8A80), width: 1),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.lock_outline, size: 15, color: Color(0xFFFF8A80)),
              const SizedBox(width: 6),
              // Flexible 而不是裸 Text：宽度不足时用省略号收尾，
              // 绝不因溢出而被裁成一个没有文字的光球。
              Flexible(
                child: Text(
                  _licenseDenyText,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 12.5,
                    fontWeight: FontWeight.w700,
                    decoration: TextDecoration.none,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// 立即重跑授权核验：用户不需要对着锁等满 60s 轮询。
  void _retryLicenseNow() {
    _licenseRetryTimer?.cancel();
    _refreshLicense();
  }

  /// 胶囊上的平台短名。这里曾经写死 '雀神'：悬浮窗跑在独立 isolate，
  /// 主页的平台选择并不会传给它，写死就变成“无论选哪个平台都显示雀神”。
  /// 引擎每帧已回传 platform key，据此显示；未知一律给「通用」，
  /// 绝不猜一个具体平台——那会把用户引向错误的牌风预期。
  static const Map<String, String> _kPlatformShort = {
    'tencent': '腾讯',
    'tuyou': '途游',
    'weile': '微乐',
    'jj': 'JJ',
    'gd_queshen': '雀神',
    'zj_sichuan': '指尖',
    'shushan': '蜀山',
    'generic': '通用',
  };

  String _platformShort(Object? key) {
    final k = (key is String ? key : '').trim().toLowerCase();
    return _kPlatformShort[k] ?? '通用';
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


  /// 主页「运势 / 好牌概率」要用的字段，只从这里过去。
  ///
  /// 三样东西引擎每帧都算好了（mood、ev_gauge、ting_chance/ukeire_chance +
  /// tile_ledger 的牌墙整数），过去白名单没放行，主页就是拿不到 —— 补的是
  /// **透传**，不是新算法。档位词与 note 一律取引擎原值：阈值口径只允许存在
  /// 在 Python 的 `probability_bands` 一份里，这里翻译一套主页就漂移一套。
  /// 整份 tile_ledger（34 型 × 11 键）不进跨引擎消息，只带面板用的那几个整数。
  static Map<String, dynamic>? _compactMood(Map<String, dynamic> json) {
    final m = json['mood'];
    if (m is! Map) return null;
    return <String, dynamic>{
      'state': m['state'],
      'badge': m['badge'],
      'desc': m['desc'],
    };
  }

  static Map<String, dynamic>? _compactEquity(Map<String, dynamic> json) {
    final g = json['ev_gauge'];
    if (g is! Map) return null;
    return <String, dynamic>{
      'level': g['level'],
      'band': g['band'],
      'tier': g['tier'],
      'note': g['note'],
      'calibrated': g['calibrated'],
      'equity_basis': g['equity_basis'],
      'insight': g['insight'],
    };
  }

  /// 好牌概率的两项事实：可推进张数（分子）与牌墙剩余（分母）。
  ///
  /// 分子有两条来源，差别只写在 `bound` 上，UI 据此决定说「至少」还是「至多」：
  /// - `lo`：账本下界 —— 听口用 ting_chance、未听口用 ukeire_chance 的
  ///   wall_lo_total（逐型牌墙下界相加，只会保守）。
  /// - `hi`：拿不到下界时退回 advice[0].ukeire —— 那是**未现**进张数，里面还
  ///   含着对手按住的牌，除以牌墙只能当上界，说成"至少"就是虚报。
  static Map<String, dynamic>? _compactTileOdds(Map<String, dynamic> json) {
    Map<dynamic, dynamic>? chance;
    final tc = json['ting_chance'];
    if (tc is Map && tc.isNotEmpty) chance = tc;

    Map<dynamic, dynamic>? top;
    final adv = json['advice'];
    if (adv is List && adv.isNotEmpty && adv.first is Map) {
      top = adv.first as Map;
      if (chance == null) {
        final uc = top['ukeire_chance'];
        final c2 = top['ting_chance'];
        if (uc is Map && uc.isNotEmpty) {
          chance = uc;
        } else if (c2 is Map && c2.isNotEmpty) {
          chance = c2;
        }
      }
    }

    int wallRemaining = -1;
    bool? ledgerOk;
    final ledger = json['tile_ledger'];
    if (ledger is Map) {
      wallRemaining = (ledger['wall_remaining'] as num?)?.toInt() ?? -1;
      if (ledger.containsKey('ok')) ledgerOk = ledger['ok'] == true;
    }
    if (wallRemaining < 0 && chance != null) {
      wallRemaining = (chance['wall_remaining'] as num?)?.toInt() ?? -1;
    }

    final out = <String, dynamic>{'wall_remaining': wallRemaining};
    if (ledgerOk != null) out['ledger_ok'] = ledgerOk;
    if (chance != null) {
      out['bound'] = 'lo';
      out['numerator'] = (chance['wall_lo_total'] as num?)?.toInt() ?? 0;
      out['unseen'] = (chance['total_unseen'] as num?)?.toInt() ?? 0;
      final t = chance['text'];
      if (t is String) out['text'] = t;
      return out;
    }
    if (top != null) {
      final u = (top['ukeire'] as num?)?.toInt();
      if (u == null) return null; // 引擎没给进张数就没有分子，不编
      out['bound'] = 'hi';
      out['numerator'] = u;
      out['unseen'] = u;
      final r = top['reason'];
      if (r is String) out['text'] = r;
      return out;
    }
    return null;
  }

  void _maybeShareStatus(Map<String, dynamic> json) {
    final mood = _compactMood(json);
    final equity = _compactEquity(json);
    final tile = _compactTileOdds(json);
    // 去抖键必须把牌墙数字算进来：同一副手牌里别人打出一张，牌墙剩余和可推进
    // 张数都会变，而 hand/best 一字不变 —— 只按旧键去抖，主页那两个按钮就会
    // 一直停在上一帧的分母上。
    // 先把参与拼键的值取成局部变量：`${}` 里再套同款引号容易踩解析歧义，
    // 直接插 Map 又会被 toString 的顺序牵着走。
    final String moodState =
        mood == null ? '-' : (mood['state']?.toString() ?? '-');
    final String equityBand =
        equity == null ? '-' : (equity['band']?.toString() ?? '-');
    final String tileSig = tile == null
        ? '-'
        : [tile['bound'], tile['numerator'], tile['wall_remaining']].join(':');
    final key = "${json['hand']}|${json['shanten']}|${json['status']}"
        "|${json['count']}|${json['best']}|$moodState|$equityBand|$tileSig";
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
      'mood': mood,
      'equity': equity,
      'tile_odds': tile,
    }).catchError((_) {});
  }

  /// 一键「新局重置」：向原生与 Python 发送重置信号，同时界面瞬间恢复满额活牌。
  /// 字牌矩阵必须按当前玩法牌墙推导（108 无字牌 / 112 仅 4 红中 / 136 全 7 字牌），
  /// 旧实现硬编码 7×4 字牌，在血战/血流等玩法下属于与牌局不符的"无中生有"数据。
  void _requestResetMatch() {
    FlutterOverlayWindow.shareData({'type': 'reset_match'}).catchError((_) {});
    if (mounted) {
      setState(() {
        final int wall = GameMode.info(selectedMode)?.wall ?? 108;
        final List<int> z = wall >= 136
            ? List.filled(7, 4)
            : (wall == 112 ? <int>[4] : <int>[]);
        final List<String> zNames = wall >= 136
            ? const ['东', '南', '西', '北', '白', '发', '中']
            : (wall == 112 ? const ['中'] : const <String>[]);
        final resetMatrix = {
          'm': List.filled(9, 4),
          'p': List.filled(9, 4),
          's': List.filled(9, 4),
          'z': z,
          'z_names': zNames,
        };
        if (result != null) {
          result = Map<String, dynamic>.from(result!)
            ..['remaining_matrix'] = resetMatrix
            ..['tile_ledger'] = null
            ..['ting_chance'] = null
            ..['hand'] = ''
            ..['count'] = 0
            ..['discards'] = ''
            ..['discard_count'] = 0
            ..['advice'] = []
            ..['best'] = ''
            ..['status'] = 'waiting'
            // 新局重置后上一局的局况（阶段/副露/实录）不得继续在屏上挂着：
            // 宁可那一行整块收起，也不能把上一局的“对家 杠 5万”当实时播报。
            ..['match_phase'] = null
            ..['message'] = '已重置新对局';
        }
        _shownAdvice = const [];
        _shownBest = '';
        _eventFlash = null;
        _eventFlashTimer?.cancel();
        _lastFlashAtMs = 0;
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

  Widget _buildModeSelectorOverlay() {
    return Positioned.fill(
      child: Container(
        decoration: BoxDecoration(
          color: const Color(0xF812151B),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: const Color(0xFFFFD54F).withAlpha(140), width: 1.0),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Icon(Icons.tune_rounded, color: Color(0xFFFFD54F), size: 14),
                const SizedBox(width: 5),
                Expanded(
                  child: Text(
                    // 条数从目录取，不写死：之前硬编码“10种规则”，上架到 19 条后
                    // 菜单列表会跟着长但标题还在说 10 种，属于界面静默错信息。
                    '切换玩法 (${GameMode.allModes.length}种规则)',
                    style: const TextStyle(
                      color: Color(0xFFFFF9C4),
                      fontSize: 10.5,
                      fontWeight: FontWeight.bold,
                      decoration: TextDecoration.none,
                    ),
                  ),
                ),
                GestureDetector(
                  onTap: () => setState(() => _showModeSelector = false),
                  behavior: HitTestBehavior.opaque,
                  child: Container(
                    padding: const EdgeInsets.all(2),
                    decoration: BoxDecoration(
                      color: Colors.white.withAlpha(20),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.close_rounded, color: Colors.white70, size: 13),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 5),
            Expanded(
              child: ListView.separated(
                physics: const BouncingScrollPhysics(),
                itemCount: GameMode.allModes.length,
                separatorBuilder: (_, __) => const SizedBox(height: 3),
                itemBuilder: (context, index) {
                  final info = GameMode.allModes[index];
                  final isCurrent = GameMode.normalizeKey(selectedMode) == info.key;
                  return GestureDetector(
                    onTap: () => _selectMode(info.key),
                    behavior: HitTestBehavior.opaque,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 4.5),
                      decoration: BoxDecoration(
                        color: isCurrent
                            ? const Color(0xFFE65100).withAlpha(100)
                            : Colors.white.withAlpha(12),
                        borderRadius: BorderRadius.circular(6),
                        border: Border.all(
                          color: isCurrent ? const Color(0xFFFFB74D) : Colors.white12,
                          width: isCurrent ? 1.0 : 0.5,
                        ),
                      ),
                      child: Row(
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(
                                  children: [
                                    Flexible(
                                      child: Text(
                                        info.name,
                                        overflow: TextOverflow.ellipsis,
                                        style: TextStyle(
                                          color: isCurrent ? const Color(0xFFFFD54F) : Colors.white,
                                          fontSize: 10,
                                          fontWeight: FontWeight.bold,
                                          decoration: TextDecoration.none,
                                        ),
                                      ),
                                    ),
                                    const SizedBox(width: 4),
                                    Container(
                                      padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 0.5),
                                      decoration: BoxDecoration(
                                        color: isCurrent
                                            ? const Color(0xFFFFD54F).withAlpha(40)
                                            : Colors.white.withAlpha(20),
                                        borderRadius: BorderRadius.circular(2),
                                      ),
                                      child: Text(
                                        info.status,
                                        style: TextStyle(
                                          color: isCurrent ? const Color(0xFFFFD54F) : Colors.white60,
                                          fontSize: 7,
                                          fontWeight: FontWeight.w600,
                                          decoration: TextDecoration.none,
                                        ),
                                      ),
                                    ),
                                  ],
                                ),
                                const SizedBox(height: 1),
                                Text(
                                  info.brief,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(
                                    color: Colors.white.withAlpha(160),
                                    fontSize: 8,
                                    decoration: TextDecoration.none,
                                  ),
                                ),
                              ],
                            ),
                          ),
                          if (isCurrent)
                            const Icon(Icons.check_circle_rounded, color: Color(0xFFFFD54F), size: 14),
                        ],
                      ),
                    ),
                  );
                },
              ),
            ),
          ],
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
    final String discards = (result?['discards'] as String?) ?? '';
    final bool isDingquePhase = (result?['dingque_phase'] == true || status == 'dingque');
    final bool isSwapPhase = (result?['swap_phase'] == true || status == 'swap') && (count >= 13) && discards.isEmpty;
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
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [Color(0xF5181C24), Color(0xF50E1217)],
          ),
          borderRadius: BorderRadius.circular(19),
          border: Border.all(color: const Color(0x38FFFFFF), width: 0.8),
          boxShadow: const [
            BoxShadow(
              color: Color(0x88000000),
              blurRadius: 10,
              offset: Offset(0, 3),
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
            Container(
              width: 6,
              height: 6,
              margin: const EdgeInsets.only(left: 3, right: 4),
              decoration: BoxDecoration(
                color: signalLost ? Colors.white24 : const Color(0xFF00E676),
                shape: BoxShape.circle,
                boxShadow: signalLost
                    ? null
                    : const [
                        BoxShadow(
                          color: Color(0x9900E676),
                          blurRadius: 4,
                          spreadRadius: 0.5,
                        ),
                      ],
              ),
            ),
            // 平台名取引擎每帧回传的 platform key。写死会让胶囊在任何平台下
            // 都显示同一个名字（此前就是 '雀神'），看起来就像平台切换没生效。
            Text(
              _platformShort(result?['platform']),
              style: const TextStyle(
                color: Color(0xFFFFF9C4),
                fontSize: 11,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.3,
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
                      ? '推演流已挂起 · 待命'
                      : (signalLost
                          ? '捕获流等待中 · 待命'
                          : (result?['status'] == 'waiting' ? '对局接入中 · 待命' : '实时推演流进行中')),
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

  /// 牌局账本结论行：把「未现 = 牌墙可摸 + 对手手上」这条拆账直接标在记牌器上。
  /// 旧面板只有每型 0~4 的未现数，看不出这些牌到底还摸得到还是被人攥着，而这两种
  /// 情况的打法完全不同（前者继续等自摸，后者根本等不到）。
  /// 对手张数未知时绝不报「牌墙 N 张」（那会把别人手上的牌也说成能摸到）。
  Widget _ledgerSummaryLine(Map<String, dynamic> ledger) {
    final bool oppKnown = ledger['opp_known'] == true;
    final bool ok = ledger['ok'] != false;
    final int bad = (ledger['violations'] as List?)?.length ?? 0;
    final int seen = (ledger['seen_total'] as num? ?? 0).toInt();
    final int unseen = (ledger['unseen_total'] as num? ?? 0).toInt();
    final int wall = (ledger['wall_remaining'] as num? ?? 0).toInt();
    final int rounds = (ledger['rounds_left'] as num? ?? 0).toInt();
    final int standing = (ledger['standing_total'] as num? ?? 0).toInt();
    final int wallOnly = (ledger['wall_only_unseen'] as num? ?? 0).toInt();
    final int wallOnlyTypes = (ledger['wall_only_types'] as num? ?? 0).toInt();

    final String head = oppKnown
        ? '牌墙 $wall 张·约 $rounds 轮·对手手上 $standing 张·已现 $seen 张'
        : '未现 $unseen 张·已现 $seen 张（对手张数未知，不拆牌墙）';
    final String tail = wallOnly > 0
        ? '·定缺门锁定 $wallOnly 张只能自摸（$wallOnlyTypes 种）'
        : '';
    final String warn = ok ? '' : '  ⚠ 记账矛盾 $bad 处，本帧不给建议';

    return Padding(
      padding: const EdgeInsets.only(left: 10, bottom: 2),
      child: Row(
        children: [
          Flexible(
            child: Text(
              '$head$tail',
              style: TextStyle(
                color: ok ? const Color(0xFF80CBC4) : const Color(0xFFFF8A80),
                fontSize: 8.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          if (!ok)
            Text(
              warn,
              style: const TextStyle(
                color: Color(0xFFFF8A80),
                fontSize: 8.5,
                fontWeight: FontWeight.bold,
              ),
            ),
        ],
      ),
    );
  }

  // 全场记牌器面板（万/筒/条 各 9 种牌及字牌/红中当前牌池/手牌扣除后的剩余存活数 0~4）
  // ledger 为牌局账本摘要：把「未现 = 牌墙 + 对手手上」这条拆账标在面板头部。
  Widget _remainingMatrixSection(Map<String, dynamic>? matrix, Map<String, dynamic>? ledger) {
    if (matrix == null) return const SizedBox.shrink();
    final List<dynamic>? m = matrix['m'] as List<dynamic>?;
    final List<dynamic>? p = matrix['p'] as List<dynamic>?;
    final List<dynamic>? s = matrix['s'] as List<dynamic>?;
    final List<dynamic>? z = matrix['z'] as List<dynamic>?;
    final List<dynamic>? zNames = matrix['z_names'] as List<dynamic>?;
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
                    cellBg = const Color(0xFF16191E);
                    borderColor = const Color(0x18FFFFFF);
                  } else if (cnt == 1) {
                    numColor = const Color(0xFFFFB74D); // 温暖金琥珀（仅剩1张）
                    cellBg = const Color(0xFF382312);   // 沉稳暖琥珀底
                    borderColor = const Color(0xFFE65100);
                  } else if (cnt == 2) {
                    numColor = const Color(0xFF81C784); // 翡翠嫩绿（2张）
                    cellBg = const Color(0xFF122818);   // 沉稳墨绿底
                    borderColor = const Color(0xFF2E7D32);
                  } else {
                    numColor = const Color(0xFF00E676); // 活跃热张（3~4张存活，清晰青绿）
                    cellBg = const Color(0xFF0C301B);
                    borderColor = const Color(0xFF00C853);
                  }

                  return Container(
                    width: 23,
                    height: 22,
                    decoration: BoxDecoration(
                      color: cellBg,
                      borderRadius: BorderRadius.circular(3.5),
                      border: Border.all(
                        color: borderColor,
                        width: 0.6,
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

    Widget buildZRow(String suitName, Color labelColor, List<dynamic> counts, List<dynamic>? names) {
      if (counts.isEmpty) return const SizedBox.shrink();
      // 字牌格名由 Python 按玩法牌集下发（三人扣只有东南西北白中、血流红中只有中）；
      // 旧写法靠「长度==1」猜中、否则按 7 个固定名字取前 N 个，6 字牌玩法会整体错位。
      // 长度对不上就不画，绝不拿错名字去标牌。
      List<String> cellNames;
      if (names != null && names.length == counts.length) {
        cellNames = names.map((e) => e.toString()).toList();
      } else if (counts.length == 1) {
        cellNames = const ['中'];
      } else if (counts.length == 7) {
        cellNames = const ['东', '南', '西', '北', '白', '发', '中'];
      } else {
        return const SizedBox.shrink();
      }
      final bool isOnlyHongZhong = counts.length == 1 && cellNames[0] == '中';
      final int displayCount = counts.length;

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
                    cellBg = const Color(0xFF16191E);
                    borderColor = const Color(0x18FFFFFF);
                  } else if (cnt == 1) {
                    numColor = const Color(0xFFFFB74D);
                    cellBg = const Color(0xFF382312);
                    borderColor = const Color(0xFFE65100);
                  } else if (cnt == 2) {
                    numColor = const Color(0xFF81C784);
                    cellBg = const Color(0xFF122818);
                    borderColor = const Color(0xFF2E7D32);
                  } else {
                    numColor = const Color(0xFF00E676);
                    cellBg = const Color(0xFF0C301B);
                    borderColor = const Color(0xFF00C853);
                  }

                  return Padding(
                    padding: const EdgeInsets.only(right: 3.5),
                    child: Container(
                      width: isOnlyHongZhong ? 34 : 23,
                      height: 22,
                      decoration: BoxDecoration(
                        color: cellBg,
                        borderRadius: BorderRadius.circular(3.5),
                        border: Border.all(
                          color: borderColor,
                          width: 0.6,
                        ),
                      ),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Text(
                            cellNames[idx],
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
      margin: const EdgeInsets.only(bottom: 5),
      padding: const EdgeInsets.symmetric(horizontal: 7.5, vertical: 5.5),
      decoration: BoxDecoration(
        color: const Color(0x3D10131A),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Colors.white.withAlpha(22), width: 0.6),
        boxShadow: const [
          BoxShadow(
            color: Color(0x18000000),
            blurRadius: 4,
            offset: Offset(0, 1.5),
          ),
        ],
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
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    gradient: const LinearGradient(
                      colors: [Color(0xFFE65100), Color(0xFFC62828)],
                    ),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(color: const Color(0x80FFB74D), width: 0.6),
                    boxShadow: const [
                      BoxShadow(
                        color: Color(0x30E65100),
                        blurRadius: 3,
                        offset: Offset(0, 1),
                      ),
                    ],
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
          if (_matrixExpanded && ledger != null) ...[
            const SizedBox(height: 2),
            _ledgerSummaryLine(ledger),
          ],
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
            buildRow('万', const Color(0xFFEF5350), m),
            buildRow('筒', const Color(0xFF42A5F5), p),
            buildRow('条', const Color(0xFF66BB6A), s),
            if (z != null && z.isNotEmpty)
              buildZRow('字', const Color(0xFFB0BEC5), z, zNames),
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
    final status = result?['status'] as String? ?? '';
    if (hand.isEmpty || count == 0 || status == 'waiting' || status == 'no_tiles') {
      return const SizedBox.shrink();
    }
    // 合法麻将立牌张数：1/2 (碰4次), 4/5 (碰3次), 7/8 (碰2次), 10/11 (碰1次), 13/14 (门清)
    // 仅在引擎确认为 partial 异常残缺（如 3, 6, 9, 12 张且持续未恢复）时才提示遮挡，杜绝摸打瞬态闪烁误报
    final bool isLegalStanding = const {1, 2, 4, 5, 7, 8, 10, 11, 13, 14}.contains(count);
    final bool partial = (status == 'partial') && count > 0 && !isLegalStanding;
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
                  const Icon(Icons.info_outline_rounded, color: Color(0xFFFFB74D), size: 12),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      '手牌感应 $count 张 · 请避免视窗遮挡牌面',
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
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xDD281547), Color(0xDD180D2E)],
        ),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFFBA68C8), width: 0.8),
        boxShadow: const [
          BoxShadow(
            color: Color(0x20000000),
            blurRadius: 4,
            offset: Offset(0, 1.5),
          ),
        ],
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
                padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                decoration: BoxDecoration(
                  color: Colors.white.withAlpha(20),
                  borderRadius: BorderRadius.circular(3),
                  border: Border.all(color: Colors.white12, width: 0.5),
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
              height: 1.15,
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
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: isHuazhu
              ? [const Color(0xDD6A0035), const Color(0xDD4A0025)]
              : [const Color(0xDD8E2500), const Color(0xDD5D1500)],
        ),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(
          color: isHuazhu ? const Color(0xFFFF4081) : const Color(0xFFFF6D00),
          width: 0.8,
        ),
        boxShadow: const [
          BoxShadow(
            color: Color(0x20000000),
            blurRadius: 4,
            offset: Offset(0, 1.5),
          ),
        ],
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

  Widget _buildTingRadarWidget(List<dynamic> tingDetails, int? shanten,
      Map<String, dynamic>? chance) {
    if (tingDetails.isEmpty) return const SizedBox.shrink();

    int totalRemaining = 0;
    for (final t in tingDetails) {
      if (t is Map) {
        totalRemaining += (t['remaining'] as num? ?? 0).toInt();
      }
    }
    final bool hasDead = tingDetails.any((t) => (t is Map && t['is_dead'] == true));

    // 牌局账本口径：同一个「余 N 张」拆成牌墙可自摸 / 对手可能打出两部分。
    // 没拿到账本（陈旧签名/未开局）时逐字回退到旧口径，绝不拿 0 去谎报。
    final int wallOnly = (chance?['wall_only'] as num?)?.toInt() ?? 0;
    final int chanceTotal = (chance?['total_unseen'] as num?)?.toInt() ?? totalRemaining;
    final bool oppKnown = chance?['opp_known'] == true;
    final String chanceText = (chance?['text'] ?? '') as String;

    return Container(
      margin: const EdgeInsets.only(bottom: 5),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: hasDead
              ? [const Color(0xEE2D1117), const Color(0xEE1E0B10)]
              : [const Color(0xEE092618), const Color(0xEE061B11)],
        ),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(
          color: hasDead ? const Color(0xFFFF5252) : const Color(0xFF00E676),
          width: 0.8,
        ),
        boxShadow: [
          BoxShadow(
            color: (hasDead ? Colors.redAccent : Colors.greenAccent).withAlpha(30),
            blurRadius: 6,
            offset: const Offset(0, 2),
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
                '🎯 听牌推算',
                style: TextStyle(
                  color: hasDead ? const Color(0xFFFF8A80) : const Color(0xFF69F0AE),
                  fontSize: 10,
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
                  hasDead
                      ? '含绝张警报'
                      : (oppKnown && wallOnly > 0
                          ? '余 $chanceTotal 张·$wallOnly 张只能自摸'
                          : '余 $chanceTotal 张机会'),
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

  /// 以下四个 `*Word` 与 `_bandProgress` 是**旧 payload 兜底**（引擎产物没带 `band` 字段时），
  /// 阈值/措辞必须与 Python 侧 `probability_bands` 逐字对齐：口径的单一来源
  /// 在 Python，这里只是防「旧引擎 + 新面板」的空窗。两边一旦分叉，
  /// `localtest/test_equity_honesty.py` 的跨语言契约用例会红。
  String _equityBandWord(String level) {
    switch (level) {
      case 'extreme':
        return '极优';
      case 'high':
        return '较优';
      case 'neutral':
        return '均势';
      case 'risk':
        return '承压';
    }
    return '未定档';
  }

  /// 三档粗分（B-P4 空白 B）：`probability_bands.coarse_tier` 的兜底副本。
  /// 它不是第二套阈值表——只把 level 投影成偏优/中性/偏劣，不看任何数值。
  String _equityTierWord(String level) {
    switch (level) {
      case 'extreme':
        return '偏优';
      case 'high':
        return '偏优';
      case 'neutral':
        return '中性';
      case 'risk':
        return '偏劣';
    }
    return '未定档';
  }

  /// 危险档位的行动指令兜底副本（B-P4 空白 C），与 `probability_bands.danger_advice`
  /// 逐字同表：面板不得自己造一句「建议谨慎打出」这种谁都不负责的废话。
  String _dangerHintWord(String level) {
    switch (level) {
      case 'safe':
        return '绝对安全 · 现物/定缺门，可放心打出';
      case 'low':
        return '轻微风险 · 当前形势可接受';
      case 'medium':
        return '中等风险 · 建议优先选低危出张';
      case 'high':
        return '高危 · 除非已听牌，否则改打安全牌';
      case 'critical':
        return '极危 · 生张，不是必胡就别打';
    }
    return '未定档';
  }

  /// 危险行动指令的配色（B-P4 空白 C）：safe=绿、low=灰、medium=黄、high/critical=红。
  /// 颜色必须从 `danger_level` 派生，不能再拿字符串比大小（旧面板写的是
  /// `danger_level >= "high"` 才显红，中间档就这样被当不存在）。
  Color _dangerHintColor(String level) {
    switch (level) {
      case 'safe':
        return const Color(0xFFA5D6A7);
      case 'low':
        return const Color(0xFFB0BEC5);
      case 'medium':
        return const Color(0xFFFFD54F);
      case 'high':
        return const Color(0xFFFF8A65);
      case 'critical':
        return const Color(0xFFFF5252);
    }
    return const Color(0xFFB0BEC5);
  }

  String _dangerBandWord(String level) {
    switch (level) {
      case 'safe':
        return '安';
      case 'low':
        return '微危';
      case 'medium':
        return '中危';
      case 'high':
        return '高危';
      case 'critical':
        return '极危';
    }
    return '未定档';
  }

  String _tenpaiBandWord(double p) {
    if (p < 0.20) return '低';
    if (p < 0.45) return '中';
    if (p < 0.70) return '高';
    return '极高';
  }

  String _heldBandWord(double p) {
    if (p < 0.35) return '低';
    if (p < 0.60) return '中';
    return '高';
  }

  /// 未标定态的进度条：只画档位格（四等分中点），不画百分比刻度。
  double _bandProgress(String level) {
    switch (level) {
      case 'extreme':
        return 1.0;
      case 'high':
        return 0.75;
      case 'neutral':
        return 0.50;
      case 'risk':
        return 0.25;
    }
    return 0.50;
  }

  /// 全场实时胡牌胜率与期望收益 (Win Equity & EV Gauge) - 极致微型
  ///
  /// 概率诚实化（B-P3）：这里的 `win_equity` / `net_ev` 都是**未标定的模型值**，
  /// 口径由 Python 侧 `probability_bands` 定死并随 payload 下发，前端只做渲染：
  /// - `calibrated == false` → 只显档位词（极优/较优/均势/承压），**绝不显「胜率 X%」**；
  ///   进度条按档位取格，不画百分比刻度（那会把模型打分伪装成测量结果）。
  /// - `net_ev` 的单位是内部评分「分」，不是「番」（旧版单位错标会让用户按番数取舍）。
  /// - `equity_basis == 'analytical'`，或 `win_equity == analytical_equity`（PVN 未训练
  ///   的恒等式）→ 标题写明「纯解析式评估」，不让用户以为有训练好的网络在算胜率。
  /// 百分比只能在 `calibrated == true` 后复活，而那需要真实标定样本，不是改字串。
  ///
  /// B-P4 空白 B（行动映射）：只把数字换小、进度条照旧画，用户仍会以为背后有模型。
  /// 因此 `pureAnalytical && !calibrated` 时整块退化成一行文本：没有进度条、
  /// 没有 `net_ev` 分值（那是合成评分，不标量纲），只有一个三档档位词（偏优/中性/偏劣）。
  Widget _buildWinEquityGaugeWidget(Map<String, dynamic> evGauge, double? winEquity,
      {double? analyticalEquity}) {
    final double netEv = (evGauge['net_ev'] as num? ?? 0.0).toDouble();
    final String level = (evGauge['level'] as String?) ?? 'neutral';
    final bool calibrated = (evGauge['calibrated'] as bool?) ?? false;
    final String band = (evGauge['band'] as String?) ?? _equityBandWord(level);
    final String basis = (evGauge['equity_basis'] as String?) ?? 'analytical';
    final String note = (evGauge['note'] as String?) ?? '';
    final String evUnit = (evGauge['net_ev_unit'] as String?) ?? '分';
    final double? rawEquity =
        winEquity ?? (evGauge['win_equity'] as num?)?.toDouble();
    // PVN 关闭态的两种等价判据：口径字段直接说明，或两个数值逐位相等。
    // 命中时面板只能说「解析式评估」，不能含糊地报成胜率。
    final bool pureAnalytical = basis != 'analytical+pvn' ||
        (rawEquity != null &&
            analyticalEquity != null &&
            (rawEquity - analyticalEquity).abs() < 1e-6);
    final String title =
        calibrated ? '胜率推算' : (pureAnalytical ? '纯解析式评估' : '牌势评估');
    final String equityChip = calibrated
        ? '胜率 ${((rawEquity ?? 0.5) * 100).toStringAsFixed(0)}%'
        : band;
    final double barValue = calibrated
        ? (rawEquity ?? 0.5).clamp(0.0, 1.0)
        : _bandProgress(level);
    // 三档粗分走 payload（`probability_bands.coarse_tier`），面板只兜旧帧。
    final String tier = (evGauge['tier'] as String?) ?? _equityTierWord(level);
    // 未标定又无模型：本块没有任何可测量的东西，只能当文本说明，不能当仪表。
    final bool degrade = pureAnalytical && !calibrated;
    final String degradeLabel = pureAnalytical
        ? '纯解析式评估 · 牌势 $tier'
        : '牌势评估 · $tier';

    Color primaryColor;
    Color gradientStart;
    Color gradientEnd;
    if (level == 'extreme') {
      primaryColor = const Color(0xFFFF5252);
      gradientStart = const Color(0x385C0A0A);
      gradientEnd = const Color(0x22360505);
    } else if (level == 'high') {
      primaryColor = const Color(0xFF00E676);
      gradientStart = const Color(0x38004D40);
      gradientEnd = const Color(0x22002E26);
    } else if (level == 'risk') {
      primaryColor = const Color(0xFFFFB74D);
      gradientStart = const Color(0x384E342E);
      gradientEnd = const Color(0x22372722);
    } else {
      primaryColor = const Color(0xFF64B5F6);
      gradientStart = const Color(0x381A237E);
      gradientEnd = const Color(0x2210164D);
    }

    return Container(
      margin: const EdgeInsets.only(bottom: 2),
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1.5),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [gradientStart, gradientEnd],
        ),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: primaryColor.withAlpha(60), width: 0.5),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.insights_rounded, color: primaryColor, size: 8),
                  const SizedBox(width: 2.5),
                  Text(
                    title,
                    style: TextStyle(
                      color: primaryColor,
                      fontSize: 7.5,
                      fontWeight: FontWeight.bold,
                      decoration: TextDecoration.none,
                    ),
                  ),
                ],
              ),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 0.5),
                    decoration: BoxDecoration(
                      color: primaryColor.withAlpha(35),
                      borderRadius: BorderRadius.circular(2),
                      border: Border.all(color: primaryColor.withAlpha(80), width: 0.4),
                    ),
                    child: Text(
                      equityChip,
                      style: TextStyle(
                        color: primaryColor,
                        fontSize: 7.0,
                        fontWeight: FontWeight.bold,
                        decoration: TextDecoration.none,
                      ),
                    ),
                  ),
                  if (!degrade) ...[
                    const SizedBox(width: 2.5),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 0.5),
                      decoration: BoxDecoration(
                        color: Colors.white.withAlpha(15),
                        borderRadius: BorderRadius.circular(2),
                      ),
                      child: Text(
                        netEv >= 0
                            ? '+${netEv.toStringAsFixed(1)}$evUnit'
                            : '${netEv.toStringAsFixed(1)}$evUnit',
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 7.0,
                          fontWeight: FontWeight.bold,
                          decoration: TextDecoration.none,
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ],
          ),
          const SizedBox(height: 1),
          if (degrade)
            Text(
              degradeLabel,
              style: TextStyle(
                color: primaryColor.withAlpha(200),
                fontSize: 6.8,
                fontWeight: FontWeight.w500,
                decoration: TextDecoration.none,
              ),
            )
          else
            ClipRRect(
              borderRadius: BorderRadius.circular(1),
              child: LinearProgressIndicator(
                value: barValue,
                minHeight: 1.5,
                backgroundColor: Colors.white12,
                valueColor: AlwaysStoppedAnimation<Color>(primaryColor),
              ),
            ),
        ],
      ),
    );
  }

  /// 点炮危险度指示勋章（主推与次选通用）
  Widget _buildDangerBadge(dynamic dangerFlow, {bool mini = false}) {
    if (dangerFlow is! Map) return const SizedBox.shrink();
    final Map<dynamic, dynamic> df = dangerFlow;
    final String dLevel = (df['danger_level'] as String?) ?? 'safe';
    // 档位词由 Python 侧 `probability_bands.danger_band` 下发。`deal_in_prob` 是
    // P(听牌)×P(打中该牌|听牌) 的手工先验乘积（0.04/0.08/0.14/0.26 全是拍的），
    // 乘 100 印成「X% 危」就是把未标定打分当频率，所以面板只显档位。
    final String dBand = (df['danger_band'] as String?) ?? _dangerBandWord(dLevel);
    Color dColor = const Color(0xFF81C784);
    if (dLevel == 'critical') {
      dColor = const Color(0xFFFF5252);
    } else if (dLevel == 'high') {
      dColor = const Color(0xFFFF7043);
    } else if (dLevel == 'medium') {
      dColor = const Color(0xFFFFB74D);
    }
    if (mini) {
      return Container(
        margin: const EdgeInsets.only(left: 3),
        padding: const EdgeInsets.symmetric(horizontal: 2.5, vertical: 0.5),
        decoration: BoxDecoration(
          color: dLevel == 'safe' ? const Color(0x3381C784) : const Color(0x33FFB74D),
          borderRadius: BorderRadius.circular(2),
        ),
        child: Text(
          dLevel == 'safe' ? '安' : dBand,
          style: TextStyle(
            color: dLevel == 'safe' ? const Color(0xFFC8E6C9) : const Color(0xFFFFCC80),
            fontSize: 7.5,
            fontWeight: FontWeight.bold,
            decoration: TextDecoration.none,
          ),
        ),
      );
    }
    return Container(
      margin: const EdgeInsets.only(left: 4),
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1.5),
      decoration: BoxDecoration(
        color: dColor.withAlpha(35),
        borderRadius: BorderRadius.circular(3),
        border: Border.all(color: dColor.withAlpha(90), width: 0.5),
      ),
      child: Text(
        dLevel == 'safe' ? '安全' : '点炮 $dBand',
        style: TextStyle(
          color: dColor,
          fontSize: 8,
          fontWeight: FontWeight.bold,
          decoration: TextDecoration.none,
        ),
      ),
    );
  }

  /// 对手手牌贝叶斯概率透视 (Bayesian Hand Range Reading) - 极致微型
  Widget _buildBayesianHandRangesWidget(List<dynamic> handRanges) {
    return Container(
      margin: const EdgeInsets.only(bottom: 2),
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
      decoration: BoxDecoration(
        color: const Color(0x281A237E),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: const Color(0x4D3F51B5), width: 0.5),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: const [
              Icon(Icons.remove_red_eye_outlined, color: Color(0xFF90CAF9), size: 8.5),
              SizedBox(width: 3),
              Text(
                '对手手牌推算',
                style: TextStyle(
                  color: Color(0xFF90CAF9),
                  fontSize: 7.8,
                  fontWeight: FontWeight.bold,
                  decoration: TextDecoration.none,
                ),
              ),
            ],
          ),
          const SizedBox(height: 1.5),
          Row(
            children: handRanges.map((opp) {
              final String name = (opp['name'] as String?) ?? '对手';
              final String dqName = (opp['dingque_name'] as String?) ?? '未定';
              final int standing = (opp['standing'] as num? ?? 13).toInt();
              final double tenpaiProb = (opp['tenpai_prob'] as num? ?? 0.2).toDouble();
              // 同上：tenpai_prob 是 logistic 手工曲线（斜率 0.28 + 鸣牌 0.20/次），
              // 未经标定，所以面板只显「叫听 高/中/低」，不显百分比。
              final String tenpaiBand =
                  (opp['tenpai_band'] as String?) ?? _tenpaiBandWord(tenpaiProb);
              final List<dynamic> topHeld = (opp['top_held'] as List<dynamic>?) ?? [];

              return Expanded(
                child: Container(
                  margin: const EdgeInsets.symmetric(horizontal: 1),
                  padding: const EdgeInsets.symmetric(horizontal: 2.5, vertical: 1.5),
                  decoration: BoxDecoration(
                    color: Colors.white.withAlpha(8),
                    borderRadius: BorderRadius.circular(2.5),
                    border: Border.all(color: Colors.white12, width: 0.4),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            name,
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 7.2,
                              fontWeight: FontWeight.bold,
                              decoration: TextDecoration.none,
                            ),
                          ),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 1.5, vertical: 0.3),
                            decoration: BoxDecoration(
                              color: dqName != '未定' ? const Color(0x4D00E676) : Colors.white10,
                              borderRadius: BorderRadius.circular(1.5),
                            ),
                            child: Text(
                              dqName != '未定' ? '缺$dqName' : '$standing张',
                              style: TextStyle(
                                color: dqName != '未定' ? const Color(0xFFB9F6CA) : Colors.white60,
                                fontSize: 6.2,
                                fontWeight: FontWeight.bold,
                                decoration: TextDecoration.none,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 0.5),
                      Text(
                        '叫听 $tenpaiBand',
                        style: TextStyle(
                          color: tenpaiProb >= 0.50 ? const Color(0xFFFF8A80) : Colors.white60,
                          fontSize: 6.2,
                          decoration: TextDecoration.none,
                        ),
                      ),
                      if (topHeld.isNotEmpty) ...[
                        const SizedBox(height: 1),
                        Wrap(
                          spacing: 1.5,
                          runSpacing: 0.5,
                          children: topHeld.take(2).map((h) {
                            final String tileStr = (h['tile'] as String?) ?? '';
                            final double p = (h['prob'] as num? ?? 0.0).toDouble();
                            final String heldBand =
                                (h['band'] as String?) ?? _heldBandWord(p);
                            final String cn = tileToChinese(tileStr);
                            return Container(
                              padding: const EdgeInsets.symmetric(horizontal: 1.5, vertical: 0.2),
                              decoration: BoxDecoration(
                                color: const Color(0x33FFD54F),
                                borderRadius: BorderRadius.circular(1.5),
                              ),
                              child: Text(
                                '$cn $heldBand',
                                style: const TextStyle(
                                  color: Color(0xFFFFECB3),
                                  fontSize: 5.8,
                                  fontWeight: FontWeight.bold,
                                  decoration: TextDecoration.none,
                                ),
                              ),
                            );
                          }).toList(),
                        ),
                      ],
                    ],
                  ),
                ),
              );
            }).toList(),
          ),
        ],
      ),
    );
  }

  /// 次选进张标签：引擎算出真实进张时恒 >0；
  /// `ukeire==0 且 shanten>=2` 表示尚未算得真实进张，显示“—”避免误导“绝张 0 张”。
  ///
  /// 另外：`ukeire` 本身是「未现牌计数」（牌墙 + 对手手上）的上界。只有当本条建议
  /// 带着账本拆账（ting_chance / ukeire_chance）时，才能直接报张数；否则必须加 ≤ 号，
  /// 不把上界说成确定的机会数（B-P3 区间 vs 点估计规则）。
  String _ukeireLabel(dynamic item) {
    final int u = (item['ukeire'] as num? ?? 0).toInt();
    final int sh = (item['shanten'] as num? ?? 0).toInt();
    if (u == 0 && sh >= 2) return '—';
    final bool ledgerBacked =
        item['ting_chance'] is Map || item['ukeire_chance'] is Map;
    return ledgerBacked ? '$u张' : '≤$u张';
  }

  /// 三段文案的唯一来源是引擎战术层；它返回空串就跟着空，那一行整块收起。
  ///
  /// 为什么不拿 `match_phase` 往这里补位（曾经补过，按要求撤掉）：战术层在 shanten≥2
  /// 时故意留白，局况层却是恒有输出的（“候牌中”“轮到你出牌”每帧都在）。拿后者填前者，
  /// 这一行就从“没建议时收起”变成对局全程常驻 —— 悬浮窗叠在真实牌局上，等于我偷偷
  /// 给它加了一块常驻面板。局况改由两处回答，都不占布局：阶段切换与关键事件闪一颗
  /// ~2.5s 的瞬时胶囊（`_eventFlashCapsule`），完整明细在主页调试页「当前牌局」卡。
  ///
  /// 本地粗推里不再有 `count % 3 == 2`：杠从手里抽走 4 张，4 ≢ 0 (mod 3)，
  /// 同余式只在没杠时成立；杠过一次后基数是 9（13-4），摸进一张是 10，
  /// `10 % 3 == 1` ⇒ 明明轮到你出牌、端上却会永远读成“候牌中”。
  /// 基数只能由引擎按本家副露扣牌算（`match_phase.hand.base`）。
  ///
  /// 下面那段本地粗推只在 payload 压根没带 phase_label 时才会走到（错误帧、隔帧重发的
  /// 旧缓存）；正常帧一律以引擎文案为准，端上不重算一套。
  String _resolvePhaseLabel(Map<String, dynamic>? res) {
    if (res == null) return '';
    final p = res['phase_label'] as String?;
    if (p != null) return p;
    final status = res['status'] as String? ?? '';
    final count = (res['count'] as num?)?.toInt() ?? 0;
    final isDrawing = res['is_drawing'] == true;
    final shanten = res['shanten'] as int?;
    if (res['swap_phase'] == true || status == 'swap') return '换三张优化';
    if (res['dingque_phase'] == true || status == 'dingque') return '定缺选门';
    if (res['pick_phase'] == true || status == 'pick') return '选牌操作中';
    if (status == 'waiting' || status == 'no_tiles' || count == 0) return '';
    if (isDrawing) {
      if (shanten == 0) return '摸牌决断 · 听牌决胜';
      if (shanten == 1) return '摸牌决断 · 进听冲刺';
      return '';
    } else {
      if (shanten == 0) return '听牌守株 · 待胡中';
      if (shanten == 1) return '一向听待命 · 候牌中';
      return '';
    }
  }

  String _resolveTacticalBadge(Map<String, dynamic>? res) {
    if (res == null) return '';
    final b = res['tactical_badge'] as String?;
    if (b != null) return b;
    final status = res['status'] as String? ?? '';
    final count = (res['count'] as num?)?.toInt() ?? 0;
    final isDrawing = res['is_drawing'] == true;
    final shanten = res['shanten'] as int?;
    if (res['swap_phase'] == true || status == 'swap') return '换三张';
    if (res['dingque_phase'] == true || status == 'dingque') return '定缺抉择';
    if (res['pick_phase'] == true || status == 'pick') return '选牌决断';
    if (status == 'waiting' || status == 'no_tiles' || count == 0) return '';
    if (isDrawing) {
      if (shanten == 0) return '听牌决胜';
      if (shanten == 1) return '进听冲刺';
      return '';
    } else {
      if (shanten == 0) return '已下叫';
      if (shanten == 1) return '一向听';
      return '';
    }
  }

  String _resolveTacticalIntent(Map<String, dynamic>? res, String best) {
    if (res == null) return '';
    final t = res['tactical_intent'] as String?;
    if (t != null) return t;
    final status = res['status'] as String? ?? '';
    final count = (res['count'] as num?)?.toInt() ?? 0;
    final isDrawing = res['is_drawing'] == true;
    final shanten = res['shanten'] as int?;
    final bestCn = best.isNotEmpty ? tileToChinese(best) : '';
    if (res['swap_phase'] == true || status == 'swap') return '换三张博弈：优选同门弱牌置换';
    if (res['dingque_phase'] == true || status == 'dingque') return '定缺决断：规避冲突锁定极小门';
    if (res['pick_phase'] == true || status == 'pick') return '候选牌张捕获：等待选定牌张';
    if (status == 'waiting' || status == 'no_tiles' || count == 0) return '';
    if (isDrawing) {
      if (shanten == 0) {
        return bestCn.isNotEmpty ? '推荐切【$bestCn】· 锁定最优叫口' : '已入听 · 锁定最优和牌路径';
      }
      if (shanten == 1) {
        return bestCn.isNotEmpty ? '推荐切【$bestCn】· 拆搭冲刺下叫' : '一向听 · 拆解孤弱冲刺听牌';
      }
      return '';
    } else {
      if (shanten == 0) return '已听牌 · 待和或防守巡检';
      if (shanten == 1) return '一向听 · 进张通道推演中';
      return '';
    }
  }

  Widget _buildTacticalPerceptionWidget({
    required String phaseLabel,
    required String tacticalBadge,
    required String tacticalIntent,
    required bool isDrawing,
    int? shanten,
    Map<String, dynamic>? mood,
  }) {
    // 明确移除：未开局时的“等待开局”与对局中的“巡视中/观望”状态，彻底不展示，完全不占用空间
    if (phaseLabel.isEmpty ||
        tacticalBadge.isEmpty ||
        tacticalBadge == '等待开局' ||
        tacticalBadge == '巡视中' ||
        tacticalBadge == '行牌中' ||
        phaseLabel.contains('等待') ||
        phaseLabel.contains('局势感知') ||
        phaseLabel.contains('巡视') ||
        phaseLabel.contains('观望')) {
      return const SizedBox.shrink();
    }

    return Container(
      margin: const EdgeInsets.only(bottom: 2.5),
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2.5),
      decoration: BoxDecoration(
        color: const Color(0xF0122220),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: const Color(0x664DB6AC), width: 0.5),
      ),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 3.5, vertical: 1),
            decoration: BoxDecoration(
              color: const Color(0xFF00897B),
              borderRadius: BorderRadius.circular(2.5),
            ),
            child: Text(
              tacticalBadge,
              style: const TextStyle(
                color: Colors.white,
                fontSize: 7.5,
                fontWeight: FontWeight.bold,
                decoration: TextDecoration.none,
              ),
            ),
          ),
          const SizedBox(width: 4),
          Expanded(
            child: Text(
              tacticalIntent.isNotEmpty ? tacticalIntent : phaseLabel,
              style: const TextStyle(
                color: Color(0xFFE0F2F1),
                fontSize: 8,
                fontWeight: FontWeight.w500,
                decoration: TextDecoration.none,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (isDrawing) ...[
            const SizedBox(width: 3),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 0.8),
              decoration: BoxDecoration(
                color: const Color(0xFFE65100).withAlpha(190),
                borderRadius: BorderRadius.circular(2),
              ),
              child: const Text(
                '摸牌',
                style: TextStyle(
                  color: Color(0xFFFFE0B2),
                  fontSize: 7,
                  fontWeight: FontWeight.bold,
                  decoration: TextDecoration.none,
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _adviceSection(List<dynamic> advice, String best, int count) {
    final status = result?['status'] as String? ?? '';
    final String discards = (result?['discards'] as String?) ?? '';
    final bool isDingquePhase = (result?['dingque_phase'] == true || status == 'dingque');
    final bool isSwapPhase = (result?['swap_phase'] == true || status == 'swap') && (count >= 13) && discards.isEmpty;
    final bool isPickPhase = (result?['pick_phase'] == true || status == 'pick');
    final bool inMatch = status != 'waiting' && status != 'no_tiles' && count >= 4;
    final List<dynamic> activeAdvice = (inMatch || isDingquePhase || isSwapPhase || isPickPhase) ? advice : const [];
    final swapData = result?['swap_advice'] as Map<String, dynamic>?;
    final alertData = result?['tenpai_alert'] as Map<String, dynamic>?;
    final tingDetails = (result?['ting_details'] as List<dynamic>?) ?? [];
    final shanten = result?['shanten'] as int?;
    final pickCandidates = (result?['pick_candidates'] as List<dynamic>?) ?? [];

    final String phaseLabel = _resolvePhaseLabel(result);
    final String tacticalBadge = _resolveTacticalBadge(result);
    final String tacticalIntent = _resolveTacticalIntent(result, best);
    final bool isDrawing = result?['is_drawing'] == true;
    final moodData = result?['mood'] as Map<String, dynamic>?;

    final Widget tacticalWidget = _buildTacticalPerceptionWidget(
      phaseLabel: phaseLabel,
      tacticalBadge: tacticalBadge,
      tacticalIntent: tacticalIntent,
      isDrawing: isDrawing,
      shanten: shanten,
      mood: moodData,
    );

    // swapWidget 仅在真正的换牌阶段有效，严禁泄露至定缺、选牌或摸打对局
    final Widget? swapWidget = (isSwapPhase && swapData != null && swapData['viable'] == true)
        ? _buildSwapAdviceWidget(swapData)
        : null;
    final Widget? alertWidget = (alertData != null && alertData['alert'] == true)
        ? _buildTenpaiAlertWidget(alertData)
        : null;
    final Widget? tingRadarWidget = (shanten == 0 || tingDetails.isNotEmpty)
        ? _buildTingRadarWidget(
            tingDetails, shanten, result?['ting_chance'] as Map<String, dynamic>?)
        : null;

    final evGaugeData = result?['ev_gauge'] as Map<String, dynamic>?;
    final winEquity = (result?['win_equity'] as num?)?.toDouble();
    // advice[0] 带着解析式原值：两者逐位相等就说明 PVN 没参与（未训练态）。
    // 面板拿它与 `equity_basis` 作双重判据，才能把标题写成「纯解析式评估」
    // 而不是含糊地报一个未标定的胜率。
    final List<dynamic> _adviceList =
        (result?['advice'] as List<dynamic>?) ?? const <dynamic>[];
    final double? analyticalEquity = (_adviceList.isNotEmpty && _adviceList.first is Map)
        ? ((_adviceList.first as Map)['analytical_equity'] as num?)?.toDouble()
        : null;
    final Widget? evGaugeWidget = (evGaugeData != null && evGaugeData['badge'] != null)
        ? _buildWinEquityGaugeWidget(evGaugeData, winEquity,
            analyticalEquity: analyticalEquity)
        : null;

    final handRangesData = result?['hand_ranges'] as List<dynamic>?;
    final Widget? handRangesWidget = (handRangesData != null && handRangesData.isNotEmpty)
        ? _buildBayesianHandRangesWidget(handRangesData)
        : null;

    // ===== 1. 换牌阶段专用 UI =====
    if (isSwapPhase) {
      final msg = (result?['message'] as String?) ?? '换牌建议推演中…';
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          tacticalWidget,
          if (swapWidget != null)
            swapWidget
          else
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 7),
              decoration: BoxDecoration(
                gradient: const LinearGradient(
                  colors: [Color(0x3800695C), Color(0x22004D40)],
                ),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: const Color(0x6680CBC4), width: 0.8),
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
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          tacticalWidget,
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 7),
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                colors: [Color(0x38E65100), Color(0x22BF360C)],
              ),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: const Color(0x66FFB74D), width: 0.8),
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
          ),
        ],
      );
    }

    // ===== 3. 定缺阶段专用 UI =====
    if (isDingquePhase) {
      final msg = (result?['message'] as String?) ?? '正在推演最佳断门…';
      final topDq = activeAdvice.isNotEmpty ? activeAdvice[0] as Map<dynamic, dynamic>? : null;
      final dqTile = (topDq != null && topDq['tile'] != null) ? topDq['tile'] as String : '';
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          tacticalWidget,
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 7),
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                colors: [Color(0x38F57F17), Color(0x22E65100)],
              ),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: const Color(0x66FFD54F), width: 0.8),
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
          ),
        ],
      );
    }

    // ===== 4. 正常摸打对局：activeAdvice 为空时的友好占位 =====
    if (activeAdvice.isEmpty) {
      if (inMatch && tingRadarWidget != null) {
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            tacticalWidget,
            if (alertWidget != null) alertWidget,
            tingRadarWidget,
          ],
        );
      }
      final String hint;
      if (status == 'waiting' || (!inMatch && count == 0)) {
        hint = '等待对局接入 · 实时感知待命';
      } else if (status == 'animation' ||
          status == 'py_error' ||
          status == 'decode_error') {
        hint = '画面帧流同步中…';
      } else if (count > 0) {
        hint = '手牌解析中 · 决策核推演中…';
      } else {
        hint = '检索有效手牌中 · 边缘自适应对齐';
      }
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (inMatch) tacticalWidget,
          if (inMatch && alertWidget != null) alertWidget,
          if (inMatch && evGaugeWidget != null) evGaugeWidget,
          if (inMatch && handRangesWidget != null) handRangesWidget,
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6.5),
            decoration: BoxDecoration(
              color: Colors.white.withAlpha(6),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: Colors.white10, width: 0.5),
            ),
            child: Row(
              children: [
                const Icon(Icons.tips_and_updates_outlined, color: Color(0xFFFFD54F), size: 14),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    hint,
                    style: const TextStyle(color: Colors.white70, fontSize: 9.5),
                  ),
                ),
              ],
            ),
          ),
        ],
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

    // ---- B-P4 实时决策空白：面板输入。口径全部在 Python 侧定死，这里只渲染。----
    // 空白 A：为什么它排在次选前面。缺字段（旧 payload）就不显这一行，
    // 绝不拿 ev 自己相减编一句「更优」——那是 B-P3 刚拆掉的伪量纲。
    final String? advantageReason = top['advantage_reason'] as String?;
    final int advRank = (top['rank'] as num?)?.toInt() ?? 0;
    final int advTotal = (top['rank_total'] as num?)?.toInt() ?? 0;
    // 决胜链走到兜底层（只剩牌的索引先后）时，引擎会明说两张牌等价。
    // 这时候徽标不能再写「较优选择」：同一张卡片上面刚承认等价，
    // 下面就不得再给一个不存在的排序背书。
    final bool topTied = top['advantage'] is Map &&
        (top['advantage'] as Map)['equivalent'] == true;
    // 空白 C：危险档位的行动指令与替代方案（引擎只在主推≥中危时给替代）。
    final Map<dynamic, dynamic> topFlow =
        top['danger_flow'] is Map ? top['danger_flow'] as Map : const {};
    final String dangerLevel = (topFlow['danger_level'] as String?) ?? '';
    final String? dangerHint = (top['danger_hint'] as String?) ??
        (dangerLevel.isEmpty ? null : _dangerHintWord(dangerLevel));
    final List<dynamic> saferAlts =
        (top['safer_alternatives'] as List<dynamic>?) ?? const [];
    // 空白 D：摸牌预演。只有引擎真的跑过摸牌情景（川麻 13 张预摸路径）才有这两行，
    // 14 张帧与 std 家族没有这份数据 → 一个字符也不渲染，不伪造「若摸到…将改打…」。
    final String? predrawLine = top['predraw_line'] as String?;
    final String? predrawFlipLine = top['predraw_flip_line'] as String?;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        tacticalWidget,
        if (evGaugeWidget != null) evGaugeWidget,
        if (handRangesWidget != null) handRangesWidget,
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 7.5, vertical: 5),
          decoration: BoxDecoration(
            gradient: const LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [Color(0x3D004D40), Color(0x2400261E)],
            ),
            borderRadius: BorderRadius.circular(7),
            border: Border.all(color: const Color(0x6680CBC4), width: 0.7),
            boxShadow: const [
              BoxShadow(
                color: Color(0x20000000),
                blurRadius: 4,
                offset: Offset(0, 1.5),
              ),
            ],
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
                          color: Color(0xFFE0F2F1),
                          fontSize: 11,
                          fontWeight: FontWeight.w600,
                          letterSpacing: 0.3,
                          decoration: TextDecoration.none,
                        ),
                      ),
                      const SizedBox(width: 5),
                      TileChip(tile: topTile, size: 21),
                      if (isDingque) ...[
                        const SizedBox(width: 4),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1.5),
                          decoration: BoxDecoration(
                            gradient: const LinearGradient(
                              colors: [Color(0xFF00796B), Color(0xFF004D40)],
                            ),
                            borderRadius: BorderRadius.circular(3),
                            border: Border.all(color: const Color(0x8080CBC4), width: 0.5),
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
                      if (defenseLevel == 'SAFE' && (top['danger_flow'] is! Map)) ...[
                        const SizedBox(width: 4),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1.5),
                          decoration: BoxDecoration(
                            gradient: const LinearGradient(
                              colors: [Color(0xFF2E7D32), Color(0xFF1B5E20)],
                            ),
                            borderRadius: BorderRadius.circular(3),
                            border: Border.all(color: const Color(0x8081C784), width: 0.5),
                          ),
                          child: const Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(Icons.shield, color: Color(0xFFC8E6C9), size: 8),
                              SizedBox(width: 1.5),
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
                          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1.5),
                          decoration: BoxDecoration(
                            gradient: const LinearGradient(
                              colors: [Color(0xFFC62828), Color(0xFF8E0000)],
                            ),
                            borderRadius: BorderRadius.circular(3),
                            border: Border.all(color: const Color(0x80FF8A80), width: 0.5),
                          ),
                          child: const Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(Icons.warning, color: Color(0xFFFFCDD2), size: 8),
                              SizedBox(width: 1.5),
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
                      if (top['danger_flow'] is Map)
                        _buildDangerBadge(top['danger_flow']),
                    ],
                  ),
                  if (topUkeire > 0)
                    Flexible(
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1.5),
                        decoration: BoxDecoration(
                          color: const Color(0xFF00E676).withAlpha(25),
                          borderRadius: BorderRadius.circular(3.5),
                          border: Border.all(color: const Color(0x6600E676), width: 0.5),
                        ),
                        child: Text(
                          // 账本拆过账才直接报张数，没拆过就带 ≤ 号（上界当机会数是虚高）。
                          '进张 ${_ukeireLabel(top)}',
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: Color(0xFF69F0AE),
                            fontSize: 10,
                            fontWeight: FontWeight.bold,
                            decoration: TextDecoration.none,
                          ),
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
                    fontSize: 8.5,
                    height: 1.2,
                    decoration: TextDecoration.none,
                  ),
                ),
              ],
              // 空白 A：把「为什么是这一张」说成一句可核对的话（措辞走
              // `discards_tiebreak.advantage_note` 的决胜链层，不是 ev 减法）。[P4A]
              if (advantageReason != null && advantageReason.isNotEmpty) ...[
                const SizedBox(height: 2.5),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (advRank <= 1 && advTotal > 1)
                      Padding(
                        padding: const EdgeInsets.only(right: 3),
                        child: Container(
                          padding:
                              const EdgeInsets.symmetric(horizontal: 3, vertical: 0.5),
                          decoration: BoxDecoration(
                            color: const Color(0xFF00E676).withAlpha(28),
                            borderRadius: BorderRadius.circular(2.5),
                          ),
                          child: Text(
                            topTied ? '并列 · 任选其一' : '推荐 · 较优选择',
                            style: const TextStyle(
                              color: Color(0xFF69F0AE),
                              fontSize: 7.5,
                              fontWeight: FontWeight.bold,
                              decoration: TextDecoration.none,
                            ),
                          ),
                        ),
                      ),
                    Expanded(
                      child: Text(
                        advantageReason,
                        style: TextStyle(
                          color: Colors.white.withAlpha(175),
                          fontSize: 8,
                          height: 1.15,
                          decoration: TextDecoration.none,
                        ),
                      ),
                    ),
                  ],
                ),
              ],
              // 空白 C：每一档危险度都跟一句可执行的话。旧面板只在 high 以上显红，
              // 看完只知道「有点危」而不知道该不该改牌；中间档从此不再沉默。[P4C]
              if (dangerHint != null && dangerHint.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(
                  dangerHint,
                  style: TextStyle(
                    color: _dangerHintColor(dangerLevel),
                    fontSize: 8,
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
                    fontSize: 8,
                    height: 1.1,
                    decoration: TextDecoration.none,
                  ),
                ),
              ],
              if (sorted.length > 1) ...[
                const SizedBox(height: 3),
                Wrap(
                  spacing: 4,
                  runSpacing: 2.5,
                  children: [
                    for (int i = 1; i < sorted.length && i < 3; i++)
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1.5),
                        decoration: BoxDecoration(
                          color: Colors.white.withAlpha(12),
                          borderRadius: BorderRadius.circular(3),
                          border: Border.all(color: Colors.white12, width: 0.5),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(
                              // 这颗标签说的是「本位与它上面那一位」的关系：上面那张
                              // 刚承认与它等价（决胜链只剩兜底）时，这里就不能再叫
                              // 「次选」——那是给一个不存在的优劣排序背书。不取本位
                              // 自己向上的判据：那个对手可能根本没被渲染（只显前两位），
                              // 用户会看到一个不知与谁并列的标签。
                              (sorted[i - 1]['advantage'] is Map &&
                                      (sorted[i - 1]['advantage'] as Map)['equivalent'] ==
                                          true)
                                  ? '并列'
                                  : '次选',
                              style: TextStyle(
                                color: Colors.white.withAlpha(160),
                                fontSize: 7.5,
                                fontWeight: FontWeight.w500,
                                decoration: TextDecoration.none,
                              ),
                            ),
                            const SizedBox(width: 2.5),
                            TileChip(tile: (sorted[i]['tile'] ?? '') as String, size: 15),
                            const SizedBox(width: 2.5),
                            Text(
                              _ukeireLabel(sorted[i]),
                              style: const TextStyle(
                                color: Color(0xFF69F0AE),
                                fontSize: 8,
                                fontWeight: FontWeight.bold,
                                decoration: TextDecoration.none,
                              ),
                            ),
                            if (sorted[i]['danger_flow'] is Map)
                              _buildDangerBadge(sorted[i]['danger_flow'], mini: true),
                          ],
                        ),
                      ),
                  ],
                ),
              ],
              // 空白 C 替代方案：只在主推已到中危及以上、且候选里确实有安全/微危牌时
              // 出现（判定在 engine.annotate_advice_decisions）。列牌不列“更安全”的承诺。[P4C]
              if (saferAlts.isNotEmpty) ...[
                const SizedBox(height: 2.5),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    Text(
                      '替代方案',
                      style: TextStyle(
                        color: Colors.white.withAlpha(130),
                        fontSize: 7.5,
                        decoration: TextDecoration.none,
                      ),
                    ),
                    const SizedBox(width: 3),
                    Expanded(
                      child: Wrap(
                        spacing: 3.5,
                        runSpacing: 2,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          for (final a in saferAlts)
                            Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                TileChip(tile: (a['tile'] ?? '') as String, size: 14),
                                const SizedBox(width: 1.5),
                                Text(
                                  (a['danger_band'] ?? '') as String,
                                  style: const TextStyle(
                                    color: Color(0xFFA5D6A7),
                                    fontSize: 7.5,
                                    decoration: TextDecoration.none,
                                  ),
                                ),
                              ],
                            ),
                        ],
                      ),
                    ),
                  ],
                ),
              ],
              // 空白 D：摸牌预演——预摸只取前几种高概率摸牌，说清「哪些摸牌会改主意」。
              // 没有模拟数据时这两行根本不存在，面板也就不会凭空说一句「若摸到…」。[P4D]
              if (predrawLine != null && predrawLine.isNotEmpty) ...[
                const SizedBox(height: 2.5),
                Text(
                  '摸牌预演 · $predrawLine',
                  style: TextStyle(
                    color: Colors.white.withAlpha(150),
                    fontSize: 7.5,
                    height: 1.15,
                    decoration: TextDecoration.none,
                  ),
                ),
              ],
              if (predrawFlipLine != null && predrawFlipLine.isNotEmpty) ...[
                const SizedBox(height: 1.5),
                Text(
                  predrawFlipLine,
                  style: const TextStyle(
                    color: Color(0xFFFFD54F),
                    fontSize: 7.8,
                    fontWeight: FontWeight.w600,
                    height: 1.15,
                    decoration: TextDecoration.none,
                  ),
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
      // 这里不再各算一份 dingque/swap/pick 阶段标志：手牌块的呈现闸门已改成只看
      // 「引擎是否报出了牌」（见下方手牌块），算了却不读只会变成下一条没接线的死读数。
      // 迷你胶囊那条链有它自己的同名局部量，与本作用域无关。
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
                colors: [Color(0xF8151820), Color(0xF80D0F14)],
              ),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: Colors.white.withAlpha(32), width: 0.8),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withAlpha(140),
                  blurRadius: 12,
                  offset: const Offset(0, 4),
                ),
              ],
            ),
            padding: const EdgeInsets.fromLTRB(7.5, 6.5, 7.5, 6.5),
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
                          width: 44,
                          height: 4,
                          margin: const EdgeInsets.only(bottom: 5),
                          decoration: BoxDecoration(
                            color: Colors.white.withAlpha(60),
                            borderRadius: BorderRadius.circular(2),
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
                                  GestureDetector(
                                    onTap: () {
                                      setState(() {
                                        _showModeSelector = !_showModeSelector;
                                      });
                                    },
                                    behavior: HitTestBehavior.opaque,
                                    child: Row(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        Flexible(
                                          child: Text(
                                            // 【v4 玩法可见】优先显示引擎回传的实际玩法名：
                                            // 屏上规则真身由引擎 mode 驱动，标题与引擎同源
                                            // 才能证明切换已生效（本地 selectedMode 仅作回退）。
                                            (result?['mode_name'] as String?)?.isNotEmpty == true
                                                ? result!['mode_name'] as String
                                                : GameMode.label(selectedMode),
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
                                        const SizedBox(width: 2),
                                        const Icon(
                                          Icons.arrow_drop_down_rounded,
                                          color: Color(0xFFFFD54F),
                                          size: 15,
                                        ),
                                      ],
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
                                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2.5),
                                margin: const EdgeInsets.only(right: 5),
                                decoration: BoxDecoration(
                                  gradient: const LinearGradient(
                                    colors: [Color(0xFFE65100), Color(0xFFC62828)],
                                  ),
                                  borderRadius: BorderRadius.circular(4),
                                  border: Border.all(color: const Color(0x99FFB74D), width: 0.6),
                                  boxShadow: const [
                                    BoxShadow(
                                      color: Color(0x30E65100),
                                      blurRadius: 3,
                                      offset: Offset(0, 1),
                                    ),
                                  ],
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
                                  color: Colors.white.withAlpha(20),
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
                // 假活/断流时淡化数据区（屏上数据已非实时）：旧 0.45 过重，正常数据
                // 也看不清、被误认成窗口故障发黑；0.72 足以传达“非实时”不伤可读性。
                Expanded(
                  child: Opacity(
                    opacity: signalLost ? 0.72 : 1.0,
                    child: SingleChildScrollView(
                    controller: _panelScrollController,
                    physics: const AlwaysScrollableScrollPhysics(
                      parent: BouncingScrollPhysics(),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        // 1. 核心建议（出牌决策）；空态提示由 _adviceSection 统一
                        //    给出（waiting/无牌/识别中各有贴切文案），不另加占位，
                        //    避免同义/矛盾文案叠加。
                        _adviceSection(advice, best, count),
                        const SizedBox(height: 5),
                        // 2. 当前手牌。闸门只看「引擎确实报出了牌」。
                        //    旧口径额外要求 inMatch 或某个特殊阶段，于是在「读到 2~3 张」
                        //    这类低张数帧上把整块手牌藏起来（count<4 ⇒ inMatch=false，
                        //    而三个阶段标志又都不成立），用户看到的就是「牌明明在屏幕上，
                        //    面板却空着甚至显示等待开局」。引擎出口现已保证
                        //    status=='waiting' 时必定没报出牌，这里只需排除它，
                        //    不再自行二次否决引擎的读数。
                        if (hand.isNotEmpty && count > 0 && status != 'waiting') ...[
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 5),
                            decoration: BoxDecoration(
                              color: const Color(0x3312151B),
                              borderRadius: BorderRadius.circular(10),
                              border: Border.all(color: Colors.white.withAlpha(18), width: 0.6),
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
                          _remainingMatrixSection(
                            result?['remaining_matrix'] as Map<String, dynamic>?,
                            result?['tile_ledger'] as Map<String, dynamic>?,
                          ),
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
          // 瞬时事件胶囊：只在有事件的那 ~2.5s 存在，Positioned 不占布局。
          if (_eventFlash != null) _eventFlashCapsule(),
          if (_showModeSelector)
            _buildModeSelectorOverlay(),
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
        // 收起/展开的轻量淡入淡出（内容均在 Expanded+滚动区内，小窗也不会 overflow）。
        child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 160),
          switchInCurve: Curves.easeOut,
          switchOutCurve: Curves.easeIn,
          child: KeyedSubtree(
            key: ValueKey<bool>(panelVisible),
            child: current,
          ),
        ),
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
