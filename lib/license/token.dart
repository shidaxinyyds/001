import 'dart:convert';

import 'package:crypto/crypto.dart';

/// 服务端签发的短期凭证。串格式（与 Edge Function 严格一致）：
///   device|code|expiresAt|tokenExp|b64url(HMAC-SHA256)
/// 规范串用 U+00A6(¦) 分隔字段做签名，避免与卡密内的连字符冲突。
class LicenseToken {
  final String device;
  final String code;
  final int expiresAt; // 授权总到期（epoch 秒，服务端权威）
  final int tokenExp; // 本张短期凭证到期（epoch 秒）

  const LicenseToken({
    required this.device,
    required this.code,
    required this.expiresAt,
    required this.tokenExp,
  });

  static const String _u00a6 = '\u00a6';
  static const String _u2027 = '\u2027';

  static String canonical(String device, String code, int expiresAt, int tokenExp) =>
      '$device$_u00a6$code$_u00a6$expiresAt$_u00a6$tokenExp';

  static String hmacB64Url(String secretHex, String canon) {
    final key = _hexToBytes(secretHex);
    if (key == null) return '';
    final mac = Hmac(sha256, key).convert(utf8.encode(canon)).bytes;
    return base64Url.encode(mac).replaceAll('=', '');
  }

  static List<int>? _hexToBytes(String hex) {
    final h = hex.trim();
    if (h.length.isOdd || h.isEmpty) return null;
    try {
      final out = <int>[];
      for (var i = 0; i < h.length; i += 2) {
        out.add(int.parse(h.substring(i, i + 2), radix: 16));
      }
      return out;
    } catch (_) {
      return null;
    }
  }

  /// 从存储串解析。签名不匹配 / 格式非法 / 设备不符 → 返回 null（视为无效凭证）。
  static LicenseToken? parseAndVerify(
      String stored, String secretHex, String deviceId) {
    if (stored.isEmpty) return null;
    // 存储层把签名里的 '|' 转义成 '‡'，这里先还原最后一段。
    final parts = stored.split('|');
    if (parts.length < 5) return null;
    final sig = parts.sublist(4).join('|').replaceAll(_u2027, '|');
    final device = parts[0];
    final code = parts[1];
    final expiresAt = int.tryParse(parts[2]);
    final tokenExp = int.tryParse(parts[3]);
    if (expiresAt == null || tokenExp == null) return null;

    final canon = canonical(device, code, expiresAt, tokenExp);
    final expected = hmacB64Url(secretHex, canon);
    if (expected.isEmpty) return null;
    if (sig != expected) return null;
    if (deviceId.isNotEmpty && device != deviceId) return null;

    return LicenseToken(
      device: device,
      code: code,
      expiresAt: expiresAt,
      tokenExp: tokenExp,
    );
  }

  /// 生成可存储串（测试用；把签名内的 '|' 转义成 '‡' 以便单段存储）。
  String toStored(String secretHex) {
    final canon = canonical(device, code, expiresAt, tokenExp);
    final sig = hmacB64Url(secretHex, canon).replaceAll('|', _u2027);
    return '$device|$code|$expiresAt|$tokenExp|$sig';
  }
}
