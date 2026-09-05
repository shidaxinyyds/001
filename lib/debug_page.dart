import 'package:flutter/material.dart';
import 'package:auto_vision/config_store.dart';

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
  // 与主页一致的配色（青绿强调 / 深灰主文 / 中性灰副文）。
  static const Color _kAccent = Color(0xFF00695C); // teal 800
  static const Color _textMain = Color(0xFF202124);
  static const Color _textSub = Color(0xFF5F6368);

  DebugConfig _cfg = DebugConfig();
  bool _loading = true;
  bool _applying = false;

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
  Future<void> _update(DebugConfig next) async {
    setState(() => _cfg = next);
    await next.save();
    await next.apply();
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

  /// 好牌机率选择面板。
  ///
  /// 防坑要点（之前会触发 "BOTTOM OVERFLOWED BY 101 PIXELS"）：
  /// 1. `isScrollControlled: true` —— 让面板高度可突破默认 50% 屏幕约束。
  /// 2. 内容外套 `SingleChildScrollView` —— 万一选项超出 75% 屏高仍可滚动。
  /// 3. `ConstrainedBox(maxHeight: 0.75*screen)` —— 防止极端长内容顶到状态栏。
  Future<void> _showRatePicker() async {
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.white,
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
                  child: Text('好牌机率',
                      style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                          color: _textMain)),
                ),
                ...DebugConfig.rates.map((r) => ListTile(
                      title: Text('$r%',
                          style: const TextStyle(
                              color: _textMain, fontSize: 15)),
                      subtitle: Text('进张 ≥ ${(r ~/ 10).clamp(1, 9)} 张',
                          style: const TextStyle(
                              color: _textSub, fontSize: 12)),
                      trailing: r == _cfg.rate
                          ? const Icon(Icons.check, color: _kAccent)
                          : null,
                      onTap: () {
                        Navigator.of(ctx).pop();
                        _update(_cfg.copyWith(rate: r));
                      },
                    )),
                const SizedBox(height: 8),
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
              ],
            ),
            const SizedBox(height: 16),
            _groupCard(
              title: '隐私与防检测',
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
          color: Colors.white,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: Colors.grey.shade200),
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
                    const Text('好牌机率',
                        style:
                            TextStyle(fontSize: 15, color: _textMain)),
                    const SizedBox(height: 4),
                    Text(
                        '只推荐进张数 ≥ ${_cfg.minUkeire} 张的打法，'
                        '档位越高推荐越少越精',
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
}
