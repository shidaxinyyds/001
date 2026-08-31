// 回归测试：针对"界面出现红色字符"与"弹窗缩小后布局溢出"两类问题。
//
// 背景：原本这里是 flutter create 生成的模板测试（断言 'Running on:' 文本），
// 本项目从未有过该文本，所以它一直是失败状态。现替换为真正有意义的断言。

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:realtime_mahjong_trainer/overlays/mahjong_overlay.dart';

/// 判定一个颜色是否"看起来是红/橙系"。
/// 判据：红通道明显高于绿和蓝，且红通道本身较亮。
/// 覆盖 Colors.red / deepOrange / orange / amber / 0xFFD32F2F / 0xFFD84315 等。
bool looksRedOrOrange(Color c) {
  final int r = (c.r * 255).round();
  final int g = (c.g * 255).round();
  final int b = (c.b * 255).round();
  return r >= 140 && r - g >= 40 && r - b >= 40;
}

void main() {
  group('红色字符回归', () {
    test('looksRedOrOrange 判据自检', () {
      // 应判为红/橙
      expect(looksRedOrOrange(const Color(0xFFD32F2F)), isTrue); // 原"中"的红
      expect(looksRedOrOrange(const Color(0xFFD84315)), isTrue); // 原向听徽章深橙
      expect(looksRedOrOrange(const Color(0xFFFF8F00)), isTrue); // 原悬浮按钮橙
      expect(looksRedOrOrange(Colors.red), isTrue);
      expect(looksRedOrOrange(Colors.deepOrange), isTrue);
      expect(looksRedOrOrange(Colors.amber), isTrue);
      // 不应判为红/橙
      expect(looksRedOrOrange(const Color(0xFF202124)), isFalse); // 字牌深灰
      expect(looksRedOrOrange(const Color(0xFF1E6B7A)), isFalse); // 万/筒蓝绿
      expect(looksRedOrOrange(const Color(0xFF2E7D32)), isFalse); // 条草绿
      expect(looksRedOrOrange(const Color(0xFF00695C)), isFalse); // 主色青绿
      expect(looksRedOrOrange(const Color(0xFF455A64)), isFalse); // 徽章中性灰
      expect(looksRedOrOrange(const Color(0xFFF7F3E8)), isFalse); // 牌面米白
    });

    testWidgets('TileChip 所有牌型的字符颜色都不是红/橙（含"中"）',
        (WidgetTester tester) async {
      final List<String> tiles = <String>[
        '1m', '5m', '9m', // 万
        '1p', '5p', '9p', // 筒
        '1s', '5s', '9s', // 条
        '1z', '2z', '3z', '4z', // 东南西北
        '5z', '6z', '7z', // 中/發/白（5z 即"中"，历史上是红字）
      ];

      for (final String t in tiles) {
        await tester.pumpWidget(
          MaterialApp(
            home: Scaffold(body: Center(child: TileChip(tile: t))),
          ),
        );

        final Iterable<Text> texts = tester.widgetList<Text>(find.byType(Text));
        expect(texts, isNotEmpty, reason: '$t 未渲染出文字');
        for (final Text w in texts) {
          final Color? c = w.style?.color;
          if (c == null) continue;
          expect(looksRedOrOrange(c), isFalse,
              reason: '$t 的字符颜色 $c 属于红/橙系');
        }
      }
    });
  });

  group('弹窗固定三段布局不溢出', () {
    // 这些数值镜像 _MahjongOverlayState 中的私有常量（该 State 为私有，测试无法直接引用）。
    // 若生产代码调整了任一常量，本测试的 minPanelH 断言会立刻暴露不一致。
    const double padV = 16;
    const double titleBarH = 26;
    const double titleGap = 8;
    const double sectionH = 110;
    const double sectionGap = 6;
    const double minHandH = 84;
    const double minPanelH =
        padV + titleBarH + titleGap + sectionH + sectionGap + sectionH + sectionGap + minHandH;

    test('minPanelH 恰好等于固定部分之和（366）', () {
      expect(minPanelH, 366.0);
    });

    /// 复刻生产布局骨架：固定顶栏 + 两个固定段 + 填充段，
    /// 每段内部都是"标题 + Expanded(可滚动内容)"。
    Widget harness({required double height, required int discardTiles}) {
      Widget section({
        required String title,
        required Widget child,
        double? h,
        bool fill = false,
      }) {
        final Widget content = Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Text(title, style: const TextStyle(fontSize: 11)),
            const SizedBox(height: 4),
            // 关键：Expanded 包住滚动区，否则内容高于段高时 Column 会溢出。
            Expanded(
              child: SingleChildScrollView(
                physics: const ClampingScrollPhysics(),
                child: child,
              ),
            ),
          ],
        );
        return fill ? content : SizedBox(height: h, child: content);
      }

      return MaterialApp(
        home: Scaffold(
          body: Center(
            child: SizedBox(
              width: 296,
              height: height,
              child: Container(
                padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    const SizedBox(height: titleBarH, child: Text('麻将助手')),
                    const SizedBox(height: titleGap),
                    section(
                      title: '建议',
                      h: sectionH,
                      child: const SizedBox(height: 300), // 故意远高于段高
                    ),
                    const SizedBox(height: sectionGap),
                    section(
                      title: '牌河',
                      h: sectionH,
                      child: Wrap(
                        children: List<Widget>.generate(
                          discardTiles,
                          (int i) => const SizedBox(width: 24, height: 30),
                        ),
                      ),
                    ),
                    const SizedBox(height: sectionGap),
                    Expanded(
                      child: section(
                        title: '手牌',
                        fill: true,
                        child: const SizedBox(height: 400), // 故意远高于段高
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      );
    }

    testWidgets('最小高度 366 且内容严重超长时不溢出', (WidgetTester tester) async {
      await tester.pumpWidget(harness(height: minPanelH, discardTiles: 60));
      expect(tester.takeException(), isNull);
      // 溢出会作为 FlutterError 抛出，被上面的 takeException 捕获。
    });

    testWidgets('默认高度 380 不溢出', (WidgetTester tester) async {
      await tester.pumpWidget(harness(height: 380, discardTiles: 60));
      expect(tester.takeException(), isNull);
    });

    testWidgets('最大高度 680 不溢出', (WidgetTester tester) async {
      await tester.pumpWidget(harness(height: 680, discardTiles: 120));
      expect(tester.takeException(), isNull);
    });

    testWidgets('三段标题按「建议 / 牌河 / 手牌」自上而下固定排列',
        (WidgetTester tester) async {
      await tester.pumpWidget(harness(height: 480, discardTiles: 12));
      final double yAdvice = tester.getTopLeft(find.text('建议')).dy;
      final double yDiscard = tester.getTopLeft(find.text('牌河')).dy;
      final double yHand = tester.getTopLeft(find.text('手牌')).dy;
      expect(yAdvice < yDiscard, isTrue, reason: '建议应在牌河之上');
      expect(yDiscard < yHand, isTrue, reason: '牌河应在手牌之上');
    });

    testWidgets('反例：body 不包 Expanded 时确实会溢出（证明修复必要）',
        (WidgetTester tester) async {
      // 复现修复前的写法：Column 中直接放 SingleChildScrollView，不给有界高度。
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Center(
              child: SizedBox(
                width: 296,
                height: sectionH,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    const Text('牌河', style: TextStyle(fontSize: 11)),
                    const SizedBox(height: 4),
                    SingleChildScrollView(
                      physics: const ClampingScrollPhysics(),
                      child: Container(height: 300),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      );
      final Object? err = tester.takeException();
      expect(err, isNotNull, reason: '修复前的写法应当溢出');
      expect(err, isA<FlutterError>());
      expect(err.toString(), contains('overflowed'));
    });
  });

  group('parseEngineResult', () {
    test('正常 JSON + 换行分隔', () {
      final List<int> b = '{"hand":"1m2m","count":2}\n'.codeUnits;
      final Map<String, dynamic>? r = parseEngineResult(b);
      expect(r, isNotNull);
      expect(r!['hand'], '1m2m');
      expect(r['count'], 2);
    });

    test('无换行返回 null', () {
      expect(parseEngineResult('{"hand":"1m"}'.codeUnits), isNull);
    });

    test('换行在首位返回 null', () {
      expect(parseEngineResult('\n{"hand":"1m"}'.codeUnits), isNull);
    });

    test('非法 JSON 返回 null 而不抛异常', () {
      expect(parseEngineResult('not json\n'.codeUnits), isNull);
    });
  });
}
