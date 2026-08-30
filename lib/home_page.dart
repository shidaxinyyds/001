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

  void showOverlay() async {
    if (await FlutterOverlayWindow.isActive()) {
      print("悬浮窗已开启");
      return;
    }
    if (await FlutterOverlayWindow.isPermissionGranted() != true) {
      if (await FlutterOverlayWindow.requestPermission() != true) {
        print("未授予悬浮窗权限");
        return;
      }
    }

    // 以 266x340 打开：悬浮窗内“悬浮按钮 + 分析面板”两块区域同时常驻，整窗可拖动。
    await FlutterOverlayWindow.showOverlay(
      enableDrag: true,
      overlayTitle: "麻将助手",
      overlayContent: '悬浮窗已开启',
      flag: OverlayFlag.defaultFlag,
      visibility: NotificationVisibility.visibilityPublic,
      positionGravity: PositionGravity.auto,
      alignment: OverlayAlignment.topRight,
      width: 266,
      height: 340,
    );
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
                        onPressed: () {
                          showOverlay();
                          setProcessingState(true);
                          setState(() {
                            isProcessing = true;
                          });
                        },
                        child: const Text("开始识别"),
                      )),
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
