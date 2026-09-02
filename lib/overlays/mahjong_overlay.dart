import 'dart:convert';
import 'dart:typed_data';
import 'dart:ui' as ui;

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
  // 绝张：手牌 + 牌河累计该牌型已出 ≥4 张。该牌在凑牌上已"死"，可放心打
  // 且别人也几乎不可能拿它和牌 —— App 自动算出来的、肉眼看不出来的高价值信号。
  // 配色：灰底 + 青绿描边 + 「绝」白底青字标，绝对不用红/橙/琥珀。
  final bool dead;

  const TileChip({
    super.key,
    required this.tile,
    this.size = 26,
    this.dim = false,
    this.dead = false,
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
      // 字牌：所有字牌统一深灰（"中" 也用深灰，不再红字 —— 红字会被误认为错误状态）。
      // 麻将牌"中"本身是红字，但此处遵循"界面任何状态下都不显示红色字符"的约束。
      charColor = const Color(0xFF202124);
    }

    // 绝张的牌面底色换成中性灰，并加一道青绿描边把它从普通牌里顶出来 —— 一眼可见。
    final Color tileBg = dead ? const Color(0xFFBDBDBD) : const Color(0xFFF7F3E8);
    final Color tileBorder = dead ? const Color(0xFF00695C) : const Color(0xFFB7A98F);
    final double tileBorderW = dead ? 1.0 : 0.6;

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
              color: Colors.black.withAlpha(40),
              blurRadius: 1,
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
  const HandChipRow({
    super.key,
    required this.hand,
    this.chipSize = 24,
    this.deadTiles,
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
  // 默认高度含预览区（~150）+ 三段；加大到 540 让首屏即可看清识别框与建议。
  double panelH = 540;

  // ── 固定三段布局的尺寸常量 ────────────────────────────────────────────
  // 这组常量同时决定"每段多高"和"面板最矮能拖到多少"（minPanelH 由它们推导）。
  // 必须同源推导，否则一旦 minPanelH 小于固定部分之和，外层 Column 就会
  // overflow —— debug 构建下会在越界侧画出红色 "BOTTOM OVERFLOWED BY x
  // PIXELS" 文字。这正是"弹窗缩到一定尺寸就冒红字"的直接原因。
  static const double _kTitleBarH = 26; // 顶部「麻将助手 / 收起」行
  static const double _kTitleGap = 8;
  static const double _kSectionGap = 6;

  static const double minPanelW = 220;
  // 宽度上限（沿用原始实现 440）：允许横向自由拉伸，但封顶避免拖出屏幕外。
  static const double maxPanelW = 440;

  // 关键修复：之前 minPanelH 由三段固定高度推导为 366，而默认 panelH=380 仅比
  // 下限高 14px —— 往下拖几乎立刻被 clamp 卡死，表现就是"高度调不动，只能调宽"。
  // 现在三段全部改为 Expanded 弹性高度（段内内容超出时自身滚动），窗口高度可
  // 自由收缩到 minPanelH 而绝不会 overflow。minPanelH 只是"可读性下限"，
  // 不再是硬性布局约束。上限 760 给足放大空间（系统会按屏幕实际高度再裁切）。
  // 下限：展开预览时 320（预览区 ~150 + 标题/三段）；折叠预览后 200，
  // 只显示标题+三段，大幅减小占位，也避免弹窗挡住自己的手牌。
  static const double _kMinPanelHExpanded = 320;
  static const double _kMinPanelHCollapsedPreview = 200;
  static const double maxPanelH = 760;
  double _minPanelH() => _previewCollapsed
      ? _kMinPanelHCollapsedPreview
      : _kMinPanelHExpanded;

  // 缩放中：此期间关闭原生拖动，避免"拖把手时整窗跟着跑"
  bool _draggingResize = false;
  bool _resizeInFlight = false;

  // 牌河折叠态。常驻可见（默认 true），但用户可手动折叠/展开以释放空间。
  // 状态在弹窗生命周期内持久化：用户收起 → 重新展开会保持上一次选择，
  // 不强制每次都重置为展开。
  bool _discardExpanded = true;

  // 识别区域（实时预览区）折叠态。默认展开，用户可一键收起以释放屏幕空间。
  // 收起后不再显示实时 preview，避免"画中画"遮挡，也降低解码/传输开销。
  bool _previewCollapsed = false;

  // ===== 实时画面预览 + 可拖动识别框（ROI）=====
  // 引擎每帧随结果一起发来一张全屏缩略图（PNG），这里解码出来显示在面板顶部，
  // 用户拖动上面的"高亮带"对准自己手牌所在的纵向位置；带的比例经 MethodChannel
  // 告诉引擎，引擎只识别带内 → 真机布局与训练截图不同时也能对准，解决"识别不出来"。
  ui.Image? _preview;
  // 识别框纵向比例（相对整屏高度，[0,1]）。默认整屏。
  double _roiTop = 0.0;
  double _roiBottom = 1.0;
  // 拖动识别框时，本次手势锁定操作的边（按下瞬间按落点决定）。
  String _roiDragMode = 'both';
  // 预览区固定高度。预览图按 BoxFit.contain 原比例显示，不再拉伸变形。
  static const double _kPreviewH = 132.0;

  void _decodePreview(List<int> png) {
    if (png.isEmpty) return;
    // 异步解码：即便某帧解码失败也只是少一张预览，绝不抛错影响主流程。
    ui.instantiateImageCodec(Uint8List.fromList(png)).then((codec) {
      return codec.getNextFrame();
    }).then((frame) {
      if (mounted) setState(() => _preview = frame.image);
    }).catchError((Object e) {
      print('预览解码失败（已忽略）: $e');
    });
  }

  void _sendRoi() {
    // 悬浮窗是独立 Flutter 引擎，直接调 MethodChannel 到不了 MainActivity。
    // 必须经 shareData 回主 App，由主 App 的 overlayListener 转成 MethodChannel。
    try {
      FlutterOverlayWindow.shareData({
        'type': 'roi',
        'top': _roiTop,
        'bottom': _roiBottom,
      });
    } catch (e) {
      print('识别框位置发送失败（已忽略）: $e');
    }
  }

  // 拖动识别框：which 决定动哪条边。
  //  'top'    仅移上边；'bottom' 仅移下边；'both' 整条带跟随手指移动。
  void _dragRoi(double dy, String which) {
    final double f = (dy / _kPreviewH).clamp(0.0, 1.0);
    setState(() {
      if (which == 'top') {
        _roiTop = f.clamp(0.0, _roiBottom - 0.05);
      } else if (which == 'bottom') {
        _roiBottom = f.clamp(_roiTop + 0.05, 1.0);
      } else {
        // 整条带跟随：保持当前高度，中心移到手指处。
        final double h = _roiBottom - _roiTop;
        double c = f;
        _roiTop = (c - h / 2).clamp(0.0, 1.0 - h);
        _roiBottom = _roiTop + h;
      }
    });
    _sendRoi();
  }

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
          // 解析出 PNG 预览（JSON 之后第一个 '\n' 之后），异步解码显示。
          // 解码失败只影响预览，不影响识别结果本身。
          final int sep = data.indexOf(10);
          if (sep > 0 && sep + 1 < data.length) {
            _decodePreview(data.sublist(sep + 1));
          }
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

  // 牌河段标题：左侧"牌河 · N 张"。右侧的"展开▸ / 收起▾"只在有牌时
  // 才显示——牌河为空时按钮"能点但无可见反馈"，给用户造成"按钮坏了"的
  // 错觉。空牌河直接留白比假装可交互更好。
  Widget _discardSectionHeader({
    required int discardCount,
    required bool expanded,
    required VoidCallback onToggle,
  }) {
    final hasContent = discardCount > 0;
    return Row(
      children: [
        const Text(
          '牌河',
          style: TextStyle(
            color: Colors.white70,
            fontSize: 11,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        ),
        const SizedBox(width: 6),
        Text(
          hasContent ? '· $discardCount 张' : '· 暂无',
          style: const TextStyle(
            color: Colors.white38,
            fontSize: 11,
            fontWeight: FontWeight.w400,
          ),
        ),
        const Spacer(),
        // 只有真有牌时才渲染折叠按钮，避免空状态下"按钮能点却什么变化都没有"
        if (hasContent)
          GestureDetector(
            onTap: onToggle,
            behavior: HitTestBehavior.opaque,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
              child: Text(
                expanded ? '收起 ▾' : '展开 ▸',
                style: const TextStyle(
                  color: Color(0xFF80CBC4), // 青绿 200（主色家族），提示交互
                  fontSize: 10,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ),
          ),
      ],
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
    // 张数不全（≥5 张但 <13/14）时显示一条浅色小字，告诉用户这是「正在识别」，
    // 而不是 bug。原实现在 count!=13/14 时整段不显示，造成「啥也没有」的观感。
    final bool partial = count > 0 && count < 13;
    return Container(
      padding: const EdgeInsets.fromLTRB(8, 6, 8, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (partial)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Text(
                '已识别 $count 张（识别中…稳定后会追加）',
                style: TextStyle(
                  color: Colors.white.withAlpha(110),
                  fontSize: 9,
                  fontStyle: FontStyle.italic,
                ),
              ),
            ),
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
      // 原实现在这里直接返回 SizedBox.shrink()，导致用户刚开始时看到一段空白
      // ——不知道是「正在识别」「没牌可看」还是「坏了」。给个轻量的初始提示。
      final bool hasHand = count > 0;
      final String hint = hasHand
          ? '向听 / 打牌建议（识别稳定后会显示）'
          : '尚无手牌 — 等待识别到 13/14 张再给出推荐\n'
              '· 若持续无牌：请把弹窗拖到不遮挡手牌的位置\n'
              '· 也可点上方"收起"只保留悬浮按钮\n'
              '· 仍无法识别可能是牌面美术与内置模板不匹配';
      return Padding(
        padding: const EdgeInsets.fromLTRB(8, 6, 8, 8),
        child: Text(
          hint,
          style: TextStyle(
            color: Colors.white.withAlpha(110),
            fontSize: 10,
            fontStyle: FontStyle.italic,
            height: 1.35,
          ),
        ),
      );
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
                // 顶部栏：标题 + 收起。
                // 固定高度而非自然高度：minPanelH 的推导依赖这个确定值，
                // 若让它随字体/图标自然变化，算术就不再成立。
                SizedBox(
                  height: _kTitleBarH,
                  child: Row(
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
                      // 连接状态条：一直可见，"●实时 / ⏳等待 / ⚠错误"任一
                      _statusBanner(),
                      const SizedBox(width: 6),
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
                ),
                const SizedBox(height: _kTitleGap),
                // 实时画面预览 + 可上下滑动/缩放的识别框。用户把高亮带对准
                // 自己手牌所在的纵向区域，引擎只识别带内 —— 真机布局与训练
                // 截图不同时也能对准，直接解决"识别不出来"。
                _previewArea(),
                const SizedBox(height: _kSectionGap),
                // 三段严格按"建议 / 牌河 / 手牌"顺序，自上而下排列。
                // 三段都是 Expanded 弹性高度，随窗口一起伸缩：窗口调高则三段一起
                // 长大、调低则一起压缩（内容超出时各自段内滚动），永不溢出。
                // 这样高度可以自由调整（只受 minPanelH/maxPanelH 限制），不再被锁死。
                Expanded(
                  child: _section(
                    title: '建议',
                    child: _adviceSection(advice, best, count),
                    fillHeight: true,
                  ),
                ),
                const SizedBox(height: _kSectionGap),
                Expanded(
                  child: _section(
                    title: '牌河',
                    titleOverride: _discardSectionHeader(
                      discardCount: discardCount,
                      expanded: _discardExpanded,
                      onToggle: () =>
                          setState(() => _discardExpanded = !_discardExpanded),
                    ),
                    child: _discardExpanded
                        ? _discardSection(
                            discards,
                            discardCount,
                            hand: hand,
                          )
                        : const SizedBox.shrink(),
                    fillHeight: true,
                  ),
                ),
                const SizedBox(height: _kSectionGap),
                Expanded(
                  child: _section(
                    title: '手牌',
                    child: (hand.isNotEmpty && count > 0)
                        ? _handSection(hand, count)
                        : const SizedBox.shrink(),
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

  /// 实时画面预览区 + 可拖动/缩放的识别框（ROI）。
  ///
  /// 整块区域是一个 GestureDetector：按下瞬间按落点判定操作"上边/下边/整条带"，
  /// 拖动时所有 dy 都是相对整块预览区的局部坐标 → 比例直接等于相对整屏高度，
  /// 引擎据此裁剪识别区域。识别框仅仅是个视觉指示，真正的识别范围由比例决定。
  /// 即便预览还没解码出来（_preview 为 null），拖动依然有效——比例照常发送。
  Widget _previewArea() {
    // 折叠态：只显示一行提示+展开按钮，释放屏幕空间，避免弹窗遮挡手牌。
    if (_previewCollapsed) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Expanded(
                child: Text(
                  '识别区域 · 已收起',
                  style: TextStyle(
                    color: Colors.white54,
                    fontSize: 9.5,
                    letterSpacing: 0.3,
                  ),
                ),
              ),
              GestureDetector(
                onTap: () => setState(() {
                  _previewCollapsed = false;
                  // 展开后把面板高度恢复到能容纳预览区的最小值，避免挤压
                  if (panelH < _kMinPanelHExpanded) {
                    panelH = _kMinPanelHExpanded;
                    _ensureSize(panelW, panelH);
                  }
                }),
                child: const Padding(
                  padding: EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                  child: Text(
                    '展开 ▾',
                    style: TextStyle(
                      color: Color(0xFF80CBC4),
                      fontSize: 10,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ),
              ),
            ],
          ),
        ],
      );
    }

    final double bandTop = _roiTop * _kPreviewH;
    final double bandH = (_roiBottom - _roiTop) * _kPreviewH;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            const Expanded(
              child: Padding(
                padding: EdgeInsets.only(bottom: 3),
                child: Text(
                  '识别区域 · 拖动高亮框对准手牌',
                  style: TextStyle(
                    color: Colors.white54,
                    fontSize: 9.5,
                    letterSpacing: 0.3,
                  ),
                ),
              ),
            ),
            GestureDetector(
              onTap: () => setState(() {
                _previewCollapsed = true;
                // 收起后允许面板更矮，并立即把当前高度限制在折叠后的最小值以上
                panelH = panelH.clamp(_minPanelH(), maxPanelH).toDouble();
                _ensureSize(panelW, panelH);
              }),
              child: const Padding(
                padding: EdgeInsets.only(left: 6, bottom: 3),
                child: Text(
                  '收起 ▴',
                  style: TextStyle(
                    color: Color(0xFF80CBC4),
                    fontSize: 10,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
            ),
          ],
        ),
        SizedBox(
          height: _kPreviewH,
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onVerticalDragStart: (d) {
              final double y = (d.localPosition.dy / _kPreviewH).clamp(0.0, 1.0);
              setState(() {
                if (y < _roiTop + 0.06) {
                  _roiDragMode = 'top';
                } else if (y > _roiBottom - 0.06) {
                  _roiDragMode = 'bottom';
                } else {
                  _roiDragMode = 'both';
                }
              });
            },
            onVerticalDragUpdate: (d) => _dragRoi(d.localPosition.dy, _roiDragMode),
            onVerticalDragEnd: (_) => _sendRoi(),
            child: Stack(
              children: [
                // 预览底图（或占位）。用 BoxFit.contain 保持原比例，不再拉伸变形。
                Positioned.fill(
                  child: Container(
                    color: Colors.black.withAlpha(120),
                    child: _preview == null
                        ? const Center(
                            child: Text(
                              '等待画面…',
                              style: TextStyle(color: Colors.white54, fontSize: 10),
                            ),
                          )
                        : RawImage(
                            image: _preview,
                            fit: BoxFit.contain,
                            alignment: Alignment.center,
                          ),
                  ),
                ),
                // 被识别区域高亮带（纯视觉）
                Positioned(
                  top: bandTop,
                  left: 0,
                  right: 0,
                  height: bandH,
                  child: Container(
                    decoration: BoxDecoration(
                      border: Border.all(
                        color: const Color(0xFF80CBC4),
                        width: 1.5,
                      ),
                      color: const Color(0x2080CBC4),
                    ),
                  ),
                ),
                // 上边把手（视觉提示）
                Positioned(
                  top: bandTop - 1.5,
                  left: 0,
                  right: 0,
                  height: 3,
                  child: Container(color: const Color(0xFF80CBC4)),
                ),
                // 下边把手（视觉提示）
                Positioned(
                  top: bandTop + bandH - 1.5,
                  left: 0,
                  right: 0,
                  height: 3,
                  child: Container(color: const Color(0xFF80CBC4)),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  /// 段容器：固定高度的标题栏 + 可滚动内容。无 LayoutBuilder / 无 MediaQuery。
  /// 可选 titleOverride：传入则用自定义标题组件（如带折叠开关的牌河标题），
  /// 不传则回退到默认的纯文本标题。
  Widget _section({
    required String title,
    required Widget child,
    double? height,
    bool fillHeight = false,
    Widget? titleOverride,
  }) {
    final Widget header = titleOverride ??
        Text(
          title,
          style: const TextStyle(
            color: Colors.white70,
            fontSize: 11,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        );
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
        header,
        const SizedBox(height: 4),
        // 必须 Expanded：body 内是 SingleChildScrollView，在 Column 中若不给出
        // 有界高度，它会取"子内容的完整高度"。牌河牌多时子内容远高于段高，
        // Column 随即 overflow 并画出红色越界文字 —— 且与窗口尺寸无关。
        // 包上 Expanded 后滚动区被限制在剩余空间内，超出部分改为段内滚动。
        Expanded(child: body),
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
