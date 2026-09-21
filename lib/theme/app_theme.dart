import 'package:flutter/material.dart';

import 'app_tokens.dart';

/// 统一主题（Material 3）。
///
/// 版本安全铁律：CI 锁 Flutter 3.13.0，本地可能更新。此处只使用跨版本
/// 都存在、且值为「普通类型」（非 WidgetState/MaterialStateProperty）的主题
/// 字段——彻底规避 `MaterialStateProperty`→`WidgetStateProperty`、
/// `CardTheme`→`CardThemeData`、`withOpacity`→`withValues` 三类改名冲突。
/// 按钮圆角/内距等在调用点用 `XxxButton.styleFrom(...)`（取普通值）定制。
class AppTheme {
  AppTheme._();

  static final ThemeData light = _buildLight();

  static ThemeData _buildLight() {
    final ColorScheme scheme = ColorScheme.fromSeed(
      seedColor: AppTokens.brand,
      primary: AppTokens.brand,
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: AppTokens.bg,

      appBarTheme: const AppBarTheme(
        backgroundColor: AppTokens.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        foregroundColor: AppTokens.ink,
        iconTheme: IconThemeData(color: AppTokens.ink),
      ),

      dividerTheme: const DividerThemeData(
        color: AppTokens.border,
        thickness: 1,
        space: 1,
      ),

      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: AppTokens.surface,
        contentPadding: const EdgeInsets.symmetric(
            horizontal: AppTokens.s16, vertical: AppTokens.s16),
        hintStyle: const TextStyle(color: AppTokens.faint, fontSize: 15),
        border: OutlineInputBorder(
          borderRadius: AppTokens.radius12,
          borderSide: const BorderSide(color: AppTokens.border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: AppTokens.radius12,
          borderSide: const BorderSide(color: AppTokens.border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: AppTokens.radius12,
          borderSide: const BorderSide(color: AppTokens.brand, width: 2),
        ),
      ),
    );
  }
}
