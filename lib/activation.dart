import 'dart:async';
import 'dart:io';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 激活状态的本地存储键。
const String _kActivated = 'av_activated';
const String _kActivatedAt = 'av_activated_at'; // ISO8601
const String _kActivatedSystem = 'av_activated_system';

/// 是否已激活（首次激活成功后永久记住，之后进入软件不再要求卡密）。
Future<bool> isActivated() async {
  try {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_kActivated) ?? false;
  } catch (_) {
    return false;
  }
}

/// 仅在【首次】激活成功时调用：写入激活标记、激活时间、激活时识别到的系统信息。
/// 之后再次进入软件不会调用本函数，从而保证"激活时间"与"激活系统"固定为那一刻。
Future<void> activate({required String systemInfo}) async {
  final prefs = await SharedPreferences.getInstance();
  final now = DateTime.now();
  await prefs.setBool(_kActivated, true);
  await prefs.setString(_kActivatedAt, now.toIso8601String());
  await prefs.setString(_kActivatedSystem, systemInfo);
}

/// 已持久化的激活信息（主页"激活成功"卡片读取用）。
class ActivationInfo {
  final DateTime activatedAt;
  final String systemInfo;
  const ActivationInfo(this.activatedAt, this.systemInfo);
}

Future<ActivationInfo?> getActivation() async {
  try {
    final prefs = await SharedPreferences.getInstance();
    final at = prefs.getString(_kActivatedAt);
    final sys = prefs.getString(_kActivatedSystem);
    if (at == null || sys == null) return null;
    final dt = DateTime.tryParse(at);
    if (dt == null) return null;
    return ActivationInfo(dt, sys);
  } catch (_) {
    return null;
  }
}

/// 卡密页顶部展示的真实设备状态（系统与网络均真实探测）。
class DeviceStatus {
  final String system; // 真实系统信息，如 "Android 13 · Google Pixel 6"
  final String network; // 真实网络状态文案，如 "网络通畅" / "网络异常"
  final bool networkOk; // 是否真实可达
  const DeviceStatus(this.system, this.network, this.networkOk);
}

/// 真实识别设备系统与网络连通性。
/// 全程 try/catch：任何探测异常都降级为"未知"，绝不让卡密页崩溃。
Future<DeviceStatus> detectDeviceStatus() async {
  String system = '未知设备';
  try {
    final deviceInfo = DeviceInfoPlugin();
    if (Platform.isAndroid) {
      final a = await deviceInfo.androidInfo;
      final brand = (a.brand).trim().isEmpty ? '' : '${a.brand.trim()} ';
      final model = (a.model).trim();
      system = 'Android ${a.version.release} · $brand$model'
          .replaceAll(RegExp(r'\s+'), ' ')
          .trim();
    } else if (Platform.isIOS) {
      final i = await deviceInfo.iosInfo;
      system = 'iOS ${i.systemVersion} · ${i.model}';
    }
  } catch (_) {
    system = '未知设备';
  }

  String network = '网络检测中…';
  bool networkOk = false;
  try {
    final result = await Connectivity().checkConnectivity();
    if (result == ConnectivityResult.none) {
      network = '未连接网络';
      networkOk = false;
    } else {
      networkOk = await _probeInternet();
      network = networkOk ? '网络通畅' : '网络异常';
    }
  } catch (_) {
    network = '网络状态未知';
    networkOk = false;
  }

  return DeviceStatus(system, network, networkOk);
}

/// 真实可达性探测：向高可用主机发起 HTTPS 请求，超时即视为不可达。
/// 先试国内可靠的 baidu，再试 gstatic 兜底，任一成功即判"通畅"。
Future<bool> _probeInternet() async {
  const hosts = [
    'https://www.baidu.com',
    'https://www.gstatic.com',
  ];
  for (final url in hosts) {
    HttpClient? client;
    try {
      client = HttpClient();
      client.connectionTimeout = const Duration(seconds: 4);
      final req = await client.getUrl(Uri.parse(url));
      final resp = await req.close();
      final ok = resp.statusCode >= 200 && resp.statusCode < 400;
      await resp.drain<void>().catchError((_) {});
      client.close(force: true);
      if (ok) return true;
    } catch (_) {
      // 该主机不可达，尝试下一个
    } finally {
      client?.close(force: true);
    }
  }
  return false;
}
