import 'package:flutter/material.dart';

import 'mode_store.dart';
import 'theme/app_tokens.dart';

class KnowledgePage extends StatefulWidget {
  const KnowledgePage({super.key});

  @override
  State<KnowledgePage> createState() => _KnowledgePageState();
}

class _KnowledgePageState extends State<KnowledgePage> {
  int _categoryIndex = 0; // 0: 战术心法, 1: 玩法百科, 2: 智脑推演
  String _selectedModeKey = 'sc_hz';

  // ===== 1. 战术心法数据 =====
  static const List<Map<String, dynamic>> _tactics = [
    {
      'title': '金三银七 · 尖张法则',
      'tag': '进攻核心',
      'tagColor': Color(0xFFD97706),
      'tagBg': Color(0xFFFEF3C7),
      'icon': Icons.stars_rounded,
      'summary': '3万/筒/条与7万/筒/条是麻将中牌效延展性最强的黄金连搭牌。',
      'principles': [
        '连搭宽度极广：单张3能与1/2/4/5形成顺子搭子，覆盖4种有效进张；',
        '做雀头稳定：三七对子兼具两面听牌与碰牌价值，轻易不拆；',
        '防守警惕：中局对手若突然切出3或7，多半已进一向听或已叫听高危张。',
      ],
      'evRule': '在向听推进阶段，保留3/7搭子将赋予 +10.0 战术 EV 权重加成。',
    },
    {
      'title': '现物避炮 · 绝对防守',
      'tag': '绝对安全',
      'tagColor': Color(0xFF047857),
      'tagBg': Color(0xFFECFDF5),
      'icon': Icons.shield_rounded,
      'summary': '全桌牌河或特定对手已打出过的牌，本轮点炮率为零。',
      'principles': [
        '逆风跟熟：向听数深（>=2向听）且场上有人疑似下叫时，紧咬现物绝不生冲；',
        '同门避险：上家刚打出的牌，下家与对家跟打具有最高时效安全性；',
        '防大番点炮：宁可自拆未成型搭子，坚决不打全场0见的生张中张。',
      ],
      'evRule': '逆风局势下，打出现物牌将直接赋予 +25.0 战术防守加成。',
    },
    {
      'title': '筋牌防守 · Suji 推理',
      'tag': '防守博弈',
      'tagColor': Color(0xFF2563EB),
      'tagBg': Color(0xFFEFF6FF),
      'icon': Icons.timeline_rounded,
      'summary': '基于两面听牌（Ryanmen）特征推导的筋牌防线（1-4-7, 2-5-8, 3-6-9）。',
      'principles': [
        '两端已现：若对手出过1和7，中张4无法形成 2-3（听1-4）或 5-6（听4-7）的两面听，4为完全筋牌；',
        '半熟片筋：若对手仅打出1，则4仅防住了 2-3 边，仍需警惕 5-6 两面（片筋半熟）；',
        '单骑/嵌张盲区：筋牌仅防两面听，无法百分之百规避嵌张、边张或单吊。',
      ],
      'evRule': '无现物时优先出筋牌，知识库自动赋予 +12.0 安全加权。',
    },
    {
      'title': '四张壁牌 · Kabe 绝张',
      'tag': '高阶推断',
      'tagColor': Color(0xFF7C3AED),
      'tagBg': Color(0xFFF5F3FF),
      'icon': Icons.wb_shade_rounded,
      'summary': '场上可见4张相同的牌（Kabe/壁），其外侧相邻牌无法形成顺子。',
      'principles': [
        '外侧壁安全：若场上4张二筒全见，任何人都无法组成 2-3-4 顺子，一筒绝对无法成两面听；',
        '双重壁断路：若3与7各见4张，则 1/2 与 8/9 的外侧延展全部被封死；',
        '薄壁参考：若某张中张已见3张（薄壁），邻近外侧牌安全度亦大幅跃升。',
      ],
      'evRule': '壁牌安全推断生效时，候选打牌自动获得 +18.0 安全加权。',
    },
    {
      'title': '孤张决断 · 牌效优化',
      'tag': '起手布阵',
      'tagColor': Color(0xFF0D9488),
      'tagBg': Color(0xFFF0FDFA),
      'icon': Icons.tune_rounded,
      'summary': '开局两向听以上，排布孤张先后顺序直接决定向听推进速度。',
      'principles': [
        '字牌先走：单张风箭牌（东南西北中发白）无延展性，前三巡果断切出；',
        '幺九断边：1/9单张仅能与2或3连接，进张面狭窄，次优舍弃；',
        '留万能中张：4/5/6单张有双向进张能力（连3/4/5/6/7），厚度极高。',
      ],
      'evRule': '开局孤字切出赋予 +15.0 牌效加分；幺九孤张赋予 +8.0 加分。',
    },
    {
      'title': '定缺绝杀 · 川麻铁律',
      'tag': '川麻必修',
      'tagColor': Color(0xFFDC2626),
      'tagBg': Color(0xFFFEF2F2),
      'icon': Icons.block_rounded,
      'summary': '血战到底、血流红中开局必做定缺，排空一门方可胡牌。',
      'principles': [
        '排空防花猪：对局结束前若定缺门仍未打完，将按花猪赔付全场封顶番数；',
        '开局快速拔草：摸打前几巡不管进张多好，无脑优先打尽定缺色手牌；',
        '控叫逼退：定缺牌打完前不得留恋碰牌，争分夺秒进入清爽向听。',
      ],
      'evRule': '定缺牌打出优先级置顶，强制注入 +50.0 绝对优先 EV。',
    },
  ];

  // ===== 2. 玩法百科详尽番型与规则 =====
  static const Map<String, Map<String, dynamic>> _modeDetails = {
    'sc_hz': {
      'name': '血流红中 (全网顶流)',
      'tiles': '112张 (万/筒/条 108张 + 4/8张万能红中)',
      'features': '红中百搭 · 连胡到底 · 查大叫查花猪 · 换三张定缺',
      'rules': [
        '开局换三张（同花色顺子/对子换出），随后确定定缺门（万/筒/条必缺一门）；',
        '红中为万能赖子牌，可替代除缺门外的任意牌组成顺子、刻子、雀头；',
        '胡牌后不倒牌，继续摸打，一张手牌可以多次点炮胡、自摸胡直至摸完牌墙。',
      ],
      'fans': [
        {'name': '平胡', 'fan': '1番', 'desc': '基本胡牌牌型，顺子刻子组合'},
        {'name': '对对胡', 'fan': '2番', 'desc': '手牌全部由碰/刻子与雀头组成'},
        {'name': '清一色', 'fan': '4番', 'desc': '全部手牌为同一门花色（除红中外）'},
        {'name': '带幺九', 'fan': '4番', 'desc': '每组顺子/刻子及雀头均含一或九'},
        {'name': '七对', 'fan': '4番', 'desc': '七个对子组成，红中可做任意对'},
        {'name': '清对', 'fan': '8番', 'desc': '清一色 + 对对胡组合大番'},
        {'name': '金钩钓', 'fan': '4番', 'desc': '四副碰杠副露，手牌仅剩单张独钓'},
      ],
    },
    'sc_xz': {
      'name': '川麻·血战到底',
      'tiles': '108张 (万/筒/条 各36张，无字牌)',
      'features': '一家胡牌继续打 · 缺一门 · 查大叫查花猪 · 经典川麻精髓',
      'rules': [
        '必须定缺一门，手中定缺色未打光前严禁胡牌；',
        '一家胡牌后牌局不结束，其余三家继续战斗，直到三家胡牌或摸完流局；',
        '流局时进行查花猪（手中有定缺未出赔全场满番）与查大叫（没叫向有叫赔叫）。',
      ],
      'fans': [
        {'name': '素番平胡', 'fan': '1番', 'desc': '常规进张下叫'},
        {'name': '大对子', 'fan': '2番', 'desc': '四组刻子加一对'},
        {'name': '暗七对', 'fan': '4番', 'desc': '门清七对下叫'},
        {'name': '清一色', 'fan': '4番', 'desc': '纯单门花色'},
        {'name': '龙七对', 'fan': '8番', 'desc': '七对中含有4张相同的暗杠'},
        {'name': '杠上开花', 'fan': '+1番', 'desc': '开杠摸牌自摸胡'},
      ],
    },
    'sc_xl': {
      'name': '川麻·血流成河',
      'tiles': '108/112张 (万/筒/条 各36张，可选红中)',
      'features': '一张牌反复胡 · 多次胡牌 · 极限大番 · 控绝张博弈',
      'rules': [
        '与血战到底类似，但一家胡牌后不仅不离场，且胡过的牌继续留在牌桌上继续胡；',
        '极度考验绝张算牌与牌池记忆，活牌越少风险越高。',
      ],
      'fans': [
        {'name': '平胡', 'fan': '1番', 'desc': '基本胡牌'},
        {'name': '清一色', 'fan': '4番', 'desc': '纯色大番'},
        {'name': '清大对', 'fan': '8番', 'desc': '清一色碰碰胡'},
        {'name': '十八罗汉', 'fan': '32番', 'desc': '开满四个杠单钓'},
      ],
    },
    'std_tdh': {
      'name': '大众推倒胡 (全国通用)',
      'tiles': '136张 (万/筒/条 108张 + 东南西北中发白 28张)',
      'features': '全牌型吃碰杠听 · 经典稳赢平胡 · 牌面整洁节奏快',
      'rules': [
        '可吃、可碰、可杠，最纯粹的现代竞技麻将底模；',
        '手牌凑足四组顺子/刻子加一对雀头即可推倒胡牌；',
        '适合全国各省市休闲玩家无门槛快速畅玩。',
      ],
      'fans': [
        {'name': '点炮平胡', 'fan': '1番', 'desc': '抓炮推倒'},
        {'name': '门清自摸', 'fan': '2番', 'desc': '无吃碰副露自摸'},
        {'name': '混一色', 'fan': '3番', 'desc': '一门花色加上字牌'},
        {'name': '清一色', 'fan': '6番', 'desc': '全单一数牌花色'},
        {'name': '十三幺', 'fan': '88番', 'desc': '全幺九与字牌各一张加一雀头'},
      ],
    },
    'wh_kk': {
      'name': '武汉开口翻',
      'tiles': '136张 (万/筒/条 + 字牌 + 痞子癞子)',
      'features': '必须开口吃碰 · 痞子发财 · 翻倍封顶 · 防守极为严苛',
      'rules': [
        '必须开口（至少吃、碰或杠一次）方可具备胡牌资格，门清不可胡；',
        '开局翻出一张牌，其上一张即为“癞子（万能牌）”，发财为固定“痞子”；',
        '封顶制算番，对手大番下叫时防点炮是第一要义。',
      ],
      'fans': [
        {'name': '开口底胡', 'fan': '1番', 'desc': '完成吃碰基本开口'},
        {'name': '将一色', 'fan': '10番', 'desc': '全部由 2/5/8 组成的刻子雀头'},
        {'name': '风一色', 'fan': '10番', 'desc': '全部由东南西北中发白组成'},
        {'name': '碰碰胡', 'fan': '2番', 'desc': '四组碰刻'},
      ],
    },
    'db_qh': {
      'name': '东北穷胡',
      'tiles': '136张 (万/筒/条 + 东南西北中发白)',
      'features': '胡牌必须带幺九 · 必须闭门有刻 · 严格防诈胡',
      'rules': [
        '胡牌条件苛刻：手牌中必须包含幺或九（字牌亦可算幺九）；',
        '必须有刻子（碰或暗刻），必须有顺子，必须开门（不可纯门清）；',
        '四家缺门不能胡，极大考验排牌搭子构筑。',
      ],
      'fans': [
        {'name': '穷胡平胡', 'fan': '1番', 'desc': '满足幺九+顺刻条件平胡'},
        {'name': '飘胡', 'fan': '3番', 'desc': '全刻子碰碰胡带幺九'},
        {'name': '海底捞月', 'fan': '2番', 'desc': '最后一张自摸'},
      ],
    },
    'hz_bd': {
      'name': '杭州百搭',
      'tiles': '136张 (白板为万能百搭)',
      'features': '不可吃只能碰 · 白板百搭 · 爆头大番 · 抢杠暴击',
      'rules': [
        '白板作为万能百搭牌，不可出牌，但可化作任意牌凑顺刻；',
        '不能吃牌，只能碰牌与杠牌；',
        '听牌时手握单张百搭单吊自摸称为“爆头”，番数翻倍。',
      ],
      'fans': [
        {'name': '平胡', 'fan': '1番', 'desc': '常规胡牌'},
        {'name': '爆头', 'fan': '2番', 'desc': '手握百搭自摸胡'},
        {'name': '七宝', 'fan': '4番', 'desc': '手握四张白板万能牌'},
      ],
    },
    'gd_hz': {
      'name': '广东红中王',
      'tiles': '112/136张 (红中万能鬼牌)',
      'features': '红中做鬼 · 极速自摸 · 抓鸟买马 · 翻倍极爽',
      'rules': [
        '红中可充当任何牌，红中不可打出；',
        '胡牌后从牌墙按胡牌者方位摸 2~4 张牌作为“中鸟”，中中鸟筹码翻倍。',
      ],
      'fans': [
        {'name': '鸡胡', 'fan': '1番', 'desc': '基础自摸'},
        {'name': '无鬼自摸', 'fan': '2番', 'desc': '手中没有红中自摸胡牌'},
        {'name': '红中八只', 'fan': '32番', 'desc': '摸齐8张红中直接通吃'},
      ],
    },
    'cs_zz': {
      'name': '长沙转转麻将',
      'tiles': '112张 (万/筒/条 + 4张红中)',
      'features': '不能吃只能碰 · 全刻子转转胡 · 捉鸟翻倍 · 节奏极快',
      'rules': [
        '只能碰和杠，不能吃牌；',
        '红中不可打出，可作任意牌；点炮只能胡一人，抢杠胡可得大收益。',
      ],
      'fans': [
        {'name': '平胡', 'fan': '1番', 'desc': '基本碰碰或顺子胡'},
        {'name': '转转大碰', 'fan': '2番', 'desc': '四刻子对对胡'},
      ],
    },
    'gy_zj': {
      'name': '贵阳捉鸡',
      'tiles': '108张 (万/筒/条 + 红中百搭)',
      'features': '捉鸡算分 · 金鸡乌骨鸡 · 豆杠全算 · 西南特色顶流',
      'rules': [
        '胡牌后从牌墙翻开一张牌，下一张牌的花色点数为“鸡”（如翻三万，四万为鸡）；',
        '手中与打出的牌若有“鸡”，按张结算额外筹码；幺鸡称为“金鸡”，八条称为“乌骨鸡”。',
      ],
      'fans': [
        {'name': '平胡', 'fan': '1番', 'desc': '常规胡牌'},
        {'name': '大叫', 'fan': '2番', 'desc': '听大番'},
      ],
    },
  };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppTokens.bg,
      appBar: AppBar(
        title: const Text(
          '战术知识库 · 国手心法与玩法百科',
          style: TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.bold,
            color: AppTokens.ink,
          ),
        ),
        backgroundColor: AppTokens.surface,
        elevation: 0,
        centerTitle: false,
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(44),
          child: Container(
            decoration: const BoxDecoration(
              border: Border(
                bottom: BorderSide(color: AppTokens.border, width: 0.8),
              ),
            ),
            child: Row(
              children: [
                _buildCategoryTab(0, '战术心法 (6大定式)'),
                _buildCategoryTab(1, '玩法百科 (10大规则)'),
                _buildCategoryTab(2, '智脑推演 (AI原理)'),
              ],
            ),
          ),
        ),
      ),
      body: IndexedStack(
        index: _categoryIndex,
        children: [
          _buildTacticsBody(),
          _buildModesBody(),
          _buildAiCoreBody(),
        ],
      ),
    );
  }

  Widget _buildCategoryTab(int index, String title) {
    final bool active = _categoryIndex == index;
    return Expanded(
      child: GestureDetector(
        onTap: () => setState(() => _categoryIndex = index),
        behavior: HitTestBehavior.opaque,
        child: Container(
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(vertical: 10),
          decoration: BoxDecoration(
            border: Border(
              bottom: BorderSide(
                color: active ? AppTokens.brand : Colors.transparent,
                width: 2.2,
              ),
            ),
          ),
          child: Text(
            title,
            style: TextStyle(
              fontSize: 12,
              fontWeight: active ? FontWeight.bold : FontWeight.w500,
              color: active ? AppTokens.brandDark : AppTokens.muted,
            ),
          ),
        ),
      ),
    );
  }

  // ===== 1. 战术心法列表 =====
  Widget _buildTacticsBody() {
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      itemCount: _tactics.length,
      itemBuilder: (context, i) {
        final t = _tactics[i];
        final Color tagColor = t['tagColor'] as Color;
        final Color tagBg = t['tagBg'] as Color;
        final List<String> principles = (t['principles'] as List<String>);

        return Container(
          margin: const EdgeInsets.only(bottom: 12),
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: AppTokens.surface,
            borderRadius: BorderRadius.circular(AppTokens.r12),
            border: Border.all(color: AppTokens.border, width: 0.8),
            boxShadow: const [
              BoxShadow(
                color: Color(0x06000000),
                blurRadius: 6,
                offset: Offset(0, 2),
              ),
            ],
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(t['icon'] as IconData, color: tagColor, size: 20),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      t['title'] as String,
                      style: const TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.bold,
                        color: AppTokens.ink,
                      ),
                    ),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                    decoration: BoxDecoration(
                      color: tagBg,
                      borderRadius: BorderRadius.circular(AppTokens.r8),
                    ),
                    child: Text(
                      t['tag'] as String,
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.bold,
                        color: tagColor,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                t['summary'] as String,
                style: const TextStyle(
                  fontSize: 12,
                  color: AppTokens.ink2,
                  height: 1.4,
                ),
              ),
              const SizedBox(height: 10),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: AppTokens.pillBg,
                  borderRadius: BorderRadius.circular(AppTokens.r8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: principles.map((p) {
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('• ', style: TextStyle(color: AppTokens.brand, fontWeight: FontWeight.bold)),
                          Expanded(
                            child: Text(
                              p,
                              style: const TextStyle(fontSize: 11, color: AppTokens.ink2, height: 1.35),
                            ),
                          ),
                        ],
                      ),
                    );
                  }).toList(),
                ),
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  const Icon(Icons.bolt_rounded, color: Color(0xFFD97706), size: 14),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      t['evRule'] as String,
                      style: const TextStyle(
                        fontSize: 10.5,
                        fontWeight: FontWeight.w600,
                        color: Color(0xFFB45309),
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        );
      },
    );
  }

  // ===== 2. 玩法百科 =====
  /// 未录入百科条目的玩法：从目录元数据派生基础口径，**绝不回退到其它玩法的详情**。
  /// （早期写法是 `_modeDetails[key] ?? _modeDetails['sc_hz']!`，结果是用户点
  /// 「无字推倒胡」却读到一屏血流红中的规则与番型，属于静默错信息。）
  /// 派生卡只说牌集/结构这些与引擎一致的事实，不编造未校准的计分表。
  static Map<String, dynamic> _deriveBasics(MahjongModeInfo? info) {
    final m = info;
    if (m == null) {
      return {
        'name': '未知玩法',
        'tiles': '未登记的玩法 key',
        'features': '该玩法未在目录中，请切换到已登记玩法',
        'rules': <String>['目录中找不到当前 key，分析结果可能按默认规则计算。'],
        'fans': <Map<String, String>>[],
      };
    }
    return {
      'name': m.name,
      'tiles': '${m.wall}张 · ${m.category}',
      'features': m.subtitle,
      'rules': <String>[
        m.brief,
        '牌集与结构要点：${m.tags.join(' · ')}；',
        '引擎按「牌集 / 鬼牌 / 结构约束（可否吃牌、是否全刻子、七对/国士开关）」计算向听与出牌建议；',
        '本玩法的完整番型与计分表尚未录入，因此下方不展示任何分数，以免误导。',
      ],
      'fans': <Map<String, String>>[],
    };
  }

  Widget _buildModesBody() {
    final details = _modeDetails[_selectedModeKey] ??
        _deriveBasics(GameMode.info(_selectedModeKey));
    final fans = (details['fans'] as List<Map<String, String>>);
    final rules = (details['rules'] as List<String>);

    return Column(
      children: [
        // 玩法横向切片标签
        Container(
          color: AppTokens.surface,
          height: 48,
          child: ListView.separated(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            scrollDirection: Axis.horizontal,
            itemCount: GameMode.allModes.length,
            separatorBuilder: (_, __) => const SizedBox(width: 8),
            itemBuilder: (context, idx) {
              final m = GameMode.allModes[idx];
              final bool sel = m.key == _selectedModeKey;
              return GestureDetector(
                onTap: () => setState(() => _selectedModeKey = m.key),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
                  decoration: BoxDecoration(
                    color: sel ? AppTokens.brandContainer : AppTokens.pillBg,
                    borderRadius: BorderRadius.circular(AppTokens.r8),
                    border: Border.all(
                      color: sel ? AppTokens.brand : AppTokens.border,
                      width: sel ? 1.0 : 0.6,
                    ),
                  ),
                  child: Center(
                    child: Text(
                      m.name,
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: sel ? FontWeight.bold : FontWeight.normal,
                        color: sel ? AppTokens.brandDark : AppTokens.ink2,
                      ),
                    ),
                  ),
                ),
              );
            },
          ),
        ),
        // 详情内容
        Expanded(
          child: ListView(
            padding: const EdgeInsets.all(14),
            children: [
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: AppTokens.surface,
                  borderRadius: BorderRadius.circular(AppTokens.r12),
                  border: Border.all(color: AppTokens.border, width: 0.8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      details['name'] as String,
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                        color: AppTokens.ink,
                      ),
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        const Icon(Icons.style_outlined, size: 14, color: AppTokens.muted),
                        const SizedBox(width: 4),
                        Text(
                          details['tiles'] as String,
                          style: const TextStyle(fontSize: 11.5, color: AppTokens.muted),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Row(
                      children: [
                        const Icon(Icons.auto_awesome, size: 14, color: AppTokens.brand),
                        const SizedBox(width: 4),
                        Text(
                          details['features'] as String,
                          style: const TextStyle(fontSize: 11.5, color: AppTokens.brandDark, fontWeight: FontWeight.w600),
                        ),
                      ],
                    ),
                    const Divider(height: 20),
                    const Text('规则精要', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.bold, color: AppTokens.ink)),
                    const SizedBox(height: 6),
                    ...rules.map((r) => Padding(
                          padding: const EdgeInsets.only(bottom: 4),
                          child: Text('• $r', style: const TextStyle(fontSize: 11.5, color: AppTokens.ink2, height: 1.35)),
                        )),
                    // 番型表只在真的有条目时才渲染，避免新玩法出现“空标题”
                    if (fans.isNotEmpty) ...[
                      const Divider(height: 20),
                      const Text('核心番型与计分表', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.bold, color: AppTokens.ink)),
                      const SizedBox(height: 8),
                      ...fans.map((f) => Container(
                          margin: const EdgeInsets.only(bottom: 6),
                          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
                          decoration: BoxDecoration(
                            color: AppTokens.pillBg,
                            borderRadius: BorderRadius.circular(AppTokens.r8),
                          ),
                          child: Row(
                            children: [
                              Text(f['name']!, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: AppTokens.ink)),
                              const Spacer(),
                              Container(
                                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1.5),
                                decoration: BoxDecoration(
                                  color: AppTokens.brandContainer,
                                  borderRadius: BorderRadius.circular(4),
                                ),
                                child: Text(f['fan']!, style: const TextStyle(fontSize: 10.5, fontWeight: FontWeight.bold, color: AppTokens.brandDark)),
                              ),
                            ],
                          ),
                        )),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  // ===== 3. 智脑推演机制 =====
  Widget _buildAiCoreBody() {
    return ListView(
      padding: const EdgeInsets.all(14),
      children: [
        Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: AppTokens.surface,
            borderRadius: BorderRadius.circular(AppTokens.r12),
            border: Border.all(color: AppTokens.border, width: 0.8),
          ),
          child: const Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(Icons.psychology_rounded, color: AppTokens.brand, size: 22),
                  SizedBox(width: 8),
                  Text('AI 推演双核联动原理', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: AppTokens.ink)),
                ],
              ),
              SizedBox(height: 10),
              Text(
                '工具在对局中并非单纯枚举有效进张，而是将「博弈论期望值网络」与「国手战术知识库」进行实时双核权衡：',
                style: TextStyle(fontSize: 12, color: AppTokens.ink2, height: 1.45),
              ),
              SizedBox(height: 12),
              _PrincipleCard(
                step: '1',
                title: '向听数与活牌厚度 (Ukeire)',
                desc: '实时全牌河扣减绝张，精确计算 0~6 向听每种打法下的有效进张面与下叫速度。',
              ),
              _PrincipleCard(
                step: '2',
                title: '贝叶斯手牌透视 (Bayesian Range)',
                desc: '根据对手出牌轨迹与定缺逆推手牌分布，推断场上潜在叫口与高危流向。',
              ),
              _PrincipleCard(
                step: '3',
                title: '国手战术知识库加权 (KB Boost)',
                desc: '匹配金三银七、四张壁牌与现物防守法则，动态为建议出牌注入国手心法批注与 EV 加分。',
              ),
              _PrincipleCard(
                step: '4',
                title: '强化学习 Policy-Value 排序',
                desc: '融合稳胡极速流与大番收益流，输出最符合职业国手打牌逻辑的最优推荐。',
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _PrincipleCard extends StatelessWidget {
  final String step;
  final String title;
  final String desc;

  const _PrincipleCard({
    required this.step,
    required this.title,
    required this.desc,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 22,
            height: 22,
            alignment: Alignment.center,
            decoration: const BoxDecoration(
              color: AppTokens.brandContainer,
              shape: BoxShape.circle,
            ),
            child: Text(
              step,
              style: const TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.bold,
                color: AppTokens.brandDark,
              ),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.bold,
                    color: AppTokens.ink,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  desc,
                  style: const TextStyle(
                    fontSize: 11,
                    color: AppTokens.muted,
                    height: 1.35,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
