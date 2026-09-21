import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';

import 'package:auto_vision/theme/app_tokens.dart';
import 'license_service.dart';
import 'license_status.dart';

/// 授权闸门：包住主页。
///   - 启动即服务器心跳核验；有效 → 直接放行（离线秒开）。
///   - 无效/未激活/到期 → 显示激活页，输入卡密联网激活。
///   - 放行后：进前台(resume)、每 30 分钟、以及到期精确点都会强制心跳复核，
///     失权即自动退回激活页并收起悬浮窗。
class LicenseGate extends StatefulWidget {
  final Widget child;
  const LicenseGate({Key? key, required this.child}) : super(key: key);

  @override
  State<LicenseGate> createState() => _LicenseGateState();
}

class _LicenseGateState extends State<LicenseGate> with WidgetsBindingObserver {
  // 常规定期轮询间隔；短卡到期由精确 one-shot 定时器兜住。
  static const Duration _pollInterval = Duration(minutes: 30);

  final TextEditingController _code = TextEditingController();
  LicenseState? _state;
  bool _checking = true;
  bool _busy = false;
  Timer? _timer;
  Timer? _expiryTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refresh();
    // 低频轮询作为兜底：到期/被拉黑最迟 30 分钟内退回（到期精确点另有 one-shot 兜底）。
    _timer = Timer.periodic(_pollInterval, (_) => _refresh());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _timer?.cancel();
    _expiryTimer?.cancel();
    _code.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // 进入前台：立即强制一次服务器心跳核验（对齐需求“每次进入前台”）。
    if (state == AppLifecycleState.resumed) {
      _refresh();
    }
  }

  Future<void> _refresh() async {
    LicenseState st;
    try {
      st = await LicenseService.instance.heartbeat();
    } catch (_) {
      if (!mounted) return;
      // 授权核验自身异常：绝不闪退，也绝不把一段本就可用的会话误重置为「未激活」。
      // 已有可用状态时原地保持（不收起悬浮窗、不跳回激活页），仅在从未激活过时兜底。
      if (_state?.allowsUsage ?? false) {
        _scheduleExpiryCheck(_state!);
        return;
      }
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
    _scheduleExpiryCheck(st);
  }

  /// 若总到期在轮询间隔内（如 10 分钟短卡），排一个到期精确 one-shot，
  /// 到期即时复核；否则交给 30 分钟轮询，不占用长时效定时器。
  void _scheduleExpiryCheck(LicenseState st) {
    _expiryTimer?.cancel();
    final exp = st.expiresAt;
    if (exp == null || !st.allowsUsage) return;
    final serverNow = DateTime.fromMillisecondsSinceEpoch(
        LicenseService.instance.serverNowSec * 1000);
    final remainMs = exp.difference(serverNow).inMilliseconds;
    if (remainMs <= 0) {
      _refresh();
      return;
    }
    if (remainMs > _pollInterval.inMilliseconds) return; // 长时效卡交给轮询
    _expiryTimer = Timer(
        Duration(milliseconds: remainMs + 1500), _refresh);
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
    if (st.allowsUsage) {
      _code.clear();
      // 激活成功后立即排定到期复核（短卡到期即时退回激活页），与 _refresh 一致，
      // 不再等下一次 30 分钟轮询。
      _scheduleExpiryCheck(st);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_checking) {
      return const Scaffold(
        backgroundColor: AppTokens.bg,
        body: Center(
            child: CircularProgressIndicator(
                color: AppTokens.brand, strokeWidth: 3)),
      );
    }
    final st = _state;
    if (st != null && st.allowsUsage) {
      return widget.child;
    }
    return _activationScreen(st);
  }

  Widget _activationScreen(LicenseState? st) {
    // 提示文案只反映真实状态，绝不拿"已到期"兜底吓用户：
    //  - licenseExpired：签名内 expires_at 真到达（或服务器确认到期）才说"已到期"；
    //  - refused：服务端拉黑/换设备/篡改等，逐条显示服务端的拒因原文；
    //  - 其它（未激活/激活失败）：只是请用户输入卡密，不提任何到期字样。
    final status = st?.status;
    final bool isExpired = status == LicenseStatus.licenseExpired;
    final bool isRefused = status == LicenseStatus.refused;
    final String prompt;
    if (isExpired) {
      prompt = '卡密授权已到期，请输入新卡密激活';
    } else if (isRefused) {
      prompt = st?.message ?? '授权校验未通过，请重新输入卡密激活';
    } else {
      prompt = '输入卡密开始使用（一卡绑一台设备）';
    }
    return Scaffold(
      // 明亮商务底：极淡的品牌青向下过渡到近白，营造高级感而不喧宾夺主。
      backgroundColor: AppTokens.bg,
      body: SafeArea(
        child: DecoratedBox(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: <Color>[AppTokens.brandSoft, AppTokens.bg],
            ),
          ),
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.symmetric(
                  horizontal: AppTokens.s24, vertical: AppTokens.s24),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                child: Container(
                  padding: const EdgeInsets.all(AppTokens.s24),
                  decoration: BoxDecoration(
                    color: AppTokens.surface,
                    borderRadius: AppTokens.radius20,
                    border: Border.all(color: AppTokens.border),
                    boxShadow: AppTokens.raised,
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      // 品牌标识：圆形 tonal 容器承托图标，形成记忆点。
                      Align(
                        alignment: Alignment.center,
                        child: Container(
                          width: 72,
                          height: 72,
                          decoration: const BoxDecoration(
                            color: AppTokens.brandContainer,
                            shape: BoxShape.circle,
                          ),
                          child: const Icon(Icons.workspace_premium_outlined,
                              size: 38, color: AppTokens.brandDark),
                        ),
                      ),
                      const SizedBox(height: AppTokens.s20),
                      const Text(
                        '激活授权',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                            fontSize: 24,
                            fontWeight: FontWeight.w800,
                            color: AppTokens.ink,
                            letterSpacing: 0.5),
                      ),
                      const SizedBox(height: AppTokens.s8),
                      Text(
                        prompt,
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                            fontSize: 13, color: AppTokens.muted),
                      ),
                      const SizedBox(height: AppTokens.s24),
                      TextField(
                        controller: _code,
                        enabled: !_busy,
                        autocorrect: false,
                        enableSuggestions: false,
                        textCapitalization: TextCapitalization.characters,
                        keyboardType: TextInputType.text,
                        inputFormatters: [
                          // 仅放行字母/数字/连字符，杜绝输入法塞入中文或空白。
                          FilteringTextInputFormatter.allow(
                              RegExp(r'[A-Za-z0-9\-]')),
                        ],
                        decoration: InputDecoration(
                          hintText: '例如 MJ-XXXX-XXXX-XXXX-XXXX',
                          prefixIcon: const Icon(Icons.vpn_key_outlined,
                              color: AppTokens.faint),
                          border: OutlineInputBorder(
                              borderRadius: AppTokens.radius12),
                          enabledBorder: OutlineInputBorder(
                              borderRadius: AppTokens.radius12,
                              borderSide:
                                  const BorderSide(color: AppTokens.border)),
                          focusedBorder: OutlineInputBorder(
                              borderRadius: AppTokens.radius12,
                              borderSide: const BorderSide(
                                  color: AppTokens.brand, width: 2)),
                        ),
                        style: const TextStyle(
                            fontSize: 16,
                            letterSpacing: 1.2,
                            fontFamily: 'monospace',
                            color: AppTokens.ink),
                      ),
                      if (st?.message != null && !isExpired && !isRefused) ...[
                        const SizedBox(height: AppTokens.s12),
                        Text(
                          st!.message!,
                          style: const TextStyle(
                              color: AppTokens.danger, fontSize: 13),
                        ),
                      ],
                      const SizedBox(height: AppTokens.s20),
                      SizedBox(
                        height: 50,
                        child: FilledButton(
                          onPressed: _busy ? null : _activate,
                          style: FilledButton.styleFrom(
                            backgroundColor: AppTokens.brand,
                            foregroundColor: Colors.white,
                            shape: RoundedRectangleBorder(
                                borderRadius: AppTokens.radius12),
                          ),
                          child: _busy
                              ? const SizedBox(
                                  width: 22,
                                  height: 22,
                                  child: CircularProgressIndicator(
                                      strokeWidth: 2.5, color: Colors.white))
                              : const Text('激活',
                                  style: TextStyle(
                                      fontSize: 16,
                                      fontWeight: FontWeight.w700)),
                        ),
                      ),
                      const SizedBox(height: AppTokens.s16),
                      const Text(
                        '如提示联网失败，请检查网络后重试；卡密激活后即绑定本机。',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                            fontSize: 11.5, color: AppTokens.faint),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
