import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:auto_vision/activation.dart';
import 'package:auto_vision/home_page.dart';
import 'package:auto_vision/license_keys.dart';

/// 卡密验证页：软件首次启动后的页面。
/// 必须输入正确的内置卡密才能进入主页；激活成功后本地记住，之后进入不再要求。
class LicenseGate extends StatefulWidget {
  const LicenseGate({Key? key}) : super(key: key);

  @override
  State<LicenseGate> createState() => _LicenseGateState();
}

class _LicenseGateState extends State<LicenseGate> {
  final _controller = TextEditingController();
  final _focusNode = FocusNode();
  bool _obscure = true;
  DeviceStatus? _status;
  bool _checking = true;
  bool _showError = false;
  bool _activating = false;

  // 与主色保持一致（青绿）。错误态用中性红，符合常规认知。
  static const _accent = Color(0xFF00695C);

  @override
  void initState() {
    super.initState();
    _detect();
  }

  Future<void> _detect() async {
    final s = await detectDeviceStatus();
    if (mounted) setState(() => _checking = false);
    if (mounted) setState(() => _status = s);
  }

  Future<void> _submit() async {
    if (_activating) return;
    final c = checkLicenseKey(_controller.text);
    if (c != LicenseCheck.ok) {
      // 卡密错误：提示并清空，避免用户对着错误内容反复试。
      setState(() => _showError = true);
      _controller.clear();
      _focusNode.requestFocus();
      return;
    }
    setState(() => _activating = true);
    // 记录首次激活那一刻识别到的系统信息（已探测完成则用真实值，否则降级）。
    final system = (_status?.system.isNotEmpty == true)
        ? _status!.system
        : '未知设备';
    // 即便持久化写入因异常失败，也允许本次进入（只是不会记住激活状态）。
    try {
      await activate(systemInfo: system);
    } catch (_) {}
    if (mounted) {
      // 替换路由进入主页（无返回栈，无法退回卡密页）。
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const HomePage()),
      );
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final systemText =
        _checking ? '系统检测中…' : (_status?.system ?? '未知设备');
    final networkText =
        _checking ? '网络检测中…' : (_status?.network ?? '网络状态未知');
    final networkOk = _status?.networkOk ?? false;

    return Scaffold(
      backgroundColor: const Color(0xFF0E1116),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(28),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // 顶部真实设备信息条：系统与网络均真实探测。
                Container(
                  width: double.infinity,
                  padding:
                      const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                  decoration: BoxDecoration(
                    color: const Color(0xFF161B22),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: Colors.white12),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      _infoRow(Icons.phone_android_outlined, '系统', systemText),
                      const SizedBox(height: 8),
                      _infoRow(
                        networkOk ? Icons.wifi : Icons.wifi_off,
                        '网络',
                        networkText,
                        accent: networkOk ? _accent : Colors.orangeAccent,
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 36),
                const Icon(Icons.verified_user_outlined,
                    size: 48, color: _accent),
                const SizedBox(height: 10),
                const Text('授权验证',
                    style: TextStyle(
                        color: Colors.white,
                        fontSize: 20,
                        fontWeight: FontWeight.w600)),
                const SizedBox(height: 28),
                TextField(
                  controller: _controller,
                  focusNode: _focusNode,
                  obscureText: _obscure,
                  // 关键防坑：关闭自动大写 / 自动纠错 / 建议，
                  // 否则移动端会把首字母自动大写，破坏"区分大小写"校验。
                  autocorrect: false,
                  enableSuggestions: false,
                  textCapitalization: TextCapitalization.none,
                  keyboardType: TextInputType.visiblePassword,
                  enabled: !_activating,
                  onChanged: (_) {
                    if (_showError) setState(() => _showError = false);
                  },
                  onSubmitted: (_) => _submit(),
                  inputFormatters: [
                    // 仅允许字母与数字，杜绝中文/符号误输入。
                    FilteringTextInputFormatter.allow(RegExp(r'[a-zA-Z0-9]')),
                  ],
                  style: const TextStyle(
                      color: Colors.white, letterSpacing: 2, fontSize: 18),
                  decoration: InputDecoration(
                    hintText: '请输入卡密',
                    hintStyle: const TextStyle(color: Colors.white38),
                    filled: true,
                    fillColor: const Color(0xFF161B22),
                    prefixIcon: const Icon(Icons.key_outlined,
                        color: Colors.white54),
                    suffixIcon: IconButton(
                      icon: Icon(
                        _obscure
                            ? Icons.visibility
                            : Icons.visibility_off,
                        color: Colors.white54,
                      ),
                      onPressed: () =>
                          setState(() => _obscure = !_obscure),
                    ),
                    enabledBorder: OutlineInputBorder(
                      borderSide: BorderSide(
                          color: _showError ? Colors.redAccent : Colors.white24),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    focusedBorder: OutlineInputBorder(
                      borderSide: BorderSide(color: _accent),
                      borderRadius: BorderRadius.circular(10),
                    ),
                  ),
                ),
                if (_showError) ...[
                  const SizedBox(height: 10),
                  const Text('卡密无效，请核对后重试',
                      style: TextStyle(
                          color: Colors.redAccent, fontSize: 13)),
                ],
                const SizedBox(height: 24),
                SizedBox(
                  width: double.infinity,
                  height: 46,
                  child: ElevatedButton(
                    onPressed: _activating ? null : _submit,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: _accent,
                      foregroundColor: Colors.white,
                      disabledBackgroundColor:
                          Colors.teal.shade800.withOpacity(0.5),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(10),
                      ),
                    ),
                    child: _activating
                        ? SizedBox(
                            width: 18,
                            height: 18,
                            child: const CircularProgressIndicator(
                                strokeWidth: 2, color: Colors.white),
                          )
                        : const Text('激活',
                            style: TextStyle(fontSize: 16)),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _infoRow(IconData icon, String label, String value,
      {Color? accent}) {
    return Row(
      children: [
        Icon(icon, size: 16, color: accent ?? Colors.white54),
        const SizedBox(width: 8),
        Text('$label  ',
            style: const TextStyle(color: Colors.white54, fontSize: 13)),
        Expanded(
          child: Text(
            value,
            style: TextStyle(
                color: accent ?? Colors.white,
                fontSize: 13,
                fontWeight: FontWeight.w500),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}
