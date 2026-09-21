/// 授权状态。悬浮窗与主页据此决定"给不给建议"。
enum LicenseStatus {
  /// 本地短期凭证有效 → 放行，可离线使用。
  valid,

  /// 从未激活过（无本地凭证）。
  notActivated,

  /// 凭证已到期、正处于网络宽限内（软状态）：仍可离线使用，后台重试续签。
  needsRenew,

  /// 总授权到期 → 自动销毁，需买新卡密。
  licenseExpired,

  /// 被服务端作废 / 本地凭证被篡改、换设备、改时间 → 直接拒绝。
  refused,
}

class LicenseState {
  final LicenseStatus status;
  final DateTime? expiresAt;
  final DateTime? tokenExp;
  final String? message;
  final bool isRevoked;

  const LicenseState(
    this.status, {
    this.expiresAt,
    this.tokenExp,
    this.message,
    this.isRevoked = false,
  });

  bool get isUsable => status == LicenseStatus.valid;

  /// 是否允许展示牌建议：valid 与 needsRenew(宽限内) 均放行，其余一律禁用。
  bool get allowsUsage =>
      status == LicenseStatus.valid || status == LicenseStatus.needsRenew;

  /// 剩余天数（向上取整到"天"，不足一天显示为 <1 天由 UI 处理）。
  int get remainingDays {
    final e = expiresAt;
    if (e == null) return 0;
    final d = e.difference(DateTime.now());
    if (d.isNegative) return 0;
    return (d.inHours / 24).ceil();
  }

  bool get isExpiredLike =>
      status == LicenseStatus.licenseExpired || status == LicenseStatus.refused;

  /// 严苛的终止态判定：仅在真正过期或被服务端明确拉黑时成立，绝不拿瞬态错误当终止。
  bool get isStrictlyTerminated {
    if (isRevoked) return true;
    if (status == LicenseStatus.licenseExpired) {
      if (expiresAt == null) return true;
      return DateTime.now().isAfter(expiresAt!);
    }
    return false;
  }
}
