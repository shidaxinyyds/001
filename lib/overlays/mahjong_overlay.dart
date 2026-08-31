import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
import 'package:realtime_mahjong_trainer/overlays/tile_labels.dart';
import 'package:realtime_mahjong_trainer/server.dart';

// 解析原生层发来的分析结果：前 10('\n') 之前为 JSON，之后为 PNG 预览图字节。
// 预览图当前不在界面上展示（仅保留字节以备扩展），因此这里不做解码，
// 避免每帧在 UI 线程上解码图片造成卡顿。
Map<String, dynamic>? parseEngineResult(List<int> b) {
  final int sepIndex = b.indexOf(10); // 对应 '\n'
  if (sepIndex <= 0) {
    return null;
  }
  try {
    return jsonDecode(String.fromCharCodes(b.sublist(0, sepIndex)))
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

  const TileChip({super.key, required this.tile, this.size = 26, this.dim = false});

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
      // 字牌：所有字牌统一深灰（"中" 也用深灰，不再红字 —— 红字会被误认为错误状态）。
      // 麻将牌"中"本身是红字，但此处遵循"界面任何状态下都不显示红色字符"的约束。
      charColor = const Color(0xFF202124);
    }

    return Opacity(
      opacity: dim ? 0.45 : 1.0,
      child: Container(
        width: size,
        height: size * 1.18,
        margin: const EdgeInsets.only(right: 2),
        decoration: BoxDecoration(
          color: const Color(0xFFF7F3E8),
          borderRadius: BorderRadius.circular(3),
          border: Border.all(color: const Color(0xFFB7A98F), width: 0.6),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withAlpha(40),
              blurRadius: 1,
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
    );
  }
}

/// 一排手牌 chip（最多 14 张）。多余空间自动 wrap。
class HandChipRow extends StatelessWidget {
  final String hand; // mpsz 形式
  final double chipSize;
  const HandChipRow({super.key, required this.hand, this.chipSize = 24});

  @override
  Widget build(BuildContext context) {
    final tiles = _mpszToTiles(hand);
    if (tiles.isEmpty) {
      return const SizedBox.shrink();
    }
    return Wrap(
      spacing: 1,
      runSpacing: 3,
      children: tiles.map((t) => TileChip(tile: t, size: chipSize)).toList(),
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

/// "推荐打这张"卡片。打 [牌] → 进张 N 张。
class AdviceCard extends StatelessWidget {
  final String tile;
  final int ukeire;
  final bool best;
  const AdviceCard({
    super.key,
    required this.tile,
    required this.ukeire,
    this.best = false,
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
          RichText(
            text: TextSpan(
              children: [
                TextSpan(
                  text: '进张 ',
                  style: const TextStyle(color: Colors.white70, fontSize: 11),
                ),
                TextSpan(
                  text: '$ukeire',
                  style: TextStyle(
                    // 用浅蓝替代琥珀 —— 不再出现任何琥珀/橙黄色（与琥珀相邻的
                    // 色域容易被误认成红色，且琥珀与"错误/警告"语义过近）。
                    color: best ? Colors.lightGreenAccent : Colors.lightBlueAccent,
                    fontSize: 14,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                TextSpan(
                  text: ' 张',
                  style: const TextStyle(color: Colors.white70, fontSize: 11),
                ),
              ],
            ),
          ),
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

  static const double collapsed = 56;
  double panelW = 296;
  double panelH = 380;

  static const double minPanelW = 220;
  static const double minPanelH = 180;
  static const double maxPanelW = 440;
  static const double maxPanelH = 680;

  // 缩放中：此期间关闭原生拖动，避免"拖把手时整窗跟着跑"
  bool _draggingResize = false;
  bool _resizeInFlight = false;

  @override
  void initState() {
    super.initState();

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
              ready = true;
              // 引擎已读到玩法文件并回传，与本地选择不一致时以回传为准，保持两端同步。
              // 引擎回传的 mode 与本地一致即可，不再校验 kModeOptions。
              final m = json['mode'];
              if (m is String && m != selectedMode) {
                selectedMode = m;
              }
            });
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
      _ensureSize(collapsed, collapsed);
    });

    // 玩法文件已改由主页通过 Java MethodChannel 写入；这里不再读 dart:io 文件。
    // （注：本 Flutter 端的 selectedMode 仍保留，仅用于把当前模式透传给主界面。）
  }

  // 只在识别内容真正变化时回传一次摘要给主 App，
  // 让主界面也能确认"后端确实在识别"，而不是每帧刷屏。
  String _lastSharedKey = '';

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

  // 悬浮按钮点击：在"仅按钮"与"分析面板"之间切换（窗口始终常驻在屏幕上）
  Future<void> _togglePanel() async {
    if (!mounted) return;
    final next = !panelVisible;
    setState(() {
      panelVisible = next;
    });
    if (next) {
      await _ensureSize(panelW, panelH);
    } else {
      await _ensureSize(collapsed, collapsed);
    }
  }

  // 牌河（所有玩家打出的牌）展示，按花色分组，与手牌同款 chip
  Widget _discardSection(String discards, int discardCount) {
    if (discards.isEmpty) return const SizedBox.shrink();
    final grouped = _groupHand(discards);
    final tilesAll = grouped.values.fold<int>(0, (s, l) => s + l.length);
    if (tilesAll == 0) return const SizedBox.shrink();
    final order = ['m', 'p', 's', 'z'];
    return Container(
      padding: const EdgeInsets.fromLTRB(8, 6, 8, 8),
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
                        k == 'z' ? '字' : (k == 'm' ? '万' : (k == 'p' ? '筒' : '条')),
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
                            hand: grouped[k]!.join(), chipSize: 18)),
                  ],
                ),
              ),
        ],
      ),
    );
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
            panelH = (panelH + e.delta.dy).clamp(minPanelH, maxPanelH).toDouble();
          });
          _resizeLive(panelW, panelH);
        },
        onPointerUp: (_) async {
          setState(() => _draggingResize = false);
          // 缩放结束，恢复原生拖动（这样窗口还能继续被拖到任意位置）
          await _ensureSize(panelW, panelH, drag: true);
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

  // ---------- 内容区小部件 ----------

  Widget _handSection(String hand, int count) {
    final grouped = _groupHand(hand);
    // 顺序固定：万 → 筒 → 条 → 字。空组不渲染。
    final order = ['m', 'p', 's', 'z'];
    final tilesAll = grouped.values.fold<int>(0, (s, l) => s + l.length);
    if (tilesAll == 0) {
      return const SizedBox.shrink();
    }
    return Container(
      padding: const EdgeInsets.fromLTRB(8, 6, 8, 8),
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
                        k == 'z' ? '字' : (k == 'm' ? '万' : (k == 'p' ? '筒' : '条')),
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
                    Expanded(child: HandChipRow(hand: grouped[k]!.join(), chipSize: 20)),
                  ],
                ),
              ),
        ],
      ),
    );
  }

  Widget _adviceSection(List<dynamic> advice, String best, int count) {
    if (advice.isEmpty) {
      return const SizedBox.shrink();
    }
    // best 第一张，其余按 ukeire 降序展示
    final sorted = [...advice];
    sorted.sort((a, b) => ((b['ukeire'] ?? 0) as int)
        .compareTo(((a['ukeire'] ?? 0) as int)));
    return Wrap(
      spacing: 4,
      runSpacing: 4,
      children: [
        for (var i = 0; i < sorted.length && i < 4; i++)
          AdviceCard(
            tile: (sorted[i]['tile'] ?? '') as String,
            ukeire: (sorted[i]['ukeire'] ?? 0) as int,
            best: best.isNotEmpty && sorted[i]['tile'] == best,
          ),
      ],
    );
  }

  // 上一手点评（commentary）已不在弹窗内显示——
// 只在 shareData 中作为状态传给主界面（主界面有专门区域呈现），避免弹窗被文字占满。

  @override
  Widget build(BuildContext context) {
    if (!panelVisible) {
      return SizedBox.expand(child: _floatingButton());
    }

    final String hand = (result?['hand'] ?? '') as String;
    final int count = (result?['count'] ?? 0) as int;
    final List<dynamic> advice =
        (result?['advice'] ?? const []) as List<dynamic>;
    final String best = (result?['best'] ?? '') as String;
    final String discards = (result?['discards'] ?? '') as String;
    final int discardCount = (result?['discard_count'] ?? 0) as int;

    // 三段固定布局：顶部建议 / 中部牌河 / 下部手牌。
    // 严格按要求：移除所有 LayoutBuilder/MediaQuery 自适应分支。
    return SizedBox.expand(
      child: Stack(
        children: [
          Container(
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [Color(0xF01E2126), Color(0xF0111418)],
              ),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: Colors.white.withAlpha(28)),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withAlpha(80),
                  blurRadius: 12,
                  offset: const Offset(0, 4),
                ),
              ],
            ),
            padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // 顶部栏：标题 + 收起
                Row(
                  children: [
                    const MahjongTileIcon(size: 16),
                    const SizedBox(width: 5),
                    const Expanded(
                      child: Text(
                        '麻将助手',
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: Colors.white,
                          fontWeight: FontWeight.bold,
                          fontSize: 13,
                          letterSpacing: 0.5,
                        ),
                      ),
                    ),
                    GestureDetector(
                      onTap: _togglePanel,
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 3),
                        color: Colors.white.withAlpha(28),
                        child: const Text(
                          '收起',
                          style: TextStyle(color: Colors.white, fontSize: 11),
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                // 三段严格按"建议 / 牌河 / 手牌"顺序，自上而下排列。
                // 不再做任何横竖屏判断、不再 Wrap 高度自动平衡：每段都是
                // 固定高度的可滚动内容；超出段高时该段自身滚动，不影响其他段。
                _section(
                  title: '建议',
                  child: _adviceSection(advice, best, count),
                  height: 110,
                ),
                const SizedBox(height: 6),
                _section(
                  title: '牌河',
                  child: _discardSection(discards, discardCount),
                  height: 110,
                ),
                const SizedBox(height: 6),
                Expanded(
                  child: _section(
                    title: '手牌',
                    child: (hand.isNotEmpty && count > 0)
                        ? _handSection(hand, count)
                        : const SizedBox.shrink(),
                    // 下段（手牌）高度自动填满，不固定
                    fillHeight: true,
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

  /// 段容器：固定高度的标题栏 + 可滚动内容。无 LayoutBuilder / 无 MediaQuery。
  Widget _section({
    required String title,
    required Widget child,
    double? height,
    bool fillHeight = false,
  }) {
    final Widget body = Container(
      decoration: BoxDecoration(
        color: Colors.white.withAlpha(8),
        borderRadius: BorderRadius.circular(6),
      ),
      padding: const EdgeInsets.all(6),
      child: SingleChildScrollView(
        physics: const ClampingScrollPhysics(),
        child: child,
      ),
    );
    final Widget content = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          title,
          style: const TextStyle(
            color: Colors.white70,
            fontSize: 11,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        ),
        const SizedBox(height: 4),
        body,
      ],
    );
    if (fillHeight) {
      return content;
    }
    return SizedBox(
      height: height ?? 100,
      child: content,
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
