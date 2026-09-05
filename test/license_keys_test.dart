import 'package:flutter_test/flutter_test.dart';
import 'package:auto_vision/license_keys.dart';

void main() {
  test('内置 100 个卡密全部校验通过且唯一', () {
    expect(kBuiltInLicenseKeys.length, 100);
    expect(Set<String>.from(kBuiltInLicenseKeys).length, 100);
    for (final k in kBuiltInLicenseKeys) {
      expect(k.length, 12, reason: '卡密长度须为 12');
      expect(checkLicenseKey(k), LicenseCheck.ok, reason: '合法卡密应放行: $k');
    }
  });

  test('区分大小写：改动一位大小写即失败', () {
    final k = kBuiltInLicenseKeys.first;
    final flipped = k[0] == k[0].toUpperCase()
        ? k.replaceFirst(k[0], k[0].toLowerCase())
        : k.replaceFirst(k[0], k[0].toUpperCase());
    expect(flipped, isNot(k));
    expect(checkLicenseKey(flipped), isNot(LicenseCheck.ok));
  });

  test('清单外的卡密 -> notFound', () {
    expect(checkLicenseKey('AbCdEf123456'), LicenseCheck.notFound);
  });

  test('带空格 / 连字符的复制粘贴可归一化通过', () {
    final k = kBuiltInLicenseKeys.first;
    expect(checkLicenseKey(' $k '), LicenseCheck.ok);
    expect(checkLicenseKey('${k.substring(0, 6)}-${k.substring(6)}'),
        LicenseCheck.ok);
  });

  test('空 / 长度 / 格式错误分类正确', () {
    expect(checkLicenseKey(''), LicenseCheck.empty);
    expect(checkLicenseKey('short'), LicenseCheck.length);
    // 前 6 位应为字母，这里填数字 -> 格式错误
    expect(checkLicenseKey('123456abcdef'), LicenseCheck.format);
    // 后 6 位应为数字，这里填字母 -> 格式错误
    expect(checkLicenseKey('abcdefghijkl'), LicenseCheck.format);
  });
}
