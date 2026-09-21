import 'package:flutter/material.dart';

/// 全局设计 token（明亮现代商务 · slate + 翡翠青主色）。
///
/// 版本兼容铁律：本项目 CI 锁 Flutter 3.13.0，本地可能更高。为避免
/// 跨版本 API 冲突（`withOpacity`→`withValues`、`MaterialStateProperty`→
/// `WidgetStateProperty`、`CardTheme`→`CardThemeData`），这里一律使用
/// **静态 ARGB 常量 `Color(0xAARRGGBB)`** 表达半透明，绝不运行期调用
/// 透明度/状态属性相关 API。新增 token 请遵循同一约束。
class AppTokens {
  AppTokens._();

  // ===== 品牌色 =====
  static const Color brand = Color(0xFF0D9488); // teal-600 主色
  static const Color brandDark = Color(0xFF0F766E); // teal-700 按压/强调
  static const Color brandContainer = Color(0xFFCCFBF1); // teal-100 选中底
  static const Color brandSoft = Color(0xFFF0FDFA); // teal-50 极淡底

  // ===== 中性 / 文本 =====
  static const Color ink = Color(0xFF0F172A); // slate-900 主文本
  static const Color ink2 = Color(0xFF1E293B); // slate-800 次主文本
  static const Color muted = Color(0xFF64748B); // slate-500 说明文本
  static const Color faint = Color(0xFF94A3B8); // slate-400 占位/禁用

  // ===== 表面 / 背景 / 描边 =====
  static const Color surface = Color(0xFFFFFFFF);
  static const Color bg = Color(0xFFF8FAFC); // slate-50 页面底
  static const Color border = Color(0xFFE2E8F0); // slate-200
  static const Color borderStrong = Color(0xFFCBD5E1); // slate-300
  static const Color pillBg = Color(0xFFF1F5F9); // slate-100 分段/徽章底

  // ===== 语义色 =====
  static const Color success = Color(0xFF10B981);
  static const Color successDark = Color(0xFF047857);
  static const Color successBg = Color(0xFFECFDF5);
  static const Color successBorder = Color(0xFFA7F3D0);
  static const Color danger = Color(0xFFDC2626);
  static const Color warn = Color(0xFFD97706);

  // 全局错误占位（中性灰，杜绝红字）
  static const Color errorPlaceholder = Color(0xFF37474F); // blueGrey-800

  // ===== 圆角 =====
  static const double r8 = 8;
  static const double r10 = 10;
  static const double r12 = 12;
  static const double r16 = 16;
  static const double r20 = 20;
  static const double rPill = 999;

  static final BorderRadius radius12 = BorderRadius.circular(r12);
  static final BorderRadius radius16 = BorderRadius.circular(r16);
  static final BorderRadius radius20 = BorderRadius.circular(r20);

  // ===== 间距 =====
  static const double s4 = 4;
  static const double s8 = 8;
  static const double s12 = 12;
  static const double s16 = 16;
  static const double s20 = 20;
  static const double s24 = 24;

  // ===== 阴影层级（用静态 ARGB，避开 withOpacity）=====
  static const List<BoxShadow> soft = <BoxShadow>[
    BoxShadow(color: Color(0x0F000000), blurRadius: 10, offset: Offset(0, 4)),
  ];
  static const List<BoxShadow> raised = <BoxShadow>[
    BoxShadow(color: Color(0x1A000000), blurRadius: 18, offset: Offset(0, 8)),
  ];

  // ===== 字重快捷 =====
  static const FontWeight wRegular = FontWeight.w400;
  static const FontWeight wMedium = FontWeight.w500;
  static const FontWeight wSemi = FontWeight.w600;
  static const FontWeight wBold = FontWeight.w700;
}
