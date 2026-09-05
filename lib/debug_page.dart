import 'package:flutter/material.dart';
import 'package:auto_vision/activation.dart';

/// 调试页：与主页一致的浅色主题（青绿），内容为「插件注入 / 状态诊断」风格。
///
/// 说明：用户提供的原型是 uni-app(.vue) 模板，本文件是其 **Flutter 等价实现**，
/// 视觉结构与功能开关一一对应。本页为自包含 StatefulWidget：功能开关、游戏ID、
/// 好牌机率、注入按钮与识别浮层均为本地状态，不依赖主页识别引擎
/// （与模板语义一致：注入流程恒成功并弹出识别面板，识别结果为模拟数据）。
///
/// 主题与主页保持一致：浅色背景 + 青绿强调色，规避全局禁用的红/橙/琥珀系。
class DebugPage extends StatefulWidget {
  final VoidCallback? onBack;

  const DebugPage({Key? key, this.onBack}) : super(key: key);

  @override
  State<DebugPage> createState() => _DebugPageState();
}

class _DebugPageState extends State<DebugPage> {
  // 与主页一致的配色
  static const Color _kAccent = Color(0xFF00695C); // teal 800
  static const Color _kAccentBg = Color(0xFFE0F2F1); // teal 50
  static const Color _kGreen = Color(0xFF00C853); // 状态"正常"绿
  static const Color _textMain = Color(0xFF202124);
  static const Color _textSub = Color(0xFF5F6368);

  bool _activated = false;
  bool _injecting = false;
  final TextEditingController _gameIdCtl = TextEditingController();

  // 10 个功能开关（键名即显示名，与模板逐项对应）
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

  // —— 注入成功后识别面板（模拟数据，与模板 demo 语义一致）——
  String _suggestion = '建议打出 3p，听 2p/5p/8p 两面';
  List<String> _lastTiles = const [
    '1m', '2m', '3m', '4m', '5m', '6m', '7m', '8m', '9m', '1p', '2p', '3p', '4p'
  ];
  List<String> _remaining =
      const ['5p', '6p', '7p', '8p', '9p', '1s', '2s', '3s'];

  static const List<String> _tilePool = [
    '1m', '2m', '3m', '4m', '5m', '6m', '7m', '8m', '9m',
    '1p', '2p', '3p', '4p', '5p', '6p', '7p', '8p', '9p',
    '1s', '2s', '3s', '4s', '5s', '6s', '7s', '8s', '9s',
    '1z', '2z', '3z', '4z', '5z', '6z', '7z',
  ];
  static const List<String> _suggestionPool = [
    '建议打出 3p，听 2p/5p/8p 两面',
    '建议打出 5s，听 4s/7s 两面',
    '建议保持，已听牌',
    '建议打出 9m，向听数 -1',
  ];

  @override
  void initState() {
    super.initState();
    // 进入主页前必须经过卡密激活，这里再读一次以决定注入按钮是否锁定。
    isActivated().then((v) {
      if (mounted) setState(() => _activated = v);
    });
  }

  @override
  void dispose() {
    _gameIdCtl.dispose();
    super.dispose();
  }

  void _toggleFeature(String name) {
    setState(() => _features[name] = !(_features[name] ?? false));
  }

  void _showRatePicker() {
    final rates = <int>[10, 20, 30, 40, 50, 60, 70, 80, 90];
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => SafeArea(
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
                      style: const TextStyle(color: _textMain, fontSize: 15)),
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
    );
  }

  Future<void> _inject() async {
    if (!_activated) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text('请先激活设备'),
        duration: Duration(seconds: 2),
      ));
      return;
    }
    final gameId = _gameIdCtl.text.trim();
    if (gameId.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
        content: Text('请输入游戏ID'),
        duration: Duration(seconds: 2),
      ));
      return;
    }
    setState(() => _injecting = true);
    // 注入流程（模拟）：短暂加载后弹出识别面板，恒成功（与模板语义一致）。
    await Future<void>.delayed(const Duration(milliseconds: 1200));
    if (!mounted) return;
    setState(() => _injecting = false);
    _openVerify();
  }

  void _openVerify() {
    _showVerifyDialog();
  }

  void _showVerifyDialog() {
    showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogCtx) => StatefulBuilder(
        builder: (ctx2, setInner) => AlertDialog(
          backgroundColor: Colors.white,
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16)),
          title: Row(
            children: const [
              Icon(Icons.verified, color: _kAccent, size: 22),
              SizedBox(width: 8),
              Text('识别面板',
                  style: TextStyle(color: _textMain, fontSize: 18)),
            ],
          ),
          content: SizedBox(
            width: double.maxFinite,
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('出牌建议',
                      style: TextStyle(fontSize: 13, color: _textSub)),
                  const SizedBox(height: 4),
                  Text(_suggestion,
                      style: const TextStyle(
                          fontSize: 15,
                          color: _kAccent,
                          fontWeight: FontWeight.w600)),
                  const SizedBox(height: 12),
                  const Text('已识别手牌',
                      style: TextStyle(fontSize: 13, color: _textSub)),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: _lastTiles
                        .map((t) => Chip(
                              label: Text(t,
                                  style: const TextStyle(
                                      fontSize: 12, color: _textMain)),
                              backgroundColor: _kAccentBg,
                              visualDensity: VisualDensity.compact,
                            ))
                        .toList(),
                  ),
                  const SizedBox(height: 12),
                  const Text('牌墙剩余',
                      style: TextStyle(fontSize: 13, color: _textSub)),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: _remaining
                        .map((t) => Chip(
                              label: Text(t,
                                  style: const TextStyle(
                                      fontSize: 12, color: _textMain)),
                              backgroundColor: _kAccentBg,
                              visualDensity: VisualDensity.compact,
                            ))
                        .toList(),
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => setInner(() {
                final idx = (_suggestionPool.indexOf(_suggestion) + 1) %
                    _suggestionPool.length;
                _suggestion = _suggestionPool[idx];
                _lastTiles = List<String>.from(_tilePool.skip(7).take(13));
              }),
              child: const Text('重新识别',
                  style: TextStyle(color: _kAccent)),
            ),
            TextButton(
              onPressed: () => setInner(() {
                _suggestion = '已清空记牌';
                _lastTiles = const [];
                _remaining = const [];
              }),
              child: const Text('清空记牌',
                  style: TextStyle(color: _kAccent)),
            ),
            TextButton(
              onPressed: () {
                Navigator.of(dialogCtx).pop();
              },
              child:
                  const Text('关闭', style: TextStyle(color: _kAccent)),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    // 背景与主页一致：使用主页所在 Scaffold 的主题背景色（浅色）。
    final bg = Theme.of(context).scaffoldBackgroundColor;
    return Container(
      color: bg,
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _header(),
              const SizedBox(height: 16),
              _gameCard(),
              const SizedBox(height: 16),
              _gameIdField(),
              const SizedBox(height: 16),
              _featuresCard(),
              const SizedBox(height: 16),
              _rateRow(),
              const SizedBox(height: 24),
              _injectButton(),
              const SizedBox(height: 16),
            ],
          ),
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

  Widget _gameCard() => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: _kAccentBg,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: _kAccent.withOpacity(0.25)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: _kAccent.withOpacity(0.4)),
              ),
              alignment: Alignment.center,
              child: const Text('🎮', style: TextStyle(fontSize: 34)),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Text('服务器连接状态',
                          style: TextStyle(fontSize: 13, color: _textSub)),
                      const SizedBox(width: 8),
                      const Text('【正常】',
                          style: TextStyle(
                              fontSize: 13,
                              color: _kGreen,
                              fontWeight: FontWeight.w600)),
                    ],
                  ),
                  const SizedBox(height: 8),
                  _statusLine('Dylib状态', '正常'),
                  _statusLine('线路状态', '正常'),
                  _statusLine('游戏状态', '正常'),
                  _statusLine('基址读取', '正常'),
                ],
              ),
            ),
          ],
        ),
      );

  Widget _statusLine(String label, String value) => Padding(
        padding: const EdgeInsets.only(bottom: 4),
        child: Row(
          children: [
            SizedBox(
              width: 76,
              child: Text(label,
                  style: const TextStyle(fontSize: 12, color: _textSub)),
            ),
            Text(value,
                style: const TextStyle(fontSize: 12, color: _kGreen)),
          ],
        ),
      );

  Widget _gameIdField() => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('游戏ID',
              style: TextStyle(
                  fontSize: 14,
                  color: _textMain,
                  fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          TextField(
            controller: _gameIdCtl,
            decoration: InputDecoration(
              hintText: '请输入游戏ID',
              hintStyle: const TextStyle(color: Color(0xFF9AA0A6)),
              filled: true,
              fillColor: Colors.white,
              contentPadding: const EdgeInsets.symmetric(
                  horizontal: 14, vertical: 12),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(10),
                borderSide: BorderSide(color: Colors.grey.shade300),
              ),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(10),
                borderSide: BorderSide(color: Colors.grey.shade300),
              ),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(10),
                borderSide: const BorderSide(color: _kAccent),
              ),
            ),
            style: const TextStyle(fontSize: 14, color: _textMain),
          ),
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

  Widget _injectButton() => SizedBox(
        height: 54,
        child: ElevatedButton(
          style: ElevatedButton.styleFrom(
            backgroundColor: _activated ? _kAccent : Colors.grey.shade300,
            foregroundColor: _activated ? Colors.white : Colors.grey.shade600,
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(14)),
            elevation: 0,
          ),
          onPressed: _injecting ? null : _inject,
          child: _injecting
              ? const SizedBox(
                  width: 20,
                  height: 20,
                  child: CircularProgressIndicator(
                      strokeWidth: 2, color: Colors.white),
                )
              : Text(
                  _activated ? '注入插件功能' : '请先激活设备',
                  style: const TextStyle(
                      fontSize: 16, fontWeight: FontWeight.w600),
                ),
        ),
      );
}
