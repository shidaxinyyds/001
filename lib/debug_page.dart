import 'package:flutter/material.dart';

/// 调试页：与主页一致的浅色主题（青绿），保留功能开关与好牌机率。
///
/// 已移除：诊断卡片（服务器/Dylib/线路/游戏/基址状态）与「注入插件功能」按钮
/// 及背后一整套模拟注入/识别面板逻辑，避免成为无用代码与潜在 UI 卡顿。
class DebugPage extends StatefulWidget {
  final VoidCallback? onBack;

  const DebugPage({Key? key, this.onBack}) : super(key: key);

  @override
  State<DebugPage> createState() => _DebugPageState();
}

class _DebugPageState extends State<DebugPage> {
  // 与主页一致的配色（青绿强调 / 深灰主文 / 中性灰副文）。
  static const Color _kAccent = Color(0xFF00695C); // teal 800
  static const Color _textMain = Color(0xFF202124);
  static const Color _textSub = Color(0xFF5F6368);

  // 10 个功能开关（键名即显示名）。
  final Map<String, bool> _features = {
    '随意选牌': false,
    '高级推理': false,
    '起手暗杠': false,
    '语音提示': false,
    '快速自摸': false,
    '防中断': false,
    '智能出牌': false,
    '自动规划': false,
    '卡密平台检测': false,
    '防封号': false,
  };

  int _goodCardRate = 10;

  void _toggleFeature(String name) {
    setState(() => _features[name] = !(_features[name] ?? false));
  }

  /// 显示好牌机率选择面板。
  ///
  /// 防坑要点（之前会触发 "BOTTOM OVERFLOWED BY 101 PIXELS"）：
  /// 1. `isScrollControlled: true` —— 让面板高度可突破默认 50% 屏幕约束。
  /// 2. 内容外套 `SingleChildScrollView` —— 万一选项超出 75% 屏高仍可滚动。
  /// 3. `ConstrainedBox(maxHeight: 0.75*screen)` —— 防止极端长内容顶到状态栏。
  void _showRatePicker() {
    final rates = <int>[10, 20, 30, 40, 50, 60, 70, 80, 90];
    showModalBottomSheet<void>(
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
                ...rates.map((r) => ListTile(
                      title: Text('$r%',
                          style: const TextStyle(
                              color: _textMain, fontSize: 15)),
                      trailing: r == _goodCardRate
                          ? const Icon(Icons.check, color: _kAccent)
                          : null,
                      onTap: () {
                        Navigator.of(ctx).pop();
                        setState(() => _goodCardRate = r);
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
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _header(),
            const SizedBox(height: 16),
            _featuresCard(),
            const SizedBox(height: 16),
            _rateRow(),
            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }

  Widget _header() => Row(
        children: [
          IconButton(
            icon: const Icon(Icons.arrow_back_ios,
                color: _textMain, size: 20),
            onPressed: () {
              if (widget.onBack != null) {
                widget.onBack!();
              } else {
                Navigator.of(context).maybePop();
              }
            },
          ),
          const Expanded(
            child: Text('调试工具',
                textAlign: TextAlign.center,
                style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                    color: _textMain)),
          ),
          const SizedBox(width: 40),
        ],
      );

  Widget _featuresCard() => Container(
        padding: const EdgeInsets.symmetric(horizontal: 4),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: Colors.grey.shade200),
        ),
        child: Column(
          children: _features.keys.map((name) => _featureRow(name)).toList(),
        ),
      );

  Widget _featureRow(String name) => Container(
        decoration: const BoxDecoration(
          border: Border(
              bottom: BorderSide(color: Color(0x11000000))),
        ),
        child: ListTile(
          contentPadding: const EdgeInsets.symmetric(horizontal: 8),
          title: Text(name,
              style: const TextStyle(fontSize: 15, color: _textMain)),
          trailing: Switch(
            value: _features[name] ?? false,
            activeColor: _kAccent,
            onChanged: (_) => _toggleFeature(name),
          ),
        ),
      );

  Widget _rateRow() => InkWell(
        onTap: _showRatePicker,
        borderRadius: BorderRadius.circular(12),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: Colors.grey.shade200),
          ),
          child: Row(
            children: [
              const Text('好牌机率',
                  style: TextStyle(fontSize: 15, color: _textMain)),
              const Spacer(),
              Text('$_goodCardRate%',
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
}
