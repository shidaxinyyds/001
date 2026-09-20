import 'dart:async';

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
/// 放行判定：
///   - 凭证有效且未过期 → valid（离线可用）
///   - 凭证到续签点 → 联网 renew；网络失败但仍在宽限内 → needsRenew（软，仍可用）
///   - 服务端明确 expired / revoked / 换设备 / 改时间 → 硬拒绝
class LicenseService {
  LicenseService._();
  static final LicenseService instance = LicenseService._();

  static const String _kToken = 'lic_token';
  static const String _kMaxWallMs = 'lic_max_wall_ms';
  // 改时间回拨容忍窗口：比这个大得多的回拨视为篡改。
  static const int _rollbackTolS = 86400; // 1 天
  // 凭证到期后的网络宽限：宽限内 renew 失败仍可离线用。
  static const int _graceS = 86400; // 1 天

  final MethodChannel _ch = const MethodChannel(CHANNEL_NAME);
  final LicenseClient _client = LicenseClient();

  SharedPreferences? _prefs;
  String? _deviceId;
  bool _ready = false;

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

  LicenseState _evaluate(LicenseToken tk, DateTime now) {
    final expiresAt =
        DateTime.fromMillisecondsSinceEpoch(tk.expiresAt * 1000);
    final tokenExp = DateTime.fromMillisecondsSinceEpoch(tk.tokenExp * 1000);
    final nowMs = now.millisecondsSinceEpoch;

    if (_clockTampered(nowMs)) {
      return LicenseState(LicenseStatus.refused,
          expiresAt: expiresAt, message: '检测到系统时间被回拨，请联网校准');
    }
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
  bool _dueRenew(LicenseToken tk, DateTime now) {
    final tokenExp = DateTime.fromMillisecondsSinceEpoch(tk.tokenExp * 1000);
    return !now.isBefore(
        tokenExp.subtract(const Duration(seconds: LicenseConfig.renewLeadS)));
  }

  // ===== 对外：拿状态（必要时静默续签）=====

  Future<LicenseState> ensureUsable() async {
    await init();
    final now = DateTime.now();
    final tk = _loadToken();
    if (tk == null) {
      final p = _prefs;
      final had = (p?.getString(_kToken) ?? '').isNotEmpty;
      // 存了但验签失败：换设备 / 篡改 / 密钥不符。
      return LicenseState(had ? LicenseStatus.refused : LicenseStatus.notActivated,
          message: had ? '授权与本机不匹配或已损坏' : null);
    }
    var st = _evaluate(tk, now);
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
        (st.status == LicenseStatus.valid && _dueRenew(tk, now));
    if (renewTrigger) {
      // 悬浮窗等独立引擎拿不到设备指纹（deviceId 为空）：服务端按 device 定位授权，
      // 空号会被拒（no_device）导致误锁。续签交给主 App 做，这里只信本地验签结果。
      if (deviceId.isEmpty) return st;
      st = await _tryRenew(tk, st, now);
    }
    return st;
  }

  Future<LicenseState> _tryRenew(
      LicenseToken tk, LicenseState cur, DateTime now) async {
    final r = await _client.renew(deviceId);
    if (r.ok && r.token != null) {
      _saveToken(r.token!);
      final re = LicenseToken.parseAndVerify(
          r.token!, LicenseConfig.licenseTokenSecretHex, deviceId);
      if (re != null) return _evaluate(re, DateTime.now());
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
      _saveToken(r.token!);
      final re = LicenseToken.parseAndVerify(
          r.token!, LicenseConfig.licenseTokenSecretHex, deviceId);
      if (re != null) return _evaluate(re, DateTime.now());
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
