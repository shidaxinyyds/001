import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
import 'package:auto_vision/config_store.dart';
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

  const TileChip({
    super.key,
    required this.tile,
    this.size = 26,
    this.dim = false,
    this.dead = false,
    this.isDrawing = false,
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

    return Opacity(
      opacity: dim ? 0.45 : 1.0,
      child: Container(
        width: size,
        height: size * 1.18,
        margin: const EdgeInsets.only(right: 2),
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
        child: Stack(
          clipBehavior: Clip.none,
          children: [
            FittedBox(
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
            if (isDrawing)
              Positioned(
                left: -4,
                top: -4,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 2.5, vertical: 0.5),
                  decoration: BoxDecoration(
                    color: const Color(0xFF2E7D32),
                    borderRadius: BorderRadius.circular(3),
                    border: Border.all(color: const Color(0xFFFFD54F), width: 0.8),
                  ),
                  child: const Text(
                    '摸',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 7,
                      fontWeight: FontWeight.bold,
                      height: 1.0,
                    ),
                  ),
                ),
              ),
            if (dead)
              Positioned(
                right: -4,
                top: -4,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 3, vertical: 1),
                  decoration: BoxDecoration(
                    color: const Color(0xFFE0F2F1), // 青绿浅底，与主色统一
                    borderRadius: BorderRadius.circular(3),
                    border: Border.all(color: const Color(0xFF00695C), width: 0.5),
                  ),
                  child: const Text(
                    '绝',
                    style: TextStyle(
                      color: Color(0xFF00695C),
                      fontSize: 7,
                      fontWeight: FontWeight.bold,
                      height: 1.0,
                    ),
                  ),
                ),
              ),
          ],
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
  const HandChipRow({
    super.key,
    required this.hand,
    this.chipSize = 24,
    this.deadTiles,
    this.drawingTile,
  });

  @override
  Widget build(BuildContext context) {
    final tiles = _mpszToTiles(hand);
    if (tiles.isEmpty) {
      return const SizedBox.shrink();
    }
    return Wrap(
      spacing: 1,
      runSpacing: 3,
      children: tiles
          .map((t) => TileChip(
                tile: t,
                size: chipSize,
                dead: deadTiles?.contains(t) ?? false,
                isDrawing: drawingTile != null && t == drawingTile,
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

/// 切分手牌为 m/p/s/z 各一组、组内按数字排序（人眼好读）。
Map<String, List<String>> _groupHand(String hand) {
  final tiles = _mpszToTiles(hand);
  final groups = <String, List<String>>{
    'm': <String>[],
    'p': <String>[],
    's': <String>[],
    'z': <String>[],
  };
  for (final t in tiles) {
    final s = t[1];
    if (groups.containsKey(s)) groups[s]!.add(t);
  }
  for (final k in groups.keys) {
    groups[k]!.sort((a, b) => int.parse(a[0]).compareTo(int.parse(b[0])));
  }
  return groups;
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

  // 当前玩法：悬浮窗写入共享文件，Python 引擎每帧读取。默认四麻。
  String selectedMode = '4p';

  // 防封号：建议做人类式延迟显示。手牌/牌河随 result 立即刷新，
  // 仅「建议」段经 _shownAdvice/_shownBest 延迟（随机 180–420ms）呈现，
  // 避免每帧瞬时刷新建议带来的「机械/外挂」节奏特征。
  bool _antiBan = false;
  List<dynamic> _shownAdvice = const [];
  String _shownBest = '';
  Timer? _adviceTimer;

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

  // ── 紧凑布局尺寸常量 ──
  static const double _kTitleBarH = 28; // 顶部栏高度

  static const double minPanelW = 190;
  static const double maxPanelW = 380;
  static const double minPanelH = 120;
  static const double maxPanelH = 550;

  double _minPanelH() => minPanelH;

  // 缩放中：此期间关闭原生拖动，避免"拖把手时整窗跟着跑"
  bool _draggingResize = false;
  bool _resizeInFlight = false;

  // ── 标题栏拖拽控制（解决反向与抖动跳屏）──
  double _overlayBaseX = 0.0;
  double _overlayBaseY = 0.0;
  bool _isDraggingTitle = false;
  bool _moveInFlight = false;
  OverlayPosition? _pendingMove;

  void _sendMoveOverlay(double x, double y) {
    if (_moveInFlight) {
      _pendingMove = OverlayPosition(x, y);
      return;
    }
    _moveInFlight = true;
    FlutterOverlayWindow.moveOverlay(OverlayPosition(x, y))
        .catchError((_) => false)
        .whenComplete(() {
      _moveInFlight = false;
      final next = _pendingMove;
      if (next != null) {
        _pendingMove = null;
        _sendMoveOverlay(next.x, next.y);
      }
    });
  }

  // 牌河折叠态。常驻可见（默认 true），但用户可手动折叠/展开以释放空间。
  // 状态在弹窗生命周期内持久化：用户收起 → 重新展开会保持上一次选择，
  // 不强制每次都重置为展开。
  // 牌河折叠态：常驻显示在弹窗，常规是收起状态（释放空间，点击展开）
  bool _discardExpanded = false;

  @override
  void initState() {
    super.initState();

    // 防封号：读取调试页写下的开关（悬浮窗独立运行，启动时读一次即可）。
    DebugConfig.load().then((c) {
      if (mounted) setState(() => _antiBan = c.antiBan);
    });

    // 监听原生层通过本地 socket 发来的每帧分析结果（端口 12345 与 ImageProcessor 发送端一致）。
    // 即便 socket 启动失败也不能让悬浮窗引擎崩溃（否则按钮永远不渲染），因此整体 try/catch 兜底。
    try {
      Server(
        callback: (data) {
          final json = parseEngineResult(data);
          if (json == null) return;
          if (mounted) {
            setState(() {
              result = json;
              if (json['status'] == 'waiting') {
                _shownAdvice = const [];
                _shownBest = '';
              }
              ready = true;
              // 引擎已读到玩法文件并回传，与本地选择不一致时以回传为准，保持两端同步。
              // 引擎回传的 mode 与本地一致即可，不再校验 kModeOptions。
              final m = json['mode'];
              if (m is String && m != selectedMode) {
                selectedMode = m;
              }
            });
            // 防封号：建议做人类式延迟显示（手牌/牌河已随 result 立即刷新）。
            if (json['status'] != 'waiting') {
              _applyAdviceDelay(json);
            }
          }
          _maybeShareStatus(json);
        },
        host: "127.0.0.1",
        port: 12345,
      );
    } catch (e) {
      print("悬浮窗分析服务初始化失败（不影响按钮显示）：$e");
    }

    // 插件 showOverlay 时把 width/height 当作物理像素使用（未做 dp 转换），
    // 56dp 的按钮在 3 倍密度屏上会被画成 56 像素（约 7mm，几乎看不见）。
    // 因此这里由悬浮窗自身按 dp 重新设定一次尺寸。
    // 注意：resizeOverlay 走的是悬浮窗引擎的通道，只有悬浮窗自己调用才生效。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final double w = _capsuleMode ? _kCapsuleW : collapsed;
      final double h = _capsuleMode ? _kCapsuleH : collapsed;
      _ensureSize(w, h);
    });

    // 玩法文件已改由主页通过 Java MethodChannel 写入；这里不再读 dart:io 文件。
    // （注：本 Flutter 端的 selectedMode 仍保留，仅用于把当前模式透传给主界面。）
  }

  @override
  void dispose() {
    _adviceTimer?.cancel();
    super.dispose();
  }

  // 防封号：建议做人类式延迟显示。关闭开关时立即呈现（与以往一致）；
  // 开启时随机延迟 180–420ms 再刷新 _shownAdvice/_shownBest，
  // 让建议出现节奏更接近人类而非每帧瞬时刷新。手牌/牌河仍随 result 立即刷新。
  void _applyAdviceDelay(Map<String, dynamic> json) {
    final List<dynamic> advice =
        (json['advice'] ?? const []) as List<dynamic>;
    final String best = (json['best'] ?? '') as String;
    _adviceTimer?.cancel();
    if (!_antiBan) {
      setState(() {
        _shownAdvice = advice;
        _shownBest = best;
      });
      return;
    }
    final int delay = 180 + Random().nextInt(241); // [180, 420]
    _adviceTimer = Timer(Duration(milliseconds: delay), () {
      if (mounted) {
        setState(() {
          _shownAdvice = advice;
          _shownBest = best;
        });
      }
    });
  }

  // 只在识别内容真正变化时回传一次摘要给主 App，
  // 让主界面也能确认"后端确实在识别"，而不是每帧刷屏。
  String _lastSharedKey = '';

  // 弹窗顶部状态条：**恒定**显示「● 实时」。
  //
  // 不再切「等待画面 / x.xs 无更新」。原因不只是观感：
  // 悬浮窗每几百毫秒就收到一帧，3 秒超时判据本身就在临界值附近抖，
  // 状态条会忽而「实时」忽而「无更新」，用户据此以为识别在断断续续地挂。
  // 引擎侧已经保证 hand 一旦建立永不为空（多重集稳定器），界面上有没有
  // 内容才是用户真正关心的，这条状态条只需要传达"本窗在实时工作"。
  //
  // 同时移除了原本每秒一次的「连接心跳」定时器：它唯一的作用就是驱动这条
  // 状态条重绘，而每秒 setState 会把整个悬浮窗 Widget 树重建一遍，在
  // 覆盖层里是实打实的额外开销。状态条恒定为常量后它就没有任何意义了。
  Widget _statusBanner() {
    return const Padding(
      padding: EdgeInsets.only(left: 4),
      child: Text(
        '● 实时',
        style: TextStyle(
          color: Color(0xFF80CBC4), // 青绿 200
          fontSize: 9,
          letterSpacing: 0.3,
        ),
      ),
    );
  }

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

  /// 拖动缩放把手时实时调整尺寸。
  ///
  /// 关键：不能"忙时直接丢弃"。resizeOverlay 走 MethodChannel 到原生，一次来回要几十毫秒，
  /// 手指快速拖动时绝大多数调用都会被丢弃，窗口只能零零散散地追上去 —— 表现就是剧烈抖动。
  /// 改成"最后一次的尺寸一定会被应用"：忙时先记下来，空闲后立刻补上，
  /// 这样窗口会平滑地收敛到手指停下的位置。
  void _resizeLive(double w, double h) {
    if (_resizeInFlight) {
      _pendingW = w;
      _pendingH = h;
      return;
    }
    _resizeInFlight = true;
    FlutterOverlayWindow.resizeOverlay(w.toInt(), h.toInt(), false)
        .catchError((Object _) => null)
        .whenComplete(() {
      _resizeInFlight = false;
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
      // 展开分析面板时关闭原生全局拖动，释放触摸手势给 Flutter 内容区顺畅滚动
      await _ensureSize(panelW, panelH, drag: false);
    } else {
      final double w = _capsuleMode ? _kCapsuleW : collapsed;
      final double h = _capsuleMode ? _kCapsuleH : collapsed;
      // 收起态开启原生拖动，方便随时拖动胶囊/按钮到屏幕任意边缘
      await _ensureSize(w, h, drag: true);
    }
  }





  // 牌河段容器：展开时占弹性高度（Expanded），折叠时收回成一行标题、
  // 不占弹性高度，把纵向空间让给「建议 / 手牌」两段 —— 与识别区域收起行为一致。
  Widget _discardBlock({
    required String discards,
    required int discardCount,
    required String hand,
  }) {
    if (discards.isEmpty || discardCount == 0) {
      return const SizedBox.shrink();
    }
    return Container(
      margin: const EdgeInsets.only(bottom: 2),
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
                '牌河 ($discardCount张)',
                style: const TextStyle(
                  color: Colors.white70,
                  fontSize: 9.5,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 0.3,
                ),
              ),
              const Spacer(),
              GestureDetector(
                onTap: () => setState(() => _discardExpanded = !_discardExpanded),
                behavior: HitTestBehavior.opaque,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                  child: Text(
                    _discardExpanded ? '收起 ▾' : '展开 ▸',
                    style: const TextStyle(
                      color: Color(0xFF80CBC4),
                      fontSize: 9.5,
                    ),
                  ),
                ),
              ),
            ],
          ),
          if (_discardExpanded) ...[
            const SizedBox(height: 3),
            _discardSection(discards, discardCount, hand: hand),
          ],
        ],
      ),
    );
  }

  // 牌河内容：按花色分组的 chip 列表，绝张牌自动标灰底+青绿描边+"绝"标。
  // 牌河（所有玩家打出的牌）展示，按花色分组，与手牌同款 chip
  Widget _discardSection(
    String discards,
    int discardCount, {
    required String hand,
  }) {
    if (discards.isEmpty || discardCount == 0) {
      // 空状态占位：让用户知道"按钮没坏，是因为还没识别到牌河"。
      // 用中性灰文字 + 字号 10，绝不引红/橙/琥珀色。
      return Padding(
        padding: const EdgeInsets.fromLTRB(8, 6, 8, 8),
        child: Text(
          '等识别到各家打出的牌后在这里展示…',
          style: TextStyle(
            color: Colors.white.withAlpha(85),
            fontSize: 10,
            fontStyle: FontStyle.italic,
          ),
        ),
      );
    }
    final grouped = _groupHand(discards);
    final tilesAll = grouped.values.fold<int>(0, (s, l) => s + l.length);
    if (tilesAll == 0) return const SizedBox.shrink();
    // 计算绝张：手牌 + 牌河 累计 ≥ 4 张的牌型（这是 App 自动算出来的、肉眼看不出来的高价值信息）。
    final dead = _computeDeadTiles(hand, discards);
    final order = ['m', 'p', 's', 'z'];
    return Container(
      padding: const EdgeInsets.fromLTRB(8, 2, 8, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final k in order)
            if (grouped[k]!.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    SizedBox(
                      width: 14,
                      child: Text(
                        k == 'z'
                            ? '字'
                            : (k == 'm' ? '万' : (k == 'p' ? '筒' : '条')),
                        style: TextStyle(
                          color: k == 'z'
                              ? const Color(0xFFB0BEC5)
                              : (k == 'm'
                                  ? const Color(0xFF1E6B7A)
                                  : (k == 'p'
                                      ? const Color(0xFF1E6B7A)
                                      : const Color(0xFF66BB6A))),
                          fontSize: 10,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                    Expanded(
                      child: HandChipRow(
                        hand: grouped[k]!.join(),
                        chipSize: 18,
                        deadTiles: dead,
                      ),
                    ),
                  ],
                ),
              ),
        ],
      ),
    );
  }

  // 绝张计算：手牌 + 牌河 累计 ≥ 4 张的牌型（mpsz 形式）视为"绝张"。
  // 绝张 = 该牌型 4 张全部可见（手牌里 + 牌河里），任何人都凑不出该牌。
  // 对自己的意义：① 该牌不可能凑成对子/刻子，可作为优先弃牌；② 别人也几乎不可能拿它和牌 → 安全牌。
  Set<String> _computeDeadTiles(String hand, String discards) {
    final counts = <String, int>{};
    for (final t in _mpszToTiles(hand)) {
      counts[t] = (counts[t] ?? 0) + 1;
    }
    for (final t in _mpszToTiles(discards)) {
      counts[t] = (counts[t] ?? 0) + 1;
    }
    return counts.entries.where((e) => e.value >= 4).map((e) => e.key).toSet();
  }

  // 缩放把手：右下角，可自由改变弹窗长宽
  Widget _resizeHandle() {
    return Positioned(
      right: 0,
      bottom: 0,
      child: Listener(
        // 按下瞬间就关掉原生拖动，否则拖动把手时整个窗口会跟着移动
        onPointerDown: (_) {
          setState(() => _draggingResize = true);
          _resizeLive(panelW, panelH);
        },
        onPointerMove: (PointerMoveEvent e) {
          setState(() {
            // 显式 toDouble()：旧版 Dart 的 clamp 返回 num，直接赋给 double 会报错
            panelW = (panelW + e.delta.dx).clamp(minPanelW, maxPanelW).toDouble();
            panelH = (panelH + e.delta.dy).clamp(_minPanelH(), maxPanelH).toDouble();
          });
          _resizeLive(panelW, panelH);
        },
        onPointerUp: (_) async {
          setState(() => _draggingResize = false);
          // 缩放结束，面板保持打开，drag 保持 false（由内部平滑滚动与标题栏拖动）
          await _ensureSize(panelW, panelH, drag: false);
        },
        child: SizedBox(
          width: 28,
          height: 28,
          child: CustomPaint(
            painter: _GripPainter(
              // 用中性浅灰替代琥珀 —— 拖动把手不应像"警告"。
              color: _draggingResize
                  ? Colors.white
                  : Colors.white.withAlpha(140),
            ),
          ),
        ),
      ),
    );
  }

  // 收起态：屏幕上只保留一个圆形悬浮按钮（内含麻将牌图标）
  Widget _floatingButton({double size = collapsed}) {
    final shanten = result?['shanten'];
    return GestureDetector(
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
    final shanten = result?['shanten'];
    final adviceList = _shownAdvice.isNotEmpty ? _shownAdvice : (result?['advice'] as List<dynamic>? ?? const []);
    final topAdvice = adviceList.isNotEmpty ? adviceList[0] as Map<dynamic, dynamic>? : null;
    final String bestTile = _shownBest.isNotEmpty ? _shownBest : (result?['best'] ?? '');
    final String tileStr = (topAdvice != null && topAdvice['tile'] != null) ? topAdvice['tile'] as String : bestTile;
    final int ukeire = (topAdvice != null && topAdvice['ukeire'] is int) ? topAdvice['ukeire'] as int : 0;
    final String? reason = (topAdvice != null && topAdvice['reason'] is String) ? topAdvice['reason'] as String : null;

    return GestureDetector(
      onTap: _togglePanel,
      behavior: HitTestBehavior.opaque,
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
            if (tileStr.isNotEmpty) ...[
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
            ] else ...[
              Flexible(
                child: Text(
                  result?['status'] == 'waiting' ? '等待对局…' : '实时分析…',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: Colors.white38,
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
                  if (cnt == 0) {
                    numColor = Colors.white24;
                    cellBg = Colors.white.withAlpha(4);
                  } else if (cnt == 1) {
                    numColor = const Color(0xFFFFB74D); // 仅剩1张预警
                    cellBg = const Color(0x33FFB74D);
                  } else {
                    numColor = const Color(0xFF81C784); // 2~4张充足
                    cellBg = const Color(0x2281C784);
                  }

                  return Container(
                    width: 23,
                    height: 25,
                    decoration: BoxDecoration(
                      color: cellBg,
                      borderRadius: BorderRadius.circular(3),
                      border: Border.all(
                        color: cnt == 0
                            ? Colors.white10
                            : (cnt == 1 ? const Color(0x66FFB74D) : const Color(0x4481C784)),
                        width: 0.5,
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
      const zNames = ['东', '南', '西', '北', '白', '发', '中'];
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
                mainAxisAlignment: MainAxisAlignment.start,
                children: List.generate(7, (idx) {
                  final int cnt = (counts.length > idx && counts[idx] is int) ? counts[idx] as int : 0;
                  final Color numColor;
                  final Color cellBg;
                  final bool isHongzhong = (idx == 6);
                  if (cnt == 0) {
                    numColor = Colors.white24;
                    cellBg = Colors.white.withAlpha(4);
                  } else if (cnt == 1) {
                    numColor = const Color(0xFFFFB74D);
                    cellBg = const Color(0x33FFB74D);
                  } else {
                    numColor = isHongzhong ? const Color(0xFFFF8A80) : const Color(0xFF81C784);
                    cellBg = isHongzhong ? const Color(0x33FF5252) : const Color(0x2281C784);
                  }

                  return Padding(
                    padding: const EdgeInsets.only(right: 3.5),
                    child: Container(
                      width: 23,
                      height: 25,
                      decoration: BoxDecoration(
                        color: cellBg,
                        borderRadius: BorderRadius.circular(3),
                        border: Border.all(
                          color: cnt == 0
                              ? Colors.white10
                              : (cnt == 1 ? const Color(0x66FFB74D) : (isHongzhong ? const Color(0x88FF5252) : const Color(0x4481C784))),
                          width: 0.5,
                        ),
                      ),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Text(
                            zNames[idx],
                            style: TextStyle(
                              color: isHongzhong ? const Color(0xFFFF5252) : labelColor.withAlpha(cnt == 0 ? 70 : 220),
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

  // ---------- 内容区小部件 ----------

  Widget _handSection(String hand, int count) {
    if (hand.isEmpty || count == 0) {
      return const SizedBox.shrink();
    }
    final bool partial = count > 0 && count < 13;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (partial)
            const Padding(
              padding: EdgeInsets.only(bottom: 3),
              child: Text(
                '手牌识别中…稳定后自动补齐',
                style: TextStyle(
                  color: Colors.white60,
                  fontSize: 9,
                  decoration: TextDecoration.none,
                ),
              ),
            ),
          HandChipRow(
            hand: hand,
            chipSize: 20,
            drawingTile: result?['is_drawing'] == true
                ? (result?['drawing_tile'] as String?)
                : null,
          ),
        ],
      ),
    );
  }

  Widget _adviceSection(List<dynamic> advice, String best, int count) {
    if (advice.isEmpty) {
      final status = result?['status'] as String? ?? '';
      final String hint;
      if (status == 'waiting') {
        hint = '等待牌局开始（进入游戏后自动识别）';
      } else if (count > 0) {
        hint = '手牌识别中，正在推演建议…';
      } else {
        hint = '等待手牌入镜（请勿遮挡底部手牌）';
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

    final sorted = [...advice];
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

    return Container(
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
                    const SizedBox(width: 5),
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
                          fontSize: 8.5,
                          fontWeight: FontWeight.bold,
                          decoration: TextDecoration.none,
                        ),
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
                        '${sorted[i]['ukeire'] ?? 0}张',
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
    );
  }

  @override
  Widget build(BuildContext context) {
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
      final String discards = (result?['discards'] ?? '') as String;
      final int discardCount = (result?['discard_count'] ?? 0) as int;

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
                // 顶部控制栏：精简干净，杜绝溢出，无多余干扰
                SizedBox(
                  height: _kTitleBarH,
                  child: Row(
                    children: [
                      const MahjongTileIcon(size: 15),
                      const SizedBox(width: 5),
                      Expanded(
                        child: GestureDetector(
                          behavior: HitTestBehavior.opaque,
                          onPanStart: (details) async {
                            _isDraggingTitle = true;
                            try {
                              final pos = await FlutterOverlayWindow.getOverlayPosition();
                              _overlayBaseX = pos.x;
                              _overlayBaseY = pos.y;
                            } catch (_) {}
                          },
                          onPanUpdate: (details) {
                            if (!_isDraggingTitle) return;
                            // 注意：showOverlay 采用了 topRight 对齐。
                            // 在 Android WindowManager (Gravity.RIGHT) 下，params.x 代表距屏幕右边缘的距离。
                            // 手指往左拖动 (dx < 0)，距右边缘变大，因此 params.x 需增加（即 -= dx）。
                            // 手指往右拖动 (dx > 0)，距右边缘变小，因此 params.x 需减小（即 -= dx）。
                            _overlayBaseX -= details.delta.dx;
                            _overlayBaseY += details.delta.dy;

                            final mediaQuery = MediaQuery.of(context);
                            final screenW = mediaQuery.size.width;
                            final screenH = mediaQuery.size.height;
                            final maxX = max(0.0, screenW - panelW);
                            final maxY = max(0.0, screenH - panelH);
                            final targetX = _overlayBaseX.clamp(0.0, maxX);
                            final targetY = _overlayBaseY.clamp(0.0, maxY);

                            _sendMoveOverlay(targetX, targetY);
                          },
                          onPanEnd: (_) => _isDraggingTitle = false,
                          onPanCancel: () => _isDraggingTitle = false,
                          child: const Row(
                            children: [
                              Text(
                                '雀神助手',
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                  color: Colors.white,
                                  fontWeight: FontWeight.bold,
                                  fontSize: 12,
                                  letterSpacing: 0.3,
                                  decoration: TextDecoration.none,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                      _statusBanner(),
                      const SizedBox(width: 6),
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
                const SizedBox(height: 5),
                // 核心卡片滚动流：全包裹于 SingleChildScrollView，彻底杜绝 RenderFlex overflow
                Expanded(
                  child: SingleChildScrollView(
                    physics: const BouncingScrollPhysics(
                      parent: AlwaysScrollableScrollPhysics(),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        // 1. 核心建议（出牌决策）
                        _adviceSection(advice, best, count),
                        const SizedBox(height: 5),
                        // 2. 当前手牌
                        if (hand.isNotEmpty && count > 0) ...[
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
                        // 3. 全场记牌器 (剩余活牌)
                        _remainingMatrixSection(result?['remaining_matrix'] as Map<String, dynamic>?),
                        const SizedBox(height: 5),
                        // 4. 牌河弃牌（框架常驻显示在弹窗，常规收起状态）
                        _discardBlock(discards: discards, discardCount: discardCount, hand: hand),
                      ],
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
