import 'package:shared_preferences/shared_preferences.dart';

/// 主页「对局信息」里的游戏 ID：本地持久化 + 字符校验。
///
/// **接线单（只写在代码里，UI 不标注接线进度）**：
/// - 本文件只做两件事：把用户输入的 ID 存进 shared_preferences（重启不丢）、
///   以及给出"能不能存"的判据。
/// - 后续通路是既定的：MethodChannel `setGameId` → Java 仿 `writePlatformFile`
///   写 `mahjong_session.json`（内/外部存储双写）→ Python 引擎逐帧读入，写进
///   采集 meta 的 `game_id` 做素材溯源。
/// - 在那之前这个 ID 只在本机保存，所以 UI 文案的边界是"已保存 <id>"——说成
///   "已参与采集"就是把打算当成事实。
class SessionStore {
  SessionStore._();

  static const String _kGameId = 'session_game_id';

  /// 与未来 Java 侧写文件的正则保持一致：只允许 ASCII 字母/数字/下划线/连字符。
  /// 一旦放行中文或空格，文件里就得做 JSON 转义，届时两侧口径必然分叉。
  static final RegExp _pattern = RegExp(r'^[A-Za-z0-9_\-]+$');
  static const int maxLen = 32;

  static String normalize(String raw) => raw.trim();

  /// 校验用户输入。返回 null 表示可以保存（空串 = 清空），否则返回给用户看的原因。
  static String? validate(String raw) {
    final v = normalize(raw);
    if (v.isEmpty) return null;
    if (v.length > maxLen) return '最长 $maxLen 个字符（当前 ${v.length}）';
    if (!_pattern.hasMatch(v)) return '只允许字母、数字、下划线、连字符';
    return null;
  }

  /// 锁定配置前的必填判据。与 `validate` 分开两条是有原因的：
  /// 「清空输入框」这个动作本身必须允许（否则用户删不掉上一局的旧 ID），
  /// 但**空着不许锁定** —— 没有对局号的配置事后无从对齐到具体哪一局。
  static String? require(String raw) =>
      normalize(raw).isEmpty ? requiredNotice : null;

  /// 空 ID 时给用户看的那句话（输入框提示、锁定失败原因、启动后首屏都用它，
  /// 三处口径必须一模一样：同一个坑不能一会儿叫「必填」一会儿叫「请填」）。
  static const String requiredNotice = '对局ID 必填：填牌桌上的对局号或你的玩家ID';

  /// 读取已保存的游戏 ID；存储不可用时安全返回空串，绝不让主页抛异常。
  static Future<String> loadGameId() async {
    try {
      final p = await SharedPreferences.getInstance();
      return normalize(p.getString(_kGameId) ?? '');
    } catch (_) {
      return '';
    }
  }

  /// 保存（空串视为清空）。返回 false 表示本地存储失败——UI 必须如实回滚提示，
  /// 不能"看着保存成功、下次进来却空了"。
  static Future<bool> saveGameId(String raw) async {
    final v = normalize(raw);
    try {
      final p = await SharedPreferences.getInstance();
      if (v.isEmpty) {
        await p.remove(_kGameId);
      } else {
        await p.setString(_kGameId, v);
      }
      return true;
    } catch (_) {
      return false;
    }
  }
}
