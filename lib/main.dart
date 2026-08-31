import 'package:flutter/material.dart';
import 'package:realtime_mahjong_trainer/home_page.dart';
import 'package:realtime_mahjong_trainer/overlays/mahjong_overlay.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  _installNeutralErrorWidget();
  runApp(const MyApp());
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
  _installNeutralErrorWidget();
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
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: _appTheme,
      home: HomePage(),
    );
  }
}
