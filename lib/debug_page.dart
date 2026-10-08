import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
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

  // ── 实时链路读数（悬浮窗按 2s 上报的端到端帧龄）。
  // 只存最近一次上报，不自己算分位数 —— 分位数只能在收到帧的那一侧算（见
  // LatencyProbe 的口径说明），本页重复计算就是两处同一个数字开始漂。
  Map<String, dynamic>? _latency;
  StreamSubscription<dynamic>? _latencySub;

  /// 实战运势与攻防心法（去伪存真：杜绝无计算依据的伪百分比指标，专注实战牌势与心理心流调节）。
  /// 参悟心法：结合牌势推演与心理建设，助牌手保持最高期望决策。
  static const String _kHeartMethodNote = '参悟心法 · 实战牌势';

  static const List<Map<String, String>> _kFortunes = [
    {
      'title': '金汤固守 · 蓄势待发',
      'level': '攻守兼备',
      'direction': '中盘控场 · 现物控盘',
      'element': '万字通达 · 结构稳固',
      'quote': '逆风防守守其险，顺风进攻取其胜。急躁乃败军之由，定心即立于不败之地。',
      'comfort': '起手牌杂切莫急躁，跟打熟张不点炮；守住底分，转机与大牌往往后发制人。',
    },
    {
      'title': '顺风推进 · 争抢先手',
      'level': '速攻争先',
      'direction': '起手顺风 · 提速抢叫',
      'element': '筒子连络 · 优先成搭',
      'quote': '宁弃一手烂牌，不放一人点炮。牌顺则乘胜追击，算准进张步步为营。',
      'comfort': '牌局进张顺畅时敢打敢拼，紧盯上家舍牌节奏，保持最高听牌速度与和牌期望。',
    },
    {
      'title': '严防死守 · 避点大炮',
      'level': '铜墙铁壁',
      'direction': '尾盘戒备 · 紧盯生张',
      'element': '字张沉底 · 留备退路',
      'quote': '胜负皆常理，心定牌自通。尾盘防生张，长线期望唯在沉着防守。',
      'comfort': '面对对手明显叫听大牌，坚决弃和扣死生张，跟打安全张，保住积分方能笑到最后。',
    },
    {
      'title': '厚积薄发 · 逆转翻盘',
      'level': '定心致远',
      'direction': '沉着应对 · 算尽盘理',
      'element': '条子聚气 · 逢叫必和',
      'quote': '心怀平常心，算尽盘中理。牌流起伏有周期，真正的高手胜在情绪稳定。',
      'comfort': '牌流暂时受挫时切忌上头盲冲，AI 实时辅助牌河分析，从容应对定能长线盈利。',
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
            Icon(Icons.refresh_rounded, color: Color(0xFF34D399), size: 17),
            SizedBox(width: 8),
            Expanded(
              child: Text(
                '已刷新当前牌势运势：实战攻防重心已更新！',
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
    // overlayListener 是广播流，主页已有几路局部订阅，再开一路不冲突。
    // 只接 latency 帧：其余类型（status/roi/reset_match）由主页负责，这里插手
    // 会变成两个页面各自维护一份识别状态。
    _latencySub = FlutterOverlayWindow.overlayListener.listen((event) {
      if (!mounted) return;
      if (event is Map && event['type'] == 'latency') {
        setState(() => _latency = Map<String, dynamic>.from(event));
      }
    });
  }

  @override
  void dispose() {
    _latencySub?.cancel();
    _latencySub = null;
    super.dispose();
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
              title: '实时链路',
              children: [_latencyReadout()],
            ),
            const SizedBox(height: 16),
            _groupCard(
              title: '当前牌局',
              children: [_matchPhaseReadout()],
            ),
            const SizedBox(height: 16),
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
                  desc: '开启后采帧间隔从 15ms 改为在 80–120ms 间随机抖动'
                      '（静止巡检档 80–100ms），避免固定节奏被识别为机械/外挂。'
                      '只改变采帧节奏，不影响识别准确率与建议内容',
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

  /// 实时链路读数。这张卡的唯一职责是把「端到端到底多少毫秒」变成可核对的数字。
  /// 无数据/已过时都如实说明，绝不印一个 0ms 冒充「很快」。
  Widget _latencyReadout() {
    final Map<String, dynamic>? ev = _latency;
    final int n = (ev?['n'] as num?)?.toInt() ?? 0;
    final int dropped = (ev?['dropped'] as num?)?.toInt() ?? 0;
    final int? p50 = (ev?['p50_ms'] as num?)?.toInt();
    final int? p95 = (ev?['p95_ms'] as num?)?.toInt();
    final int? maxMs = (ev?['max_ms'] as num?)?.toInt();
    final int? enc = (ev?['encode_p50_ms'] as num?)?.toInt();
    final int? eng = (ev?['engine_p50_ms'] as num?)?.toInt();
    final int sampledAt = (ev?['sampled_at'] as num?)?.toInt() ?? 0;
    final bool stale = sampledAt > 0 &&
        DateTime.now().millisecondsSinceEpoch - sampledAt > 6000;

    String ms(int? v) => v == null ? '—' : '$v';

    // 采集层心跳自报的计数（由悬浮窗随帧龄一起转发）。这一行的职责是让上面那组
    // 帧龄「可对账」：dropped 说的「有多少结果帧没带时间戳」应当能被 parse_fail +
    // non_finite + 发送失败解释；对不上就说明链路里还有第三种没被识别的丢帧原因。
    final Map<String, dynamic> java =
        (ev?['java'] as Map?)?.cast<String, dynamic>() ??
            const <String, dynamic>{};
    int ji(String k) => (java[k] as num?)?.toInt() ?? 0;

    final List<String> notes = <String>[];
    if (n == 0) {
      notes.add('暂无读数：开始识别后这里跟着每一帧变化');
    } else {
      notes.add('近 $n 帧样本');
      if (enc != null || eng != null) {
        notes.add('其中整屏编码约 ${ms(enc)}ms、识别往返约 ${ms(eng)}ms（同为典型值）');
      }
      if (dropped > 0) {
        notes.add('$dropped 帧因缺采集时间戳或墙钟回拨被丢弃，未计入');
      }
    }
    if (stale) {
      notes.add('读数已停止更新（识别可能已停止或悬浮窗已关闭）');
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              _latencyStat('典型', ms(p50), 'ms', true),
              _latencyStat('偶发', ms(p95), 'ms', false),
              _latencyStat('最差', ms(maxMs), 'ms', false),
            ],
          ),
          const SizedBox(height: 8),
          Text(notes.join('；'),
              style: const TextStyle(
                  fontSize: 11, color: _textSub, height: 1.4)),
          if (java.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
                '采集侧：本帧解析+注入均值 ${ji('stamp_avg_ms')}ms，'
                '未带时间戳的帧 ${ji('parse_fail') + ji('non_finite')}（解析失败 ${ji('parse_fail')}、'
                '数值非法 ${ji('non_finite')}），累计采集 ${ji('frames')} 帧、已处理 ${ji('proc')} 帧、'
                '发送失败 ${ji('send_fail')} 次',
                style: const TextStyle(
                    fontSize: 11, color: _textSub, height: 1.4)),
          ],
          const SizedBox(height: 4),
          const Text(
              '口径：从系统截屏到悬浮窗收到结果，不含 Flutter 上屏那一段'
              '（有数据帧前沿 0ms 上屏，尾部合并窗口≤36ms）。',
              style: TextStyle(
                  fontSize: 10.5, color: _textSub, height: 1.4)),
        ],
      ),
    );
  }

  Widget _latencyStat(
          String label, String value, String unit, bool emphasize) =>
      Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label,
                style: const TextStyle(fontSize: 11, color: _textSub)),
            const SizedBox(height: 2),
            RichText(
              text: TextSpan(
                children: <TextSpan>[
                  TextSpan(
                      text: value,
                      style: TextStyle(
                          fontSize: emphasize ? 22 : 16,
                          fontWeight: FontWeight.w700,
                          color: emphasize ? _kAccent : _textMain)),
                  TextSpan(
                      text: ' $unit',
                      style: const TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w500,
                          color: _textSub)),
                ],
              ),
            ),
          ],
        ),
      );

  /// 当前牌局读数。悬浮窗是叠在真实牌局上的覆盖层，不放任何常驻局况（只在阶段切换与
  /// 碰/杠/听牌那一瞬闪一颗 ~2.5s 的小胶囊），明细只在这页展开：轮到谁 / 刚刚发生了什么 /
  /// 各家牌河确认到几张 / 哪些读数被确认门挡下。数据是悬浮窗每 2s 随帧龄转发的引擎原样
  /// 输出，这里不再算一遍（端上重算一套必然与引擎漂移）。
  Widget _matchPhaseReadout() {
    final Map<String, dynamic> ev = _latency ?? const <String, dynamic>{};
    final Map<String, dynamic> mp =
        (ev['match_phase'] as Map?)?.cast<String, dynamic>() ??
            const <String, dynamic>{};
    final String phase = mp['phase'] as String? ?? '';
    final String label = mp['label'] as String? ?? '';
    final String hint = mp['hint'] as String? ?? '';
    final String basis = mp['turn_basis'] as String? ?? '';
    final Map<String, dynamic> hand =
        (mp['hand'] as Map?)?.cast<String, dynamic>() ?? const <String, dynamic>{};
    final List<dynamic> melds = (mp['melds'] as List?) ?? const [];
    final List<dynamic> feed = (mp['feed'] as List?) ?? const [];
    final Map<String, dynamic> river =
        (mp['river'] as Map?)?.cast<String, dynamic>() ?? const <String, dynamic>{};
    final Map<String, dynamic> rejected =
        (mp['rejected'] as Map?)?.cast<String, dynamic>() ??
            const <String, dynamic>{};
    final int? confirmMs = (mp['confirm_ms'] as num?)?.toInt();
    final int? overMs = (mp['over_ms'] as num?)?.toInt();
    final int? seq = (mp['seq'] as num?)?.toInt();
    final int updatedAt = (mp['updated_at_ms'] as num?)?.toInt() ?? 0;
    final bool stale = updatedAt > 0 &&
        DateTime.now().millisecondsSinceEpoch - updatedAt > 6000;

    void addLine(List<Widget> out, String text) {
      out.add(Padding(
        padding: const EdgeInsets.only(bottom: 3),
        child: Text(text,
            style: const TextStyle(
                fontSize: 11, color: _textSub, height: 1.4)),
      ));
    }

    final List<Widget> rows = <Widget>[];
    if (phase.isEmpty) {
      addLine(rows, '还没收到局况读数：开启识别后，悬浮窗会每 2 秒往这里补一次。');
    } else if (phase == 'idle') {
      addLine(rows, '画面里没牌桌（未开局、结算中，或被弹窗暂时挡住），所以不报阶段。');
    } else {
      rows.add(Padding(
        padding: const EdgeInsets.only(bottom: 4),
        child: Text('$label${hint.isEmpty ? '' : ' · $hint'}',
            style: const TextStyle(
                fontSize: 13, fontWeight: FontWeight.w600, color: _textMain)),
      ));
      if (basis.isNotEmpty) addLine(rows, '判定依据：$basis');
      final int? count = (hand['count'] as num?)?.toInt();
      final int? base = (hand['base'] as num?)?.toInt();
      final int? removed = (hand['meld_removed'] as num?)?.toInt();
      if (count != null && base != null) {
        addLine(rows, '本家立牌 $count 张 · 应然基数 $base'
            '（副露扣走 ${removed ?? 0} 张，杠抽走 4 张所以不是 13 的余数）');
      }
      // 本家牌河不摆在这一行：自家弃牌是由手牌张数转移直接统计的（再喂一份牌河增量会
      // 把同一件事播两遍），那个读数恒为 0 —— 印一个 0 在“已确认张数”后面就是假数。
      final Iterable<MapEntry<String, dynamic>> rivals =
          river.entries.where((e) => e.key != '本家');
      if (rivals.isNotEmpty) {
        addLine(rows, '他家牌河（已确认张数）：'
            '${rivals.map((e) => '${e.key} ${e.value}').join('、')}'
            '；本家弃牌按手牌张数转移统计，不计在这一行');
      }
      if (melds.isNotEmpty) {
        addLine(rows, '副露：${melds.join('、')}');
      }
      if (feed.isNotEmpty) {
        final List<dynamic> latest = feed.reversed.take(6).toList();
        addLine(rows, '最近事件（新到旧）：');
        for (final dynamic e in latest) {
          addLine(rows, '  · ${e is Map ? (e['text'] ?? '') : e}');
        }
      }
      final Map<String, dynamic> block = Map<String, dynamic>.from(rejected);
      final int blockedSum = block.values
          .whereType<num>()
          .fold<int>(0, (a, b) => a + b.toInt());
      addLine(rows, blockedSum == 0
          ? '读数一路自洽，没有需要挡掉的帧。'
          : '挡下可疑读数共 $blockedSum 次：'
              '牌河超上限 ${block['river_over_cap'] ?? 0}、副露组数超限 '
              '${block['meld_group_cap'] ?? 0}、张数反而变小 ${block['non_monotonic'] ?? 0}、'
              '单帧事件超量 ${block['event_cap'] ?? 0}、墙钟回拨 ${block['clock_back'] ?? 0}');
    }
    if (stale) {
      addLine(rows, '读数已停止更新（识别已关闭、或悬浮窗已收起）。');
    }
    rows.add(const Padding(
      padding: EdgeInsets.only(top: 2),
      child: Text(
          '口径：同一候选牌数需稳定满 60ms 才当真（抗单帧抖动），离开牌桌持续满 400ms '
          '才报“本局结束”；两个阈值都是时间，不是帧数。',
          style: TextStyle(fontSize: 10.5, color: _textSub, height: 1.4)),
    ));

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ...rows,
          if (seq != null)
            Align(
              alignment: Alignment.centerRight,
              child: Text('局况序号 $seq'
                  '${confirmMs != null && overMs != null ? ' · 确认 ${confirmMs}ms / 结束 ${overMs}ms' : ''}',
                  style: const TextStyle(fontSize: 10, color: _textSub)),
            ),
        ],
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
                        '实战心法 · 牌势走向与心态签文调节',
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
                          '当前运势',
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
