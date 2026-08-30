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

    channel.setMethodCallHandler((MethodCall call) async {
      if (call.method == 'deliverAnalysis') {
        // Note: It is only possible to shareData from main to overlay with thi
        await FlutterOverlayWindow.shareData(call.arguments);
        return 0;
      }
      return null;
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
      print("Overlay already open.");
      return;
    }
    if (await FlutterOverlayWindow.isPermissionGranted() != true) {
      if (await FlutterOverlayWindow.requestPermission() != true) {
        print("Permission not granted");
        return;
      }
    }

    await FlutterOverlayWindow.showOverlay(
      enableDrag: true,
      overlayTitle: "X-SLAYER",
      overlayContent: 'Overlay Enabled',
      flag: OverlayFlag.clickThrough,
      visibility: NotificationVisibility.visibilityPublic,
      positionGravity: PositionGravity.auto,
      width: WindowSize.matchParent,
      height: WindowSize.matchParent,
      alignment: OverlayAlignment.bottomCenter,
    );
  }

  void hideOverlay() async {
    await FlutterOverlayWindow.closeOverlay();
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
    double width = MediaQuery.of(context).size.width;
    double height = MediaQuery.of(context).size.height;
    double devicePixelRatio = MediaQuery.of(context).devicePixelRatio;

    return Scaffold(
      appBar: AppBar(
        title: const Text("Mahjong"),
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
                  child: Text("Grant permissions"),
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
                        child: Text("Stop Streaming"),
                      )
                    : TextButton(
                        onPressed: () {
                          showOverlay();
                          setProcessingState(true);
                          setState(() {
                            isProcessing = true;
                          });
                        },
                        child: Text("Start Streaming"),
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
