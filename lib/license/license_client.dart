import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'config.dart';

/// 一次网络请求的结果：ok / 网络失败(可重试) / 服务端明确拒绝(不可重试)。
class LicenseRpcResult {
  final bool ok;
  final bool networkError; // true=网络/超时，不代表授权无效，应重试而非拒绝
  final String? token;
  final String? error;
  final int? expiresAt;
  final int? tokenExp;
  final String? cardType;
  final int? serverTime; // 服务器签名时间（epoch 秒），供客户端时间锚点

  const LicenseRpcResult._({
    required this.ok,
    this.networkError = false,
    this.token,
    this.error,
    this.expiresAt,
    this.tokenExp,
    this.cardType,
    this.serverTime,
  });

  factory LicenseRpcResult.success(
    String token,
    int expiresAt,
    int tokenExp,
    String? cardType, {
    int? serverTime,
  }) =>
      LicenseRpcResult._(
          ok: true,
          token: token,
          expiresAt: expiresAt,
          tokenExp: tokenExp,
          cardType: cardType,
          serverTime: serverTime);

  factory LicenseRpcResult.refused(String error) =>
      LicenseRpcResult._(ok: false, error: error);

  factory LicenseRpcResult.network() =>
      LicenseRpcResult._(ok: false, networkError: true, error: 'network');
}

/// 一次设备心跳的结果：服务器权威判定该设备当前是否仍有效。
/// 注意 ok:true + valid:false 是"服务器明确未授权"（到期/拉黑/查无），
/// 属确定性拒绝，不是可重试的网络错误。
class LicenseHeartbeatResult {
  final bool ok; // 请求成功送达并解析（不含授权是否有效）
  final bool networkError; // true=网络/超时，不代表授权无效
  final bool valid; // 服务器权威：当前是否仍可用
  final bool revoked;
  final int? expiresAt; // 服务器侧总到期（epoch 秒），可能为 null（查无此设备）
  final int? serverTime; // 服务器签名时间（epoch 秒）
  final String? error;

  const LicenseHeartbeatResult._({
    required this.ok,
    this.networkError = false,
    this.valid = false,
    this.revoked = false,
    this.expiresAt,
    this.serverTime,
    this.error,
  });

  factory LicenseHeartbeatResult.status(
    bool valid,
    bool revoked,
    int? expiresAt,
    int? serverTime,
    String? error,
  ) =>
      LicenseHeartbeatResult._(
          ok: true,
          valid: valid,
          revoked: revoked,
          expiresAt: expiresAt,
          serverTime: serverTime,
          error: error);

  factory LicenseHeartbeatResult.network() =>
      LicenseHeartbeatResult._(ok: false, networkError: true, error: 'network');
}

/// 极简 HTTP 客户端：不引入 supabase SDK，直接 POST Edge Function。
class LicenseClient {
  final http.Client _client;
  LicenseClient({http.Client? client}) : _client = client ?? http.Client();

  Future<LicenseRpcResult> activate(String deviceId, String code) =>
      _post({'action': 'activate', 'device_id': deviceId, 'code': code.trim()});

  Future<LicenseRpcResult> renew(String deviceId) =>
      _post({'action': 'renew', 'device_id': deviceId});

  /// 设备心跳：服务器读一行授权，回 valid + 服务器时间。轻量、只读、失败不锁。
  Future<LicenseHeartbeatResult> heartbeat(String deviceId) async {
    try {
      final res = await _client
          .post(
            Uri.parse(LicenseConfig.licenseFunctionUrl),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({'action': 'heartbeat', 'device_id': deviceId}),
          )
          .timeout(const Duration(seconds: 15));
      if (res.statusCode != 200) {
        return LicenseHeartbeatResult.network();
      }
      final json = jsonDecode(res.body) as Map<String, dynamic>;
      if (json['ok'] != true) {
        return LicenseHeartbeatResult.network();
      }
      return LicenseHeartbeatResult.status(
        json['valid'] == true,
        json['revoked'] == true,
        (json['expires_at'] as num?)?.toInt(),
        (json['server_time'] as num?)?.toInt(),
        json['error'] as String?,
      );
    } catch (_) {
      return LicenseHeartbeatResult.network();
    }
  }

  Future<LicenseRpcResult> _post(Map<String, dynamic> body) async {
    try {
      final res = await _client
          .post(
            Uri.parse(LicenseConfig.licenseFunctionUrl),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode(body),
          )
          .timeout(const Duration(seconds: 15));
      if (res.statusCode != 200) {
        // 非 200（网关/超时/暂停）当网络错误处理，不误判为授权无效。
        return LicenseRpcResult.network();
      }
      final json = jsonDecode(res.body) as Map<String, dynamic>;
      if (json['ok'] == true && json['token'] is String) {
        return LicenseRpcResult.success(
          json['token'] as String,
          (json['expires_at'] as num).toInt(),
          (json['token_exp'] as num).toInt(),
          json['card_type'] as String?,
          serverTime: (json['server_time'] as num?)?.toInt(),
        );
      }
      return LicenseRpcResult.refused((json['error'] ?? 'refused').toString());
    } catch (_) {
      return LicenseRpcResult.network();
    }
  }

  void close() => _client.close();
}
