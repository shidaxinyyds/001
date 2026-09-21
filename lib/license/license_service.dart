import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../channel.dart';
import 'config.dart';
import 'license_client.dart';
import 'license_status.dart';
import 'token.dart';

/// 授权服务单例。主引擎与悬浮窗引擎各自持有实例，但共用同一份
/// SharedPreferences（同 app 私有区），因此后台续签写入后悬浮窗可读到。
///
/// 放行判定（一律以服务器签名时间为准，本地时钟仅用于测流逝）：
///   - 凭证有效且未过期 → valid（离线可用）
///   - 凭证到续签点 → 联网 renew；网络失败但仍在 72h 宽限内 → needsRenew（软，仍可用）
///   - 服务端心跳明确 expired / revoked / 换设备 / 改时间 → 硬拒绝
class LicenseService {
  LicenseService._({LicenseClient? client}) : _client = client ?? LicenseClient();
  static final LicenseService instance = LicenseService._();

  static const String _kToken = 'lic_token';
  static const String _kMaxWallMs = 'lic_max_wall_ms';
  // 服务器时间高水位（epoch 秒）：跨进程重启仍单调不回退，防重启后调时钟属滥用。
  static const String _kMaxServerNowS = 'lic_max_server_now_s';
  // 改时间回拨容忍窗口：比这个大得多的回拨视为篡改。
  static const int _rollbackTolS = 86400; // 1 天
  // 凭证到期后的网络宽限：宽限内 renew/心跳失败仍可离线用（最多 72 小时）。
  static const int _graceS = 259200; // 72 小时

  final MethodChannel _ch = const MethodChannel(CHANNEL_NAME);
  final LicenseClient _client;

  SharedPreferences? _prefs;
  String? _deviceId;
  bool _ready = false;

  // 服务器时间锚点：_srvNowS 为上次心跳的服务器秒，_srvBaseElapsedMs 为当时单调钟读数。
  // 单调钟(Stopwatch)测流逝，本地墙钟被前调/后拨都无法伪造"当前服务器时间"。
  int? _srvNowS;
  int _srvBaseElapsedMs = 0;
  final Stopwatch _mono = Stopwatch()..start();

  // ===== 测试钩子（不改变生产行为）=====
  @visibleForTesting
  static LicenseService newForTest({LicenseClient? client}) =>
      LicenseService._(client: client);

  @visibleForTesting
  void primeForTest({required String deviceId, SharedPreferences? prefs}) {
    _deviceId = deviceId;
    _prefs = prefs;
    _ready = true;
  }

  @visibleForTesting
  void noteServerTimeForTest(int serverNowSec) => _noteServerTime(serverNowSec);

  /// 直接对已验签的凭证求值（跳过 HMAC），便于无密钥环境下测试到期/宽限/服务器时间。
  @visibleForTesting
  LicenseState evaluateForTest(LicenseToken tk) => _evaluate(tk);

  Future<void> init() async {
    if (_ready) return;
    // 存储不可用属极端环境异常：吞掉并置 _prefs=null（上层退化为"未激活"），
    // 绝不让授权检查本身把 App 首屏带崩。
    try {
      _prefs = await SharedPreferences.getInstance();
    } catch (_) {
      _prefs = null;
    }
    try {
      _deviceId = (await _ch.invokeMethod<String>('getDeviceId')) ?? '';
    } catch (_) {
      _deviceId = '';
    }
    _ready = true;
  }

  String get deviceId => _deviceId ?? '';

  /// 当前服务器锚定时间（epoch 秒），供上层排到期精确定时器。
  int get serverNowSec => _effectiveNowSec();

  // ===== 本地状态计算（不联网）=====

  LicenseToken? _loadToken() {
    final p = _prefs;
    if (p == null) return null;
    final stored = p.getString(_kToken) ?? '';
    if (stored.isEmpty) return null;
    return LicenseToken.parseAndVerify(
        stored, LicenseConfig.licenseTokenSecretHex, deviceId);
  }

  void _saveToken(String token) {
    _prefs?.setString(_kToken, token);
  }

  void _clearToken() {
    _prefs?.remove(_kToken);
  }

  bool _clockTampered(int nowMs) {
    final p = _prefs;
    if (p == null) return false;
    final maxWall = p.getInt(_kMaxWallMs) ?? 0;
    if (maxWall > 0 && (maxWall - nowMs) > _rollbackTolS * 1000) {
      return true; // 时间被往回拨超过容忍窗口
    }
    if (nowMs > maxWall) {
      p.setInt(_kMaxWallMs, nowMs); // 推进单调锚点
    }
    return false;
  }

  // ===== 服务器时间锚点（防本地时钟篡改的唯一时间基准）=====

  /// 记录一次服务器签名时间，重置单调锚点；并把高水位 best-effort 落盘（主引擎）。
  void _noteServerTime(int? serverTimeSec) {
    if (serverTimeSec == null || serverTimeSec <= 0) return;
    _srvNowS = serverTimeSec;
    _srvBaseElapsedMs = _mono.elapsedMilliseconds;
    final p = _prefs;
    if (p != null) {
      final floor = p.getInt(_kMaxServerNowS) ?? 0;
      if (serverTimeSec > floor) p.setInt(_kMaxServerNowS, serverTimeSec);
    }
  }

  /// 当前"服务器时间"秒：有锚点则 = 上次服务器秒 + 单调流逝；无锚点退回本地墙钟。
  /// 无论哪种，都不得回退到已知高水位之下（防重启后调时钟/断网前调后移到未来）。
  int _effectiveNowSec() {
    final localSec = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    final s = _srvNowS;
    int eff;
    if (s != null) {
      final elapsedSec = (_mono.elapsedMilliseconds - _srvBaseElapsedMs) ~/ 1000;
      eff = s + elapsedSec;
      final p = _prefs;
      if (p != null && eff > (p.getInt(_kMaxServerNowS) ?? 0)) {
        p.setInt(_kMaxServerNowS, eff); // 仅在有锚点时推进高水位
      }
    } else {
      eff = localSec;
    }
    final floor = _prefs?.getInt(_kMaxServerNowS) ?? 0;
    if (floor > eff) eff = floor;
    return eff;
  }

  DateTime _serverNow() =>
      DateTime.fromMillisecondsSinceEpoch(_effectiveNowSec() * 1000);

  LicenseState _evaluate(LicenseToken tk) {
    final expiresAt =
        DateTime.fromMillisecondsSinceEpoch(tk.expiresAt * 1000);
    final tokenExp = DateTime.fromMillisecondsSinceEpoch(tk.tokenExp * 1000);
    // 回拨检测必须用本地墙钟（唯一能发现用户把时间调过去的信号）。
    if (_clockTampered(DateTime.now().millisecondsSinceEpoch)) {
      return LicenseState(LicenseStatus.refused,
          expiresAt: expiresAt, message: '检测到系统时间被回拨，请联网校准');
    }
    // 到期/宽限比较一律用服务器锚定时间，本地时钟无法伪造。
    final now = _serverNow();
    if (expiresAt.isBefore(now)) {
      // 总授权到期 → 自动销毁，需新卡密。
      return LicenseState(LicenseStatus.licenseExpired, expiresAt: expiresAt);
    }
    if (tokenExp.add(Duration(seconds: _graceS)).isBefore(now)) {
      // 凭证过期且超出宽限、又没续上 → 需重新联网（可能被拉黑）。
      return LicenseState(LicenseStatus.refused,
          expiresAt: expiresAt, message: '授权校验失败，请联网后重试或续费');
    }
    if (tokenExp.isBefore(now)) {
      // 凭证过期但在宽限内：软状态，仍可用，后台重试续签。
      return LicenseState(LicenseStatus.needsRenew,
          expiresAt: expiresAt, tokenExp: tokenExp);
    }
    return LicenseState(LicenseStatus.valid,
        expiresAt: expiresAt, tokenExp: tokenExp);
  }

  /// 是否已接近续签点（提前 renewLeadS 触发）。
  bool _dueRenew(LicenseToken tk) {
    final tokenExp = DateTime.fromMillisecondsSinceEpoch(tk.tokenExp * 1000);
    return !_serverNow().isBefore(
        tokenExp.subtract(const Duration(seconds: LicenseConfig.renewLeadS)));
  }

  // ===== 对外：拿状态（必要时静默续签）=====

  Future<LicenseState> ensureUsable() async {
    await init();
    final now = _serverNow();
    final tk = _loadToken();
    if (tk == null) {
      final p = _prefs;
      final had = (p?.getString(_kToken) ?? '').isNotEmpty;
      // 存了但验签失败：换设备 / 篡改 / 密钥不符。
      return LicenseState(had ? LicenseStatus.refused : LicenseStatus.notActivated,
          message: had ? '授权与本机不匹配或已损坏' : null);
    }
    var st = _evaluate(tk);
    // 续签触发：①needsRenew(宽限内)；②licenseExpired；③refused 且凭证已超宽限。
    // 后两类中，expires_at / token_exp 都是 HMAC 签名的本地不可改：
    //  - refused+超宽限：用户离线过久但卡可能仍有效 → 必须给续签机会；
    //  - licenseExpired：多半真到期，但也可能是本地时钟超前，联网以服务器为准核一次。
    // 真到期/被拉黑时服务器会再次拒绝→硬锁；网络失败则维持原判，绝不误放行。
    final pastGraceRefused = st.status == LicenseStatus.refused &&
        DateTime.fromMillisecondsSinceEpoch(tk.tokenExp * 1000)
            .add(const Duration(seconds: _graceS))
            .isBefore(now);
    final renewTrigger = st.status == LicenseStatus.needsRenew ||
        st.status == LicenseStatus.licenseExpired ||
        pastGraceRefused ||
        (st.status == LicenseStatus.valid && _dueRenew(tk));
    if (renewTrigger) {
      // 悬浮窗等独立引擎拿不到设备指纹（deviceId 为空）：服务端按 device 定位授权，
      // 空号会被拒（no_device）导致误锁。续签交给主 App 做，这里只信本地验签结果。
      if (deviceId.isEmpty) return st;
      st = await _tryRenew(tk, st);
    }
    return st;
  }

  Future<LicenseState> _tryRenew(LicenseToken tk, LicenseState cur) async {
    final r = await _client.renew(deviceId);
    if (r.ok && r.token != null) {
      _noteServerTime(r.serverTime);
      _saveToken(r.token!);
      final re = LicenseToken.parseAndVerify(
          r.token!, LicenseConfig.licenseTokenSecretHex, deviceId);
      if (re != null) return _evaluate(re);
      return LicenseState(LicenseStatus.valid,
          expiresAt: DateTime.fromMillisecondsSinceEpoch(tk.expiresAt * 1000));
    }
    if (r.networkError) {
      return cur; // 宽限内软失败，保持可用
    }
    // 服务端明确拒绝（revoked / expired / not_found）
    if (r.error == 'license_expired') {
      return LicenseState(LicenseStatus.licenseExpired,
          expiresAt: DateTime.fromMillisecondsSinceEpoch(tk.expiresAt * 1000));
    }
    return LicenseState(LicenseStatus.refused, message: _friendly(r.error));
  }

  // ===== 用户主动激活 =====

  Future<LicenseState> activate(String code) async {
    await init();
    final input = code.trim();
    if (input.isEmpty) {
      return const LicenseState(LicenseStatus.notActivated, message: '请输入卡密');
    }
    final r = await _client.activate(deviceId, input);
    if (r.ok && r.token != null) {
      _noteServerTime(r.serverTime);
      _saveToken(r.token!);
      final re = LicenseToken.parseAndVerify(
          r.token!, LicenseConfig.licenseTokenSecretHex, deviceId);
      if (re != null) return _evaluate(re);
    }
    if (r.networkError) {
      return const LicenseState(LicenseStatus.notActivated,
          message: '网络连接失败，请稍后重试');
    }
    return LicenseState(LicenseStatus.notActivated, message: _friendly(r.error));
  }

  /// 强制下线时清理本地凭证（调试/换卡用）。
  void logout() {
    _clearToken();
  }

  /// 本地离线判定（不联网）：本地验签券 + 服务器时间锚点 + 72h 宽限。
  LicenseState _localEval() {
    final tk = _loadToken();
    if (tk == null) {
      final had = (_prefs?.getString(_kToken) ?? '').isNotEmpty;
      return LicenseState(had ? LicenseStatus.refused : LicenseStatus.notActivated,
          message: had ? '授权与本机不匹配或已损坏' : null);
    }
    return _evaluate(tk);
  }

  /// 轻量级心跳核验（主 App 与悬浮窗子引擎共用）。
  /// 服务器按 device_id 读一行授权→权威判 valid + 回服务器时间；拿到有效回包
  /// 才继续放行；明确无效则立即硬锁；仅网络失败时走离线宽限（绝不因断网误锁）。
  Future<LicenseState> heartbeat() async {
    await init();
    if (deviceId.isEmpty) {
      // 拿不到设备指纹：无法按设备心跳，退回本地判定（保 fail-open，绝不误锁正常用户）。
      return ensureUsable();
    }
    final r = await _client.heartbeat(deviceId);
    if (r.networkError) {
      // 心跳失败≠授权失效：按本地券 + 服务器锚点 + 72h 宽限离线判定。
      return _localEval();
    }
    _noteServerTime(r.serverTime); // 收到服务器时间就刷新锚点
    final expDt = r.expiresAt != null
        ? DateTime.fromMillisecondsSinceEpoch(r.expiresAt! * 1000)
        : null;
    if (!r.valid) {
      // revoked（服务端明确停用）：必须硬清本地券，杜绝被拉黑的券被复用。
      if (r.revoked) {
        _clearToken();
        return LicenseState(LicenseStatus.refused,
            expiresAt: expDt, message: '该授权已被停用，请联系卖家');
      }
      // not_found（查无此设备）可能是服务端瞬时不一致：新授权行尚未对读可见 /
      // 主备切换读空 / 项目刚从空闲恢复。绝不能据此销毁一张「签名有效、尚未到期」
      // 的本地凭证——那会把已激活的正常用户从首页误踢回激活页并强制重输卡密。
      // 真到期的封顶仍由签名内 expires_at 兜住（下面 _localEval 已按此判定）。
      // 注：此 fail-open 的暴露上界＝离线宽限本就信任的 token_exp+72h（非整卡周期、
      // 不可续用，因不触发 renew）；故拉黑必须用 revoked=true（走上面硬清分支），
      // 不得以「删授权行」作为拉黑手段，否则被删设备会拖到该上界才锁。
      if (r.error == 'not_found') {
        final local = _localEval();
        if (local.allowsUsage) return local; // 本地仍能证明有效：保持放行，不清券
      }
      // 其余明确负信号（到期）：清本地券，杜绝残留券被后续复用（含换设备后旧券）。
      _clearToken();
      return LicenseState(LicenseStatus.licenseExpired, expiresAt: expDt);
    }
    // 服务器判仍有效：走 ensureUsable（临近续签点时顺带换新券并刷新锚点）。
    return ensureUsable();
  }

  String _friendly(String? err) {
    switch (err) {
      case 'invalid_code':
        return '卡密无效或已被使用';
      case 'device_already_activated':
        return '本机已有生效授权，无需重复激活';
      case 'revoked':
        return '该授权已被停用，请联系卖家';
      case 'license_expired':
        return '授权已到期，请续费';
      case 'no_device':
        return '无法获取设备标识，请重试';
      default:
        return '激活失败：${err ?? "未知错误"}';
    }
  }
}
