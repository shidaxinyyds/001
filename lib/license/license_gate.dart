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
  Timer? _lockoutTimer;
  // 心跳在飞标志：防 resume/轮询/到期复核多路触发叠乘，也封死任何
  // “守卫→排程→再心跳”的自递归风暴。
  bool _refreshInFlight = false;
  // 冷启动首验为“未激活”时的一次性延迟复核：存储/指纹通道就绪竞态
  // 不该把已激活设备直接切进激活页（全程仅复核一次，绝不循环）。
  bool _coldRecheckDone = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // 0ms 快速本地验签：若本地已存有合法可用凭证，直接瞬间放行，杜绝闪白/激活页闪现
    final localFast = LicenseService.instance.ensureLocalFast();
    if (localFast.allowsUsage) {
      _state = localFast;
      _checking = false;
    }
    _refresh();
    // 低频轮询作为兜底：到期/被拉黑最迟 30 分钟内退回（到期精确点另有 one-shot 兜底）。
    _timer = Timer.periodic(_pollInterval, (_) => _refresh());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _timer?.cancel();
    _expiryTimer?.cancel();
    _lockoutTimer?.cancel();
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
    if (_refreshInFlight) return; // 已有心跳在飞：不叠乘、不递归
    _refreshInFlight = true;
    try {
      await _doRefresh();
    } finally {
      _refreshInFlight = false;
    }
  }

  Future<void> _doRefresh() async {
    // 1. 本地极速兜底：如果本地凭证目前有效，且当前尚未放行，先立即可用
    final localFast = LicenseService.instance.ensureLocalFast();
    if (localFast.allowsUsage && (_state == null || !_state!.allowsUsage)) {
      if (mounted) {
        setState(() {
          _state = localFast;
          _checking = false;
        });
      }
    }

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
      final local = LicenseService.instance.ensureLocalFast();
      if (local.allowsUsage) {
        setState(() {
          _state = local;
          _checking = false;
        });
        _scheduleExpiryCheck(local);
        return;
      }
      st = const LicenseState(LicenseStatus.notActivated);
    }
    if (!mounted) return;
    // 【冷启动一次性复核 v3】从未放行过的新会话首验撞上 notActivated：可能是
    // SharedPreferences/指纹通道尚未就绪的时序竞态。保持 Loading 1s 后只复核
    // 一次；真未激活的用户也只多等一秒，绝不因此反复重验。
    if (st.status == LicenseStatus.notActivated &&
        _state == null &&
        !localFast.allowsUsage &&
        !_coldRecheckDone) {
      _coldRecheckDone = true;
      _expiryTimer = Timer(const Duration(seconds: 1), _refresh);
      return;
    }

    // 【核心铁律：仅在真到期或确凿拉黑时才允许退回激活页】
    // 若当前会话已经处于放行可用状态（_state?.allowsUsage == true）或者本地合法可用（localFast.allowsUsage == true）：
    // 只要尚未被服务端明确拉黑（st.isRevoked），且自身有效期尚未真正到期：
    // 无论最新检查结果是 notActivated、缺少指纹导致的 refused、网络抖动超时、
    // 插件初始化延迟、还是服务器心跳并发竞态，统统原地保持功能页放行，严禁跳转激活页！
    final currentAllows = (_state?.allowsUsage ?? false) || localFast.allowsUsage;
    if (currentAllows) {
      final bool isRevoked = st.isRevoked;
      final effectiveSt = (_state?.allowsUsage ?? false) ? _state! : localFast;
      final bool isExpired = (st.status == LicenseStatus.licenseExpired) &&
          !_retainedSessionStillValid(effectiveSt);

      if (!isRevoked && !isExpired) {
        // 既没有确凿拉黑，也没有真正到期（仍在有效期内）：
        // 绝对不得踢回激活页，原地保持放行会话！
        if (st.allowsUsage) {
          setState(() {
            _state = st;
            _checking = false;
          });
          _scheduleExpiryCheck(st);
        } else {
          // 新状态是瞬态错误/网络失败/设备未就绪等异常：
          // 原地维持放行态，不关闭悬浮窗，不跳激活页，仅安排下次复核
          if (_state == null || !_state!.allowsUsage) {
            setState(() {
              _state = effectiveSt;
              _checking = false;
            });
          }
          _scheduleExpiryCheck(effectiveSt);
        }
        return;
      }
    }

    // 真正失权（确凿拉黑或真正到期）时才收起悬浮窗并跳转激活页
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

  /// 被保留的会话是否仍在自身有效期内（双时钟防误杀）。
  bool _retainedSessionStillValid(LicenseState st) {
    final exp = st.expiresAt;
    if (exp == null) return true;
    final serverNow = DateTime.fromMillisecondsSinceEpoch(
        LicenseService.instance.serverNowSec * 1000);
    return exp.isAfter(serverNow) || exp.isAfter(DateTime.now());
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
      // 到期已过：用有界 Timer 5s 后复核，绝不直调 _refresh——旧直调与
      // “保留会话”守卫路径会构成无界自递归（心跳风暴/界面冻结）。
      _expiryTimer = Timer(const Duration(seconds: 5), _refresh);
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
    if (LicenseService.instance.isLockedOut) {
      _startLockoutTimer();
    }
    if (st.allowsUsage) {
      _code.clear();
      // 激活成功后立即排定到期复核（短卡到期即时退回激活页），与 _refresh 一致，
      // 不再等下一次 30 分钟轮询。
      _scheduleExpiryCheck(st);
    }
  }

  void _startLockoutTimer() {
    _lockoutTimer?.cancel();
    _lockoutTimer = Timer.periodic(const Duration(seconds: 1), (t) {
      if (!mounted) {
        t.cancel();
        return;
      }
      if (!LicenseService.instance.isLockedOut) {
        t.cancel();
      }
      setState(() {});
    });
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
    final int remLock = LicenseService.instance.lockoutRemainingSeconds;
    final bool isLocked = remLock > 0;
    // 提示文案只反映真实状态，绝不拿"已到期"兜底吓用户：
    //  - 冷却锁定：防爆破惩罚倒计时；
    //  - licenseExpired：签名内 expires_at 真到达（或服务器确认到期）才说"已到期"；
    //  - refused：服务端拉黑/换设备/篡改等，逐条显示服务端的拒因原文；
    //  - 其它（未激活/激活失败）：只是请用户输入卡密，不提任何到期字样。
    final status = st?.status;
    final bool isExpired = status == LicenseStatus.licenseExpired;
    final bool isRefused = status == LicenseStatus.refused;
    final String prompt;
    if (isLocked) {
      prompt = '安全防护模式已生效 · 冷却剩余 ${remLock}s';
    } else if (isExpired) {
      prompt = '授权凭证已到期 · 请输入有效卡密';
    } else if (isRefused) {
      prompt = st?.message ?? '凭证核验未通过 · 请输入有效卡密';
    } else {
      prompt = '请输入授权卡密完成本机硬件指纹绑定';
    }
    return Scaffold(
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
                    borderRadius: AppTokens.radius16,
                    border: Border.all(color: AppTokens.border),
                    boxShadow: AppTokens.raised,
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      // 终端安全徽标
                      Align(
                        alignment: Alignment.center,
                        child: Container(
                          width: 64,
                          height: 64,
                          decoration: const BoxDecoration(
                            color: AppTokens.brandContainer,
                            shape: BoxShape.circle,
                          ),
                          child: const Icon(Icons.security_rounded,
                              size: 32, color: AppTokens.brandDark),
                        ),
                      ),
                      const SizedBox(height: AppTokens.s16),
                      const Text(
                        '终端安全认证',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                            fontSize: 22,
                            fontWeight: FontWeight.w800,
                            color: AppTokens.ink,
                            letterSpacing: 0.8),
                      ),
                      const SizedBox(height: AppTokens.s6),
                      Text(
                        prompt,
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                            fontSize: 12.5,
                            fontWeight: FontWeight.w500,
                            color: AppTokens.muted),
                      ),
                      const SizedBox(height: AppTokens.s20),
                      TextField(
                        controller: _code,
                        enabled: !_busy && !isLocked,
                        autocorrect: false,
                        enableSuggestions: false,
                        textCapitalization: TextCapitalization.characters,
                        keyboardType: TextInputType.text,
                        inputFormatters: [
                          FilteringTextInputFormatter.allow(
                              RegExp(r'[A-Za-z0-9\-]')),
                        ],
                        decoration: InputDecoration(
                          hintText: '输入授权卡密 (如 MJ-XXXX-...)',
                          hintStyle: const TextStyle(
                              fontSize: 13.5, color: AppTokens.faint),
                          prefixIcon: const Icon(Icons.key_rounded,
                              size: 19, color: AppTokens.faint),
                          suffixIcon: IconButton(
                            icon: const Icon(Icons.content_paste_rounded,
                                size: 18, color: AppTokens.brand),
                            tooltip: '粘贴',
                            onPressed: () async {
                              final data = await Clipboard.getData('text/plain');
                              if (data != null && (data.text?.isNotEmpty ?? false)) {
                                _code.text = data.text!.trim();
                              }
                            },
                          ),
                          border: OutlineInputBorder(
                              borderRadius: AppTokens.radius10),
                          enabledBorder: OutlineInputBorder(
                              borderRadius: AppTokens.radius10,
                              borderSide:
                                  const BorderSide(color: AppTokens.border)),
                          focusedBorder: OutlineInputBorder(
                              borderRadius: AppTokens.radius10,
                              borderSide: const BorderSide(
                                  color: AppTokens.brand, width: 1.5)),
                        ),
                        style: const TextStyle(
                            fontSize: 15,
                            letterSpacing: 1.2,
                            fontFamily: 'monospace',
                            color: AppTokens.ink),
                      ),
                      if (st?.message != null && !isExpired && !isRefused) ...[
                        const SizedBox(height: AppTokens.s10),
                        Text(
                          st!.message!,
                          style: const TextStyle(
                              color: AppTokens.danger, fontSize: 12),
                        ),
                      ],
                      const SizedBox(height: AppTokens.s16),
                      SizedBox(
                        height: 48,
                        child: FilledButton(
                          onPressed: (_busy || isLocked) ? null : _activate,
                          style: FilledButton.styleFrom(
                            backgroundColor: isLocked ? AppTokens.borderStrong : AppTokens.brand,
                            foregroundColor: Colors.white,
                            elevation: 0,
                            shape: RoundedRectangleBorder(
                                borderRadius: AppTokens.radius10),
                          ),
                          child: _busy
                              ? const SizedBox(
                                  width: 20,
                                  height: 20,
                                  child: CircularProgressIndicator(
                                      strokeWidth: 2.2, color: Colors.white))
                              : Text(isLocked ? '安全保护锁定 (${remLock}s)' : '验 证 并 激 活',
                                  style: const TextStyle(
                                      fontSize: 15,
                                      fontWeight: FontWeight.w700,
                                      letterSpacing: 0.6)),
                        ),
                      ),
                      const SizedBox(height: AppTokens.s14),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(Icons.shield_outlined,
                              size: 13, color: AppTokens.faint),
                          const SizedBox(width: 5),
                          const Text(
                            '硬件指纹单向哈希 · 离线签名加密',
                            style: TextStyle(
                                fontSize: 11,
                                color: AppTokens.faint,
                                letterSpacing: 0.2),
                          ),
                        ],
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
