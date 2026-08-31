import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
import 'package:realtime_mahjong_trainer/overlays/tile_labels.dart';
import 'package:realtime_mahjong_trainer/server.dart';

/// 与 python/modes.py 完全一致的玩法共享文件路径。
/// 悬浮窗写入、Python 引擎每帧读取，用于跨层切换二/三/四麻。
const String kMahjongModePath =
    '/storage/emulated/0/Android/data/com.example.realtime_mahjong_trainer/files/mahjong_mode.json';

const Map<String, String> kModeOptions = {
  '2p': '二麻',
  '3p': '三麻',
  '4p': '四麻',
};

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
      // 字牌：东 南 西 北 黑；中 红；发 绿
      switch (cn) {
        case '中':
          charColor = const Color(0xFFD32F2F);
          break;
        case '發':
          charColor = const Color(0xFF2E7D32);
          break;
        default:
          charColor = const Color(0xFF202124);
      }
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
                    color: best ? Colors.greenAccent : Colors.amber,
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
              final m = json['mode'];
              if (m is String && kModeOptions.containsKey(m) && m != selectedMode) {
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

    // 启动时读取一次玩法文件，使悬浮窗初始选择与服务端一致（用户之前切换过则沿用）。
    _loadMode();
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

  // 读取玩法共享文件（与 python/modes.py 同一份路径），初始化本地选择。
  Future<void> _loadMode() async {
    try {
      final f = File(kMahjongModePath);
      if (await f.exists()) {
        final data = jsonDecode(await f.readAsString()) as Map<String, dynamic>;
        final m = data['mode'] as String?;
        if (m != null && kModeOptions.containsKey(m) && mounted) {
          setState(() => selectedMode = m);
        }
      }
    } catch (_) {
      // 读取失败就沿用默认四麻，不阻塞界面。
    }
  }

  // 切换玩法：更新本地状态并把选择写入共享文件，Python 引擎下一帧就会读到。
  Future<void> _setMode(String m) async {
    if (m == selectedMode) return;
    if (mounted) setState(() => selectedMode = m);
    try {
      final f = File(kMahjongModePath);
      await f.parent.create(recursive: true);
      await f.writeAsString(jsonEncode({'mode': m}));
    } catch (e) {
      print('写入玩法文件失败：$e');
    }
  }

  // 玩法切换分段控件（二麻 / 三麻 / 四麻）
  Widget _modeSwitch() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 2, vertical: 3),
      decoration: BoxDecoration(
        color: Colors.white.withAlpha(12),
        borderRadius: BorderRadius.circular(7),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: kModeOptions.entries.map((e) {
          final active = e.key == selectedMode;
          return GestureDetector(
            onTap: () => _setMode(e.key),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 4),
              margin: const EdgeInsets.symmetric(horizontal: 1.5),
              decoration: BoxDecoration(
                color: active ? const Color(0xFFFF8F00) : Colors.transparent,
                borderRadius: BorderRadius.circular(5),
              ),
              child: Text(
                e.value,
                style: TextStyle(
                  color: active ? Colors.white : Colors.white70,
                  fontSize: 11,
                  fontWeight: active ? FontWeight.bold : FontWeight.normal,
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
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
      decoration: BoxDecoration(
        color: Colors.white.withAlpha(8),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _sectionTitle('牌河'),
              const Spacer(),
              Text('$discardCount 张',
                  style: const TextStyle(color: Colors.white54, fontSize: 10)),
            ],
          ),
          const SizedBox(height: 4),
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

  // 剩余 / 绝张 统计条
  Widget _statsRow(int remaining, int dead) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        _statBox('剩余', '$remaining', Colors.cyanAccent),
        const SizedBox(width: 6),
        _statBox('绝张', '$dead', dead > 0 ? Colors.redAccent : Colors.white54),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            '剩余＝墙内未现的牌数；绝张＝4 张已全在桌面，摸不到、也不该打',
            style: const TextStyle(color: Colors.white38, fontSize: 9, height: 1.3),
          ),
        ),
      ],
    );
  }

  Widget _statBox(String label, String value, Color c) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: c.withAlpha(28),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: c.withAlpha(110), width: 0.6),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(value,
              style: TextStyle(color: c, fontSize: 14, fontWeight: FontWeight.bold)),
          Text(label, style: const TextStyle(color: Colors.white54, fontSize: 9)),
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
              color: _draggingResize
                  ? Colors.amber
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
          color: const Color(0xFFFF8F00),
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
      decoration: BoxDecoration(
        color: Colors.white.withAlpha(8),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _sectionTitle('手牌'),
              const Spacer(),
              Text(
                '$count / 14',
                style: const TextStyle(color: Colors.white54, fontSize: 10),
              ),
            ],
          ),
          const SizedBox(height: 4),
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

  Widget _sectionTitle(String t) => Text(
        t,
        style: const TextStyle(
          color: Colors.white54,
          fontSize: 10,
          fontWeight: FontWeight.bold,
          letterSpacing: 0.5,
        ),
      );

  Widget _shantenSection(int shanten) {
    final Color c;
    final String label;
    if (shanten < 0) {
      c = Colors.greenAccent;
      label = '已和牌';
    } else if (shanten == 0) {
      c = Colors.greenAccent;
      label = '听牌';
    } else {
      c = Colors.amber;
      label = '$shanten 向听';
    }
    return Row(
      children: [
        _sectionTitle('向听'),
        const SizedBox(width: 8),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
          decoration: BoxDecoration(
            color: c.withAlpha(40),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: c.withAlpha(120), width: 0.6),
          ),
          child: Text(
            label,
            style: TextStyle(
              color: c,
              fontSize: 12,
              fontWeight: FontWeight.bold,
            ),
          ),
        ),
      ],
    );
  }

  Widget _adviceSection(List<dynamic> advice, String best, int count) {
    if (advice.isEmpty) {
      // 即使 advice 为空也展示一行说明，给用户明确反馈
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Text(
          count >= 14
              ? '暂无可推荐打法（手牌需要调整）'
              : '识别 $count 张，不足以给出推荐（建议等手牌完整时再看）',
          style: const TextStyle(color: Colors.white38, fontSize: 11),
        ),
      );
    }
    // best 第一张，其余按 ukeire 降序展示
    final sorted = [...advice];
    sorted.sort((a, b) => ((b['ukeire'] ?? 0) as int)
        .compareTo(((a['ukeire'] ?? 0) as int)));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            _sectionTitle('推荐打法'),
            const Spacer(),
            Text(
              sorted.length > 1 ? '共 ${sorted.length} 个候选' : '',
              style: const TextStyle(color: Colors.white38, fontSize: 10),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Wrap(
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
        ),
      ],
    );
  }

  // 状态文案：每种 status 一行短描述
  Widget _statusChip(String status, int count, double topScore) {
    String text;
    Color color;
    switch (status) {
      case 'ok':
        text = '✓ 已识别 $count 张';
        color = Colors.greenAccent;
        break;
      case 'incomplete':
        final missing = math.max(0, 13 - count);
        text = '识别到 $count 张，还差约 $missing 张（可能被遮挡或漏检）';
        color = Colors.orangeAccent;
        break;
      case 'no_tiles':
        text = '未识别到牌面';
        color = Colors.white38;
        break;
      case 'engine_ready':
        text = '识别引擎已就绪，等待画面…';
        color = Colors.greenAccent;
        break;
      case 'no_frames':
        text = '已授权，未采集到画面';
        color = Colors.orangeAccent;
        break;
      case 'projection_stopped':
        text = '录屏已被系统结束，请重新开始识别';
        color = Colors.redAccent;
        break;
      case 'send_error':
        text = '结果发送失败（悬浮窗链路断开）';
        color = Colors.redAccent;
        break;
      case 'py_error':
      case 'decode_error':
      case 'java_error':
      case 'capture_error':
      case 'start_failed':
        text = '识别链路异常（见下方）';
        color = Colors.redAccent;
        break;
      default:
        text = '等待识别…';
        color = Colors.white38;
    }
    final String diagHint = _diagHint(status, topScore);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(
              width: 6,
              height: 6,
              decoration: BoxDecoration(color: color, shape: BoxShape.circle),
            ),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                text,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: color, fontSize: 11, fontWeight: FontWeight.w600),
              ),
            ),
          ],
        ),
        if (diagHint.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(left: 12, top: 2),
            child: Text(
              diagHint,
              style: const TextStyle(color: Colors.white38, fontSize: 10),
            ),
          ),
      ],
    );
  }

  String _diagHint(String status, double topScore) {
    if (status == 'ok' || status == 'engine_ready') return '';
    if (topScore < 0.20) return '屏幕里没找到麻将牌（确认已打开牌局）';
    if (topScore < 0.45) return '匹配分偏低，牌面样式与模板差异较大';
    return '';
  }

  Widget _boundaryNote() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: Colors.blueGrey.withAlpha(60),
        borderRadius: BorderRadius.circular(5),
      ),
      child: const Text(
        '说明：识别你自己的 13/14 张手牌 + 全场牌河，'
        '据此推算剩余/绝张并给出推荐打法。'
        '别家手牌在多数麻将 App 是反扣的（看不见）。'
        '切换上方玩法会同步可用牌集。',
        style: TextStyle(color: Colors.white70, fontSize: 10, height: 1.35),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (!panelVisible) {
      return SizedBox.expand(child: _floatingButton());
    }

    final String status = (result?['status'] ?? '') as String;
    final String hand = (result?['hand'] ?? '') as String;
    final int count = (result?['count'] ?? 0) as int;
    final int? shanten = result?['shanten'] as int?;
    final String? commentary = result?['commentary'] as String?;
    final List<dynamic> advice = (result?['advice'] ?? const []) as List<dynamic>;
    final double topScore =
        (result?['top_score'] as num?)?.toDouble() ?? 0.0;
    final String message = (result?['message'] ?? '') as String;
    final String best = (result?['best'] ?? '') as String;
    final String discards = (result?['discards'] ?? '') as String;
    final int discardCount = (result?['discard_count'] ?? 0) as int;
    final int remaining = (result?['remaining'] ?? 0) as int;
    final int dead = (result?['dead'] ?? 0) as int;

    return SizedBox.expand(
      child: Stack(
        children: [
          // 主面板：半透明深色 + 圆角 + 细边框（毛玻璃观感）
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
              crossAxisAlignment: CrossAxisAlignment.start,
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
                        decoration: BoxDecoration(
                          color: Colors.white.withAlpha(28),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: const Text(
                          '收起',
                          style: TextStyle(color: Colors.white, fontSize: 11),
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                // 滚动内容区
                Expanded(
                  child: SingleChildScrollView(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _statusChip(status, count, topScore),
                        if (message.isNotEmpty) ...[
                          const SizedBox(height: 4),
                          Text(
                            message,
                            style: const TextStyle(
                                color: Colors.redAccent, fontSize: 10),
                          ),
                        ],
                        const SizedBox(height: 8),
                        // 玩法切换：二麻 / 三麻 / 四麻
                        Center(child: _modeSwitch()),
                        const SizedBox(height: 8),
                        // 牌河（所有玩家打出的牌）
                        _discardSection(discards, discardCount),
                        const SizedBox(height: 8),
                        // 剩余 / 绝张 统计
                        _statsRow(remaining, dead),
                        const SizedBox(height: 8),
                        if (hand.isNotEmpty && count > 0) ...[
                          _handSection(hand, count),
                          const SizedBox(height: 8),
                        ],
                        if (shanten != null) ...[
                          _shantenSection(shanten),
                          const SizedBox(height: 8),
                        ],
                        _adviceSection(advice, best, count),
                        if (commentary != null && commentary.isNotEmpty) ...[
                          const SizedBox(height: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 8, vertical: 5),
                            decoration: BoxDecoration(
                              color: Colors.white.withAlpha(10),
                              borderRadius: BorderRadius.circular(5),
                            ),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                _sectionTitle('上一手点评'),
                                const SizedBox(height: 3),
                                Text(
                                  commentary,
                                  style: const TextStyle(
                                    color: Colors.white70,
                                    fontSize: 11,
                                    height: 1.45,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                        const SizedBox(height: 8),
                        _boundaryNote(),
                        const SizedBox(height: 8),
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
        color: tenpai ? Colors.green : const Color(0xFFD84315),
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
