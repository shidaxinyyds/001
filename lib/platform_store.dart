import 'package:flutter/services.dart';

import 'channel.dart';

/// 游戏平台详细元数据
class GamePlatformInfo {
  final String key;
  final String name;
  final String subtitle;
  final String badge;
  final List<double> handRoi; // [top, bottom, left, right]
  final List<String> supportedModes;
  final String defaultMode;

  const GamePlatformInfo({
    required this.key,
    required this.name,
    required this.subtitle,
    required this.badge,
    required this.handRoi,
    required this.supportedModes,
    required this.defaultMode,
  });
}

/// 游戏平台预设管理（经 Java MethodChannel 与 Python 引擎同步联动）
class GamePlatform {
  static const MethodChannel _ch = MethodChannel(CHANNEL_NAME);
  static const String defaultPlatform = 'tencent';

  static const List<GamePlatformInfo> allPlatforms = [
    GamePlatformInfo(
      key: 'tencent',
      name: '腾讯欢乐麻将',
      subtitle: '官方标准画幅 · 手牌底端 30% · 四方牌河对齐',
      badge: '官方主流',
      handRoi: [0.70, 1.00, 0.00, 1.00],
      supportedModes: ['sc_hz', 'sc_xz', 'sc_xl', 'std_tdh', 'gd_hz', 'wh_kk'],
      defaultMode: 'sc_hz',
    ),
    GamePlatformInfo(
      key: 'tuyou',
      name: '途游四川麻将',
      subtitle: '紧凑手牌排版 · 预设上移 2% · 宽阔牌河',
      badge: '热门竞技',
      handRoi: [0.68, 0.98, 0.02, 0.98],
      supportedModes: ['sc_hz', 'sc_xz', 'cs_zz', 'db_qh'],
      defaultMode: 'sc_hz',
    ),
    GamePlatformInfo(
      key: 'weile',
      name: '微乐地方麻将',
      subtitle: '微乐经典宽屏 · 地方特色牌面 · 精密节距对齐',
      badge: '地方首选',
      handRoi: [0.69, 0.98, 0.01, 0.99],
      supportedModes: ['sc_xz', 'cs_zz', 'db_qh', 'std_tdh', 'hz_bd'],
      defaultMode: 'sc_xz',
    ),
    GamePlatformInfo(
      key: 'jj',
      name: 'JJ比赛麻将',
      subtitle: '专业竞技大厅 · 高对比牌桌 · 紧凑出牌排版',
      badge: '专业比赛',
      handRoi: [0.71, 0.99, 0.00, 1.00],
      supportedModes: ['std_tdh', 'sc_xz', 'sc_hz'],
      defaultMode: 'std_tdh',
    ),
    GamePlatformInfo(
      key: 'generic',
      name: '通用平台自适应',
      subtitle: 'HSV 掩码全局动态寻界 · 兼容所有平台与变体',
      badge: '全能兼容',
      handRoi: [0.70, 1.00, 0.00, 1.00],
      supportedModes: [
        'sc_hz', 'sc_xz', 'sc_xl', 'gy_zj', 'std_tdh',
        'wh_kk', 'db_qh', 'hz_bd', 'gd_hz', 'cs_zz'
      ],
      defaultMode: 'sc_hz',
    ),
  ];

  static const List<String> allowed = [
    'tencent',
    'tuyou',
    'weile',
    'jj',
    'generic',
  ];

  static GamePlatformInfo? info(String platformKey) {
    final k = platformKey.trim().toLowerCase();
    for (final p in allPlatforms) {
      if (p.key == k) return p;
    }
    return null;
  }

  static String label(String platformKey) {
    final inf = info(platformKey);
    return inf != null ? inf.name : platformKey;
  }

  static String shortBadge(String platformKey) {
    switch (platformKey.toLowerCase()) {
      case 'tencent':
        return '腾讯';
      case 'tuyou':
        return '途游';
      case 'weile':
        return '微乐';
      case 'jj':
        return 'JJ';
      default:
        return '通用';
    }
  }

  /// 读取当前生效的游戏平台预设；失败走默认 tencent
  static Future<String> current() async {
    try {
      final v = await _ch.invokeMethod<String>('getPlatform');
      if (v != null && allowed.contains(v.trim().toLowerCase())) {
        return v.trim().toLowerCase();
      }
    } catch (_) {}
    return defaultPlatform;
  }

  /// 切换游戏平台预设
  static Future<bool> set(String platformKey) async {
    final k = platformKey.trim().toLowerCase();
    if (!allowed.contains(k)) return false;
    try {
      final rc = await _ch.invokeMethod<int>('setPlatform', {'platform': k});
      return rc == 0;
    } catch (_) {
      return false;
    }
  }
}
