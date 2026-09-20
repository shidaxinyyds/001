import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';

import 'license_service.dart';
import 'license_status.dart';

/// 授权闸门：包住主页。
///   - 启动即本地校验短期凭证；有效 → 直接放行（离线秒开）。
///   - 无效/未激活/到期 → 显示激活页，输入卡密联网激活。
///   - 放行后仍每 30 分钟静默复核一次，到期即自动退回激活页。
class LicenseGate extends StatefulWidget {
  final Widget child;
  const LicenseGate({Key? key, required this.child}) : super(key: key);

  @override
  State<LicenseGate> createState() => _LicenseGateState();
}

class _LicenseGateState extends State<LicenseGate> {
  static const Color _accent = Color(0xFF0D9488);

  final TextEditingController _code = TextEditingController();
  LicenseState? _state;
  bool _checking = true;
  bool _busy = false;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _refresh();
    // 放行后低频复核：到期/被拉黑最迟 30 分钟内退回（凭证本身到期也会触发续签）。
    _timer = Timer.periodic(const Duration(minutes: 30), (_) => _refresh());
  }

  @override
  void dispose() {
    _timer?.cancel();
    _code.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    LicenseState? st;
    try {
      st = await LicenseService.instance.ensureUsable();
    } catch (_) {
      // 授权检查自身异常绝不闪退：兜底为"未激活"，交给激活页。
      st = const LicenseState(LicenseStatus.notActivated);
    }
    if (!mounted) return;
    // 主闸门所在引擎能读到本地凭证，是到期的权威判定方；一旦失权
    // （到期/被拒/被拉黑）立即收起悬浮窗，兜住子窗无法自验签的到期场景。
    if (!st.allowsUsage) {
      try {
        if (await FlutterOverlayWindow.isActive()) {
          await FlutterOverlayWindow.closeOverlay();
        }
      } catch (_) {}
    }
    setState(() {
      _state = st;
      _checking = false;
    });
  }

  Future<void> _activate() async {
    FocusScope.of(context).unfocus();
    setState(() => _busy = true);
    LicenseState st;
    try {
      st = await LicenseService.instance.activate(_code.text);
    } catch (_) {
      st = const LicenseState(LicenseStatus.notActivated,
          message: '激活异常，请重试');
    }
    if (!mounted) return;
    setState(() {
      _state = st;
      _busy = false;
    });
    if (st.allowsUsage) _code.clear();
  }

  @override
  Widget build(BuildContext context) {
    if (_checking) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator()),
      );
    }
    final st = _state;
    if (st != null && st.allowsUsage) {
      return widget.child;
    }
    return _activationScreen(st);
  }

  Widget _activationScreen(LicenseState? st) {
    final remaining = st?.remainingDays ?? 0;
    final isExpired =
        st != null && st.status != LicenseStatus.notActivated;
    return Scaffold(
      backgroundColor: Colors.white,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Icon(Icons.workspace_premium_outlined,
                    size: 56, color: _accent),
                const SizedBox(height: 16),
                const Text(
                  '激活授权',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w800,
                      color: Color(0xFF0F172A)),
                ),
                const SizedBox(height: 6),
                Text(
                  isExpired
                      ? (st.message ??
                          '授权已到期，请输入新卡密激活')
                      : '输入卡密开始使用（一卡绑一台设备）',
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 13, color: Color(0xFF64748B)),
                ),
                if (isExpired && remaining > 0) ...[
                  const SizedBox(height: 4),
                  Text(
                    '剩余 $remaining 天',
                    textAlign: TextAlign.center,
                    style: const TextStyle(fontSize: 12, color: _accent),
                  ),
                ],
                const SizedBox(height: 24),
                TextField(
                  controller: _code,
                  enabled: !_busy,
                  autocorrect: false,
                  enableSuggestions: false,
                  textCapitalization: TextCapitalization.characters,
                  keyboardType: TextInputType.text,
                  inputFormatters: [
                    // 仅放行字母/数字/连字符，杜绝输入法塞入中文或空白。
                    FilteringTextInputFormatter.allow(RegExp(r'[A-Za-z0-9\-]')),
                  ],
                  decoration: InputDecoration(
                    hintText: '例如 MJ-XXXX-XXXX-XXXX-XXXX',
                    border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10)),
                    enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10),
                        borderSide: const BorderSide(color: Color(0xFFE2E8F0))),
                    focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10),
                        borderSide: const BorderSide(color: _accent, width: 2)),
                    contentPadding: const EdgeInsets.symmetric(
                        horizontal: 14, vertical: 14),
                  ),
                  style: const TextStyle(
                      fontSize: 16, letterSpacing: 1.2, fontFamily: 'monospace'),
                ),
                if (st?.message != null && !isExpired) ...[
                  const SizedBox(height: 10),
                  Text(
                    st!.message!,
                    style: const TextStyle(color: Color(0xFFB71C1C), fontSize: 13),
                  ),
                ],
                const SizedBox(height: 18),
                SizedBox(
                  height: 48,
                  child: ElevatedButton(
                    onPressed: _busy ? null : _activate,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: _accent,
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(10)),
                    ),
                    child: _busy
                        ? const SizedBox(
                            width: 22,
                            height: 22,
                            child: CircularProgressIndicator(
                                strokeWidth: 2.5, color: Colors.white))
                        : const Text('激活',
                            style:
                                TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                  ),
                ),
                const SizedBox(height: 14),
                const Text(
                  '如提示联网失败，请检查网络后重试；卡密激活后即绑定本机。',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 11.5, color: Color(0xFF94A3B8)),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
