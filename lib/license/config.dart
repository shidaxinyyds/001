/// 卡密授权后端配置。
///
/// ⚠️ 密钥绝不写进本文件（仓库是公开的！）。两个值都在构建时由
/// --dart-define 注入：GitHub CI 从仓库 Secrets 自动传；本地手机构建则
/// 自己传参（见 supabase/README.md）。没注入时 App 不会崩，只是激活/验签失败。
///
/// 安全说明：该密钥只用于**验证**服务端签发的短期凭证。它挡得住"改本地时间
/// 长期白嫖""一张卡多人传用"，但挡不住 root+改包的高级破解（任何客户端方案
/// 都挡不住）。真正的强边界在服务端：卡密一次性绑定、到期/拉黑由服务器判定。
class LicenseConfig {
  static const String licenseFunctionUrl = String.fromEnvironment(
    'LICENSE_FUNCTION_URL',
    defaultValue: 'https://REPLACE_ME.supabase.co/functions/v1/license',
  );

  static const String licenseTokenSecretHex = String.fromEnvironment(
    'LICENSE_TOKEN_SECRET',
    defaultValue: '',
  );

  /// 单张短期凭证名义有效期（秒），与服务端 TOKEN_S 对齐，仅用于 UI 估算。
  static const int tokenWindowS = 3 * 24 * 3600;

  /// 续签提前量：凭证到期前多久即开始尝试续签（秒）。
  static const int renewLeadS = 12 * 3600;
}
