import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_overlay_window/flutter_overlay_window.dart';
import 'package:realtime_mahjong_trainer/overlays/analysis.dart';
import 'package:realtime_mahjong_trainer/overlays/tile_labels.dart';
import 'package:realtime_mahjong_trainer/server.dart';

// 解析原生层发来的分析结果：前 10('\n') 之前为 JSON，之后为 PNG 图片字节。
parseEngineResult(List<int> b) {
  int sepIndex = b.indexOf(10); // 对应 '\n'

  String jsonString = String.fromCharCodes(b.sublist(0, sepIndex));
  var json = jsonDecode(jsonString);

  // 图片字节当前未直接用于 UI（手牌/点评已由 JSON 承载），保留解析以备扩展。
  // ignore: unused_local_variable
  Image image = Image.memory(Uint8List.fromList(b.sublist(sepIndex + 1)));

  return (json, image);
}

class MahjongOverlay extends StatefulWidget {
  @override
  State<MahjongOverlay> createState() => _MahjongOverlayState();
}

class _MahjongOverlayState extends State<MahjongOverlay> {
  late Map<String, dynamic> result;
  bool ready = false;

  // 初始仅显示“悬浮按钮”（收起态）；点击后展开为“分析小窗口”。
  bool panelVisible = false;

  static const double _collapsed = 56;
  static const double _expandedW = 260;
  static const double _expandedH = 320;

  // 按 dp 设置悬浮窗尺寸。
  // 注意：resizeOverlay 走的是悬浮窗引擎的通道（"x-slayer/overlay"），
  // 只有【悬浮窗自身】调用才生效，主 App 里调用到不了。
  // 服务尚未把视图挂上时该方法返回 false，故轮询重试直到成功。
  Future<void> _applySize(double w, double h) async {
    for (int i = 0; i < 40; i++) {
      try {
        final ok =
            await FlutterOverlayWindow.resizeOverlay(w.toInt(), h.toInt(), true);
        if (ok == true) return;
      } catch (_) {}
      await Future<void>.delayed(const Duration(milliseconds: 100));
    }
    print('悬浮窗尺寸校正失败（w=$w, h=$h）');
  }

  @override
  void initState() {
    super.initState();

    // 插件 showOverlay 时把 width/height 当作物理像素使用（未做 dp 转换），
    // 56dp 的按钮在 3 倍密度屏上会被画成 56 像素（约 7mm，几乎看不见）。
    // 因此这里由悬浮窗自身按 dp 重新设定一次尺寸。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _applySize(_collapsed, _collapsed);
    });

    // 监听原生层通过本地 socket 发来的每帧分析结果（端口 12345 与 ImageProcessor 发送端一致）。
    // 即便 socket 启动失败也不能让悬浮窗引擎崩溃（否则按钮永远不渲染），因此整体 try/catch 兜底。
    try {
      Server(
        callback: (data) {
          final tup = parseEngineResult(data);
          final json = tup.$1;
          if (mounted) {
            setState(() {
              result = json;
              ready = true;
            });
          }
        },
        host: "127.0.0.1",
        port: 12345,
      );
    } catch (e) {
      print("悬浮窗分析服务初始化失败（不影响按钮显示）：$e");
    }
  }

  // 悬浮按钮点击：在“仅按钮”与“分析小窗口”之间切换（窗口始终常驻在屏幕上）
  Future<void> _togglePanel() async {
    if (!mounted) return;
    final next = !panelVisible;
    setState(() {
      panelVisible = next;
    });
    // 展开/收起时同样按 dp 调整窗口尺寸（由悬浮窗自身调用才生效）
    if (next) {
      await _applySize(_expandedW, _expandedH);
    } else {
      await _applySize(_collapsed, _collapsed);
    }
  }

  // 悬浮窗内“停止”：通过 shareData 通知主 App 真正停止识别，并关闭整个悬浮窗。
  Future<void> _stop() async {
    try {
      await FlutterOverlayWindow.shareData('stop');
    } catch (_) {}
    try {
      await FlutterOverlayWindow.closeOverlay();
    } catch (_) {}
  }

  // 悬浮按钮本体（橙色圆形，常驻在屏幕上，可拖动整窗）
  Widget _floatingButton({double size = _collapsed}) {
    return GestureDetector(
      onTap: _togglePanel,
      child: Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          color: Colors.orange,
          shape: BoxShape.circle,
          boxShadow: const [
            BoxShadow(
              color: Colors.black45,
              blurRadius: 4,
              spreadRadius: 1,
            ),
          ],
        ),
        child: Center(
          child: ready
              ? const Icon(Icons.visibility, color: Colors.white, size: 26)
              : const SizedBox(
                  width: 22,
                  height: 22,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: Colors.white,
                  ),
                ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    // 收起态：屏幕上只保留一个悬浮按钮
    if (!panelVisible) {
      return _floatingButton();
    }

    // 展开态：分析小窗口（手牌 / 向听数 / 建议），一直常驻在屏幕上
    final hand = ready ? (result['hand'] ?? '') : '';
    final Map<String, dynamic>? analysis = ready ? result['analysis'] : null;

    return Container(
      width: _expandedW,
      height: _expandedH,
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: Colors.black.withAlpha(190),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 顶部栏：标题 + 悬浮按钮（点击收起窗口）+ 停止
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text('麻将助手',
                  style: TextStyle(
                      color: Colors.white,
                      fontWeight: FontWeight.bold,
                      fontSize: 14)),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  _floatingButton(size: 36),
                  const SizedBox(width: 8),
                  GestureDetector(
                    onTap: _stop,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 6),
                      decoration: BoxDecoration(
                        color: Colors.red,
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: const Text('停止',
                          style:
                              TextStyle(color: Colors.white, fontSize: 13)),
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 8),
          Expanded(
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (!ready)
                    const Text('正在识别牌局…',
                        style: TextStyle(color: Colors.white70, fontSize: 13))
                  else ...[
                    if (hand.isNotEmpty)
                      Text('手牌：${handToChinese(hand)}',
                          style: const TextStyle(
                              color: Colors.white70, fontSize: 12)),
                    const SizedBox(height: 4),
                    if (analysis != null) Analysis(analysis),
                  ],
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
