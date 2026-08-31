import 'package:flutter/material.dart';
import 'package:realtime_mahjong_trainer/home_page.dart';
import 'package:realtime_mahjong_trainer/overlays/mahjong_overlay.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const MyApp());
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
