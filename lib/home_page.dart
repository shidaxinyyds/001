import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:realtime_mahjong_trainer/channel.dart';

class HomePage extends StatefulWidget {
  const HomePage({Key? key}) : super(key: key);

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  String? latestMessageFromOverlay;

  static const channel = MethodChannel(CHANNEL_NAME);

  bool isProcessing = false;

  @override
  void initState() {
    super.initState();

    // 接收悬浮窗内“停止”按钮发来的指令（悬浮窗与主 App 通过 shareData 通信）
    FlutterOverlayWindow.overlayListener.listen((event) {
      if (event == 'stop') {
        setProcessingState(false);
        hideOverlay();
        if (mounted) {
          setState(() {
            isProcessing = false;
          });
        }
      }
    });
  }

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
                '悬浮窗需要"显示在其他应用上层"权限。请到系统设置→应用→麻将训练器→权限中开启，再点一次"开始识别"。'),
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
        overlayTitle: "麻将助手",
        overlayContent: '麻将助手已开启',
        flag: OverlayFlag.defaultFlag,
        visibility: NotificationVisibility.visibilityPublic,
        positionGravity: PositionGravity.auto,
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text("麻将训练器"),
      ),
      body: LayoutBuilder(
        builder: (BuildContext context, BoxConstraints constraints) {
          return Center(
            child: Column(
              children: [
                TextButton(
                  onPressed: () {
                    permissions();
                  },
                  child: const Text("授权通知"),
                ),
                (isProcessing
                    ? TextButton(
                        onPressed: () {
                          setProcessingState(false);
                          hideOverlay();
                          setState(() {
                            isProcessing = false;
                          });
                        },
                        child: const Text("停止识别"),
                      )
                    : TextButton(
                        onPressed: () async {
                          // 先弹出悬浮按钮（需要“显示在其他应用上层”权限），再开始识别
                          await showOverlay();
                          setProcessingState(true);
                          setState(() {
                            isProcessing = true;
                          });
                        },
                        child: const Text("开始识别"),
                      )),
                TextButton(
                  onPressed: _refreshDiag,
                  child: const Text("刷新悬浮窗状态"),
                ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: SelectableText(
                    _status,
                    style: const TextStyle(fontSize: 13, color: Colors.blue),
                    textAlign: TextAlign.center,
                  ),
                ),
                const SizedBox(height: 12),
                const Padding(
                  padding: EdgeInsets.symmetric(horizontal: 16),
                  child: Text(
                    '提示：点"开始识别"后，屏幕右上角会出现一个橙色悬浮按钮（可拖动）。\n'
                    '若没出现，先把上面那行状态截图发我，可快速定位。',
                    style: TextStyle(fontSize: 12, color: Colors.grey),
                    textAlign: TextAlign.center,
                  ),
                ),
                Clock(),
              ],
            ),
          );
        },
      ),
    );
  }
}

class Clock extends StatefulWidget {
  const Clock({super.key});

  @override
  State<Clock> createState() => _ClockState();
}

class _ClockState extends State<Clock> {
  @override
  void initState() {
    super.initState();
    Timer.periodic(
        Duration(seconds: 1),
        (Timer t) => setState(() {
              time = DateTime.now();
            }));
  }

  DateTime time = DateTime.now();
  @override
  Widget build(BuildContext context) {
    return Text(time.toString());
  }
}
