import 'package:flutter/services.dart';

import 'channel.dart';

/// 单个麻将玩法的详细元数据
class MahjongModeInfo {
  final String key;
  final String name;
  final String category; // '川麻血流', '经典大众', '地方顶流'
  final String subtitle;
  final String brief;
  final List<String> tags;
  final String status;
  final int wall;

  const MahjongModeInfo({
    required this.key,
    required this.name,
    required this.category,
    required this.subtitle,
    required this.brief,
    required this.tags,
    required this.status,
    required this.wall,
  });
}

/// 玩法共享状态（经 Java MethodChannel 落地到 mahjong_mode.json）。
class GameMode {
  static const MethodChannel _ch = MethodChannel(CHANNEL_NAME);
  static const String defaultMode = 'sc_hz';

  static const List<String> categories = ['川麻血流', '经典大众', '地方顶流'];

  static const List<MahjongModeInfo> allModes = [
    // 1. 川麻血流系列 (占手游 60%+ 流量)
    MahjongModeInfo(
      key: 'sc_hz',
      name: '血流红中',
      category: '川麻血流',
      subtitle: '全网顶流 · 4/8红中百搭 · 连胡到底 · 实时最高番推荐',
      brief: '112张 · 红中百搭 · 连胡到底',
      tags: ['112张', '万能赖子', '自动定缺', '绝张避炮'],
      status: '全网NO.1',
      wall: 112,
    ),
    MahjongModeInfo(
      key: 'sc_xz',
      name: '川麻·血战到底',
      category: '川麻血流',
      subtitle: '经典川麻 · 缺门查叫 · 一家胡牌继续打 · 防点炮控牌',
      brief: '108张 · 缺一门 · 查大叫',
      tags: ['108张', '缺一门', '查大叫查花猪', '活牌监控'],
      status: '常青王牌',
      wall: 108,
    ),
    MahjongModeInfo(
      key: 'sc_xl',
      name: '川麻·血流成河',
      category: '川麻血流',
      subtitle: '高倍竞技 · 一张牌多次胡 · 绝张控牌 · 局局大番',
      brief: '108张 · 缺一门 · 连胡到底',
      tags: ['108张', '连胡到底', '定缺门', '算番神器'],
      status: '高倍竞技',
      wall: 108,
    ),
    MahjongModeInfo(
      key: 'gy_zj',
      name: '贵阳捉鸡',
      category: '川麻血流',
      subtitle: '西南顶流 · 金鸡乌骨鸡 · 豆杠全算 · 绝张叫牌推演',
      brief: '108张 · 捉鸡定缺 · 豆杠全算',
      tags: ['108张', '捉鸡算分', '缺一门', '杠牌监控'],
      status: '西南顶流',
      wall: 108,
    ),

    // 2. 经典大众系列
    MahjongModeInfo(
      key: 'std_tdh',
      name: '大众推倒胡',
      category: '经典大众',
      subtitle: '全国通用 · 136张全牌 · 吃碰杠听 · 经典稳赢平胡',
      brief: '136张全牌 · 吃碰杠听',
      tags: ['136张全牌', '东南西北中发白', '吃碰杠', '向听推演'],
      status: '全国通用',
      wall: 136,
    ),
    MahjongModeInfo(
      key: 'wh_kk',
      name: '武汉开口翻',
      category: '经典大众',
      subtitle: '技术流博弈 · 必须开口 · 痞子癞子 · 封顶避炮推演',
      brief: '136张 · 必须开口 · 痞子癞子',
      tags: ['136张', '必须开口', '痞子癞子', '封顶算番'],
      status: '湖北第一',
      wall: 136,
    ),
    MahjongModeInfo(
      key: 'db_qh',
      name: '东北穷胡',
      category: '经典大众',
      subtitle: '北方大区王牌 · 必须带幺九 · 三门齐开门 · 防诈胡',
      brief: '136张 · 必须带幺九 · 三门齐',
      tags: ['136张', '带幺九', '三门齐', '严格判定'],
      status: '东北王牌',
      wall: 136,
    ),
    MahjongModeInfo(
      key: 'hz_bd',
      name: '杭州百搭',
      category: '经典大众',
      subtitle: '华东高倍私庄 · 白板万能百搭 · 爆头大番最优解',
      brief: '136张 · 白板百搭 · 爆头大番',
      tags: ['136张', '白板百搭', '爆头大番', '不可吃'],
      status: '华东大客',
      wall: 136,
    ),

    // 3. 地方顶流系列
    MahjongModeInfo(
      key: 'gd_hz',
      name: '广东红中王',
      category: '地方顶流',
      subtitle: '华南第一 · 红中做鬼牌 · 鸡平胡 · 抓鸟买马翻倍',
      brief: '100张 · 红中做鬼 · 买马翻倍',
      tags: ['100张/112张', '红中做鬼', '自摸买马', '极速胡牌'],
      status: '华南第一',
      wall: 100,
    ),
    MahjongModeInfo(
      key: 'cs_zz',
      name: '长沙转转麻将',
      category: '地方顶流',
      subtitle: '华中核心 · 起手四喜六六顺 · 红中自摸抓鸟翻倍',
      brief: '108张 · 起手大番 · 转转抓鸟',
      tags: ['108张', '起手大番', '自摸抓鸟', '万能赖子'],
      status: '华中核心',
      wall: 108,
    ),
  ];

  static const List<String> allowed = [
    'sc_hz',
    'sc_xz',
    'sc_xl',
    'gy_zj',
    'std_tdh',
    'wh_kk',
    'db_qh',
    'hz_bd',
    'gd_hz',
    'cs_zz',
    'sc',
    '4p',
    '3p',
    '2p',
  ];

  /// 别名规范化
  static String normalizeKey(String mode) {
    if (mode == 'sc') return 'sc_xz';
    if (mode == '4p') return 'std_tdh';
    return mode;
  }

  /// 获取指定模式的详细信息
  static MahjongModeInfo? info(String mode) {
    final norm = normalizeKey(mode);
    for (final m in allModes) {
      if (m.key == norm) return m;
    }
    return null;
  }

  /// 友好显示名
  static String label(String mode) {
    final inf = info(mode);
    if (inf != null) return inf.name;
    switch (mode) {
      case 'sc':
        return '川麻·血战到底';
      case '4p':
        return '大众推倒胡';
      case '3p':
        return '三人竞技';
      case '2p':
        return '二人麻将';
      default:
        return mode;
    }
  }

  /// 读当前生效玩法；读不到走默认。
  static Future<String> current() async {
    try {
      final v = await _ch.invokeMethod<String>('getMode');
      if (v != null && allowed.contains(v)) {
        return normalizeKey(v);
      }
    } catch (_) {}
    return defaultMode;
  }

  /// 切到指定玩法。返回是否落地成功。
  static Future<bool> set(String mode) async {
    final norm = normalizeKey(mode);
    if (!allowed.contains(norm)) return false;
    try {
      final rc = await _ch.invokeMethod<int>('setMode', {'mode': norm});
      return rc == 0;
    } catch (_) {
      return false;
    }
  }
}
