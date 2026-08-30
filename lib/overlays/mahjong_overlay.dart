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

  // 悬浮按钮始终常驻；点击按钮可收起/展开“分析悬浮窗”面板。
  bool panelVisible = true;

  static const double _windowWidth = 266;
  static const double _windowHeight = 340;

  @override
  void initState() {
    super.initState();

    // 监听原生层通过本地 socket 发来的每帧分析结果（端口 12345 与 ImageProcessor 发送端一致）
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
      host: "0.0.0.0",
      port: 12345,
    );
  }

  // 悬浮窗内的“停止”：通过 shareData 通知主 App 真正停止识别。
  // 悬浮窗运行在独立的 Flutter 引擎，没有 MainActivity 注册的 MethodChannel 处理器，
  // 无法直接调用原生 stopProcessing，因此借助 overlayListener 通道。
  Future<void> _stop() async {
    try {
      await FlutterOverlayWindow.shareData('stop');
    } catch (_) {}
    try {
      await FlutterOverlayWindow.closeOverlay();
    } catch (_) {}
  }

  // 悬浮按钮点击：收起 / 展开分析面板（悬浮按钮本身与悬浮窗始终常驻）
  void _togglePanel() {
    if (!mounted) return;
    setState(() {
      panelVisible = !panelVisible;
    });
  }

  @override
  Widget build(BuildContext context) {
    final hand = ready ? (result['hand'] ?? '') : '';
    final Map<String, dynamic>? analysis = ready ? result['analysis'] : null;

    // 说明：flutter_overlay_window 0.4.5 仅支持单实例悬浮窗，无法开两个独立系统窗口。
    // 这里用“一个透明悬浮窗 + 两块常驻区域”实现“悬浮按钮 + 悬浮窗同时常驻”，
    // 整窗可拖动（enableDrag），满足双窗口常驻体验。
    return Container(
      width: _windowWidth,
      height: _windowHeight,
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: Colors.black.withAlpha(190),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 顶部栏：标题 + 悬浮按钮（常驻）+ 停止
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
                  // 悬浮按钮：始终常驻，点击收起/展开分析面板
                  GestureDetector(
                    onTap: _togglePanel,
                    child: Container(
                      width: 36,
                      height: 36,
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
                            ? const Icon(Icons.visibility,
                                color: Colors.white, size: 20)
                            : const SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: Colors.white,
                                ),
                              ),
                      ),
                    ),
                  ),
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
          // 分析悬浮窗：默认展开，可点击悬浮按钮收起
          if (panelVisible)
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
            )
          else
            const Text('（面板已收起，点击上方橙色按钮展开）',
                style: TextStyle(color: Colors.white38, fontSize: 11)),
        ],
      ),
    );
  }
}
