import 'package:flutter/services.dart';

import 'channel.dart';

/// 玩法（人数）共享状态（仅经 Java MethodChannel 落地）。
///
/// - Dart 端通过 [GameMode.current] 异步读出"上次生效"的玩法（来源：
///   Java 写入的 /storage/emulated/0/Android/data/.../files/mahjong_mode.json）；
/// - 切换时调 [GameMode.set]，Java 端覆写该文件，Python 引擎每一帧从这里读取；
/// - 合法值仅 "2p" / "3p" / "4p"，默认 "4p"；
/// - 不走 SharedPreferences：避免 Flutter 侧再起一个存储路径与 Python
///   不一致引发的"两边值对不上"的隐患。
class GameMode {
  static const MethodChannel _ch = MethodChannel(CHANNEL_NAME);
  static const String defaultMode = '4p';
  static const List<String> allowed = ['2p', '3p', '4p'];

  /// 友好名（主页显示用）。
  static String label(String mode) {
    switch (mode) {
      case '2p':
        return '二人';
      case '3p':
        return '三人';
      default:
        return '四人';
    }
  }

  /// 读当前生效玩法；读不到走默认。
  static Future<String> current() async {
    try {
      final v = await _ch.invokeMethod<String>('getMode');
      if (v != null && allowed.contains(v)) return v;
    } catch (_) {}
    return defaultMode;
  }

  /// 切到指定玩法。返回是否落地成功。
  static Future<bool> set(String mode) async {
    if (!allowed.contains(mode)) return false;
    try {
      final rc = await _ch.invokeMethod<int>('setMode', {'mode': mode});
      return rc == 0;
    } catch (_) {
      return false;
    }
  }
}
