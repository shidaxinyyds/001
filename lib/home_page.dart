import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:auto_vision/channel.dart';
import 'package:auto_vision/debug_page.dart';
import 'package:auto_vision/device_info_card.dart';
import 'package:auto_vision/engine_snapshot.dart';
import 'package:auto_vision/mode_store.dart';
import 'package:auto_vision/platform_store.dart';
import 'package:auto_vision/session_store.dart';
import 'package:auto_vision/knowledge_page.dart';
import 'package:auto_vision/license/license_service.dart';
import 'package:auto_vision/license/license_status.dart';
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

class _HomePageState extends State<HomePage> with WidgetsBindingObserver {
  String? latestMessageFromOverlay;

  static const channel = MethodChannel(CHANNEL_NAME);

  bool isProcessing = false;
  // 当前选中的玩法，初始默认川麻血流红中，秒级就绪，按钮绝不卡灰。
  String? selectedMode = GameMode.defaultMode;
  String _selectedCategory = '川麻血流';
  bool _modeReady = true;

  // 当前选中的游戏平台预设（腾讯、途游、微乐、JJ、通用）
  String? selectedPlatform = GamePlatform.defaultPlatform;
  bool _platformReady = true;

  // ===== 对局信息卡（平台搜索直达 + 游戏ID + 运势/好牌概率）=====
  // 平台输入框不是"第二个平台真值"：它只是 selectedPlatform 的另一种输入法，
  // 命中预设就调 _selectPlatform（与点卡片同一条路径），不命中就明确拒绝，
  // 绝不让"框里写的"和"引擎在用的"变成两个平台。
  final TextEditingController _platformCtrl = TextEditingController();
  final TextEditingController _gameIdCtrl = TextEditingController();
  String _platformNotice = '';
  String _gameIdNotice = '';
  // 「程序同步输入框」与「用户键入」共用一个 controller：不按住这个标志，
  // 回填当前平台名会再触发一次 onChanged，形成自我回声。
  bool _syncingField = false;
  // 两个按钮的展开态：null = 都没展开
  String? _oddsTab;

  // 悬浮窗→主 App 的回传订阅。必须持有并在 dispose 取消：旧实现只 listen
  // 不 cancel，页面每次被重建都叠加一个监听/或撞单订阅流报错，状态回传
  // 链路越用越卡甚至损坏。
  StreamSubscription<dynamic>? _overlaySub;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);

    // 拉一次当前游戏平台预设
    GamePlatform.current().then((p) {
      if (!mounted) return;
      setState(() {
        selectedPlatform = p;
        _platformReady = true;
      });
      _syncPlatformField(p);
    });

    // 拉一次本机已存的游戏ID（持久化在 shared_preferences，重启不丢）
    SessionStore.loadGameId().then((v) {
      if (!mounted) return;
      _setFieldText(_gameIdCtrl, v);
      setState(() => _gameIdNotice = v.isEmpty ? '' : '已保存 $v');
    });

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
      // 识别回传帧：喂给全局只读快照，主页「运势 / 好牌概率」两处按钮从这里取值。
      // 关键：不在这里 setState——识别高峰期每帧整页重建正是"点什么都没反应"的
      // 病根（教训见 _RecognitionStatusView 的局部订阅注释），这里只通知订阅了
      // 快照的那一小块结果区。
      if (event is Map && event['type'] == 'status') {
        EngineSnapshot.instance.push(Map<String, dynamic>.from(event));
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
      // 悬浮窗「切换玩法」：把玩法经主引擎 MethodChannel 转给 Java/引擎，并同步更新主页状态
      if (event is Map && event['type'] == 'set_mode') {
        final mode = event['mode'] as String?;
        if (mode != null && mode.isNotEmpty) {
          GameMode.set(mode).then((ok) {
            if (ok && mounted) {
              setState(() {
                selectedMode = mode;
              });
            }
          });
        }
      }
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      // 从系统设置开启权限或多任务切回前台：强制解除开窗忙碌标志，重检真实运行态与权限
      _overlayBusy = false;
      FlutterOverlayWindow.isActive().then((act) {
        if (!mounted) return;
        if (act != isProcessing) {
          setState(() => isProcessing = act);
        } else {
          setState(() {});
        }
      }).catchError((_) {
        if (mounted) setState(() {});
      });

      // 防破解/防绕过：切回前台立即核验授权有效性，失权立即终止识别并关窗
      if (isProcessing) {
        LicenseService.instance.ensureUsable().then((lic) {
          if (!lic.allowsUsage && mounted) {
            setProcessingState(false);
            hideOverlay();
            setState(() => isProcessing = false);
            ScaffoldMessenger.of(context).showSnackBar(SnackBar(
              content: Text(lic.status == LicenseStatus.licenseExpired
                  ? '卡密已到期，已停止识别服务'
                  : '卡密授权校验未通过，已停止识别服务'),
              backgroundColor: AppTokens.danger,
            ));
          }
        }).catchError((_) {});
      }
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _overlaySub?.cancel();
    _overlaySub = null;
    _platformCtrl.dispose();
    _gameIdCtrl.dispose();
    super.dispose();
  }

  // 悬浮窗开启/停止流程在飞标志：防用户重复点击重入（二次 showOverlay/closeOverlay
  // 与第一轮位置校正循环互相踩踏，是“开窗过程抽风”的常见诱因）。
  bool _overlayBusy = false;

  // 底部导航栏当前页（0=主页, 1=知识库, 2=调试）
  int _tab = 0;

  Future<void> setProcessingState(bool start) async {
    try {
      if (start) {
        final licState = await LicenseService.instance.ensureUsable();
        if (!licState.allowsUsage) {
          print('[LicCheck] License check failed before starting processing');
          return;
        }
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

  /// 开启悬浮窗；返回是否真正打开成功（失败时调用方必须保持待命态，
  /// 绝不能在服务未起来的情况下把 UI 标成“识别中”）。
  Future<bool> showOverlay() async {
    try {
      if (await FlutterOverlayWindow.isActive()) {
        _setStatus('悬浮窗已在运行');
        return true;
      }
      // 若未授予"显示在其他应用上层"权限，先引导到系统设置开启。
      bool granted = await FlutterOverlayWindow.isPermissionGranted() == true;
      if (!granted) {
        _setStatus('未授予悬浮窗权限，正在请求…');
        await FlutterOverlayWindow.requestPermission();
        // 用户去系统设置开启后切回，轮询 4 次（每次 150ms），确保系统 Settings 数据库落盘
        for (int i = 0; i < 4; i++) {
          if (await FlutterOverlayWindow.isPermissionGranted() == true) {
            granted = true;
            break;
          }
          await Future<void>.delayed(const Duration(milliseconds: 150));
        }
      }
      if (!granted) {
        // 权限未授予时悬浮窗无法显示，提示用户去系统设置开启。
        _setStatus('✗ 未授予"显示在其他应用上层"权限，悬浮窗无法显示');
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('悬浮窗需要"显示在其他应用上层"权限。请在设置中开启后重试。'),
            duration: Duration(seconds: 4),
          ));
        }
        return false;
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
      return true;
    } catch (e) {
      _setStatus('✗ showOverlay 异常：$e');
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('开启悬浮窗失败，请再试一次。若反复失败请反馈本机型与时间。'),
        ));
      }
      return false;
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
  /// 共享文件才 setState，手感就是“点一下卡半秒”）；异步落地失败再回滚并提示。
  /// 玩法切换不走网络、不等原生：纯本地 setState + 后台落盘，秒响应。
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

  Future<void> _selectPlatform(String platform) async {
    if (platform == selectedPlatform) return;
    final prev = selectedPlatform;
    setState(() => selectedPlatform = platform);
    final ok = await GamePlatform.set(platform);
    if (!ok && mounted) {
      if (selectedPlatform != platform) return;
      setState(() => selectedPlatform = prev);
      _syncPlatformField(prev ?? GamePlatform.defaultPlatform);
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text('切换到 ${GamePlatform.label(platform)} 失败，请重试'),
      ));
    } else if (mounted) {
      // 落地成功才把搜索框写成生效中的平台名：框里显示"想切的"而引擎在用
      // "别的"，是这个输入框最坏的骗人方式。
      _syncPlatformField(platform);
      final pInfo = GamePlatform.info(platform);
      if (pInfo != null && selectedMode != null) {
        if (!pInfo.supportedModes.contains(selectedMode)) {
          _selectMode(pInfo.defaultMode);
        }
      }
    }
  }

  /// 把当前生效平台写回搜索框（程序写入，不触发用户输入分支）。
  void _syncPlatformField(String platform) =>
      _setFieldText(_platformCtrl, GamePlatform.label(platform));

  /// 程序化写输入框：TextField 监听 controller，赋值同样会回调 onChanged，
  /// 所以用标志按住——否则"回填平台名"会被当成用户又输入了一次，自我回声。
  void _setFieldText(TextEditingController c, String v) {
    if (c.text == v) return;
    _syncingField = true;
    c.value = TextEditingValue(
      text: v,
      selection: TextSelection.collapsed(offset: v.length),
    );
    _syncingField = false;
  }

  /// 在 8 个已支持预设里做模糊匹配（key / 名称 / 副标题 / 徽章都算）。
  ///
  /// 只做"找得到"的模糊，不做"猜一个"的模糊：认不出就返回 null，由 UI 明确拒绝。
  /// 平台真值始终只有 selectedPlatform 一个，这个输入框是它的另一种输入法，
  /// 不是第二个可写的地方。
  GamePlatformInfo? _matchPlatform(String raw) {
    final q = raw.trim().toLowerCase();
    if (q.isEmpty) return null;
    GamePlatformInfo? best;
    int bestScore = 0;
    for (final p in GamePlatform.allPlatforms) {
      final cands = <String>[
        p.key,
        p.name.toLowerCase(),
        p.subtitle.toLowerCase(),
        p.badge.toLowerCase(),
      ];
      int s = 0;
      for (final t in cands) {
        if (t.isEmpty) continue;
        if (t == q) {
          s = 100;
          break;
        }
        if (t.startsWith(q) || t.contains(q)) {
          if (s < 60) s = 60;
        } else if (q.contains(t)) {
          if (s < 30) s = 30;
        }
      }
      if (s > bestScore) {
        bestScore = s;
        best = p;
      }
    }
    return bestScore >= 30 ? best : null;
  }

  void _onPlatformQueryChanged(String raw) {
    if (_syncingField) return;
    final q = raw.trim();
    if (q.isEmpty) {
      if (_platformNotice.isNotEmpty) setState(() => _platformNotice = '');
      return;
    }
    final hit = _matchPlatform(q);
    final notice = hit == null
        ? '没有匹配的平台预设：${GamePlatform.allPlatforms.map((e) => e.name).join('、')}'
        : '匹配到「${hit.name}」· 回车即切换';
    if (notice != _platformNotice) setState(() => _platformNotice = notice);
  }

  void _onPlatformQuerySubmitted(String raw) {
    if (_syncingField) return;
    final hit = _matchPlatform(raw);
    if (hit == null) {
      if (raw.trim().isNotEmpty) {
        setState(() => _platformNotice = '暂不支持该平台，请从上方预设选择');
      }
      return;
    }
    setState(() => _platformNotice = '已切换：${hit.name}');
    if (hit.key != selectedPlatform) _selectPlatform(hit.key);
  }

  void _onGameIdChanged(String raw) {
    if (_syncingField) return;
    final v = SessionStore.normalize(raw);
    final err = SessionStore.validate(v);
    if (err != null) {
      setState(() => _gameIdNotice = err);
      return;
    }
    // 空串=清空：也必须落盘，否则下次进来还看得见上一局的旧 ID（残留比空白更骗人）。
    SessionStore.saveGameId(v).then((ok) {
      if (!mounted) return;
      setState(() {
        if (!ok) {
          _gameIdNotice = '本地保存失败，请重试';
        } else {
          final cur = SessionStore.normalize(_gameIdCtrl.text);
          _gameIdNotice = cur.isEmpty ? '' : '已保存 $cur';
        }
      });
    });
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
          const KnowledgePage(),
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
                child: Icon(Icons.auto_stories_outlined),
              ),
              activeIcon: Padding(
                padding: EdgeInsets.only(bottom: 2),
                child: Icon(Icons.auto_stories_rounded),
              ),
              label: '知识库',
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
    final bool canStart = !isProcessing && _modeReady && _platformReady && mode.isNotEmpty;
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
            // 游戏平台预设快速切换栏
            _buildPlatformSelector(),
            // 对局信息：平台搜索直达 / 游戏ID / 运势概率 · 好牌概率
            _buildSessionCard(),
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

            const SizedBox(height: 14),

            // 设备信息卡片：作为页面内容的一部分随页面滚动（不固定悬浮在页面上层）。
            const DeviceInfoCard(),
          ],
        ),
      ),
    );
  }

  // 开始/停止识别
  Future<void> _toggleProcessing() async {
    if (_overlayBusy) return; // 防重入：开窗流程可耗时数秒，连点会踩踏服务
    setState(() => _overlayBusy = true); // 按钮立即进入“开启中”反馈态，
    // 旧实现静默吞点击，用户体感就是“点了没反应”。
    try {
      if (isProcessing) {
        setProcessingState(false);
        hideOverlay();
        setState(() => isProcessing = false);
      } else {
        // 卡密严密防线：启动识别前核验可用性，未激活/已到期直接拦截
        final licState = await LicenseService.instance.ensureUsable();
        if (!licState.allowsUsage) {
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(SnackBar(
              content: Text(licState.status == LicenseStatus.licenseExpired
                  ? '卡密授权已到期，请重新激活后使用'
                  : (licState.message ?? '未检测到有效卡密授权，无法开启识别')),
              backgroundColor: AppTokens.danger,
              duration: const Duration(seconds: 4),
            ));
          }
          return;
        }

        final opened = await showOverlay();
        // 开悬浮窗流程可长达数秒（权限/位置校正），期间若被授权闸门卸页面，
        // 绝不拿着已销毁的 context 再 setState。
        if (!mounted) return;
        if (!opened) return; // 开窗失败：留在待命态，不假装“识别中”
        // 新一轮识别 = 新的一局牌。上一帧的牌墙数字不属于这一局，留着让按钮
        // 继续显示旧百分比，就是把旧数据当新数据卖。
        EngineSnapshot.instance.clear();
        setProcessingState(true);
        setState(() => isProcessing = true);
      }
    } finally {
      if (mounted) setState(() => _overlayBusy = false);
    }
  }

  // 游戏平台预设快速切换栏（大尺寸卡片，舒适美观，视野开阔）
  Widget _buildPlatformSelector() {
    final curPlatform = selectedPlatform ?? GamePlatform.defaultPlatform;
    final platInfo = GamePlatform.info(curPlatform);

    Widget buildPlatformItem(GamePlatformInfo p) {
      final bool sel = p.key == curPlatform;
      return Expanded(
        child: GestureDetector(
          onTap: () => _selectPlatform(p.key),
          behavior: HitTestBehavior.opaque,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 180),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
            decoration: BoxDecoration(
              color: sel ? AppTokens.brandContainer : AppTokens.surface,
              borderRadius: BorderRadius.circular(AppTokens.r10),
              border: Border.all(
                color: sel ? AppTokens.brand : AppTokens.border,
                width: sel ? 1.5 : 0.8,
              ),
              boxShadow: sel
                  ? const [
                      BoxShadow(
                        color: Color(0x150F766E),
                        blurRadius: 4,
                        offset: Offset(0, 1.5),
                      ),
                    ]
                  : null,
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                if (sel) ...[
                  const Icon(Icons.check_circle_rounded,
                      size: 14, color: AppTokens.brandDark),
                  const SizedBox(width: 4),
                ],
                Flexible(
                  child: Text(
                    p.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 12.5,
                      fontWeight: sel ? FontWeight.bold : FontWeight.w500,
                      color: sel ? AppTokens.brandDark : AppTokens.ink,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      );
    }

    final pList = GamePlatform.allPlatforms;

    return Container(
      margin: const EdgeInsets.only(bottom: AppTokens.s12),
      padding: const EdgeInsets.all(AppTokens.s14),
      decoration: BoxDecoration(
        color: AppTokens.surface,
        borderRadius: BorderRadius.circular(AppTokens.r14),
        border: Border.all(color: AppTokens.border, width: 0.9),
        boxShadow: const [
          BoxShadow(
            color: Color(0x06000000),
            blurRadius: 5,
            offset: Offset(0, 2),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.devices_rounded, size: 17, color: AppTokens.brand),
              const SizedBox(width: 7),
              const Text(
                '游戏平台预设',
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.bold,
                  color: AppTokens.ink,
                ),
              ),
              const Spacer(),
              if (platInfo != null)
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                  decoration: BoxDecoration(
                    color: AppTokens.brandContainer,
                    borderRadius: BorderRadius.circular(AppTokens.r8),
                  ),
                  child: Text(
                    platInfo.badge,
                    style: const TextStyle(
                      fontSize: 10.5,
                      fontWeight: FontWeight.bold,
                      color: AppTokens.brandDark,
                    ),
                  ),
                ),
            ],
          ),
          const SizedBox(height: 10),
          // 平台大卡片 2 列排布，大按键、不截断、极易点击
          for (int i = 0; i < pList.length; i += 2) ...[
            if (i > 0) const SizedBox(height: 7),
            Row(
              children: [
                buildPlatformItem(pList[i]),
                if (i + 1 < pList.length) ...[
                  const SizedBox(width: 8),
                  buildPlatformItem(pList[i + 1]),
                ] else ...[
                  const SizedBox(width: 8),
                  const Spacer(),
                ],
              ],
            ),
          ],
          if (platInfo != null) ...[
            const SizedBox(height: 10),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
              decoration: BoxDecoration(
                color: AppTokens.pillBg,
                borderRadius: BorderRadius.circular(AppTokens.r8),
                border: Border.all(
                    color: AppTokens.border.withAlpha(80), width: 0.5),
              ),
              child: Row(
                children: [
                  const Icon(Icons.crop_free_rounded,
                      size: 14, color: AppTokens.brand),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      platInfo.subtitle,
                      style: const TextStyle(
                        fontSize: 11,
                        color: AppTokens.ink2,
                        height: 1.25,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  // 对局信息卡：平台搜索直达 + 游戏ID + 两个取数按钮（运势概率 / 好牌概率）。
  // 版式借用参考图（左标签 + 右圆角输入框），配色一律走 AppTokens，不另起色值。
  Widget _buildSessionCard() {
    final cur = selectedPlatform ?? GamePlatform.defaultPlatform;
    final pInfo = GamePlatform.info(cur);

    return Container(
      margin: const EdgeInsets.only(bottom: AppTokens.s12),
      padding: const EdgeInsets.all(AppTokens.s14),
      decoration: BoxDecoration(
        color: AppTokens.surface,
        borderRadius: BorderRadius.circular(AppTokens.r14),
        border: Border.all(color: AppTokens.border, width: 0.9),
        boxShadow: const [
          BoxShadow(
            color: Color(0x06000000),
            blurRadius: 5,
            offset: Offset(0, 2),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.assignment_outlined,
                  size: 17, color: AppTokens.brand),
              const SizedBox(width: 7),
              const Text(
                '对局信息',
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.bold,
                  color: AppTokens.ink,
                ),
              ),
              const Spacer(),
              // 徽章只报"引擎正在用哪个平台"，与卡片选中态同一个真值。
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                decoration: BoxDecoration(
                  color: AppTokens.brandContainer,
                  borderRadius: BorderRadius.circular(AppTokens.r8),
                ),
                child: Text(
                  pInfo != null ? pInfo.name : cur,
                  style: const TextStyle(
                    fontSize: 10.5,
                    fontWeight: FontWeight.bold,
                    color: AppTokens.brandDark,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          _buildFieldRow(label: '游戏平台', field: _buildPlatformField()),
          if (_platformNotice.isNotEmpty) ...[
            const SizedBox(height: 5),
            _buildNotice(_platformNotice),
          ],
          const SizedBox(height: 10),
          _buildFieldRow(label: '游戏ID', field: _buildGameIdField()),
          if (_gameIdNotice.isNotEmpty) ...[
            const SizedBox(height: 5),
            _buildNotice(_gameIdNotice),
          ],
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(child: _buildOddsButton('mood')),
              const SizedBox(width: 8),
              Expanded(child: _buildOddsButton('tile')),
            ],
          ),
          if (_oddsTab != null) ...[
            const SizedBox(height: 10),
            _OddsResultView(tab: _oddsTab!),
          ],
        ],
      ),
    );
  }

  Widget _buildFieldRow({required String label, required Widget field}) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        SizedBox(
          width: 62,
          child: Text(
            label,
            style: const TextStyle(
              fontSize: 12.5,
              fontWeight: FontWeight.w600,
              color: AppTokens.ink2,
            ),
          ),
        ),
        Expanded(child: field),
      ],
    );
  }

  InputDecoration _fieldDecoration(String hint) {
    OutlineInputBorder line(Color c, double w) => OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppTokens.r10),
          borderSide: BorderSide(color: c, width: w),
        );
    return InputDecoration(
      isDense: true,
      hintText: hint,
      hintStyle: const TextStyle(fontSize: 12.5, color: AppTokens.faint),
      filled: true,
      fillColor: AppTokens.pillBg,
      contentPadding:
          const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      border: line(AppTokens.border, 0.8),
      enabledBorder: line(AppTokens.border, 0.8),
      focusedBorder: line(AppTokens.brand, 1.2),
    );
  }

  Widget _buildNotice(String text) {
    return Padding(
      padding: const EdgeInsets.only(left: 62),
      child: Text(
        text,
        style: const TextStyle(
          fontSize: 11,
          color: AppTokens.muted,
          height: 1.3,
        ),
      ),
    );
  }

  Widget _buildPlatformField() {
    return TextField(
      controller: _platformCtrl,
      textInputAction: TextInputAction.done,
      onChanged: _onPlatformQueryChanged,
      onSubmitted: _onPlatformQuerySubmitted,
      style: const TextStyle(fontSize: 13, color: _kTextMain),
      decoration: _fieldDecoration('请输入游戏平台'),
    );
  }

  Widget _buildGameIdField() {
    return TextField(
      controller: _gameIdCtrl,
      textInputAction: TextInputAction.done,
      onChanged: _onGameIdChanged,
      style: const TextStyle(fontSize: 13, color: _kTextMain),
      // 字符集与本机存储上限在这里就拦住：让非法输入根本进不了控制器，
      // 比"先收下再报错"少一次误导（Java 侧写文件时用的是同一条正则）。
      inputFormatters: <TextInputFormatter>[
        FilteringTextInputFormatter.allow(RegExp(r'[A-Za-z0-9_\-]')),
        LengthLimitingTextInputFormatter(SessionStore.maxLen),
      ],
      decoration: _fieldDecoration('请输入游戏ID'),
    );
  }

  Widget _buildOddsButton(String tab) {
    final bool sel = _oddsTab == tab;
    final bool mood = tab == 'mood';
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () => setState(() => _oddsTab = sel ? null : tab),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 160),
        padding: const EdgeInsets.symmetric(vertical: 9),
        decoration: BoxDecoration(
          color: sel ? AppTokens.brandContainer : AppTokens.surface,
          borderRadius: BorderRadius.circular(AppTokens.r10),
          border: Border.all(
            color: sel ? AppTokens.brand : AppTokens.border,
            width: sel ? 1.4 : 0.9,
          ),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(
              mood ? Icons.auto_awesome_rounded : Icons.percent_rounded,
              size: 15,
              color: sel ? AppTokens.brandDark : AppTokens.muted,
            ),
            const SizedBox(width: 5),
            Text(
              mood ? '运势概率' : '好牌概率',
              style: TextStyle(
                fontSize: 12.5,
                fontWeight: sel ? FontWeight.bold : FontWeight.w500,
                color: sel ? AppTokens.brandDark : AppTokens.ink,
              ),
            ),
          ],
        ),
      ),
    );
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

  // 玩法卡片（紧凑轻盈版，大幅缩减高度，一屏容纳更多玩法）
  Widget _buildModeCard(MahjongModeInfo info, bool isSelected) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
        decoration: BoxDecoration(
          color: isSelected ? _kAccentBg : AppTokens.surface,
          borderRadius: BorderRadius.circular(AppTokens.r12),
          border: Border.all(
            color: isSelected ? _kAccent : _kBorder,
            width: isSelected ? 1.5 : 0.8,
          ),
          boxShadow: isSelected ? null : AppTokens.soft,
        ),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            borderRadius: BorderRadius.circular(AppTokens.r12),
            onTap: () => _selectMode(info.key),
            child: Padding(
              padding: const EdgeInsets.symmetric(
                  horizontal: AppTokens.s12, vertical: AppTokens.s8),
              child: Row(
                children: [
                  AnimatedContainer(
                    duration: const Duration(milliseconds: 180),
                    width: 32,
                    height: 32,
                    decoration: BoxDecoration(
                      color: isSelected
                          ? AppTokens.brandContainer
                          : AppTokens.pillBg,
                      borderRadius: BorderRadius.circular(AppTokens.r8),
                    ),
                    child: Icon(
                      Icons.spa_outlined,
                      size: 17,
                      color: isSelected ? _kAccent : AppTokens.muted,
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          info.name,
                          style: TextStyle(
                            fontSize: 13.5,
                            fontWeight: FontWeight.w600,
                            color: isSelected ? _kAccent : _kTextMain,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          info.brief,
                          style: const TextStyle(
                            fontSize: 11,
                            color: _kTextMuted,
                            height: 1.15,
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
                    size: 19,
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
    } else if (_overlayBusy) {
      btnColor = _kAccent.withAlpha(179); // alpha 0.7（CI 锁 Flutter 3.13，禁用 withValues）
      btnText = '开启中…';
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
        onPressed:
            (isProcessing || canStart) && !_overlayBusy ? _toggleProcessing : null,
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
    return Center(
      key: const ValueKey<String>('idle'),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.check_circle_outline_rounded,
              size: 13,
              color: AppTokens.faint.withAlpha(166), // alpha 0.65（3.13 兼容）
            ),
            const SizedBox(width: 5),
            Text(
              '支持主流麻将玩法 · 开启后悬浮窗自动跟随推演',
              style: TextStyle(
                fontSize: 11.5,
                color: AppTokens.faint.withAlpha(204), // alpha 0.8（3.13 兼容）
                letterSpacing: 0.2,
              ),
            ),
          ],
        ),
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

/// 「运势概率 / 好牌概率」结果区：只渲染最近一帧的引擎数据。
///
/// 订阅 EngineSnapshot（ChangeNotifier），每帧只重建这一小块，不整页 setState
/// ——与 _RecognitionStatusView 同一个性能约束。
///
/// 两个按钮的口径差别是硬约束，不是样式选择：
/// - **运势**：来源是未标定的模型估值，所以只出档位词（极优/较优/均势/承压）
///   加引擎自带的 note，**这一项结构上就没有百分号**；
/// - **好牌概率**：牌局账本里两个可逐张核对的整数相除，所以可以出百分号，
///   但方向词（至少/至多）与分子分母必须同屏，用户能自己复算一遍。
/// 没有帧数据时给空态并说明缺什么，绝不填 0%、也不把档位换成"中性"糊过去。
class _OddsResultView extends StatelessWidget {
  const _OddsResultView({Key? key, required this.tab}) : super(key: key);

  final String tab;

  // 配色只读 tier 三档词（Python `coarse_tier` 的投影），不在这里重算阈值。
  static Color _inkFor(String tier) {
    switch (tier) {
      case '偏优':
        return AppTokens.successDark;
      case '中性':
        return AppTokens.warn;
      case '偏劣':
        return AppTokens.danger;
      default:
        return AppTokens.muted;
    }
  }

  static Color _bgFor(String tier) {
    switch (tier) {
      case '偏优':
        return AppTokens.successBg;
      case '中性':
        return const Color(0xFFFFF7E6);
      case '偏劣':
        return const Color(0xFFFEF2F2);
      default:
        return AppTokens.pillBg;
    }
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: EngineSnapshot.instance,
      builder: (BuildContext context, Widget? child) {
        return Container(
          padding: const EdgeInsets.all(AppTokens.s12),
          decoration: BoxDecoration(
            color: AppTokens.brandSoft,
            borderRadius: BorderRadius.circular(AppTokens.r10),
            border: Border.all(color: AppTokens.border, width: 0.8),
          ),
          child: tab == 'mood'
              ? _moodBody(EngineSnapshot.instance)
              : _tileBody(EngineSnapshot.instance),
        );
      },
    );
  }

  Widget _moodBody(EngineSnapshot s) {
    final MoodReadout m = s.mood;
    if (!m.available) return _empty(m.reason);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(
              padding:
                  const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(
                color: _bgFor(m.tier),
                borderRadius: BorderRadius.circular(AppTokens.rPill),
              ),
              child: Text(
                m.band,
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.bold,
                  color: _inkFor(m.tier),
                ),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                m.badge,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: AppTokens.ink2,
                ),
              ),
            ),
          ],
        ),
        if (m.desc.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(
            m.desc,
            style: const TextStyle(
              fontSize: 11.5,
              color: AppTokens.muted,
              height: 1.35,
            ),
          ),
        ],
        if (m.insight.isNotEmpty) ...[
          const SizedBox(height: 5),
          Text(
            m.insight,
            style: const TextStyle(
              fontSize: 11,
              color: AppTokens.ink2,
              height: 1.35,
            ),
          ),
        ],
        const SizedBox(height: 7),
        _footnote(m.note.isEmpty ? m.caliber : m.note),
        _stamp(s),
      ],
    );
  }

  Widget _tileBody(EngineSnapshot s) {
    final TileReadout t = s.goodTile;
    if (!t.available) return _empty(t.missing);
    final double? p = t.percent;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.baseline,
          textBaseline: TextBaseline.alphabetic,
          children: [
            Text(
              p == null ? '—' : '${t.boundWord} ${p.toStringAsFixed(1)}%',
              style: TextStyle(
                fontSize: 20,
                fontWeight: FontWeight.w700,
                color: p == null ? AppTokens.muted : AppTokens.brandDark,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                t.isLowerBound ? '下一摸推进牌型（保守估计）' : '下一摸推进牌型（乐观估计）',
                style: const TextStyle(
                  fontSize: 11.5,
                  color: AppTokens.muted,
                ),
              ),
            ),
          ],
        ),
        if (t.formula.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(
            '${t.boundWord} ${t.numerator} 张可推进 ÷ 牌墙剩 ${t.denominator} 张',
            style: const TextStyle(
              fontSize: 11.5,
              color: AppTokens.ink2,
              height: 1.35,
            ),
          ),
        ],
        if (t.basis.isNotEmpty) ...[
          const SizedBox(height: 5),
          Text(
            '依据：${t.basis}',
            style: const TextStyle(
              fontSize: 11,
              color: AppTokens.muted,
              height: 1.35,
            ),
          ),
        ],
        if (t.caveat.isNotEmpty) ...[
          const SizedBox(height: 5),
          Text(
            t.caveat,
            style: const TextStyle(
              fontSize: 11,
              color: AppTokens.warn,
              height: 1.35,
            ),
          ),
        ],
        const SizedBox(height: 7),
        _footnote('张数取自牌局账本，可与牌河逐张核对'),
        _stamp(s),
      ],
    );
  }

  Widget _empty(String reason) {
    return Row(
      children: [
        const Icon(Icons.hourglass_empty_rounded,
            size: 15, color: AppTokens.faint),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            reason.isEmpty ? '开启识别后自动更新' : reason,
            style: const TextStyle(
              fontSize: 11.5,
              color: AppTokens.muted,
              height: 1.35,
            ),
          ),
        ),
      ],
    );
  }

  Widget _footnote(String text) {
    return Row(
      children: [
        const Icon(Icons.info_outline_rounded,
            size: 12, color: AppTokens.faint),
        const SizedBox(width: 4),
        Expanded(
          child: Text(
            text,
            style: const TextStyle(
              fontSize: 10.5,
              color: AppTokens.faint,
              height: 1.3,
            ),
          ),
        ),
      ],
    );
  }

  /// 数据来自哪一刻要落款：识别关掉之后这里仍显示上一次的数字，
  /// 没有时间戳就会把十分钟前的牌墙当成当前牌墙。
  Widget _stamp(EngineSnapshot s) {
    final String label = s.atLabel();
    if (label.isEmpty) return const SizedBox.shrink();
    final String tiles = s.tileCount > 0 ? ' · 手牌 ${s.tileCount} 张' : '';
    return Padding(
      padding: const EdgeInsets.only(top: 7),
      child: Text(
        '更新于 $label$tiles',
        style: const TextStyle(fontSize: 10, color: AppTokens.faint),
      ),
    );
  }
}
