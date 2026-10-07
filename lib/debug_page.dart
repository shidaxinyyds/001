import 'package:flutter/material.dart';
import 'package:auto_vision/config_store.dart';
import 'package:auto_vision/theme/app_tokens.dart';

/// 配置页：所有开关都**真实下发给识别引擎**，没有一个是纯 UI 摆设。
///
/// 分组与引擎侧实现一一对应：
/// - 识别策略（auto_orient / bootstrap / strict）→ MethodChannel `setConfig`
/// - 出牌建议（show_advice / min_ukeire）→ MethodChannel `setAdviceConfig`
///
/// 页面切换由主页底部导航栏（主页 / 调试）完成，故本页不自带页头与返回箭头。
class DebugPage extends StatefulWidget {
  const DebugPage({Key? key}) : super(key: key);

  @override
  State<DebugPage> createState() => _DebugPageState();
}

class _DebugPageState extends State<DebugPage> {
  // 与主页/设计 token 一致的配色（品牌青强调 / 主文本 / 次文本）。
  static const Color _kAccent = AppTokens.brand;
  static const Color _textMain = AppTokens.ink;
  static const Color _textSub = AppTokens.muted;

  DebugConfig _cfg = DebugConfig();
  bool _loading = true;
  bool _applying = false;

  int _fortuneIndex = 0;

  /// 心态签文（原自带一个 `score` 装饰分值，已删：它会被面板渲染成「胜势指数 98%」
  /// 这种看着像测量结果的伪指标，而牌局里没有任何算式能导出它）。
  static const List<Map<String, String>> _kFortunes = [
    {
      'title': '鸿运当头 · 紫气东来',
      'level': '上上大吉',
      'direction': '东南生财 · 迎财入座',
      'element': '条子顺风 · 连珠大吉',
      'quote': '牌顺乘风破浪，牌逆静水流深。手牌不济莫慌乱，守住现物保金身。',
      'comfort': '当前牌势蒸蒸日上，气场强盛！积极做大番牌，敢打敢拼，胜利在握。',
    },
    {
      'title': '金汤固守 · 蓄势待发',
      'level': '静水深流',
      'direction': '正南护财 · 坐镇中军',
      'element': '万字通达 · 稳扎稳打',
      'quote': '逆风防守守其险，顺风进攻取其胜。急躁乃败军之由，定心即立于不败之地。',
      'comfort': '起手牌杂切莫急躁，跟打熟张不点炮；守住底分，转机与大牌往往后发制人。',
    },
    {
      'title': '潜龙在渊 · 必有大成',
      'level': '厚积薄发',
      'direction': '正东聚气 · 巧借东风',
      'element': '筒子圆满 · 逢叫必和',
      'quote': '胜负皆常理，心定牌自通。深吸一口气，保持严谨决策，牌流自会回转。',
      'comfort': '牌局瞬息万变，AI 已为您实时锁定最高 EV 期望与绝张防守，从容应对即可。',
    },
    {
      'title': '龙腾四海 · 势如破竹',
      'level': '雀圣神威',
      'direction': '西南纳祥 · 顺风破浪',
      'element': '大番聚气 · 金钩迎春',
      'quote': '宁弃一手烂牌，不放一人点炮。稳扎稳打控全场，顺势而为定乾坤。',
      'comfort': '对手弃牌动向已全息推导，跟打现物安全张，静待高番反戈一击！',
    },
    {
      'title': '泰然自若 · 稳如磐石',
      'level': '定心无量',
      'direction': '正西生金 · 心境如水',
      'element': '全色兼备 · 气度非凡',
      'quote': '心怀平常心，算尽盘中理。牌运有波峰波谷，真正的高手胜在心境沉着。',
      'comfort': '戒骄戒躁，把注意力交给当下每一次进张与舍牌，长线胜率必然眷顾沉着之人。',
    },
  ];

  void _cycleFortune() {
    setState(() {
      _fortuneIndex = (_fortuneIndex + 1) % _kFortunes.length;
    });
    ScaffoldMessenger.of(context).hideCurrentSnackBar();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        behavior: SnackBarBehavior.floating,
        backgroundColor: AppTokens.ink,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        content: Row(
          children: const [
            Icon(Icons.auto_awesome, color: Color(0xFFFBBF24), size: 17),
            SizedBox(width: 8),
            Expanded(
              child: Text(
                '气场提振成功：心态归宁，胜势气场已达巅峰！',
                style: TextStyle(fontSize: 13, color: Colors.white, fontWeight: FontWeight.w500),
              ),
            ),
          ],
        ),
        duration: const Duration(seconds: 2),
      ),
    );
  }

  @override
  void initState() {
    super.initState();
    _loadAndApply();
  }

  Future<void> _loadAndApply() async {
    final c = await DebugConfig.load();
    if (!mounted) return;
    setState(() {
      _cfg = c;
      _loading = false;
    });
    // 关键：把持久化的配置推给引擎。引擎侧默认是「全开 / 不过滤」，
    // 上次关闭过某项若不重新下发，就会出现「开关显示关、引擎仍开着」的鬼影。
    await c.apply();
  }


  /// 开关/档位变更：立即保存并下发（即时生效，符合直觉）。
  ///
  /// [clearDumpedFrames] / [clearRiverFrames]：仅对应采集开关**拨开**时为 true——
  /// 先显式清空旧数据。初始化同步绝不清空，防止重启丢失已采帧。
  Future<void> _update(
    DebugConfig next, {
    bool clearDumpedFrames = false,
    bool clearRiverFrames = false,
  }) async {
    setState(() => _cfg = next);
    await next.save();
    await next.apply(
      clearDumpedFrames: clearDumpedFrames,
      clearRiverFrames: clearRiverFrames,
    );
  }

  /// 「确认配置」：重发一次全部配置，并给出真实成败反馈。
  Future<void> _confirm() async {
    setState(() => _applying = true);
    final ok = await _cfg.apply();
    await _cfg.save();
    if (!mounted) return;
    setState(() => _applying = false);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(ok
          ? '已应用到识别引擎（下一帧生效）'
          : '部分配置下发失败，识别引擎可能尚未启动'),
      duration: const Duration(seconds: 2),
    ));
  }

  /// 好牌期望策略选择面板。
  ///
  /// 防坑要点（之前会触发 "BOTTOM OVERFLOWED BY 101 PIXELS"）：
  /// 1. `isScrollControlled: true` —— 让面板高度可突破默认 50% 屏幕约束。
  /// 2. 内容外套 `SingleChildScrollView` —— 万一选项超出 75% 屏高仍可滚动。
  /// 3. `ConstrainedBox(maxHeight: 0.75*screen)` —— 防止极端长内容顶到状态栏。
  Future<void> _showRatePicker() async {
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppTokens.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => SafeArea(
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxHeight: MediaQuery.of(ctx).size.height * 0.75,
          ),
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 14),
                  child: Text('好牌期望策略 (进张偏好)',
                      style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                          color: _textMain)),
                ),
                Container(
                  margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  decoration: BoxDecoration(
                    color: AppTokens.brand.withAlpha(20),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: AppTokens.brand.withAlpha(60), width: 0.8),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.shield_outlined, color: AppTokens.brand, size: 16),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '智能保底机制已激活：若残局活牌不足所设门槛，系统自动保底推送当前最优打法，绝不空白。',
                          style: TextStyle(color: AppTokens.brand, fontSize: 11.5, height: 1.3),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 6),
                ...DebugConfig.rates.map((r) => ListTile(
                      title: Text('$r% · ${DebugConfig.rateStrategyName(r)}',
                          style: const TextStyle(
                              color: _textMain, fontSize: 14.5, fontWeight: FontWeight.w600)),
                      subtitle: Padding(
                        padding: const EdgeInsets.only(top: 2),
                        child: Text(DebugConfig.rateStrategyDesc(r),
                            style: const TextStyle(
                                color: _textSub, fontSize: 11.5)),
                      ),
                      trailing: r == _cfg.rate
                          ? const Icon(Icons.check_circle_rounded, color: _kAccent)
                          : null,
                      onTap: () {
                        Navigator.of(ctx).pop();
                        _update(_cfg.copyWith(rate: r));
                      },
                    )),
                const SizedBox(height: 12),
              ],
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Center(
        child: CircularProgressIndicator(color: _kAccent),
      );
    }
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _buildFortuneCard(),
            _groupCard(
              title: '识别策略',
              children: [
                _switchRow(
                  title: '自动方向探测',
                  desc: '竖屏手机玩横屏麻将时自动校正画面方向。'
                      '关闭后需手动用悬浮窗「旋转」按钮校正',
                  value: _cfg.autoOrient,
                  onChanged: (v) =>
                      _update(_cfg.copyWith(autoOrient: v)),
                ),
                _switchRow(
                  title: '冷启动放宽门槛',
                  desc: '开始识别的头几帧放宽匹配门槛，更容易认出牌',
                  value: _cfg.bootstrap,
                  onChanged: (v) => _update(_cfg.copyWith(bootstrap: v)),
                ),
                _switchRow(
                  title: '严格识别门槛',
                  desc: '用较高门槛过滤误识别。关闭后更容易认出牌，'
                      '但也可能认错',
                  value: _cfg.strict,
                  onChanged: (v) => _update(_cfg.copyWith(strict: v)),
                ),
              ],
            ),
            const SizedBox(height: 16),
            _groupCard(
              title: '出牌建议',
              children: [
                _switchRow(
                  title: '显示出牌建议',
                  desc: '在悬浮窗给出推荐打法与进张数。'
                      '关闭后只显示向听数',
                  value: _cfg.showAdvice,
                  onChanged: (v) =>
                      _update(_cfg.copyWith(showAdvice: v)),
                ),
                _rateRow(),
                _switchRow(
                  title: '牌势感知与军师安抚',
                  desc: '根据起手向听与进张面实时研判顺逆风局势。逆风时自动强化'
                      '防点炮优先级并给予温和安抚，防上头保分',
                  value: _cfg.moodGuard,
                  onChanged: (v) => _update(_cfg.copyWith(moodGuard: v)),
                ),
              ],
            ),
            const SizedBox(height: 16),
            _groupCard(
              title: '安全与隐私',
              children: [
                _switchRow(
                  title: '防封号',
                  desc: '开启后截屏节奏在 350–550ms 间随机抖动，并让建议稍作'
                      '人类式延迟显示，避免固定节奏被识别为机械/外挂。'
                      '只改变采集与展示节奏，不影响识别准确率',
                  value: _cfg.antiBan,
                  onChanged: (v) => _update(_cfg.copyWith(antiBan: v)),
                ),
                _switchRow(
                  title: '防平台检测',
                  desc: '开启后仅在目标麻将 App 处于前台时才采帧，'
                      '切回本 App 或回到桌面自动暂停识别，降低持续扫描特征。'
                      '需「使用情况访问」权限；未授予时自动降级为常开',
                  value: _cfg.antiDetect,
                  onChanged: (v) => _update(_cfg.copyWith(antiDetect: v)),
                ),
              ],
            ),
            const SizedBox(height: 16),
            _groupCard(
              title: '危险牌预警',
              children: [
                _switchRow(
                  title: '防点炮',
                  desc: '开启后，悬浮窗对每张候选弃牌标注「现物安全 / 生张危险」。'
                      '依据当前牌河判断：牌河里已出现的牌为现物，不可能被点和',
                  value: _cfg.warnDealIn,
                  onChanged: (v) => _update(_cfg.copyWith(warnDealIn: v)),
                ),
                _switchRow(
                  title: '防杠',
                  desc: '开启后，悬浮窗对每张候选弃牌标注被碰 / 杠的风险。'
                      '依据该牌在牌河出现的频次估算：出现越少，越可能被对手握成对子',
                  value: _cfg.warnPonKong,
                  onChanged: (v) => _update(_cfg.copyWith(warnPonKong: v)),
                ),
              ],
            ),
            const SizedBox(height: 24),
            _confirmButton(),
          ],
        ),
      ),
    );
  }

  Widget _groupCard(
          {required String title, required List<Widget> children}) =>
      Container(
        decoration: BoxDecoration(
          color: AppTokens.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppTokens.border),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 4),
              child: Text(title,
                  style: const TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: _kAccent)),
            ),
            ...children,
          ],
        ),
      );

  Widget _switchRow({
    required String title,
    required String desc,
    required bool value,
    required ValueChanged<bool> onChanged,
  }) =>
      Column(
        children: [
          ListTile(
            contentPadding: const EdgeInsets.symmetric(horizontal: 12),
            title: Text(title,
                style: const TextStyle(fontSize: 15, color: _textMain)),
            subtitle: Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(desc,
                  style: const TextStyle(
                      fontSize: 11, color: _textSub, height: 1.35)),
            ),
            trailing: Switch(
              value: value,
              activeColor: _kAccent,
              onChanged: onChanged,
            ),
          ),
          const Divider(height: 1, indent: 12, endIndent: 12),
        ],
      );

  Widget _rateRow() => InkWell(
        onTap: _showRatePicker,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        const Text('好牌期望策略',
                            style:
                                TextStyle(fontSize: 15, color: _textMain, fontWeight: FontWeight.w500)),
                        const SizedBox(width: 6),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                          decoration: BoxDecoration(
                            color: _kAccent.withAlpha(25),
                            borderRadius: BorderRadius.circular(4),
                          ),
                          child: Text(
                            DebugConfig.rateStrategyName(_cfg.rate),
                            style: const TextStyle(color: _kAccent, fontSize: 10, fontWeight: FontWeight.bold),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                        '按全场活牌过滤打法（当前：进张 ≥ ${_cfg.minUkeire} 张）。档位越高推荐越精，智能保底绝不落空',
                        style: const TextStyle(
                            fontSize: 11, color: _textSub, height: 1.35)),
                  ],
                ),
              ),
              Text('${_cfg.rate}%',
                  style: const TextStyle(
                      fontSize: 15,
                      color: _kAccent,
                      fontWeight: FontWeight.w600)),
              const SizedBox(width: 6),
              const Icon(Icons.arrow_drop_down, color: _textSub),
            ],
          ),
        ),
      );


  Widget _confirmButton() => SizedBox(
        height: 54,
        child: ElevatedButton(
          style: ElevatedButton.styleFrom(
            backgroundColor: _kAccent,
            foregroundColor: Colors.white,
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(14)),
            elevation: 0,
          ),
          onPressed: _applying ? null : _confirm,
          child: _applying
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(
                      strokeWidth: 2, color: Colors.white),
                )
              : const Text('确认配置',
                  style:
                      TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
        ),
      );

  Widget _buildFortuneCard() {
    final cur = _kFortunes[_fortuneIndex];

    return Container(
      margin: const EdgeInsets.only(bottom: 16),
      decoration: BoxDecoration(
        color: AppTokens.surface,
        borderRadius: BorderRadius.circular(AppTokens.r16),
        border: Border.all(color: AppTokens.border, width: 0.9),
        boxShadow: const [
          BoxShadow(
            color: Color(0x08000000),
            blurRadius: 8,
            offset: Offset(0, 2),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 顶部栏：标题 + 简约参悟心法按钮
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 14, 10),
            child: Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(
                    color: const Color(0xFFFEF3C7),
                    borderRadius: BorderRadius.circular(AppTokens.r8),
                    border: Border.all(color: const Color(0xFFFDE68A), width: 0.8),
                  ),
                  child: const Icon(Icons.wb_sunny_rounded, color: Color(0xFFD97706), size: 16),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: const [
                      Text(
                        '今日雀局运势 · 军师心盘',
                        style: TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w600,
                          color: AppTokens.ink,
                          letterSpacing: 0.2,
                        ),
                      ),
                      SizedBox(height: 1),
                      Text(
                        '心理调谐 · 逆风安抚 · 胜势强化',
                        style: TextStyle(
                          fontSize: 11,
                          color: AppTokens.muted,
                        ),
                      ),
                    ],
                  ),
                ),
                InkWell(
                  onTap: _cycleFortune,
                  borderRadius: BorderRadius.circular(AppTokens.rPill),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                    decoration: BoxDecoration(
                      color: const Color(0xFFFFFBEB),
                      borderRadius: BorderRadius.circular(AppTokens.rPill),
                      border: Border.all(color: const Color(0xFFFDE68A), width: 0.8),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: const [
                        Icon(Icons.refresh_rounded, size: 13, color: Color(0xFFD97706)),
                        SizedBox(width: 4),
                        Text(
                          '参悟心法',
                          style: TextStyle(
                            color: Color(0xFFB45309),
                            fontSize: 11.5,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
          const Divider(height: 1, color: AppTokens.border),

          // 核心气运评级 + 心态签文
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      cur['title'] ?? '',
                      style: const TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w700,
                        color: AppTokens.ink,
                        letterSpacing: 0.2,
                      ),
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                      decoration: BoxDecoration(
                        color: AppTokens.brandContainer,
                        borderRadius: BorderRadius.circular(AppTokens.r8),
                        border: Border.all(color: const Color(0xFF99F6E4), width: 0.6),
                      ),
                      child: Text(
                        cur['level'] ?? '',
                        style: const TextStyle(
                          fontSize: 10.5,
                          fontWeight: FontWeight.bold,
                          color: AppTokens.brandDark,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),

                // 概率诚实化（B-P3）：这里原本印「心理胜势指数 98% · 极佳」，而那个
                // 98 是写死在 _kFortunes 里的装饰值，与手牌/牌河算式毫无关系，却长得
                // 像个测出来的指标。商用面板上每个百分比都得可追溯，因此去掉数字与
                // 能量条，只保留它的真实身份：一句心态签文（评级徽章已在上方展示）。
                Row(
                  children: const [
                    Icon(Icons.spa_outlined, size: 13, color: AppTokens.muted),
                    SizedBox(width: 5),
                    Expanded(
                      child: Text(
                        '心态签文 · 仅供调节情绪，不含牌局计算依据',
                        style: TextStyle(fontSize: 11, color: AppTokens.muted),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),

          // 吉位与顺风牌
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                Expanded(
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    decoration: BoxDecoration(
                      color: AppTokens.pillBg,
                      borderRadius: BorderRadius.circular(AppTokens.r8),
                      border: Border.all(color: AppTokens.border, width: 0.7),
                    ),
                    child: Row(
                      children: [
                        const Icon(Icons.explore_outlined, size: 14, color: Color(0xFF0284C7)),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            cur['direction'] ?? '',
                            style: const TextStyle(
                              fontSize: 11,
                              color: AppTokens.ink2,
                              fontWeight: FontWeight.w500,
                            ),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    decoration: BoxDecoration(
                      color: AppTokens.pillBg,
                      borderRadius: BorderRadius.circular(AppTokens.r8),
                      border: Border.all(color: AppTokens.border, width: 0.7),
                    ),
                    child: Row(
                      children: [
                        const Icon(Icons.casino_outlined, size: 14, color: Color(0xFF7C3AED)),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            cur['element'] ?? '',
                            style: const TextStyle(
                              fontSize: 11,
                              color: AppTokens.ink2,
                              fontWeight: FontWeight.w500,
                            ),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),

          // 军师安抚箴言与心态指引（简约清爽卡片）
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
            child: Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFFF8FAFC),
                borderRadius: BorderRadius.circular(AppTokens.r10),
                border: const Border(
                  left: BorderSide(color: AppTokens.brand, width: 3.5),
                  top: BorderSide(color: AppTokens.border, width: 0.8),
                  right: BorderSide(color: AppTokens.border, width: 0.8),
                  bottom: BorderSide(color: AppTokens.border, width: 0.8),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Icon(Icons.format_quote_rounded, color: Color(0xFFD97706), size: 15),
                      const SizedBox(width: 4),
                      Expanded(
                        child: Text(
                          cur['quote'] ?? '',
                          style: const TextStyle(
                            fontSize: 12,
                            color: AppTokens.ink,
                            height: 1.4,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Icon(Icons.shield_outlined, color: AppTokens.brand, size: 14),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          cur['comfort'] ?? '',
                          style: const TextStyle(
                            fontSize: 11,
                            color: AppTokens.muted,
                            height: 1.35,
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),

          // 联动开关：智能牌势安抚模式
          Container(
            padding: const EdgeInsets.fromLTRB(16, 6, 12, 8),
            decoration: const BoxDecoration(
              border: Border(top: BorderSide(color: AppTokens.border, width: 0.6)),
            ),
            child: Row(
              children: [
                const Icon(Icons.favorite_rounded, size: 14, color: Color(0xFFF43F5E)),
                const SizedBox(width: 6),
                const Expanded(
                  child: Text(
                    '逆风智能安抚与防守保分（避免上头）',
                    style: TextStyle(
                      fontSize: 12,
                      color: AppTokens.ink,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ),
                Switch(
                  value: _cfg.moodGuard,
                  activeColor: AppTokens.brand,
                  onChanged: (v) => _update(_cfg.copyWith(moodGuard: v)),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
