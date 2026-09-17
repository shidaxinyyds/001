import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:auto_vision/channel.dart';
import 'package:auto_vision/debug_page.dart';
import 'package:auto_vision/mode_store.dart';

/// 主色调：青绿。
/// 全局禁用红/橙/琥珀系，避免用户把"强调色"误读为"错误提示"。
/// 弹窗层（mahjong_overlay.dart）同样遵循此约定。
const Color _kAccent = Color(0xFF00695C); // teal 800
const Color _kAccentBg = Color(0xFFE0F2F1); // teal 50

class HomePage extends StatefulWidget {
  const HomePage({Key? key}) : super(key: key);

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  String? latestMessageFromOverlay;

  static const channel = MethodChannel(CHANNEL_NAME);

  bool isProcessing = false;
  // 当前选中的玩法，初始空：必须先选才能开始识别。
  String? selectedMode;
  String _selectedCategory = '川麻血流';
  bool _modeReady = false;

  @override
  void initState() {
    super.initState();

    // 拉一次当前玩法（来自 Java 写的共享文件，Python 引擎也读这个文件）
    GameMode.current().then((m) {
      if (!mounted) return;
      final info = GameMode.info(m);
      setState(() {
        selectedMode = m;
        if (info != null) {
          _selectedCategory = info.category;
        }
        _modeReady = true;
      });
    });

    // 接收悬浮窗通过 shareData 发来的消息：
    // 'stop' 为"停止"指令，Map 为识别状态回传（用于确认后端真的在识别）
    FlutterOverlayWindow.overlayListener.listen((event) {
      if (event == 'stop') {
        setProcessingState(false);
        hideOverlay();
        if (mounted) {
          setState(() {
            isProcessing = false;
          });
        }
        return;
      }
      if (event is Map && event['type'] == 'status') {
        if (mounted) {
          setState(() {
            _recogStatus = event['status']?.toString() ?? '';
            _recogCount = (event['count'] ?? 0) as int;
            _recogShanten = event['shanten'] as int?;
            _recogHand = event['hand']?.toString() ?? '';
            _recogTopScore = (event['top_score'] as num?)?.toDouble() ?? 0.0;
            _recogScreen = event['screen']?.toString() ?? '';
            _recogMessage = event['message']?.toString() ?? '';
          });
        }
      }
      // 悬浮窗拖动态识别框：把 ROI 比例经主引擎 MethodChannel 转给 Java/引擎。
      // 注意：悬浮窗是独立 Flutter 引擎，它的 MethodChannel 到不了 MainActivity
      // （那是主引擎的 messenger）—— 必须借 shareData 回主 App，再转 MethodChannel。
      if (event is Map && event['type'] == 'roi') {
        final top = (event['top'] as num?)?.toDouble() ?? 0.0;
        final bottom = (event['bottom'] as num?)?.toDouble() ?? 1.0;
        channel.invokeMethod<dynamic>('setRoi', {'top': top, 'bottom': bottom});
      }
      // 悬浮窗「旋转」按钮：把方向覆盖经主引擎 MethodChannel 转给 Java/引擎。
      if (event is Map && event['type'] == 'orient') {
        final deg = (event['deg'] as num?)?.toInt() ?? 0;
        channel.invokeMethod<dynamic>('setOrient', {'deg': deg});
      }
      // 悬浮窗「新局重置」按钮：把重置指令转发给 Java/Python 引擎
      if (event is Map && event['type'] == 'reset_match') {
        channel.invokeMethod<dynamic>('resetMatch');
      }
    });
  }

  // 由悬浮窗回传的识别状态（证明链路真的在跑，而不是摆设）
  String _recogStatus = '';
  int _recogCount = 0;
  int? _recogShanten;
  String _recogHand = '';
  double _recogTopScore = 0.0;
  String _recogScreen = '';
  String _recogMessage = '';

  // 底部导航栏当前页（0=主页, 1=调试）
  int _tab = 0;

  Future<void> setProcessingState(bool start) async {
    try {
      if (start) {
        await channel.invokeMethod<int>('startProcessing');
      } else {
        await channel.invokeMethod<int>('stopProcessing');
      }
    } on Exception catch (e) {
      print(e);
    }
  }

  // 收起态（悬浮按钮）尺寸，单位 dp（展开态尺寸由悬浮窗自身常量控制）
  static const double btnSizeDp = 56;

  // 悬浮窗初始位置（dp）。必须显式给出，原因见下方 showOverlay 注释。
  static const OverlayPosition _startPos = OverlayPosition(16, 100);

  String _status = '未开始';

  void _setStatus(String s) {
    print('[悬浮窗状态] $s');
    if (mounted) {
      setState(() {
        _status = s;
      });
    }
  }

  Future<void> showOverlay() async {
    try {
      if (await FlutterOverlayWindow.isActive()) {
        _setStatus('悬浮窗已在运行');
        return;
      }
      // 若未授予"显示在其他应用上层"权限，先引导到系统设置开启。
      bool granted = await FlutterOverlayWindow.isPermissionGranted() == true;
      if (!granted) {
        _setStatus('未授予悬浮窗权限，正在请求…');
        granted = await FlutterOverlayWindow.requestPermission() == true;
      }
      if (!granted) {
        // 权限未授予时悬浮窗无法显示，提示用户去系统设置开启。
        _setStatus('✗ 未授予"显示在其他应用上层"权限，悬浮窗无法显示');
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text(
                '悬浮窗需要"显示在其他应用上层"权限。请到系统设置→应用→Ace Mahjong→权限中开启，再点一次"开始识别"。'),
            duration: Duration(seconds: 6),
          ));
        }
        return;
      }
      _setStatus('✓ 权限已授予，正在打开悬浮窗…');

      // 关键修正：flutter_overlay_window 0.4.5 的 OverlayService.onStartCommand 有两个坑，
      // 会导致小尺寸悬浮窗被画到屏幕之外，看上去"按钮根本没出现"：
      //   1) 此处传入的 width/height 被当作【物理像素】直接使用（未做 dp 转换），
      //      所以 60 只有 60 像素，约 7mm，肉眼几乎看不见；
      //   2) 若不传 startPosition，插件会用 dy = -状态栏高度(px) 作为初始 Y，
      //      而 moveOverlay 内部又对它做了一次 dpToPx（把已是像素的值当 dp 再乘密度），
      //      得到约 -216px —— 一个 60px 高的窗口被放到 y=-216，整个落在屏幕上方之外。
      // 因此：这里显式传入 startPosition（正的 dp 值），并给一个足够大的像素初值；
      // 最终尺寸再由悬浮窗自身用 resizeOverlay(按 dp) 校正。
      await FlutterOverlayWindow.showOverlay(
        enableDrag: true,
        overlayTitle: "识牌助手",
        overlayContent: '识牌助手已开启',
        flag: OverlayFlag.defaultFlag,
        visibility: NotificationVisibility.visibilityPublic,
        // 关键：必须是 none。若为 auto，松手后插件会把窗口吸附到最近的左右边缘，
        // 无法停在屏幕任意位置。插件源码中只有 "none" 才跳过吸附动画。
        positionGravity: PositionGravity.none,
        alignment: OverlayAlignment.topLeft,
        width: (btnSizeDp * 3).toInt(),
        height: (btnSizeDp * 3).toInt(),
        startPosition: _startPos,
      );
      _setStatus('showOverlay 已调用，等待服务附加视图…');

      // 兜底：等服务把视图挂上后，再按 dp 校正一次位置（此时 dp 换算是正确的）
      bool moved = false;
      for (int i = 0; i < 30; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 100));
        if (await FlutterOverlayWindow.isActive() != true) continue;
        try {
          moved = await FlutterOverlayWindow.moveOverlay(_startPos) == true;
        } catch (_) {}
        if (moved) break;
      }

      String posText = '未知';
      try {
        final p = await FlutterOverlayWindow.getOverlayPosition();
        posText = 'x=${p.x.toStringAsFixed(0)}, y=${p.y.toStringAsFixed(0)}';
      } catch (_) {}
      _setStatus(moved
          ? '✓ 悬浮窗已显示（位置已校正：$posText）'
          : '⚠ 悬浮窗已调用，但位置校正未成功（$posText）。请把本行内容反馈给开发者。');
    } catch (e) {
      _setStatus('✗ showOverlay 异常：$e');
    }
  }

  String _recognitionText() {
    if (!isProcessing) return '未开始识别';
    switch (_recogStatus) {
      case 'ok':
        final String sh = _recogShanten == null
            ? ''
            : (_recogShanten == 0 ? '（听牌）' : '（$_recogShanten 向听）');
        return '✓ 已识别 $_recogCount 张$sh\n$_recogHand';
      case 'incomplete':
        return '识别到 $_recogCount 张，需 13/14 张才完整\n（确认牌面完整、没有被遮挡）';
      case 'no_tiles':
        return '未识别到牌面\n${_diagHint()}';
      case 'engine_ready':
        return '识别引擎已就绪，等待画面…\n（若一直停在这里，说明采集不到屏幕画面）';
      case 'no_frames':
        return '已授权录屏，但未采集到画面\n'
            '（切到牌局稍等几秒；屏幕完全静止时也属正常；\n'
            '若持续如此说明录屏会话已失效，请"停止识别"后重新开始）';
      case 'projection_stopped':
        return '录屏会话被系统结束\n（锁屏/状态栏停止共享/被其它录屏抢占）\n请重新点"开始识别"';
      case 'send_error':
        return '识别结果发送失败\n（悬浮窗数据链路断开，请停止后重新开始）';
      case 'py_error':
      case 'decode_error':
      case 'java_error':
      case 'capture_error':
      case 'start_failed':
        return '识别链路异常\n$_recogMessage';
      default:
        return '正在等待第一帧识别结果…';
    }
  }

  // 识别不出牌时，把"匹配分/分辨率"摆出来，一眼能区分
  // 是没截到屏、屏幕里没牌，还是牌面样式跟模板不匹配
  String _diagHint() {
    final String scr = _recogScreen.isEmpty ? '未知' : _recogScreen;
    final String score = _recogTopScore.toStringAsFixed(2);
    if (_recogScreen.isEmpty) {
      return '（还没收到第一帧，确认已授权录屏并打开牌局）';
    }
    if (_recogTopScore < 0.20) {
      return '屏幕 $scr｜匹配分 $score\n屏幕里没找到牌，确认已打开牌局且手牌可见';
    }
    return '屏幕 $scr｜匹配分 $score\n有牌但匹配分偏低：本 App 的牌面样式与内置模板差异较大';
  }

  // 诊断小工具：调试时可手动调用查看权限/采集链路。当前 UI 不再暴露按钮。
  // ignore: unused_element
  Future<void> _refreshDiag() async {
    bool granted = false;
    bool active = false;
    String pos = '未知';
    try {
      granted = await FlutterOverlayWindow.isPermissionGranted();
    } catch (_) {}
    try {
      active = await FlutterOverlayWindow.isActive();
    } catch (_) {}
    try {
      final p = await FlutterOverlayWindow.getOverlayPosition();
      pos = 'x=${p.x.toStringAsFixed(0)}, y=${p.y.toStringAsFixed(0)}';
    } catch (_) {}
    _setStatus('权限=${granted ? "已授予" : "未授予"}｜运行中=$active｜位置=$pos');
  }

  void hideOverlay() async {
    try {
      await FlutterOverlayWindow.closeOverlay();
    } catch (_) {}
  }

  void permissions() async {
    if (!(await Permission.notification.isGranted)) {
      print("requesting");
      await Permission.notification.request();
    } else {
      print("has permission");
    }
  }

  /// 用户点选玩法。同步写到 Java 共享文件（Python 引擎读的就是这个文件），
  /// 异步回来再 setState，避免 MethodChannel 抖动期间出现"选项闪烁"。
  Future<void> _selectMode(String mode) async {
    if (mode == selectedMode) return;
    final ok = await GameMode.set(mode);
    if (!ok) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text('切到 ${GameMode.label(mode)} 失败，请重试'),
      ));
      return;
    }
    if (!mounted) return;
    final info = GameMode.info(mode);
    setState(() {
      selectedMode = mode;
      if (info != null) {
        _selectedCategory = info.category;
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final String mode = selectedMode ?? '';
    return Scaffold(
      appBar: AppBar(title: const Text("Ace Mahjong")),
      body: IndexedStack(
        index: _tab,
        children: [
          _buildHomeBody(mode),
          const DebugPage(),
        ],
      ),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _tab,
        onTap: (i) => setState(() => _tab = i),
        selectedItemColor: _kAccent,
        items: const [
          BottomNavigationBarItem(icon: Icon(Icons.home), label: '主页'),
          BottomNavigationBarItem(icon: Icon(Icons.bug_report), label: '调试'),
        ],
      ),
    );
  }

  Widget _buildHomeBody(String mode) {
    final bool canStart = !isProcessing && _modeReady && mode.isNotEmpty;
    final currentInfo = GameMode.info(mode);
    final categoryModes = GameMode.allModes
        .where((m) => m.category == _selectedCategory)
        .toList();

    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // 顶部当前生效玩法与平台兼容状态
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: _kAccentBg,
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: _kAccent.withValues(alpha: 0.35)),
              ),
              child: Row(
                children: [
                  const Icon(Icons.verified, color: _kAccent, size: 20),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          currentInfo != null
                              ? '当前玩法：${currentInfo.name} (${currentInfo.wall}张)'
                              : '请选择麻将玩法',
                          style: const TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.bold,
                            color: _kAccent,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          '支持腾讯欢乐麻将、微乐、指尖等主流平台自适应',
                          style: TextStyle(
                            fontSize: 11,
                            color: _kAccent.withValues(alpha: 0.85),
                          ),
                        ),
                      ],
                    ),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                    decoration: BoxDecoration(
                      color: _kAccent,
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: const Text(
                      '100%完美适配',
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                        color: Colors.white,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),

            // 分类切换栏
            _buildCategorySelector(),

            // 玩法卡片列表
            ...categoryModes.map((info) {
              final bool sel = info.key == mode;
              return _buildModeCard(info, sel);
            }),

            const SizedBox(height: 12),

            // 开始/停止识别主按钮
            SizedBox(
              height: 52,
              child: ElevatedButton(
                style: ElevatedButton.styleFrom(
                  backgroundColor: canStart ? _kAccent : Colors.grey.shade400,
                  foregroundColor: Colors.white,
                  disabledBackgroundColor: Colors.grey.shade300,
                  disabledForegroundColor: Colors.grey.shade600,
                  elevation: canStart ? 2 : 0,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                ),
                onPressed: canStart ? _toggleProcessing : null,
                child: Text(
                  isProcessing
                      ? '停止识别'
                      : (mode.isEmpty ? '请先选择玩法' : '开始识别'),
                  style: const TextStyle(
                      fontSize: 18, fontWeight: FontWeight.w600),
                ),
              ),
            ),
            if (isProcessing)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: SizedBox(
                  height: 44,
                  child: OutlinedButton(
                    onPressed: _toggleProcessing,
                    child: const Text('停止识别'),
                  ),
                ),
              ),
            const SizedBox(height: 16),
            Text(
              _status,
              style: const TextStyle(fontSize: 13, color: Colors.black54),
            ),
            const SizedBox(height: 8),
            Text(
              _recognitionText(),
              style: const TextStyle(
                  fontSize: 14, color: Colors.black87, height: 1.35),
            ),
          ],
        ),
      ),
    );
  }

  // 开始/停止识别（主页按钮与调试页共用）
  Future<void> _toggleProcessing() async {
    if (isProcessing) {
      setProcessingState(false);
      hideOverlay();
      setState(() => isProcessing = false);
    } else {
      await showOverlay();
      setProcessingState(true);
      setState(() => isProcessing = true);
    }
  }

  // 分类切换栏
  Widget _buildCategorySelector() {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: Colors.grey.shade100,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Colors.grey.shade300),
      ),
      child: Row(
        children: GameMode.categories.map((cat) {
          final bool sel = cat == _selectedCategory;
          return Expanded(
            child: GestureDetector(
              onTap: () {
                if (_selectedCategory != cat) {
                  setState(() => _selectedCategory = cat);
                }
              },
              child: Container(
                padding: const EdgeInsets.symmetric(vertical: 8),
                decoration: BoxDecoration(
                  color: sel ? Colors.white : Colors.transparent,
                  borderRadius: BorderRadius.circular(8),
                  boxShadow: sel
                      ? [
                          BoxShadow(
                            color: Colors.black.withValues(alpha: 0.08),
                            blurRadius: 4,
                            offset: const Offset(0, 1),
                          ),
                        ]
                      : null,
                ),
                alignment: Alignment.center,
                child: Text(
                  cat,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: sel ? FontWeight.bold : FontWeight.w500,
                    color: sel ? _kAccent : Colors.black87,
                  ),
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  // 玩法卡片
  Widget _buildModeCard(MahjongModeInfo info, bool isSelected) {
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        color: isSelected ? _kAccentBg : Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: isSelected ? _kAccent : Colors.grey.shade300,
          width: isSelected ? 2 : 1,
        ),
        boxShadow: [
          BoxShadow(
            color: isSelected
                ? _kAccent.withValues(alpha: 0.12)
                : Colors.black.withValues(alpha: 0.04),
            blurRadius: 6,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () => _selectMode(info.key),
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        info.name,
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                          color: isSelected ? _kAccent : Colors.black87,
                        ),
                      ),
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 7, vertical: 3),
                      decoration: BoxDecoration(
                        color: isSelected
                            ? _kAccent.withValues(alpha: 0.15)
                            : Colors.grey.shade100,
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Text(
                        info.status,
                        style: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w600,
                          color: isSelected ? _kAccent : Colors.grey.shade700,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Icon(
                      isSelected
                          ? Icons.check_circle
                          : Icons.radio_button_unchecked,
                      color: isSelected ? _kAccent : Colors.grey.shade400,
                      size: 20,
                    ),
                  ],
                ),
                const SizedBox(height: 5),
                Text(
                  info.subtitle,
                  style: TextStyle(
                    fontSize: 12,
                    color: Colors.grey.shade700,
                    height: 1.25,
                  ),
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 6,
                  runSpacing: 4,
                  children: info.tags.map((tag) {
                    return Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(
                        color: isSelected
                            ? Colors.white.withValues(alpha: 0.85)
                            : Colors.grey.shade100,
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(
                          color: isSelected
                              ? _kAccent.withValues(alpha: 0.3)
                              : Colors.grey.shade300,
                          width: 0.8,
                        ),
                      ),
                      child: Text(
                        tag,
                        style: TextStyle(
                          fontSize: 11,
                          color: isSelected ? _kAccent : Colors.black54,
                        ),
                      ),
                    );
                  }).toList(),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
