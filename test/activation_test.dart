import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:auto_vision/activation.dart';

void main() {
  setUp(() {
    // 必须在 SharedPreferences.getInstance() 之前调用。
    SharedPreferences.setMockInitialValues({});
  });

  test('初始状态：未激活且无激活信息', () async {
    expect(await isActivated(), isFalse);
    expect(await getActivation(), isNull);
  });

  test('激活后 isActivated 为 true，且时间与系统信息被正确写入', () async {
    await activate(systemInfo: 'Android 13 · Google Pixel 6');
    expect(await isActivated(), isTrue);
    final info = await getActivation();
    expect(info, isNotNull);
    expect(info!.systemInfo, 'Android 13 · Google Pixel 6');
    expect(info.activatedAt, isA<DateTime>());
  });

  test('激活信息一旦写入即稳定（模拟再次进入不再调用 activate）', () async {
    await activate(systemInfo: 'Android 13 · Google Pixel 6');
    final first = await getActivation();
    // 第二次进入软件不应调用 activate，存储必须保持不变。
    final again = await getActivation();
    expect(again!.activatedAt, first!.activatedAt);
    expect(again.systemInfo, first.systemInfo);
    // 即便再次读取 isActivated 仍为 true。
    expect(await isActivated(), isTrue);
  });

  test('getActivation 对损坏/缺失数据健壮返回 null', () async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('av_activated_at', 'not-a-date');
    expect(await getActivation(), isNull);
  });
}
