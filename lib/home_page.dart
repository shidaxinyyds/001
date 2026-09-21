import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:auto_vision/channel.dart';
import 'package:auto_vision/debug_page.dart';
import 'package:auto_vision/mode_store.dart';
import 'package:auto_vision/theme/app_tokens.dart';

/// 配色统一读设计 token（明亮现代商务）。
const Color _kAccent = AppTokens.brand; // teal-600 现代翡翠青
const Color _kAccentBg = AppTokens.brandSoft; // teal-50 极淡青底色
const Color _kTextMain = AppTokens.ink;
const Color _kTextMuted = AppTokens.muted;
const Color _kBorder = AppTokens.border;
const Color _kBg = AppTokens.bg;

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

  // 悬浮窗→主 App 的回传订阅。必须持有并在 dispose 取消：旧实现只 listen
  // 不 cancel，页面每次被重建都叠加一个监听/或撞单订阅流报错，状态回传
  // 链路越用越卡甚至损坏。
  StreamSubscription<dynamic>? _overlaySub;

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
    // 'stop' 为"停止"指令；roi/orient/reset_match 是低频控制事件转给 Java。
    // 注意：高频的 'status' 识别回传**不在这里处理**——旧实现每帧回传都整页
    // setState，识别高峰期整树重建不停排队，把点玩法/点按钮的响应全拖慢
    // （"卡顿感"真正来源）。识别状态已下沉到 _RecognitionStatusView 自建
    // 订阅只重建局部小卡（overlayListener 已是广播流，支持多处订阅）。
    _overlaySub = FlutterOverlayWindow.overlayListener.listen((event) {
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

  @override
  void dispose() {
    _overlaySub?.cancel();
    _overlaySub = null;
    super.dispose();
  }

  // 悬浮窗开启/停止流程在飞标志：防用户重复点击重入（二次 showOverlay/closeOverlay
  // 与第一轮位置校正循环互相踩踏，是“开窗过程抽风”的常见诱因）。
  bool _overlayBusy = false;

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

  void _setStatus(String s) {
    print('[悬浮窗状态] $s');
    if (mounted) {
      setState(() {
        _status = s;
      });
    }
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

  /// 用户点选玩法：**乐观更新**——点下去立刻高亮选中（旧实现要等 Java 写完
  /// 共享文件才 setState，手感就是"点一下卡半秒"）；异步落地失败再回滚并提示。
  Future<void> _selectMode(String mode) async {
    if (mode == selectedMode) return;
    final prevMode = selectedMode;
    final prevCategory = _selectedCategory;
    final info = GameMode.info(mode);
    setState(() {
      selectedMode = mode;
      if (info != null) {
        _selectedCategory = info.category;
      }
    });
    final ok = await GameMode.set(mode);
    if (ok) return;
    if (!mounted) return;
    // 落地失败：仅当当前仍停在本次乐观切换的选中态时才回滚，
    // 避免快速连点时晚到的旧失败回滚踩掉后续已成功的切换。
    if (selectedMode != mode) return;
    setState(() {
      selectedMode = prevMode;
      _selectedCategory = prevCategory;
    });
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text('切到 ${GameMode.label(mode)} 失败，请重试'),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final String mode = selectedMode ?? '';
    return Scaffold(
      backgroundColor: _kBg,
      appBar: AppBar(
        backgroundColor: AppTokens.surface,
        elevation: 0,
        scrolledUnderElevation: 0,
        titleSpacing: 20,
        systemOverlayStyle: SystemUiOverlayStyle.dark,
        title: Row(
          children: [
            const Text(
              "Ace Mahjong",
              style: TextStyle(
                fontSize: 19,
                fontWeight: FontWeight.w700,
                color: _kTextMain,
                letterSpacing: -0.3,
              ),
            ),
            const Spacer(),
            AnimatedContainer(
              duration: const Duration(milliseconds: 260),
              curve: Curves.easeOut,
              padding: const EdgeInsets.symmetric(
                  horizontal: AppTokens.s12, vertical: 5),
              decoration: BoxDecoration(
                color: isProcessing ? AppTokens.successBg : AppTokens.pillBg,
                borderRadius: BorderRadius.circular(AppTokens.rPill),
                border: Border.all(
                  color: isProcessing
                      ? AppTokens.successBorder
                      : AppTokens.border,
                  width: 0.8,
                ),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  AnimatedContainer(
                    duration: const Duration(milliseconds: 260),
                    width: 7,
                    height: 7,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: isProcessing
                          ? AppTokens.success
                          : AppTokens.faint,
                    ),
                  ),
                  const SizedBox(width: 6),
                  Text(
                    isProcessing ? "识别中" : "待命",
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      color: isProcessing
                          ? AppTokens.successDark
                          : AppTokens.muted,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
      body: IndexedStack(
        index: _tab,
        children: [
          _buildHomeBody(mode),
          const DebugPage(),
        ],
      ),
      bottomNavigationBar: Container(
        decoration: const BoxDecoration(
          color: AppTokens.surface,
          border: Border(
            top: BorderSide(color: _kBorder, width: 0.8),
          ),
        ),
        child: BottomNavigationBar(
          currentIndex: _tab,
          onTap: (i) => setState(() => _tab = i),
          selectedItemColor: _kAccent,
          unselectedItemColor: AppTokens.faint,
          backgroundColor: AppTokens.surface,
          elevation: 0,
          selectedFontSize: 12,
          unselectedFontSize: 12,
          items: const [
            BottomNavigationBarItem(
              icon: Padding(
                padding: EdgeInsets.only(bottom: 2),
                child: Icon(Icons.home_outlined),
              ),
              activeIcon: Padding(
                padding: EdgeInsets.only(bottom: 2),
                child: Icon(Icons.home_rounded),
              ),
              label: '主页',
            ),
            BottomNavigationBarItem(
              icon: Padding(
                padding: EdgeInsets.only(bottom: 2),
                child: Icon(Icons.tune_outlined),
              ),
              activeIcon: Padding(
                padding: EdgeInsets.only(bottom: 2),
                child: Icon(Icons.tune_rounded),
              ),
              label: '调试',
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildHomeBody(String mode) {
    final bool canStart = !isProcessing && _modeReady && mode.isNotEmpty;
    final categoryModes = GameMode.allModes
        .where((m) => m.category == _selectedCategory)
        .toList();

    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(
            horizontal: AppTokens.s20, vertical: AppTokens.s16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // 分类切换栏
            _buildCategorySelector(),
            const SizedBox(height: AppTokens.s12),

            // 玩法卡片列表：切分类时淡入+上移过渡
            AnimatedSwitcher(
              duration: const Duration(milliseconds: 240),
              switchInCurve: Curves.easeOut,
              switchOutCurve: Curves.easeIn,
              transitionBuilder: (child, anim) => FadeTransition(
                opacity: anim,
                child: SlideTransition(
                  position: Tween<Offset>(
                          begin: const Offset(0, 0.03), end: Offset.zero)
                      .animate(anim),
                  child: child,
                ),
              ),
              child: Column(
                key: ValueKey<String>(_selectedCategory),
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: categoryModes.map((info) {
                  final bool sel = info.key == mode;
                  return _buildModeCard(info, sel);
                }).toList(),
              ),
            ),

            const SizedBox(height: 14),

            // 核心主操作按钮
            _buildActionButton(canStart, mode),

            const SizedBox(height: 14),

            // 状态 / 提示卡片
            _buildStatusCard(),
          ],
        ),
      ),
    );
  }

  // 开始/停止识别
  Future<void> _toggleProcessing() async {
    if (_overlayBusy) return; // 防重入：开窗流程可耗时数秒，连点会踩踏服务
    _overlayBusy = true;
    try {
      if (isProcessing) {
        setProcessingState(false);
        hideOverlay();
        setState(() => isProcessing = false);
      } else {
        await showOverlay();
        // 开悬浮窗流程可长达数秒（权限/位置校正），期间若被授权闸门卸页面，
        // 绝不拿着已销毁的 context 再 setState。
        if (!mounted) return;
        setProcessingState(true);
        setState(() => isProcessing = true);
      }
    } finally {
      _overlayBusy = false;
    }
  }

  // 分类切换栏（M3 风分段器，选中胶囊平滑滑动）
  Widget _buildCategorySelector() {
    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        color: AppTokens.pillBg,
        borderRadius: BorderRadius.circular(AppTokens.r12),
      ),
      child: Row(
        children: GameMode.categories.map((cat) {
          final bool sel = cat == _selectedCategory;
          return Expanded(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () {
                if (_selectedCategory != cat) {
                  setState(() => _selectedCategory = cat);
                }
              },
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 200),
                curve: Curves.easeOutCubic,
                padding: const EdgeInsets.symmetric(vertical: 9),
                decoration: BoxDecoration(
                  color: sel ? AppTokens.surface : Colors.transparent,
                  borderRadius: BorderRadius.circular(AppTokens.r8),
                  boxShadow: sel ? AppTokens.soft : null,
                ),
                alignment: Alignment.center,
                child: Text(
                  cat,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: sel ? FontWeight.w600 : FontWeight.w400,
                    color: sel ? _kTextMain : _kTextMuted,
                  ),
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  // 玩法卡片（M3 Card 风 + tonal 选中态 + 按压波纹）
  Widget _buildModeCard(MahjongModeInfo info, bool isSelected) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
        decoration: BoxDecoration(
          color: isSelected ? _kAccentBg : AppTokens.surface,
          borderRadius: AppTokens.radius16,
          border: Border.all(
            color: isSelected ? _kAccent : _kBorder,
            width: isSelected ? 1.5 : 1.0,
          ),
          boxShadow: isSelected ? null : AppTokens.soft,
        ),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            borderRadius: AppTokens.radius16,
            onTap: () => _selectMode(info.key),
            child: Padding(
              padding: const EdgeInsets.symmetric(
                  horizontal: AppTokens.s16, vertical: AppTokens.s16),
              child: Row(
                children: [
                  // 左侧图标徒章：选中时填色，形成 tonal 呼应。
                  AnimatedContainer(
                    duration: const Duration(milliseconds: 200),
                    width: 40,
                    height: 40,
                    decoration: BoxDecoration(
                      color: isSelected
                          ? AppTokens.brandContainer
                          : AppTokens.pillBg,
                      borderRadius: BorderRadius.circular(AppTokens.r12),
                    ),
                    child: Icon(
                      Icons.spa_outlined,
                      size: 22,
                      color: isSelected ? _kAccent : AppTokens.muted,
                    ),
                  ),
                  const SizedBox(width: AppTokens.s12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          info.name,
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w600,
                            color: isSelected ? _kAccent : _kTextMain,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          info.brief,
                          style: const TextStyle(
                            fontSize: 13,
                            color: _kTextMuted,
                            height: 1.2,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Icon(
                    isSelected
                        ? Icons.radio_button_checked_rounded
                        : Icons.radio_button_unchecked_rounded,
                    color: isSelected ? _kAccent : AppTokens.borderStrong,
                    size: 22,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  // 核心主操作按钮
  Widget _buildActionButton(bool canStart, String mode) {
    Color btnColor;
    String btnText;

    if (isProcessing) {
      btnColor = AppTokens.danger;
      btnText = '停止悬浮窗';
    } else if (canStart) {
      btnColor = _kAccent;
      btnText = '开启悬浮窗';
    } else {
      btnColor = AppTokens.borderStrong;
      btnText = mode.isEmpty ? '请先选择上方玩法' : '开启悬浮窗';
    }

    return SizedBox(
      height: 50,
      child: FilledButton(
        onPressed: (canStart || isProcessing) ? _toggleProcessing : null,
        style: FilledButton.styleFrom(
          backgroundColor: btnColor,
          disabledBackgroundColor: AppTokens.borderStrong,
          foregroundColor: Colors.white,
          elevation: 0,
          shape: RoundedRectangleBorder(
            borderRadius: AppTokens.radius12,
          ),
        ),
        child: Text(
          btnText,
          style: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            color: Colors.white,
            letterSpacing: 0.5,
          ),
        ),
      ),
    );
  }

  // 状态 / 提示卡片（待命↔运行 淡入过渡）
  Widget _buildStatusCard() {
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 220),
      switchInCurve: Curves.easeOut,
      switchOutCurve: Curves.easeIn,
      child: isProcessing
          ? _RecognitionStatusView(
              key: const ValueKey<String>('running'), overlayStatus: _status)
          : _buildIdleCard(),
    );
  }

  Widget _buildIdleCard() {
    return Container(
      key: const ValueKey<String>('idle'),
      padding: const EdgeInsets.symmetric(
          horizontal: AppTokens.s16, vertical: AppTokens.s16),
      decoration: BoxDecoration(
        color: AppTokens.surface,
        borderRadius: AppTokens.radius12,
        border: Border.all(color: _kBorder, width: 0.8),
      ),
      child: const Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: EdgeInsets.only(top: 2),
            child: Icon(
              Icons.info_outline_rounded,
              size: 16,
              color: AppTokens.faint,
            ),
          ),
          SizedBox(width: 10),
          Expanded(
            child: Text(
              '选定玩法后点击开启，悬浮窗将自动浮于牌局之上实时推演向听与最优出牌。',
              style: TextStyle(
                fontSize: 13,
                color: _kTextMuted,
                height: 1.4,
              ),
            ),
          ),
        ],
      ),
    );
  }

}

/// 运行中状态卡：自建对悬浮窗广播流的订阅，识别回传每帧只重建本小卡，
/// 不再触发整棵首页 setState——这是“识别开着时点玩法/按钮发卡顿”的根治。
class _RecognitionStatusView extends StatefulWidget {
  final String overlayStatus;
  const _RecognitionStatusView({Key? key, required this.overlayStatus})
      : super(key: key);

  @override
  State<_RecognitionStatusView> createState() => _RecognitionStatusViewState();
}

class _RecognitionStatusViewState extends State<_RecognitionStatusView> {
  // 由悬浮窗回传的识别状态（证明链路真的在跑，而不是摆设）
  String _recogStatus = '';
  int _recogCount = 0;
  int? _recogShanten;
  String _recogHand = '';
  double _recogTopScore = 0.0;
  String _recogScreen = '';
  String _recogMessage = '';

  StreamSubscription<dynamic>? _sub;

  @override
  void initState() {
    super.initState();
    // overlayListener 是广播流，支持主页控制事件订阅之外再开一路局部订阅。
    _sub = FlutterOverlayWindow.overlayListener.listen((event) {
      if (!mounted) return;
      if (event is Map && event['type'] == 'status') {
        setState(() {
          _recogStatus = event['status']?.toString() ?? '';
          // JSON 链路数值可能是 int/double，强转 as int 会抛错弄残监听；统一走 num。
          _recogCount = (event['count'] as num?)?.toInt() ?? 0;
          _recogShanten = (event['shanten'] as num?)?.toInt();
          _recogHand = event['hand']?.toString() ?? '';
          _recogTopScore = (event['top_score'] as num?)?.toDouble() ?? 0.0;
          _recogScreen = event['screen']?.toString() ?? '';
          _recogMessage = event['message']?.toString() ?? '';
        });
      }
    });
  }

  @override
  void dispose() {
    _sub?.cancel();
    _sub = null;
    super.dispose();
  }

  String _recognitionText() {
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

  @override
  Widget build(BuildContext context) {
    final String st = widget.overlayStatus;
    return Container(
      padding: const EdgeInsets.all(AppTokens.s16),
      decoration: BoxDecoration(
        color: AppTokens.surface,
        borderRadius: AppTokens.radius12,
        border: Border.all(color: AppTokens.successBorder, width: 1),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 8,
                height: 8,
                decoration: const BoxDecoration(
                  shape: BoxShape.circle,
                  color: AppTokens.success,
                ),
              ),
              const SizedBox(width: 8),
              const Text(
                '实时推演中',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: AppTokens.successDark,
                ),
              ),
              const Spacer(),
              if (_recogCount > 0)
                Text(
                  '识别手牌 $_recogCount 张',
                  style: const TextStyle(
                    fontSize: 12,
                    color: _kTextMuted,
                  ),
                ),
            ],
          ),
          const SizedBox(height: 10),
          if (st.isNotEmpty && st != '未开始') ...[
            Text(
              st,
              style: const TextStyle(
                fontSize: 12,
                color: _kTextMuted,
              ),
            ),
            const SizedBox(height: 6),
          ],
          Text(
            _recognitionText(),
            style: const TextStyle(
              fontSize: 13,
              color: _kTextMain,
              height: 1.4,
            ),
          ),
        ],
      ),
    );
  }
}
