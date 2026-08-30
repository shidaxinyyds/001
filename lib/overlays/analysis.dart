import 'package:flutter/material.dart';

/// 向听数与点评展示。
///
/// 原实现在 build() 里调用 setState() —— 这会在运行时直接抛
/// "setState() or markNeedsBuild() called during build" 异常。
/// 该组件本身不需要保存状态，改为 StatelessWidget 即可。
class Analysis extends StatelessWidget {
  final Map<String, dynamic>? analysis;

  const Analysis(this.analysis, {super.key});

  @override
  Widget build(BuildContext context) {
    final Map<String, dynamic>? data = analysis;
    if (data == null) {
      return const SizedBox(height: 10);
    }

    final int? shanten = data['shanten'] as int?;
    final String? commentary = data['commentary'] as String?;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (shanten != null)
          Text(
            shanten == 0 ? '向听数：听牌' : '向听数：$shanten',
            style: const TextStyle(color: Colors.white, fontSize: 13),
          ),
        if (commentary != null && commentary.isNotEmpty) ...[
          const SizedBox(height: 4),
          Text(
            commentary,
            style: const TextStyle(color: Colors.white70, fontSize: 12),
          ),
        ],
      ],
    );
  }
}
