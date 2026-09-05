import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:auto_vision/license_keys.dart';
import 'package:auto_vision/home_page.dart';

/// 卡密验证页：软件启动后的首个页面。
/// 必须输入正确的内置卡密（12 位：前 6 字母 + 后 6 数字，区分大小写）才能进入主页。
class LicenseGate extends StatefulWidget {
  const LicenseGate({Key? key}) : super(key: key);

  @override
  State<LicenseGate> createState() => _LicenseGateState();
}

class _LicenseGateState extends State<LicenseGate> {
  final _controller = TextEditingController();
  final _focusNode = FocusNode();
  bool _obscure = false;
  LicenseCheck? _lastCheck;

  // 与主页保持一致的主色调（青绿）。错误态使用中性红，符合常规认知。
  static const _accent = Color(0xFF00695C);

  String _errorText(LicenseCheck c) {
    switch (c) {
      case LicenseCheck.empty:
        return '请输入卡密';
      case LicenseCheck.length:
        return '卡密长度须为 12 位（已自动忽略空格与连字符）';
      case LicenseCheck.format:
        return '格式不正确：前 6 位须为字母、后 6 位须为数字';
      case LicenseCheck.notFound:
        return '卡密无效，请检查后重试';
      case LicenseCheck.ok:
        return '';
    }
  }

  void _submit() {
    final c = checkLicenseKey(_controller.text);
    if (c == LicenseCheck.ok) {
      // 校验通过，替换当前路由进入主页（无返回栈，无法退回卡密页）。
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const HomePage()),
      );
      return;
    }
    setState(() => _lastCheck = c);
    // 卡密错误时清空输入并重新聚焦，避免用户对着错误内容反复试。
    if (c == LicenseCheck.notFound) {
      _controller.clear();
      _focusNode.requestFocus();
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
    final hasError = _lastCheck != null && _lastCheck != LicenseCheck.ok;
    final borderColor = hasError ? Colors.redAccent : Colors.white30;
    return Scaffold(
      backgroundColor: const Color(0xFF101418),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Card(
            color: const Color(0xFF1A2026),
            elevation: 8,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
            ),
            child: Padding(
              padding: const EdgeInsets.all(28),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 380),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Icon(Icons.verified_user_outlined,
                        size: 56, color: _accent),
                    const SizedBox(height: 12),
                    const Text('卡密验证',
                        style: TextStyle(
                            color: Colors.white,
                            fontSize: 22,
                            fontWeight: FontWeight.bold)),
                    const SizedBox(height: 6),
                    const Text(
                      '请输入 12 位卡密以进入软件\n'
                      '（前 6 位字母 + 后 6 位数字，区分大小写）',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: Colors.white70, fontSize: 13),
                    ),
                    const SizedBox(height: 24),
                    TextField(
                      controller: _controller,
                      focusNode: _focusNode,
                      obscureText: _obscure,
                      // 关键防坑：关闭自动大写 / 自动纠错 / 建议，
                      // 否则移动端会自动把首字母大写，破坏"区分大小写"校验。
                      autocorrect: false,
                      enableSuggestions: false,
                      textCapitalization: TextCapitalization.none,
                      keyboardType: TextInputType.visiblePassword,
                      onSubmitted: (_) => _submit(),
                      inputFormatters: [
                        // 仅允许字母与数字，杜绝中文/符号误输入。
                        FilteringTextInputFormatter.allow(
                            RegExp(r'[a-zA-Z0-9]')),
                      ],
                      style: const TextStyle(
                          color: Colors.white,
                          letterSpacing: 2,
                          fontSize: 18),
                      decoration: InputDecoration(
                        hintText: '例如 AbCxYz123456',
                        hintStyle: const TextStyle(color: Colors.white38),
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
                            borderSide: BorderSide(color: borderColor)),
                        focusedBorder: const OutlineInputBorder(
                            borderSide: BorderSide(color: _accent)),
                        border: OutlineInputBorder(
                            borderSide: BorderSide(color: borderColor)),
                      ),
                    ),
                    if (hasError) ...[
                      const SizedBox(height: 10),
                      Text(_errorText(_lastCheck!),
                          style: const TextStyle(
                              color: Colors.redAccent, fontSize: 13)),
                    ],
                    const SizedBox(height: 22),
                    SizedBox(
                      width: double.infinity,
                      height: 46,
                      child: ElevatedButton(
                        onPressed: _submit,
                        style: ElevatedButton.styleFrom(
                          backgroundColor: _accent,
                          foregroundColor: Colors.white,
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(10),
                          ),
                        ),
                        child: const Text('进入软件',
                            style: TextStyle(fontSize: 16)),
                      ),
                    ),
                    const SizedBox(height: 12),
                    Text('内置可用卡密：${kBuiltInLicenseKeys.length} 个',
                        style: const TextStyle(
                            color: Colors.white38, fontSize: 11)),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
