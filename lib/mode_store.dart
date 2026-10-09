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
      brief: '112张 · 缺一门 · 红中赖子 · 连胡到底',
      tags: ['112张', '连胡到底', '定缺门', '红中赖子'],
      status: '高倍竞技',
      wall: 112,
    ),
    MahjongModeInfo(
      key: 'gy_zj',
      name: '贵阳捉鸡',
      category: '川麻血流',
      subtitle: '西南顶流 · 金鸡乌骨鸡 · 豆杠全算 · 绝张叫牌推演',
      brief: '112张 · 捉鸡定缺 · 红中百搭 · 豆杠全算',
      tags: ['112张', '捉鸡算分', '缺一门', '红中百搭'],
      status: '西南顶流',
      wall: 112,
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
      subtitle: '北方大区王牌 · 胡牌必带幺九 · 严格判定 · 防诈胡',
      brief: '136张 · 必须带幺九 · 防诈胡',
      tags: ['136张', '带幺九', '严格判定', '防诈胡'],
      status: '东北王牌',
      wall: 136,
    ),
    MahjongModeInfo(
      key: 'hz_bd',
      name: '杭州百搭',
      category: '经典大众',
      subtitle: '华东高倍私庄 · 白板万能百搭 · 爆头大番最优解',
      brief: '136张 · 白板百搭 · 爆头大番',
      tags: ['136张', '白板百搭', '爆头大番', '可吃可碰'],
      status: '华东大客',
      wall: 136,
    ),

    // 3. 地方顶流系列
    MahjongModeInfo(
      key: 'gd_hz',
      name: '广东红中王',
      category: '地方顶流',
      subtitle: '华南第一 · 红中做鬼牌 · 鸡平胡 · 抓鸟买马翻倍',
      brief: '136张 · 红中做鬼 · 买马翻倍',
      tags: ['136张', '红中做鬼', '自摸买马', '极速胡牌'],
      status: '华南第一',
      // 牌集曾是 112（只留红中一枚字牌）。那是把川麻血流红中的牌集形状照抄到
      // 广东玩法上：广东麻将的底子本身就是 136 全牌，「红中王」只是把牌堆里
      // 既有的红中升为鬼牌。真机实证：雀神广东麻将「红中王」桌的手牌里明摆着
      // 北(4z) 与白板(5z)。牌集是分类之前的闸门，挂 112 会让这些字牌进不了候选，
      // 面板就「认不出特殊牌」。与 Python modes.gd_hz 同一条（test_new_modes 钉）。
      wall: 136,
    ),
    MahjongModeInfo(
      key: 'cs_zz',
      name: '长沙转转麻将',
      category: '地方顶流',
      subtitle: '华中核心 · 只能碰杠不能吃 · 全刻子转转胡 · 红中赖子',
      brief: '112张 · 不可吃 · 全刻子 · 转转抓鸟',
      tags: ['112张', '不可吃', '全刻子', '红中赖子'],
      status: '华中核心',
      wall: 112,
    ),

    // 4. 扩展玩法（与 modes.py 同步；每条至少在一个能被算法精确表达的维度上有
    //    真实差异：牌集 / 鬼牌 / 结构约束。副标题不承诺未建模的算分规则。）
    MahjongModeInfo(
      key: 'wz_tdh',
      name: '无字推倒胡',
      category: '经典大众',
      subtitle: '南方普及型 · 纯万筒条108张 · 无字牌无鬼 · 只看面子结构',
      brief: '108张 · 无字牌 · 无赖子',
      tags: ['108张', '无字牌', '无赖子', '平胡推倒'],
      status: '上手最快',
      wall: 108,
    ),
    MahjongModeInfo(
      key: 'hz_all',
      name: '红中麻将（全牌）',
      category: '经典大众',
      subtitle: '红中做万能鬼 · 136张全牌 · 可吃可碰 · 七对国士均可',
      brief: '136张 · 红中作鬼 · 全牌集',
      tags: ['136张', '红中作鬼', '七对', '国士'],
      status: '南方主流',
      wall: 136,
    ),
    MahjongModeInfo(
      key: 'fc_all',
      name: '发财麻将',
      category: '经典大众',
      subtitle: '以发财代替红中做鬼牌 · 136张全牌 · 鬼牌身份不同推荐即不同',
      brief: '136张 · 发财作鬼',
      tags: ['136张', '发财作鬼', '七对', '国士'],
      status: '部分地区',
      wall: 136,
    ),
    MahjongModeInfo(
      key: 'zfb_bd',
      name: '中发白三鬼',
      category: '经典大众',
      subtitle: '中发白三门皆鬼 · 高倍快节奏 · 三张鬼牌均不得弃打',
      brief: '136张 · 中发白全鬼',
      tags: ['136张', '三鬼牌', '高倍', '鬼牌保留'],
      status: '高倍玩法',
      wall: 136,
    ),
    MahjongModeInfo(
      key: 'pp_zz',
      name: '碰碰胡（无字）',
      category: '经典大众',
      subtitle: '只能碰杠不能吃 · 牌面必为全刻子+将 · 向听按碰碰胡口径算',
      brief: '108张 · 不可吃 · 全刻子',
      tags: ['108张', '不可吃', '全刻子', '无字牌'],
      status: '结构严格',
      wall: 108,
    ),
    MahjongModeInfo(
      key: 'mj_2p',
      name: '二人麻将（筒条）',
      category: '地方顶流',
      subtitle: '1v1 快节奏 · 只用筒条 72 张 · 手牌分析不依赖人数',
      brief: '72张 · 只筒条 · 双人',
      tags: ['72张', '只筒条', '双人', '无字牌'],
      status: '休闲对战',
      wall: 72,
    ),
    MahjongModeInfo(
      key: 'mj_3p',
      name: '三人竞技（去2/8）',
      category: '地方顶流',
      subtitle: '去 2/8 数牌与白板共 108 张 · 国士牌面不完整故不报国士',
      brief: '108张 · 去2/8 · 三人',
      tags: ['108张', '去2/8', '三人', '牌集修正'],
      status: '三人局',
      wall: 108,
    ),
    MahjongModeInfo(
      key: 'hz_ne',
      name: '红中麻将（全牌·禁吃）',
      category: '地方顶流',
      subtitle: '与红中全牌只差一个规则：不能吃 · 只能碰杠',
      brief: '136张 · 红中作鬼 · 不可吃',
      tags: ['136张', '红中作鬼', '不可吃', '七对'],
      status: '只能碰杠',
      wall: 136,
    ),
    MahjongModeInfo(
      key: 'sc_xz_3p',
      name: '川麻·三人血战',
      category: '川麻血流',
      subtitle: '三人血战到底 · 108张定缺 · 手牌分析与四人血战同构',
      brief: '108张 · 缺一门 · 三人',
      tags: ['108张', '缺一门', '三人局', '血战到底'],
      status: '三人川麻',
      wall: 108,
    ),
    // cf_wild（自选鬼牌）已在 Python 侧具备能力，但**暂不上架**：它的鬼牌由本局
    // 翻牌决定，而 Java/UI 的翻牌注入通道尚未接通。此时若开放选择，引擎会按
    // 「无鬼」计算 = 静默用错规则，比不提供更坑。接入 setLaizi 通道后再添加。
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
    'wz_tdh',
    'hz_all',
    'fc_all',
    'zfb_bd',
    'pp_zz',
    'mj_2p',
    'mj_3p',
    'hz_ne',
    'sc_xz_3p',
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
