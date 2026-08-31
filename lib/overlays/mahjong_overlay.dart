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

  static const double collapsed = 56;
  double panelW = 264;
  double panelH = 300;

  static const double minPanelW = 200;
  static const double minPanelH = 160;
  static const double maxPanelW = 420;
  static const double maxPanelH = 640;

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
  }

  // 只在识别内容真正变化时回传一次摘要给主 App，
  // 让主界面也能确认"后端确实在识别"，而不是每帧刷屏。
  String _lastSharedKey = '';

  void _maybeShareStatus(Map<String, dynamic> json) {
    final key =
        "${json['hand']}|${json['shanten']}|${json['status']}|${json['count']}";
    if (key == _lastSharedKey) return;
    _lastSharedKey = key;
    FlutterOverlayWindow.shareData({
      'type': 'status',
      'hand': json['hand'] ?? '',
      'count': json['count'] ?? 0,
      'status': json['status'] ?? '',
      'shanten': json['shanten'],
      // 诊断用：最高模板匹配分与截屏分辨率。
      // 分数长期 <0.2 => 屏幕里没有牌；0.2~0.45 => 有牌但样式跟模板差异大。
      'top_score': json['top_score'] ?? 0,
      'screen': (json['screen'] as List?)?.join('x') ?? '',
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

  @override
  Widget build(BuildContext context) {
    if (!panelVisible) {
      return SizedBox.expand(child: _floatingButton());
    }

    final String status = (result?['status'] ?? '') as String;
    final String hand = (result?['hand'] ?? '') as String;
    final int count = (result?['count'] ?? 0) as int;
    final shanten = result?['shanten'];
    final String? commentary = result?['commentary'] as String?;
    final List<dynamic> advice = (result?['advice'] ?? const []) as List<dynamic>;
    final double topScore =
        (result?['top_score'] as num?)?.toDouble() ?? 0.0;

    return SizedBox.expand(
      child: Stack(
        children: [
          Container(
            decoration: BoxDecoration(
              color: const Color(0xE61C1C1E),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(
                color: Colors.white.withAlpha(31),
              ),
            ),
            padding: const EdgeInsets.fromLTRB(8, 6, 8, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // 顶部栏：标题 + 收起 + 停止
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
                        ),
                      ),
                    ),
                    GestureDetector(
                      onTap: _togglePanel,
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 7, vertical: 3),
                        decoration: BoxDecoration(
                          color: Colors.white.withAlpha(36),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: const Text('收起',
                            style:
                                TextStyle(color: Colors.white, fontSize: 11)),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                // 内容区
                Expanded(
                  child: SingleChildScrollView(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _StatusLine(status: status, count: count),
                        const SizedBox(height: 6),
                        if (hand.isNotEmpty) ...[
                          const _Label('手牌'),
                          const SizedBox(height: 2),
                          Text(
                            handToChinese(hand),
                            style: const TextStyle(
                                color: Colors.white, fontSize: 13, height: 1.4),
                          ),
                          const SizedBox(height: 8),
                        ],
                        if (shanten != null) ...[
                          Row(
                            children: [
                              const _Label('向听'),
                              const SizedBox(width: 6),
                              Text(
                                shanten == 0
                                    ? '听牌'
                                    : '$shanten 向听',
                                style: TextStyle(
                                  color: (shanten as int) <= 0
                                      ? Colors.greenAccent
                                      : Colors.amber,
                                  fontSize: 13,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 8),
                        ],
                        if (advice.isNotEmpty) ...[
                          const _Label('推荐打法'),
                          const SizedBox(height: 3),
                          Wrap(
                            spacing: 5,
                            runSpacing: 5,
                            children: advice.map((e) {
                              final m = e as Map<String, dynamic>;
                              return _AdviceChip(
                                tile: (m['tile'] ?? '') as String,
                                ukeire: (m['ukeire'] ?? 0) as int,
                              );
                            }).toList(),
                          ),
                          const SizedBox(height: 8),
                        ],
                        if (commentary != null && commentary.isNotEmpty) ...[
                          const _Label('上一手点评'),
                          const SizedBox(height: 3),
                          Text(
                            commentary,
                            style: const TextStyle(
                                color: Colors.white70, fontSize: 12, height: 1.45),
                          ),
                        ],
                        const SizedBox(height: 8),
                        _DiagLine(topScore: topScore),
                        const SizedBox(height: 14),
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

class _Label extends StatelessWidget {
  final String text;
  const _Label(this.text);

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: const TextStyle(
          color: Colors.white54, fontSize: 10, fontWeight: FontWeight.bold),
    );
  }
}

class _StatusLine extends StatelessWidget {
  final String status;
  final int count;
  const _StatusLine({required this.status, required this.count});

  @override
  Widget build(BuildContext context) {
    String text;
    Color color;
    switch (status) {
      case 'ok':
        text = '已识别 $count 张';
        color = Colors.greenAccent;
        break;
      case 'incomplete':
        text = '识别到 $count 张，需 13/14 张才完整';
        color = Colors.orangeAccent;
        break;
      case 'no_tiles':
      default:
        text = '未识别到牌面';
        color = Colors.white38;
        break;
    }
    if (!{'ok', 'incomplete', 'no_tiles'}.contains(status)) {
      text = '等待识别…';
      color = Colors.white38;
    }
    return Row(
      children: [
        Container(
          width: 6,
          height: 6,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 5),
        Expanded(
          child: Text(text,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: color, fontSize: 11)),
        ),
      ],
    );
  }
}

/// 诊断行：仅显示匹配状态提示（不显示屏幕尺寸，符合简洁要求）。
/// 识别不出牌时，这一行能区分"屏幕里没牌"和"有牌但样式与模板差异较大"。
class _DiagLine extends StatelessWidget {
  final double topScore;
  const _DiagLine({required this.topScore});

  @override
  Widget build(BuildContext context) {
    final String hint;
    if (topScore < 0.20) {
      hint = '屏幕里没找到麻将牌（确认已打开牌局）';
    } else if (topScore < 0.45) {
      hint = '有牌但匹配分偏低，牌面样式与模板差异较大';
    } else {
      hint = '匹配正常';
    }
    return Text(
      hint,
      style: const TextStyle(color: Colors.white38, fontSize: 10),
    );
  }
}

class _AdviceChip extends StatelessWidget {
  final String tile;
  final int ukeire;
  const _AdviceChip({required this.tile, required this.ukeire});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
      decoration: BoxDecoration(
        color: Colors.white.withAlpha(26),
        borderRadius: BorderRadius.circular(5),
        border: Border.all(color: Colors.white.withAlpha(46)),
      ),
      child: RichText(
        text: TextSpan(
          children: [
            TextSpan(
              text: tileToChinese(tile),
              style: const TextStyle(color: Colors.white, fontSize: 12),
            ),
            TextSpan(
              text: ' $ukeire',
              style: const TextStyle(color: Colors.amber, fontSize: 11),
            ),
          ],
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
