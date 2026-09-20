import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:auto_vision/license/token.dart';

// 与 lib/license/config.dart 无耦合：测试自带一份密钥，验证 HMAC 语义正确。
const String kSecretHex =
    '000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f';
const String kDevice = 'device-abc-123';

String _hmacB64Url(String canon) {
  final key = <int>[];
  for (var i = 0; i < kSecretHex.length; i += 2) {
    key.add(int.parse(kSecretHex.substring(i, i + 2), radix: 16));
  }
  final mac = Hmac(sha256, key).convert(utf8.encode(canon)).bytes;
  return base64Url.encode(mac).replaceAll('=', '');
}

String _makeToken(int expiresAt, int tokenExp, {String? sig}) {
  final canon = LicenseToken.canonical(kDevice, 'MJ-AAAA-BBBB', expiresAt, tokenExp);
  final s = (sig ?? _hmacB64Url(canon)).replaceAll('|', '\u2027');
  return '$kDevice|MJ-AAAA-BBBB|$expiresAt|$tokenExp|$s';
}

void main() {
  final future = DateTime.now().add(const Duration(days: 30)).millisecondsSinceEpoch ~/ 1000;
  final tokExp = DateTime.now().add(const Duration(days: 3)).millisecondsSinceEpoch ~/ 1000;

  test('合法凭证验签通过并解析出正确字段', () {
    final stored = _makeToken(future, tokExp);
    final tk = LicenseToken.parseAndVerify(stored, kSecretHex, kDevice);
    expect(tk, isNotNull);
    expect(tk!.expiresAt, future);
    expect(tk.tokenExp, tokExp);
    expect(tk.code, 'MJ-AAAA-BBBB');
  });

  test('篡改到期时间后验签失败', () {
    // 用旧 tokenExp 的签名，却把字段改大 → 签名不匹配。
    final stored = _makeToken(future, tokExp);
    final tampered = stored.replaceAll('|$tokExp|', '|${tokExp + 999999}|');
    expect(LicenseToken.parseAndVerify(tampered, kSecretHex, kDevice), isNull);
  });

  test('换设备（device 不匹配）拒绝', () {
    final stored = _makeToken(future, tokExp);
    expect(LicenseToken.parseAndVerify(stored, kSecretHex, 'other-device'), isNull);
  });

  test('错误密钥无法伪造合法凭证', () {
    final stored = _makeToken(future, tokExp);
    expect(
      LicenseToken.parseAndVerify(stored, 'ff' * 32, kDevice),
      isNull,
    );
  });

  test('格式残缺串安全返回 null', () {
    expect(LicenseToken.parseAndVerify('a|b|c', kSecretHex, kDevice), isNull);
    expect(LicenseToken.parseAndVerify('', kSecretHex, kDevice), isNull);
  });

  test('toStored 与 parseAndVerify 往返一致', () {
    final t = LicenseToken(
        device: kDevice, code: 'MJ-1111', expiresAt: future, tokenExp: tokExp);
    final stored = t.toStored(kSecretHex);
    final back = LicenseToken.parseAndVerify(stored, kSecretHex, kDevice);
    expect(back, isNotNull);
    expect(back!.expiresAt, future);
    expect(back.tokenExp, tokExp);
    expect(back.code, 'MJ-1111');
  });
}
