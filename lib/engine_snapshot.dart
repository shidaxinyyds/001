import 'package:flutter/foundation.dart';

/// 最近一帧引擎回传的**只读快照**（悬浮窗 shareData → 主页）。
///
/// 为什么要单独一个类，而不是把字段塞进 `_HomePageState`：主页整页 setState
/// 会把平台卡/玩法卡一起重建，识别高峰期就是"点什么都没反应"的卡顿来源
/// （见 home_page 里 _RecognitionStatusView 的局部订阅注释）。本类是
/// ChangeNotifier，只有真正需要这帧数据的局部组件（运势 / 好牌概率结果区）
/// 用 AnimatedBuilder 订阅，重建范围压到一个小组件。
///
/// 数据真实性口径：
/// - 这里**只做搬运与解析**，不做任何牌型判断，也不写任何兜底数字。
/// - 档位词（极优/较优/均势/承压）与 note 一律是引擎原值：阈值口径只允许存在
///   在 Python 的 `probability_bands` 一份里，前端再翻译一遍就是两处漂移。
/// - 好牌概率的分子分母来自牌局账本整数（可逐张核对），除法也只在本类做一次；
///   后期 Python 直接下发 `odds` 时改走原值分支，这里就地退化成透传。
/// - 没有帧数据时 [mood]/[goodTile] 返回 unavailable 读条，UI 必须显示"无数据"，
///   绝不用 0% 或"待定"糊过去。
class EngineSnapshot extends ChangeNotifier {
  EngineSnapshot._();

  static final EngineSnapshot instance = EngineSnapshot._();

  Map<String, dynamic> _frame = const <String, dynamic>{};
  DateTime? _at;
  String _sig = '';

  bool get hasFrame => _at != null && _frame.isNotEmpty;

  /// 本帧是否带了运势/好牌所需的原始字段（引擎口径 `odds` 或帧内事实二者之一）。
  bool get hasOdds =>
      odds != null || equityFrame != null || tileFrame != null;

  DateTime? get at => _at;

  Map<String, dynamic> get frame => _frame;

  // ===== 帧基础字段（悬浮窗现有 status 白名单里已在回传的真实数据）=====
  String get hand => (_frame['hand'] ?? '').toString();

  int get tileCount => (_frame['count'] as num?)?.toInt() ?? 0;

  int? get shanten => (_frame['shanten'] as num?)?.toInt();

  String get status => (_frame['status'] ?? '').toString();

  // ===== odds：优先用引擎直接下发的口径（后端接入后），否则用帧内事实自行组装 =====
  Map<String, dynamic>? get odds => _asMap(_frame['odds']);

  Map<String, dynamic>? get moodFrame => _asMap(_frame['mood']);

  Map<String, dynamic>? get equityFrame => _asMap(_frame['equity']);

  Map<String, dynamic>? get tileFrame => _asMap(_frame['tile_odds']);

  static Map<String, dynamic>? _asMap(Object? v) {
    if (v is Map) return Map<String, dynamic>.from(v);
    return null;
  }

  /// 运势读条。
  ///
  /// 口径要点：**这一项永远没有百分号**。它的来源是 `win_equity` 一类的模型估值，
  /// 而 `probability_bands.CALIBRATED == false`（未与真实对局结果标定过）时，
  /// 未标定数字只允许以档位词出现——把 0.81 印成「81%」就是把打分伪装成测量。
  /// 所以这里给的是档位（极优/较优/均势/承压）+ 粗分（偏优/中性/偏劣）+ note，
  /// 四个字段全是引擎原值透传，Dart 侧一个阈值都不判。
  MoodReadout get mood {
    // 引擎侧若已带 odds（后期口径），按 odds 走；否则用同帧的 equity + mood。
    // odds 是可空的，先落成局部变量再取值，省掉一路的判空重复。
    final Map<String, dynamic>? o = odds;
    final Map<String, dynamic>? fromOdds = o == null ? null : _asMap(o['mood']);
    final Map<String, dynamic>? src = fromOdds ?? equityFrame;
    if (src == null || src['band'] == null) {
      return MoodReadout.unavailable(
        hasFrame
            ? '这一帧还没形成可研判的牌面，凑齐手牌后自动给出'
            : '开启识别后这里会跟着牌局实时变化',
      );
    }
    final Map<String, dynamic>? badgeSrc = fromOdds ?? moodFrame;
    final String badge =
        badgeSrc == null ? '' : (badgeSrc['badge'] ?? '').toString();
    final String desc =
        badgeSrc == null ? '' : (badgeSrc['desc'] ?? '').toString();
    return MoodReadout(
      available: true,
      band: (src['band'] ?? '未定档').toString(),
      tier: (src['tier'] ?? '未定档').toString(),
      note: (src['note'] ?? '').toString(),
      calibrated: src['calibrated'] == true,
      badge: badge,
      desc: desc,
      insight: (src['insight'] ?? '').toString(),
      basis: (src['equity_basis'] ?? '').toString(),
    );
  }

  /// 好牌概率读条：一次摸牌能推进牌型的概率，分子分母都取自牌局账本的整数。
  ///
  /// 与运势相反，这一项**可以**印百分号——它不是模型打分，是两个可逐张核对的
  /// 整数相除：牌墙里可推进的张数 ÷ 牌墙剩余张数。但方向词必须跟着分子来源走：
  /// - `lo`（账本下界）→「至少」；
  /// - `hi`（未现进张上界，含对手按住的牌）→「至多」。
  /// 除法只在本类里做一次（后期 Python 直接下发 percent 时改为取原值），
  /// 主页不得自己再除一遍——两处算同一个数就会开始漂。
  TileReadout get goodTile {
    final Map<String, dynamic>? o = odds;
    final Map<String, dynamic>? fromOdds =
        o == null ? null : _asMap(o['good_tile']);
    if (fromOdds != null) {
      return TileReadout(
        available: fromOdds['available'] != false,
        numerator: (fromOdds['numerator'] as num?)?.toInt() ?? 0,
        denominator: (fromOdds['denominator'] as num?)?.toInt() ?? -1,
        isLowerBound: fromOdds['bound'] != 'hi',
        percentOverride: (fromOdds['percent'] as num?)?.toDouble(),
        basis: (fromOdds['basis'] ?? fromOdds['text'] ?? '').toString(),
        ledgerOk: fromOdds['ledger_ok'] != false,
        missing: (fromOdds['missing'] ?? '').toString(),
      );
    }
    final Map<String, dynamic>? t = tileFrame;
    if (t == null) {
      return TileReadout.unavailable(
        hasFrame
            ? '牌墙账本还在对齐中，稳定出牌后自动给出'
            : '开启识别后这里会跟着牌局实时变化',
      );
    }
    return TileReadout(
      available: true,
      numerator: (t['numerator'] as num?)?.toInt() ?? 0,
      denominator: (t['wall_remaining'] as num?)?.toInt() ?? -1,
      isLowerBound: t['bound'] != 'hi',
      basis: (t['text'] ?? '').toString(),
      ledgerOk: t['ledger_ok'] != false,
    );
  }

  /// 收到悬浮窗 status 回传时喂进来。签名相同则不通知（同内容没必要重建）。
  ///
  /// 签名必须把运势/好牌三项一起算进去：同一副手牌里别人打出一张，牌墙剩余与
  /// 可推进张数都会变而 hand/count/status 一字不变，漏掉它们主页就会一直停在
  /// 上一帧的数字上——那等于把旧数据当新数据卖。
  void push(Map<String, dynamic> event) {
    final Object? moodRaw = event['mood'];
    final Object? equityRaw = event['equity'];
    final Object? tileRaw = event['tile_odds'];
    final Object? oddsRaw = event['odds'];
    final sig = "${event['hand']}|${event['count']}|${event['status']}"
        "|${event['shanten']}|${event['best']}"
        "|$moodRaw|$equityRaw|$tileRaw|$oddsRaw";
    if (sig == _sig && _at != null) return;
    _sig = sig;
    _frame = event;
    _at = DateTime.now();
    notifyListeners();
  }

  /// 开始新一轮识别时清空：上一局的牌墙数字不属于这一局，留着展示就是造假。
  void clear() {
    if (_frame.isEmpty && _at == null && _sig.isEmpty) return;
    _frame = const <String, dynamic>{};
    _at = null;
    _sig = '';
    notifyListeners();
  }

  /// 快照时刻的展示文本（不用"x 秒前"那种需要定时器刷新的写法）。
  String atLabel() {
    final t = _at;
    if (t == null) return '';
    String two(int v) => v.toString().padLeft(2, '0');
    return '${two(t.hour)}:${two(t.minute)}:${two(t.second)}';
  }
}

/// 运势：模型类数值，未标定 → 只有档位，结构上不存在百分号。
class MoodReadout {
  final bool available;
  final String reason;

  /// 极优 / 较优 / 均势 / 承压（Python `probability_bands` 原词）
  final String band;

  /// 偏优 / 中性 / 偏劣（同一张表的三档投影，专供配色）
  final String tier;
  final String note;
  final bool calibrated;

  /// 军师研判（🌊 牌势顺遂 · 乘胜追击）与其说明句，引擎原值
  final String badge;
  final String desc;
  final String insight;
  final String basis;

  const MoodReadout({
    required this.available,
    this.reason = '',
    this.band = '',
    this.tier = '',
    this.note = '',
    this.calibrated = false,
    this.badge = '',
    this.desc = '',
    this.insight = '',
    this.basis = '',
  });

  const MoodReadout.unavailable(this.reason)
      : available = false,
        band = '',
        tier = '',
        note = '',
        calibrated = false,
        badge = '',
        desc = '',
        insight = '',
        basis = '';

  /// 口径行：一句话说明这项研判的呈现方式。
  /// 「未标定的模型值禁印百分号」这条阈值口径唯一存放在 Python
  /// `probability_bands`，Dart 侧只负责如实描述，不重复判阈值。
  String get caliber => calibrated
      ? '牌势概率已按实战样本标定'
      : '牌势为模型研判，以档位呈现';

  bool get isPureAnalytical => basis == 'analytical';
}

/// 好牌概率：事实类数值，分子分母都是牌局账本里可逐张核对的整数。
class TileReadout {
  final bool available;
  final String missing;
  final int numerator;

  /// 牌墙剩余张数；<=0 表示本帧没有可用分母（不猜，直接不印比例）
  final int denominator;

  /// true = 分子是账本下界（至少）；false = 未现进张上界（至多）
  final bool isLowerBound;

  /// 后期 Python 口径模块若直接下发 percent，这里取原值，Dart 不再自己除。
  final double? percentOverride;
  final String basis;
  final bool ledgerOk;

  const TileReadout({
    required this.available,
    this.missing = '',
    this.numerator = 0,
    this.denominator = -1,
    this.isLowerBound = true,
    this.percentOverride,
    this.basis = '',
    this.ledgerOk = true,
  });

  const TileReadout.unavailable(this.missing)
      : available = false,
        numerator = 0,
        denominator = -1,
        isLowerBound = true,
        percentOverride = null,
        basis = '',
        ledgerOk = true;

  String get boundWord => isLowerBound ? '至少' : '至多';

  double? get percent {
    if (!available || denominator <= 0) return null;
    final p = percentOverride;
    if (p != null) return p;
    final v = numerator * 100.0 / denominator;
    if (v < 0) return 0.0;
    return v > 100 ? 100.0 : v;
  }

  String get headline {
    final p = percent;
    if (p == null) return '牌墙余量待定';
    return '${boundWord} ${p.toStringAsFixed(1)}%';
  }

  /// 算式本体：数字必须能被用户拿牌河逐张核对，所以分子分母同屏，不只给结果。
  String get formula {
    if (!available || denominator <= 0) return '';
    return '$numerator ÷ $denominator 张';
  }

  String get caveat {
    if (available && !ledgerOk) {
      return '本帧牌局记账有冲突，上面的张数还需要人工核对';
    }
    if (available && !isLowerBound) {
      return '分子含对手手上按住的牌，所以只能给出上限';
    }
    return '';
  }
}

