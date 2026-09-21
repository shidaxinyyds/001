import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:auto_vision/license/license_client.dart';
import 'package:auto_vision/license/license_service.dart';
import 'package:auto_vision/license/license_status.dart';
import 'package:auto_vision/license/token.dart';

// 可控假客户端：心跳/续签/激活全部走桩，绝不真联网。
class _FakeClient extends LicenseClient {
  LicenseHeartbeatResult? hb;
  @override
  Future<LicenseHeartbeatResult> heartbeat(String deviceId) async =>
      hb ?? LicenseHeartbeatResult.network();
  @override
  Future<LicenseRpcResult> renew(String deviceId) async =>
      LicenseRpcResult.network();
  @override
  Future<LicenseRpcResult> activate(String deviceId, String code) async =>
      LicenseRpcResult.network();
}

// 固定一个远小于真机当前时间的 epoch 秒做锚点（1 天前基准），
// 用 noteServerTimeForTest 让"当前服务器时间"确定化，规避本地真实时钟影响。
const int _t0 = 1700000000;

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('服务器签名时间为准（问题 #6 核心）', () {
    test('有锚点且未到 expiresAt → valid', () async {
      SharedPreferences.setMockInitialValues({});
      final prefs = await SharedPreferences.getInstance();
      final svc = LicenseService.newForTest(client: _FakeClient())
        ..primeForTest(deviceId: 'dev', prefs: prefs)
        ..noteServerTimeForTest(_t0);
      final tk = LicenseToken(
          device: 'dev', code: 'MJ-X', expiresAt: _t0 + 86400, tokenExp: _t0 + 86400);
      expect(svc.evaluateForTest(tk).status, LicenseStatus.valid);
    });

    test('锚点已过 expiresAt → licenseExpired（本地时钟无法救回）', () async {
      SharedPreferences.setMockInitialValues({});
      final prefs = await SharedPreferences.getInstance();
      final svc = LicenseService.newForTest(client: _FakeClient())
        ..primeForTest(deviceId: 'dev', prefs: prefs)
        ..noteServerTimeForTest(_t0);
      final tk = LicenseToken(
          device: 'dev', code: 'MJ-X', expiresAt: _t0 - 10, tokenExp: _t0 + 86400);
      expect(svc.evaluateForTest(tk).status, LicenseStatus.licenseExpired);
    });

    test('续签券过期但在 72h 宽限内 → needsRenew（软，仍可用）', () async {
      SharedPreferences.setMockInitialValues({});
      final prefs = await SharedPreferences.getInstance();
      final svc = LicenseService.newForTest(client: _FakeClient())
        ..primeForTest(deviceId: 'dev', prefs: prefs)
        ..noteServerTimeForTest(_t0);
      final tk = LicenseToken(
          device: 'dev', code: 'MJ-X', expiresAt: _t0 + 86400, tokenExp: _t0 - 3600);
      final st = svc.evaluateForTest(tk);
      expect(st.status, LicenseStatus.needsRenew);
      expect(st.allowsUsage, isTrue);
    });

    test('续签券过期且超 72h 宽限 → refused', () async {
      SharedPreferences.setMockInitialValues({});
      final prefs = await SharedPreferences.getInstance();
      final svc = LicenseService.newForTest(client: _FakeClient())
        ..primeForTest(deviceId: 'dev', prefs: prefs)
        ..noteServerTimeForTest(_t0);
      final tk = LicenseToken(
          device: 'dev',
          code: 'MJ-X',
          expiresAt: _t0 + 86400,
          tokenExp: _t0 - 73 * 3600);
      final st = svc.evaluateForTest(tk);
      expect(st.status, LicenseStatus.refused);
      expect(st.allowsUsage, isFalse);
    });
  });

  group('心跳强制拦截', () {
    test('服务器 valid:false 查无/到期 → licenseExpired 且清本地券', () async {
      SharedPreferences.setMockInitialValues({'lic_token': 'some-stored-token'});
      final prefs = await SharedPreferences.getInstance();
      final client = _FakeClient()
        ..hb = LicenseHeartbeatResult.status(
            false, false, null, _t0, 'not_found');
      final svc = LicenseService.newForTest(client: client)
        ..primeForTest(deviceId: 'dev', prefs: prefs);
      final st = await svc.heartbeat();
      expect(st.status, LicenseStatus.licenseExpired);
      expect(st.allowsUsage, isFalse);
      // 到期必须清券，杜绝残留券被后续复用
      expect(prefs.getString('lic_token'), isNull);
    });

    test('服务器 revoked → refused 且清本地券', () async {
      SharedPreferences.setMockInitialValues({'lic_token': 'some-stored-token'});
      final prefs = await SharedPreferences.getInstance();
      final client = _FakeClient()
        ..hb = LicenseHeartbeatResult.status(
            false, true, _t0 + 86400, _t0, null);
      final svc = LicenseService.newForTest(client: client)
        ..primeForTest(deviceId: 'dev', prefs: prefs);
      final st = await svc.heartbeat();
      expect(st.status, LicenseStatus.refused);
      expect(prefs.getString('lic_token'), isNull);
    });

    test('心跳网络失败且无本地券 → notActivated（子引擎 fail-open，不误锁）', () async {
      SharedPreferences.setMockInitialValues({});
      final prefs = await SharedPreferences.getInstance();
      final client = _FakeClient(); // hb=null → 默认 network()
      final svc = LicenseService.newForTest(client: client)
        ..primeForTest(deviceId: 'dev', prefs: prefs);
      final st = await svc.heartbeat();
      expect(st.status, LicenseStatus.notActivated);
      // 悬浮窗据此不误锁：denied 仅 licenseExpired/refused
      final denied =
          st.status == LicenseStatus.licenseExpired || st.status == LicenseStatus.refused;
      expect(denied, isFalse);
    });

    test('拿不到 deviceId → 退回 ensureUsable 本地判定（绝不误锁）', () async {
      SharedPreferences.setMockInitialValues({});
      final prefs = await SharedPreferences.getInstance();
      final client = _FakeClient()
        ..hb = LicenseHeartbeatResult.status(false, false, null, _t0, 'not_found');
      final svc = LicenseService.newForTest(client: client)
        ..primeForTest(deviceId: '', prefs: prefs); // 空设备号
      final st = await svc.heartbeat();
      // deviceId 空时不发起按设备心跳，退回本地：无券 → notActivated（非误判到期）
      expect(st.status, LicenseStatus.notActivated);
    });
  });

  group('状态语义（悬浮窗/主页映射）', () {
    test('allowsUsage 仅 valid/needsRenew 放行', () {
      expect(const LicenseState(LicenseStatus.valid).allowsUsage, isTrue);
      expect(const LicenseState(LicenseStatus.needsRenew).allowsUsage, isTrue);
      expect(const LicenseState(LicenseStatus.licenseExpired).allowsUsage, isFalse);
      expect(const LicenseState(LicenseStatus.refused).allowsUsage, isFalse);
      expect(const LicenseState(LicenseStatus.notActivated).allowsUsage, isFalse);
    });

    test('isStrictlyTerminated 仅在真正到期或明确拉黑时成立', () {
      // 普通 refused（如瞬态验签格式错误/设备不匹配，未被服务端拉黑）：不属于严格终止
      expect(
          const LicenseState(LicenseStatus.refused, isRevoked: false)
              .isStrictlyTerminated,
          isFalse);

      // 服务端明确拉黑：严格终止
      expect(
          const LicenseState(LicenseStatus.refused, isRevoked: true)
              .isStrictlyTerminated,
          isTrue);

      // 到期时间在未来：不属于严格终止
      expect(
          LicenseState(LicenseStatus.licenseExpired,
                  expiresAt: DateTime.now().add(const Duration(days: 1)))
              .isStrictlyTerminated,
          isFalse);

      // 到期时间已过：属于严格终止
      expect(
          LicenseState(LicenseStatus.licenseExpired,
                  expiresAt: DateTime.now().subtract(const Duration(seconds: 1)))
              .isStrictlyTerminated,
          isTrue);
    });
  });

  group('防误踢与凭证保护（核心修复验证）', () {
    test('未到期有效凭证求值 → 始终保持放行', () {
      final svc = LicenseService.newForTest(client: _FakeClient())
        ..noteServerTimeForTest(_t0);
      final tk = LicenseToken(
        device: 'dev',
        code: 'TEST-CODE',
        expiresAt: _t0 + 7 * 86400,
        tokenExp: _t0 + 86400,
      );
      final st = svc.evaluateForTest(tk);
      expect(st.allowsUsage, isTrue);
      expect(st.status, LicenseStatus.valid);
      expect(st.isStrictlyTerminated, isFalse);
    });

    test('软状态（needsRenew，宽限期内）→ 仍保持放行且不属于严格终止', () {
      final svc = LicenseService.newForTest(client: _FakeClient())
        ..noteServerTimeForTest(_t0);
      final tk = LicenseToken(
        device: 'dev',
        code: 'TEST-CODE',
        expiresAt: _t0 + 7 * 86400,
        tokenExp: _t0 - 3600, // 凭证过期但在宽限内
      );
      final st = svc.evaluateForTest(tk);
      expect(st.allowsUsage, isTrue);
      expect(st.status, LicenseStatus.needsRenew);
      expect(st.isStrictlyTerminated, isFalse);
    });

    test('确凿拉黑（isRevoked=true）→ 属于严格终止', () {
      const st = LicenseState(
        LicenseStatus.refused,
        isRevoked: true,
        message: '该授权已被停用，请联系卖家',
      );
      expect(st.allowsUsage, isFalse);
      expect(st.isStrictlyTerminated, isTrue);
    });

    test('普通暂态 refused（指纹未就绪/网络暂态）→ 不得判定为严格终止', () {
      const st = LicenseState(
        LicenseStatus.refused,
        isRevoked: false,
        message: '授权与本机不匹配或已损坏',
      );
      expect(st.allowsUsage, isFalse);
      expect(st.isStrictlyTerminated, isFalse); // 绝不能误当成真正注销
    });
  });
}
