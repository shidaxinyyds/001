import 'package:flutter/material.dart';

/// 调试页：暗色发光风格（参照用户提供的模板）。
/// 实时显示识别链路诊断 + 三个识别策略开关 + 方向控制。
/// 所有数据由主页 _HomePageState 每帧通过 build() 下发，本页为纯展示 + 回调。
class DebugPage extends StatelessWidget {
  final String status;
  final int count;
  final int? shanten;
  final String hand;
  final double topScore;
  final String screen;
  final String message;
  final bool isProcessing;
  final int orientDeg;
  final bool cfgAutoOrient;
  final bool cfgBootstrap;
  final bool cfgStrict;
  final void Function(String key, bool value) onToggle;
  final VoidCallback onRotate;
  final VoidCallback onReprobe;
  final VoidCallback onStartStop;

  const DebugPage({
    Key? key,
    required this.status,
    required this.count,
    required this.shanten,
    required this.hand,
    required this.topScore,
    required this.screen,
    required this.message,
    required this.isProcessing,
    required this.orientDeg,
    required this.cfgAutoOrient,
    required this.cfgBootstrap,
    required this.cfgStrict,
    required this.onToggle,
    required this.onRotate,
    required this.onReprobe,
    required this.onStartStop,
  }) : super(key: key);

  static const Color _bgTop = Color(0xFF0A0A0A);
  static const Color _bgBottom = Color(0xFF0D0D0D);
  static const Color _accent = Color(0xFF00695C); // teal 800
  static const Color _accentGreen = Color(0xFF00C853);
  static const Color _textMain = Color(0xFFFFFFFF);
  static const Color _textSub = Color(0x99FFFFFF);

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [_bgTop, _bgBottom],
        ),
      ),
      child: SafeArea(
        child: Stack(
          children: [
            // 发光装饰
            Positioned(
              top: 20,
              left: -60,
              child: _glow(160, const Color(0x263C8870)),
            ),
            Positioned(
              bottom: 120,
              right: -40,
              child: _glow(140, const Color(0x2600C853)),
            ),
            SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text('调试',
                      style: TextStyle(
                          fontSize: 22,
                          fontWeight: FontWeight.bold,
                          color: _textMain)),
                  const SizedBox(height: 4),
                  const Text('实时识别链路诊断 · 策略开关',
                      style: TextStyle(fontSize: 13, color: _textSub)),
                  const SizedBox(height: 16),
                  _diagCard(),
                  const SizedBox(height: 18),
                  _switchCard(),
                  const SizedBox(height: 18),
                  _actionCard(),
                  const SizedBox(height: 12),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _glow(double size, Color color) => Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          color: color,
          shape: BoxShape.circle,
          boxShadow: [BoxShadow(blurRadius: 60, color: color)],
        ),
      );

  Widget _diagCard() {
    final String shantenText = shanten == null
        ? '—'
        : (shanten == 0 ? '听牌' : '$shanten 向听');
    return _panel(
      title: '识别状态',
      child: Column(
        children: [
          _row('状态', _statusText()),
          _row('切牌数', count > 0 ? '$count 张' : '—'),
          _row('方向', '$orientDeg°'),
          _row('向听', shantenText),
          _row('匹配分', topScore.toStringAsFixed(2)),
          _row('屏幕', screen.isEmpty ? '未知' : screen),
          const Divider(color: Color(0x22FFFFFF), height: 16),
          _row('手牌', hand.isEmpty ? '（空）' : hand,
              valueColor: _accentGreen),
          if (message.isNotEmpty)
            _row('消息', message, valueColor: _textSub),
        ],
      ),
    );
  }

  String _statusText() {
    switch (status) {
      case 'ok':
        return '✓ 识别正常';
      case 'incomplete':
        return '识别不完整';
      case 'no_tiles':
        return '未识别到牌';
      case 'engine_ready':
        return '引擎就绪·等待画面';
      case 'no_frames':
        return '无画面·确认录屏';
      case 'projection_stopped':
        return '录屏被系统结束';
      case 'send_error':
        return '结果发送失败';
      case 'py_error':
      case 'decode_error':
      case 'java_error':
      case 'capture_error':
      case 'start_failed':
        return '链路异常';
      default:
        return status.isEmpty ? '未开始' : status;
    }
  }

  Widget _switchCard() {
    return _panel(
      title: '识别策略',
      child: Column(
        children: [
          _switchRow('自动旋转探测', '横屏/竖屏自动归一方向',
              cfgAutoOrient, (v) => onToggle('auto_orient', v)),
          _switchRow('冷启动', '启动期放宽门槛，打破严格门槛死锁',
              cfgBootstrap, (v) => onToggle('bootstrap', v)),
          _switchRow('严格门槛', '关闭则一律放宽（更易识别但更易误识）',
              cfgStrict, (v) => onToggle('strict', v)),
        ],
      ),
    );
  }

  Widget _actionCard() {
    return _panel(
      title: '方向 / 识别控制',
      child: Column(
        children: [
          Row(
            children: [
              Expanded(
                child: _btn('旋转 ⟳ ($orientDeg°)', onRotate),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _btn('重探方向', onReprobe),
              ),
            ],
          ),
          const SizedBox(height: 12),
          SizedBox(
            height: 50,
            child: ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor:
                    isProcessing ? Colors.grey.shade700 : _accent,
                foregroundColor: _textMain,
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12)),
              ),
              onPressed: onStartStop,
              child: Text(isProcessing ? '停止识别' : '开始识别',
                  style: const TextStyle(
                      fontSize: 16, fontWeight: FontWeight.w600)),
            ),
          ),
        ],
      ),
    );
  }

  Widget _panel({required String title, required Widget child}) {
    return Container(
      decoration: BoxDecoration(
        color: const Color(0x0DFFFFFF),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0x1AFFFFFF)),
      ),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title,
              style: const TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: _accentGreen)),
          const SizedBox(height: 12),
          child,
        ],
      ),
    );
  }

  Widget _row(String label, String value, {Color? valueColor}) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 64,
            child: Text(label,
                style: const TextStyle(fontSize: 13, color: _textSub)),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(value,
                style: TextStyle(
                    fontSize: 13,
                    color: valueColor ?? _textMain,
                    height: 1.3)),
          ),
        ],
      ),
    );
  }

  Widget _switchRow(
      String name, String desc, bool value, void Function(bool) onChanged) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(name,
                    style: const TextStyle(
                        fontSize: 14, color: _textMain)),
                const SizedBox(height: 2),
                Text(desc,
                    style: const TextStyle(
                        fontSize: 11, color: _textSub)),
              ],
            ),
          ),
          Switch(
            value: value,
            activeColor: _accent,
            onChanged: onChanged,
          ),
        ],
      ),
    );
  }

  Widget _btn(String text, VoidCallback onTap) {
    return SizedBox(
      height: 46,
      child: OutlinedButton(
        style: OutlinedButton.styleFrom(
          side: const BorderSide(color: _accent),
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12)),
        ),
        onPressed: onTap,
        child: Text(text,
            style: const TextStyle(fontSize: 14, color: _textMain)),
      ),
    );
  }
}
