import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:auto_vision/activation.dart';
import 'package:auto_vision/home_page.dart';
import 'package:auto_vision/license_gate.dart';
import 'package:auto_vision/overlays/mahjong_overlay.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  _installErrorGuards();
  runApp(const MyApp());
}

/// 全局错误兜底：杜绝「应用程序停止运行」式闪退。
///
/// 根因：Flutter 有三道会**直接终止进程**的崩溃路径，普通的 ErrorWidget.builder
/// 只能替换 UI、拦不住它们：
///   1. 未捕获的 Dart 异常（build/layout/async 抛错落到平台线程）—— 进程直接死；
///   2. PlatformDispatcher 层错误（平台回调里抛错）—— 进程直接死；
///   3. FlutterError（非 debug 下默认也会终止）。
/// 这里统一装三道 handler：打印日志但不向上抛，进程继续活。
/// 即便某一帧识别结果字段异常、某次解码失败，悬浮窗也只会显示占位，
/// 而不是整窗消失、App 重启。
void _installErrorGuards() {
  // 平台层（平台回调 / isolate 错误）兜底，返回 true = 已处理，不终止。
  ui.PlatformDispatcher.instance.onError = (Object error, StackTrace stack) {
    FlutterError.reportError(FlutterErrorDetails(
      exception: error,
      stack: stack,
      library: 'app-guard',
      context: ErrorDescription('全局兜底：已拦截，未终止进程'),
    ));
    return true;
  };
  // 普通 FlutterError 改为仅上报（不再触发红色错误页 / 终止）。
  FlutterError.onError = (FlutterErrorDetails details) {
    FlutterError.dumpErrorToConsole(details, forceReport: true);
  };
  _installNeutralErrorWidget();
}

/// 彻底消除"红色字符"的最后一道防线。
///
/// 根因说明：Flutter 在 debug 构建下有两个内建的红色渲染路径，与业务配色无关，
/// 无法通过改 Theme 消除：
///   1. RenderFlex overflow —— 溢出时在越界侧画黄黑条纹，并叠加红色
///      "BOTTOM OVERFLOWED BY x PIXELS" 文字；
///   2. ErrorWidget —— 任何 build/layout 抛异常都会渲染满屏红底白字的错误页。
/// 悬浮窗被拖到很小尺寸时，正是这两条路径最容易被触发的时刻。
///
/// 布局侧已通过"固定分段高度 + 段内 SingleChildScrollView"消除了 overflow 来源；
/// 这里再把 ErrorWidget 替换为中性灰占位，保证即使出现未预期异常，
/// 界面也绝不会出现红色字符。
void _installNeutralErrorWidget() {
  ErrorWidget.builder = (FlutterErrorDetails details) {
    return Container(
      color: const Color(0xFF37474F), // blueGrey 800，中性
      alignment: Alignment.center,
      padding: const EdgeInsets.all(8),
      child: const Text(
        '界面刷新中…',
        textAlign: TextAlign.center,
        style: TextStyle(
          color: Colors.white,
          fontSize: 12,
          decoration: TextDecoration.none,
        ),
      ),
    );
  };
}

// 全局主题：移除所有文字下划线装饰
final ThemeData _appTheme = ThemeData(
  useMaterial3: false,
  textTheme: const TextTheme(
    bodyLarge: TextStyle(decoration: TextDecoration.none),
    bodyMedium: TextStyle(decoration: TextDecoration.none),
    bodySmall: TextStyle(decoration: TextDecoration.none),
    labelLarge: TextStyle(decoration: TextDecoration.none),
    labelMedium: TextStyle(decoration: TextDecoration.none),
    labelSmall: TextStyle(decoration: TextDecoration.none),
    titleMedium: TextStyle(decoration: TextDecoration.none),
    titleSmall: TextStyle(decoration: TextDecoration.none),
  ),
);

// 悬浮窗入口：flutter_overlay_window 会在独立的 Flutter 引擎中以该函数作为入口点启动。
@pragma("vm:entry-point")
void overlayMain() {
  WidgetsFlutterBinding.ensureInitialized();
  _installErrorGuards();
  runApp(
    MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: _appTheme,
      home: MahjongOverlay(),
    ),
  );
}

class MyApp extends StatefulWidget {
  const MyApp({Key? key}) : super(key: key);

  @override
  State<MyApp> createState() => _MyAppState();
}

class _MyAppState extends State<MyApp> {
  // 启动时读取本地激活状态：已激活则直接进入主页，未激活才显示卡密页。
  bool? _activated;

  @override
  void initState() {
    super.initState();
    isActivated().then((v) {
      if (mounted) setState(() => _activated = v);
    });
  }

  @override
  Widget build(BuildContext context) {
    late final Widget home;
    if (_activated == null) {
      // 读取激活状态期间显示一个极简 loading，避免首帧白屏/闪烁。
      home = const _Splash();
    } else {
      home = _activated! ? const HomePage() : const LicenseGate();
    }
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: _appTheme,
      home: home,
    );
  }
}

/// 启动占位页：仅在读取本地激活状态的极短时间内显示。
class _Splash extends StatelessWidget {
  const _Splash();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      backgroundColor: Color(0xFF0E1116),
      body: Center(
        child: CircularProgressIndicator(color: Color(0xFF00695C)),
      ),
    );
  }
}
