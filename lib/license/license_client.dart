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

  const LicenseRpcResult._({
    required this.ok,
    this.networkError = false,
    this.token,
    this.error,
    this.expiresAt,
    this.tokenExp,
    this.cardType,
  });

  factory LicenseRpcResult.success(
    String token,
    int expiresAt,
    int tokenExp,
    String? cardType,
  ) =>
      LicenseRpcResult._(
          ok: true, token: token, expiresAt: expiresAt, tokenExp: tokenExp, cardType: cardType);

  factory LicenseRpcResult.refused(String error) =>
      LicenseRpcResult._(ok: false, error: error);

  factory LicenseRpcResult.network() =>
      LicenseRpcResult._(ok: false, networkError: true, error: 'network');
}

/// 极简 HTTP 客户端：不引入 supabase SDK，直接 POST Edge Function。
class LicenseClient {
  final http.Client _client;
  LicenseClient({http.Client? client}) : _client = client ?? http.Client();

  Future<LicenseRpcResult> activate(String deviceId, String code) =>
      _post({'action': 'activate', 'device_id': deviceId, 'code': code.trim()});

  Future<LicenseRpcResult> renew(String deviceId) =>
      _post({'action': 'renew', 'device_id': deviceId});

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
        );
      }
      return LicenseRpcResult.refused((json['error'] ?? 'refused').toString());
    } catch (_) {
      return LicenseRpcResult.network();
    }
  }

  void close() => _client.close();
}
