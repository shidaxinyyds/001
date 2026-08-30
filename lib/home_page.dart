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

  Future<void> showOverlay() async {
    try {
      if (await FlutterOverlayWindow.isActive()) {
        print("悬浮窗已开启");
        return;
      }
      // 若未授予“显示在其他应用上层”权限，先引导到系统设置开启。
      bool granted = await FlutterOverlayWindow.isPermissionGranted() == true;
      if (!granted) {
        granted = await FlutterOverlayWindow.requestPermission() == true;
      }
      if (!granted) {
        // 权限未授予时悬浮窗无法显示，提示用户去系统设置开启。
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text(
                '悬浮窗需要“显示在其他应用上层”权限。请到系统设置→应用→麻将训练器→权限中开启，再点一次“开始识别"。'),
            duration: Duration(seconds: 6),
          ));
        }
        print("未授予悬浮窗权限，无法显示悬浮按钮");
        return;
      }

      // 先以 60x60 的“悬浮按钮”形态出现（可拖动，常驻屏幕）；
      // 用户点击按钮后由悬浮窗自身 resizeOverlay 展开为分析小窗口。
      await FlutterOverlayWindow.showOverlay(
        enableDrag: true,
        overlayTitle: "麻将助手",
        overlayContent: '麻将助手',
        flag: OverlayFlag.defaultFlag,
        visibility: NotificationVisibility.visibilityPublic,
        positionGravity: PositionGravity.auto,
        alignment: OverlayAlignment.topRight,
        width: 60,
        height: 60,
      );
    } catch (e) {
      print("showOverlay error: $e");
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
                const SizedBox(height: 12),
                const Text(
                  '提示：点“开始识别”后，屏幕右上角会出现一个橙色悬浮按钮（可拖动）。\n'
                  '若没出现，请到系统设置→应用→麻将训练器→权限，开启“显示在其他应用上层”，再点一次。',
                  style: TextStyle(fontSize: 12, color: Colors.grey),
                  textAlign: TextAlign.center,
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
