import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'channel.dart';

/// 调试页配置：识别策略开关 + 出牌建议配置。
///
/// 引擎侧现状决定了两类参数必须走**两条不同通道**：
///
/// - **布尔识别策略**（auto_orient / bootstrap / strict / anti_ban /
///   anti_detect）：MethodChannel `setConfig` → Java `ImageProcessor.setConfig`
///   → Python `Engine.set_config`，直接改引擎 `self._cfg`，下一帧生效。
///   - `anti_ban` / `anti_detect`（防封号 / 防平台检测）的**真实行为在 Java 侧**：
///     截屏节奏随机抖动、前台感知采样，由 `ImageProcessor` 的采集循环直接执行；
///     Python 侧仅存档，不影响识别结果。
/// - **出牌建议配置**（show_advice 布尔 + min_ukeire 整数）：MethodChannel
///   `setAdviceConfig` → Java 写 `mahjong_advice.json` → Python 每帧 reload。
///   原因：`min_ukeire` 是整数，而 `setConfig` 只接受布尔，塞不进去。
///
/// 所有下发都吞异常：引擎未启动 / 通道未就绪时静默失败，绝不让 UI 崩。
class DebugConfig {
  static const MethodChannel _ch = MethodChannel(CHANNEL_NAME);

  // 默认值必须与引擎侧严格一致，否则会出现「开关显示关、引擎其实开着」的鬼影：
  //   engine.py  self._cfg             : auto_orient/bootstrap/strict 均为 True
  //   modes.py   DEFAULT_SHOW_ADVICE   = True
  //   modes.py   DEFAULT_MIN_UKEIRE    = 0（0 表示不过滤）
  static const bool defAutoOrient = true;
  static const bool defBootstrap = true;
  static const bool defStrict = true;
  static const bool defShowAdvice = true;
  static const int defRate = 10;
  // 危险牌预警（防点炮 / 防杠）：当前为**纯 UI 占位**，后端危险度计算尚未接入。
  // 默认关闭，仅经 shared_preferences 保存开关状态，**不驱动任何引擎行为**。
  // 接入后端前，绝不声称该功能已生效（避免假开关误导）。
  static const bool defWarnDealIn = false;
  static const bool defWarnPonKong = false;
  // 防封号 / 防平台检测：默认关闭。
  // 两者都是「行为层」隐私措施（见 ImageProcessor 采集循环），默认关意味着
  // 不改动任何既有行为，用户主动打开才生效。
  static const bool defAntiBan = false;
  static const bool defAntiDetect = false;
  // 采集存帧（风格库自举）：开启后把引擎看到的原始帧落盘到 app 外部 files/frames/，
  // 供后续 harvest→cluster→标注重建该风格的完整模板库。默认关闭。
  static const bool defDumpFrames = false;
  // 牌河真实帧采集（P4 再训练闭环数据入口）：引擎在牌河内容变化时存
  // 整帧+元数据到 files/river_frames/。默认关闭。
  static const bool defCollectRiver = false;
  // 牌河 YOLO 影子对比（E）：引擎每帧额外跑 YOLO 条带法检测牌河，只写
  // diag/日志供离线对比，绝不参与显示与建议。默认关闭（有推理开销）。
  static const bool defYoloRiver = false;

  /// 好牌机率可选项（百分比）。
  static const List<int> rates = [10, 20, 30, 40, 50, 60, 70, 80, 90];

  bool autoOrient;
  bool bootstrap;
  bool strict;
  bool showAdvice;
  int rate;
  bool warnDealIn;
  bool warnPonKong;
  bool antiBan;
  bool antiDetect;
  bool dumpFrames;
  bool collectRiver;
  bool yoloRiver;

  DebugConfig({
    this.autoOrient = defAutoOrient,
    this.bootstrap = defBootstrap,
    this.strict = defStrict,
    this.showAdvice = defShowAdvice,
    this.rate = defRate,
    this.warnDealIn = defWarnDealIn,
    this.warnPonKong = defWarnPonKong,
    this.antiBan = defAntiBan,
    this.antiDetect = defAntiDetect,
    this.dumpFrames = defDumpFrames,
    this.collectRiver = defCollectRiver,
    this.yoloRiver = defYoloRiver,
  });

  /// 好牌机率 → 引擎「进张数下限」。
  /// 10%→1 张 … 90%→9 张：贴合真实麻将进张数区间，语义直观。
  /// 阈值过高会把所有打法过滤掉，此时引擎返回空建议列表。
  int get minUkeire => ((rate ~/ 10).clamp(1, 9)).toInt();

  DebugConfig copyWith({
    bool? autoOrient,
    bool? bootstrap,
    bool? strict,
    bool? showAdvice,
    int? rate,
    bool? warnDealIn,
    bool? warnPonKong,
    bool? antiBan,
    bool? antiDetect,
    bool? dumpFrames,
    bool? collectRiver,
    bool? yoloRiver,
  }) {
    return DebugConfig(
      autoOrient: autoOrient ?? this.autoOrient,
      bootstrap: bootstrap ?? this.bootstrap,
      strict: strict ?? this.strict,
      showAdvice: showAdvice ?? this.showAdvice,
      rate: rate ?? this.rate,
      warnDealIn: warnDealIn ?? this.warnDealIn,
      warnPonKong: warnPonKong ?? this.warnPonKong,
      antiBan: antiBan ?? this.antiBan,
      antiDetect: antiDetect ?? this.antiDetect,
      dumpFrames: dumpFrames ?? this.dumpFrames,
      collectRiver: collectRiver ?? this.collectRiver,
      yoloRiver: yoloRiver ?? this.yoloRiver,
    );
  }

  // ===== 持久化（shared_preferences）=====
  static const String _kAutoOrient = 'dbg_auto_orient';
  static const String _kBootstrap = 'dbg_bootstrap';
  static const String _kStrict = 'dbg_strict';
  static const String _kShowAdvice = 'dbg_show_advice';
  static const String _kRate = 'dbg_rate';
  static const String _kWarnDealIn = 'dbg_warn_deal_in';
  static const String _kWarnPonKong = 'dbg_warn_pon_kong';
  static const String _kAntiBan = 'dbg_anti_ban';
  static const String _kAntiDetect = 'dbg_anti_detect';
  static const String _kDumpFrames = 'dbg_dump_frames';
  static const String _kCollectRiver = 'dbg_collect_river';
  static const String _kYoloRiver = 'dbg_yolo_river';

  static Future<DebugConfig> load() async {
    try {
      final p = await SharedPreferences.getInstance();
      final rate = p.getInt(_kRate) ?? defRate;
      return DebugConfig(
        autoOrient: p.getBool(_kAutoOrient) ?? defAutoOrient,
        bootstrap: p.getBool(_kBootstrap) ?? defBootstrap,
        strict: p.getBool(_kStrict) ?? defStrict,
        showAdvice: p.getBool(_kShowAdvice) ?? defShowAdvice,
        // 历史值可能已不在候选项里（版本变更），夹回合法档位再使用
        rate: rates.contains(rate) ? rate : defRate,
        warnDealIn: p.getBool(_kWarnDealIn) ?? defWarnDealIn,
        warnPonKong: p.getBool(_kWarnPonKong) ?? defWarnPonKong,
        antiBan: p.getBool(_kAntiBan) ?? defAntiBan,
        antiDetect: p.getBool(_kAntiDetect) ?? defAntiDetect,
        dumpFrames: p.getBool(_kDumpFrames) ?? defDumpFrames,
        collectRiver: p.getBool(_kCollectRiver) ?? defCollectRiver,
        yoloRiver: p.getBool(_kYoloRiver) ?? defYoloRiver,
      );
    } catch (_) {
      return DebugConfig();
    }
  }

  Future<void> save() async {
    try {
      final p = await SharedPreferences.getInstance();
      await p.setBool(_kAutoOrient, autoOrient);
      await p.setBool(_kBootstrap, bootstrap);
      await p.setBool(_kStrict, strict);
      await p.setBool(_kShowAdvice, showAdvice);
      await p.setInt(_kRate, rate);
      await p.setBool(_kWarnDealIn, warnDealIn);
      await p.setBool(_kWarnPonKong, warnPonKong);
      await p.setBool(_kAntiBan, antiBan);
      await p.setBool(_kAntiDetect, antiDetect);
      await p.setBool(_kDumpFrames, dumpFrames);
      await p.setBool(_kCollectRiver, collectRiver);
      await p.setBool(_kYoloRiver, yoloRiver);
    } catch (_) {
      // 存不下就算了，不能因为本地存储失败影响识别主流程
    }
  }

  /// 把当前配置下发给引擎。返回是否全部下发成功。
  ///
  /// 注意：引擎未启动（没点"开始识别"）时，`setConfig` 仍能写进 Java 静态变量，
  /// 下次启动识别即生效；`setAdviceConfig` 写文件后 Python 每帧读，同样生效。
  ///
  /// warnDealIn / warnPonKong（危险牌预警）走 `setAdviceConfig` 文件通道：
  /// 开启后引擎对每张候选弃牌附上基于「牌河」的真实危险度（防点炮 / 防杠），
  /// 由悬浮窗决定如何展示。
  ///
  /// [clearDumpedFrames]：仅当用户**手动拨开**采集存帧开关时传 true——先发一条
  /// 显式 `clear_frames` 让 Java 清空旧帧目录。初始化同步 / 「确认配置」重发
  /// 绝不能传 true：否则 app 重启后 apply() 重发 dump_frames=true 会被当成
  /// 上升沿清空已采集的帧（已采集数据丢失）。
  /// [clearRiverFrames]：同上约定，仅用户手动拨开「牌河采集」时传 true。
  Future<bool> apply({
    bool clearDumpedFrames = false,
    bool clearRiverFrames = false,
  }) async {
    bool ok = true;

    // 用户手动开启采集存帧：先显式清空旧帧，保证每轮是干净集合。
    if (clearDumpedFrames) {
      try {
        await _ch.invokeMethod<dynamic>('setConfig', {
          'key': 'clear_frames',
          'value': true,
        });
      } catch (_) {
        ok = false;
      }
    }
    if (clearRiverFrames) {
      try {
        await _ch.invokeMethod<dynamic>('setConfig', {
          'key': 'clear_river',
          'value': true,
        });
      } catch (_) {
        ok = false;
      }
    }

    // 三条布尔识别策略：走已有的 setConfig 通道
    for (final e in <String, bool>{
      'auto_orient': autoOrient,
      'bootstrap': bootstrap,
      'strict': strict,
      // 防封号 / 防平台检测：行为在 Java 侧采集循环执行，这里只下发开关。
      'anti_ban': antiBan,
      'anti_detect': antiDetect,
      // 采集存帧（风格库自举）：行为在 Java 侧把原始帧落盘到 files/frames/。
      'dump_frames': dumpFrames,
      // 牌河真实帧采集：真正的采帧在 Python 引擎（牌河变化触发），
      // 落盘到 files/river_frames/（jpg+json 成对）。
      'collect_river': collectRiver,
      // 牌河 YOLO 影子对比：只写 diag/日志供离线评测，不影响显示。
      'yolo_river': yoloRiver,
    }.entries) {
      try {
        await _ch.invokeMethod<dynamic>('setConfig', {
          'key': e.key,
          'value': e.value,
        });
      } catch (_) {
        ok = false;
      }
    }

    // 出牌建议配置：走文件通道（min_ukeire 是整数）
    try {
      final rc = await _ch.invokeMethod<int>('setAdviceConfig', {
        'showAdvice': showAdvice,
        'minUkeire': minUkeire,
        'warnDealIn': warnDealIn,
        'warnPonKong': warnPonKong,
      });
      if (rc != 0) ok = false;
    } catch (_) {
      ok = false;
    }

    return ok;
  }

  /// 触发 Java 侧把 river_frames 打包成 zip 并返回绝对路径。
  /// 返回 null 表示通道异常（引擎/Activity 未就绪），空串表示暂无采集数据。
  /// 调用方拿到路径后自行交给 share_plus 走系统分享面板。
  static Future<String?> exportRiverFrames() async {
    try {
      return await _ch.invokeMethod<String>('exportRiverFrames');
    } catch (_) {
      return null;
    }
  }
}
