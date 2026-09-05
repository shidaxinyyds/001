import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:auto_vision/activation.dart';
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

  // 首次激活信息（主页"激活成功"卡片展示用），由 initState 异步读取。
  ActivationInfo? _activation;

  static const channel = MethodChannel(CHANNEL_NAME);

  bool isProcessing = false;
  // 当前选中的玩法，初始空：必须先选才能开始识别。
  String? selectedMode;
  bool _modeReady = false;

  @override
  void initState() {
    super.initState();

    // 读取首次激活信息（时间与系统），用于主页"激活成功"卡片。
    getActivation().then((a) {
      if (mounted) setState(() => _activation = a);
    });

    // 拉一次当前玩法（来自 Java 写的共享文件，Python 引擎也读这个文件）
    GameMode.current().then((m) {
      if (!mounted) return;
      setState(() {
        selectedMode = m;
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
  static const OverlayPosition _startPos = OverlayPosition(8, 140);

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
        alignment: OverlayAlignment.topRight,
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
    setState(() {
      selectedMode = mode;
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

  /// 主页"激活成功"卡片：展示首次激活的时间与系统信息。
  /// 仅当本地存在激活记录时显示（未激活走卡密页，不会到达主页）。
  /// 主页"激活成功"卡片：展示首次激活的时间与系统信息。
  /// 仅当本地存在激活记录时显示（未激活走卡密页，不会到达主页）。
  /// 已放大：图标 48、标题 20、信息 14，并加圆角阴影，使其更醒目。
  Widget _buildActivationCard() {
    final a = _activation;
    if (a == null) return const SizedBox.shrink();
    final t = a.activatedAt;
    final time =
        '${t.year}-${_pad(t.month)}-${_pad(t.day)} ${_pad(t.hour)}:${_pad(t.minute)}:${_pad(t.second)}';
    return Container(
      margin: const EdgeInsets.only(bottom: 20),
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: _kAccentBg,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: _kAccent.withOpacity(0.45), width: 1.5),
        boxShadow: const [
          BoxShadow(
              color: Color(0x14000000), blurRadius: 8, offset: Offset(0, 2)),
        ],
      ),
      child: Row(
        children: [
          Container(
            width: 48,
            height: 48,
            decoration: BoxDecoration(
              color: _kAccent,
              borderRadius: BorderRadius.circular(12),
            ),
            child: const Icon(Icons.check, color: Colors.white, size: 30),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('激活成功',
                    style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.w800,
                        color: _kAccent)),
                const SizedBox(height: 8),
                Text('激活时间：$time',
                    style: const TextStyle(fontSize: 14, color: Colors.black54)),
                const SizedBox(height: 4),
                Text('激活系统：${a.systemInfo}',
                    style: const TextStyle(fontSize: 14, color: Colors.black54)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _pad(int n) => n.toString().padLeft(2, '0');

  Widget _buildHomeBody(String mode) {
    final bool canStart = !isProcessing && _modeReady && mode.isNotEmpty;
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const SizedBox(height: 6),
            _buildActivationCard(),
            const Text(
              '选择玩法',
              style: TextStyle(fontSize: 14, color: Colors.black54),
            ),
            const SizedBox(height: 8),
            _modeRow(mode),
            const SizedBox(height: 24),
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

  // 玩法选择：3 个等宽 SegmentedButton 风格卡。当前选中项高亮 + 上边框加粗。
  Widget _modeRow(String mode) {
    Widget tile(String value) {
      final bool sel = value == mode;
      return Expanded(
        child: GestureDetector(
          onTap: () => _selectMode(value),
          child: Container(
            margin: const EdgeInsets.symmetric(horizontal: 4),
            height: 64,
            decoration: BoxDecoration(
              color: sel ? _kAccentBg : Colors.white,
              border: Border.all(
                color: sel ? _kAccent : Colors.grey.shade400,
                width: sel ? 2 : 1,
              ),
              borderRadius: BorderRadius.circular(10),
            ),
            alignment: Alignment.center,
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  GameMode.label(value),
                  style: TextStyle(
                    fontSize: 16,
                    fontWeight: sel ? FontWeight.w700 : FontWeight.w500,
                    color: sel ? _kAccent : Colors.black87,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  value,
                  style: TextStyle(
                      fontSize: 11, color: Colors.grey.shade600),
                ),
              ],
            ),
          ),
        ),
      );
    }

    return Row(
      children: [
        tile('2p'),
        tile('3p'),
        tile('4p'),
      ],
    );
  }
}
