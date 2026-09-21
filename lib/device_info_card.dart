import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';

import 'package:auto_vision/channel.dart';
import 'package:auto_vision/theme/app_tokens.dart';

/// 主页底部「设备信息卡片」。
///
/// 设计约束：
/// - **真实可靠**：品牌/型号/系统版本/网络类型/运营商全部来自 Android 原生
///   （Build + ConnectivityManager + TelephonyManager，经 [CHANNEL_NAME] 通道），
///   分辨率/DPI 来自 Flutter `platformDispatcher`，悬浮窗权限来自
///   `flutter_overlay_window`。不引入任何三方包，避免 CI(Dart 3.1) 版本解析风险。
/// - **刷新频率合理**：仅在首帧、App 回前台、用户点刷新时读取，**绝不定时轮询**
///   系统 API；原生侧逐项 try/catch 兜底，任一字段拿不到只回退“未知”，不抛异常。
class DeviceInfoCard extends StatefulWidget {
  const DeviceInfoCard({Key? key}) : super(key: key);

  @override
  State<DeviceInfoCard> createState() => _DeviceInfoCardState();
}

class _DeviceInfoCardState extends State<DeviceInfoCard>
    with WidgetsBindingObserver {
  static const MethodChannel _channel = MethodChannel(CHANNEL_NAME);

  String _os = '检测中…';
  String _brandModel = '检测中…';
  String _network = '检测中…';
  String _screen = '检测中…';
  String _dpi = '—';
  bool? _overlayGranted; // null = 未知/检测中
  bool _loading = false;
  DateTime? _updatedAt;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refresh();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // 仅在前台恢复时刷新：网络/权限可能在系统设置里被改动；不做任何定时轮询。
    if (state == AppLifecycleState.resumed) _refresh();
  }

  Future<void> _refresh() async {
    if (_loading) return;
    _loading = true;
    if (mounted) setState(() {});

    // 1) 屏幕分辨率 + DPI：纯 Flutter 侧即时可得，无需系统通道，零成本。
    try {
      final vp = WidgetsBinding.instance.platformDispatcher.views.first;
      final Size size = vp.physicalSize;
      final double dpr = vp.devicePixelRatio;
      if (size.width > 0 && size.height > 0) {
        _screen = '${size.width.round()} × ${size.height.round()} px';
        _dpi = '${dpr.toStringAsFixed(1)}× · ${(dpr * 160).round()} dpi';
      } else {
        _screen = '未知';
        _dpi = '未知';
      }
    } catch (_) {
      _screen = '未知';
      _dpi = '未知';
    }

    // 2) 设备/网络：走原生 MethodChannel，逐项安全兜底。
    try {
      final info = await _channel
          .invokeMethod<Map<dynamic, dynamic>>('getDeviceInfo');
      if (info != null) {
        final String brand = (info['brand'] ?? '').toString();
        final String model = (info['model'] ?? '').toString();
        final String osRel = (info['os_release'] ?? '').toString();
        final Object? sdk = info['sdk_int'];
        final String netType = (info['network_type'] ?? 'unknown').toString();
        final String carrier = (info['carrier'] ?? '').toString();

        _os = osRel.isEmpty
            ? '未知'
            : 'Android $osRel${sdk != null ? ' (API $sdk)' : ''}';
        _brandModel = _composeBrandModel(brand, model);
        _network = _composeNetwork(netType, carrier);
      } else {
        _os = '未知';
        _brandModel = '未知';
        _network = '未知';
      }
    } catch (_) {
      _os = '未知';
      _brandModel = '未知';
      _network = '未知';
    }

    // 3) 悬浮窗权限状态。
    try {
      final bool? g = await FlutterOverlayWindow.isPermissionGranted();
      _overlayGranted = g == true;
    } catch (_) {
      _overlayGranted = null;
    }

    _updatedAt = DateTime.now();
    _loading = false;
    if (mounted) setState(() {});
  }

  static String _composeBrandModel(String brand, String model) {
    final String b = _capitalize(brand);
    final String m = model.trim();
    if (b.isEmpty && m.isEmpty) return '未知';
    if (m.isEmpty) return b;
    if (b.isEmpty) return m;
    // 型号已含品牌前缀时不重复拼接（如 brand=Xiaomi model="Xiaomi 14"）。
    if (m.toLowerCase().startsWith(b.toLowerCase())) return m;
    return '$b $m';
  }

  static String _composeNetwork(String netType, String carrier) {
    final String label;
    switch (netType) {
      case 'wifi':
        label = 'Wi-Fi';
        break;
      case 'cellular':
        label = '移动数据';
        break;
      case 'offline':
        label = '离线';
        break;
      case 'other':
        label = '其他网络';
        break;
      default:
        label = '未知';
    }
    final String c = carrier.trim();
    return c.isEmpty ? label : '$label · $c';
  }

  static String _capitalize(String s) {
    final String t = s.trim();
    if (t.isEmpty) return t;
    return t[0].toUpperCase() + t.substring(1);
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: AppTokens.s12),
      padding: const EdgeInsets.fromLTRB(
          AppTokens.s16, AppTokens.s12, AppTokens.s12, AppTokens.s12),
      decoration: BoxDecoration(
        color: AppTokens.surface,
        borderRadius: AppTokens.radius16,
        border: Border.all(color: _kBorder, width: 1.0),
        boxShadow: AppTokens.soft,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Icon(Icons.memory_rounded, size: 16, color: _kAccent),
              const SizedBox(width: 6),
              const Text(
                '设备信息',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: _kTextMain,
                  letterSpacing: 0.2,
                ),
              ),
              const Spacer(),
              if (_updatedAt != null)
                Text(
                  '更新于 ${_hhmm(_updatedAt!)}',
                  style: TextStyle(
                    fontSize: 10.5,
                    color: AppTokens.faint.withAlpha(204), // alpha 0.8（3.13 兼容）
                  ),
                ),
              IconButton(
                onPressed: _loading ? null : _refresh,
                visualDensity: VisualDensity.compact,
                tooltip: '刷新',
                iconSize: 18,
                icon: _loading
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: _kAccent,
                        ),
                      )
                    : const Icon(Icons.refresh_rounded, color: _kAccent),
              ),
            ],
          ),
          const SizedBox(height: 2),
          _row(Icons.phone_android_rounded, '系统', _os),
          _row(Icons.devices_other_rounded, '机型', _brandModel),
          _row(Icons.wifi_rounded, '网络', _network),
          _row(Icons.monitor_rounded, '屏幕', '$_screen · $_dpi'),
          _overlayRow(),
        ],
      ),
    );
  }

  Widget _row(IconData icon, String label, String value,
      {Color? valueColor}) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4.5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 15, color: AppTokens.muted),
          const SizedBox(width: 8),
          SizedBox(
            width: 40,
            child: Text(
              label,
              style: const TextStyle(fontSize: 12, color: _kTextMuted),
            ),
          ),
          Expanded(
            child: Text(
              value,
              textAlign: TextAlign.right,
              overflow: TextOverflow.ellipsis,
              maxLines: 2,
              style: TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: valueColor ?? _kTextMain,
                height: 1.25,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _overlayRow() {
    final bool? g = _overlayGranted;
    final String text =
        g == null ? '检测中…' : (g ? '已授权' : '未授权');
    final Color color = g == null
        ? _kTextMuted
        : (g ? AppTokens.successDark : AppTokens.danger);
    final IconData icon = g == null
        ? Icons.shield_outlined
        : (g ? Icons.verified_user_rounded : Icons.gpp_maybe_rounded);
    return _row(icon, '悬浮窗', text, valueColor: color);
  }

  static String _hhmm(DateTime t) {
    final String h = t.hour.toString().padLeft(2, '0');
    final String m = t.minute.toString().padLeft(2, '0');
    return '$h:$m';
  }
}

const Color _kAccent = AppTokens.brand;
const Color _kTextMain = AppTokens.ink;
const Color _kTextMuted = AppTokens.muted;
const Color _kBorder = AppTokens.border;
