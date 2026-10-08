/// 端到端帧龄统计（P1-e：让"端到端延迟"从一个读不出来的数字变成可验收的指标）。
///
/// 为什么只能在悬浮窗侧算：延迟的定义是「牌桌画面发生变化 → 面板上出现结果」，
/// 它的**终点**在悬浮窗、**起点**在 Java 采集线程，只有收到帧的这一侧能同时拿到
/// 两端。Java 把采集时刻与分段耗时随帧注入 `captured_at_ms`/`encode_ms`/
/// `engine_ms`，本类用本地接收时刻相减并按段统计。
///
/// 为什么可以直接相减：`System.currentTimeMillis()`（Java）与
/// `DateTime.now().millisecondsSinceEpoch`（Dart）在 Android 上都是**设备 epoch
/// 墙钟**，同一个时钟源，且 epoch 计数与时区无关。因此不需要任何跨进程时钟同步
/// 协议 —— 这也意味着它只能量"同一台设备内的两个进程"，量不了跨设备。
///
/// 诚实性口径（本类存在的意义就是说谎不了）：
/// - 缺 `captured_at_ms` 的帧（Java 降级路径、心跳信标、旧版本 APK）一律**不计入
///   样本**，只累加 `dropped`。绝不拿"上一帧的帧龄"或 0 去填。
/// - 墙钟回拨（NTP 校时、用户改系统时间、休眠唤醒）会算出负值或荒谬值，同样丢弃。
/// - 分位数在无样本时返回 null，UI 必须显示"—"，不许印 0ms。
/// - 窗口必须有界（capacity）：无界累计既泄漏内存，又会让"历史最好成绩"糊住当前的退化。
///
/// 口径边界（必须如实告诉读数字的人）：帧龄量到的是「采集 → Dart 收到」，**不含**
/// Flutter 光栅化上屏那一段；前沿上屏是 0ms（见 _ingestEngineResult 的"前沿：0ms
/// 立即上屏响应"），尾部合并窗口 36ms，所以渲染段是一个已知的、有上界的附加项，
/// 而不是被漏掉的暗段。
class LatencyProbe {
  LatencyProbe({this.capacity = 120, this.maxPlausibleMs = 60000});

  /// 滚动窗口容量：只保留最近 N 帧。
  final int capacity;

  /// 单帧帧龄的合理上限。超过它说明两端时钟不再同源（校时/唤醒），
  /// 这个样本已不可能反映链路速度，丢弃比记录更诚实。
  final int maxPlausibleMs;

  final _Window _age = _Window();
  final _Window _encode = _Window();
  final _Window _engine = _Window();
  int _dropped = 0;

  /// 本侧最后一次收到可信帧的时刻（ms epoch），供 UI 判断读数是否已过时。
  int lastSampleAtMs = 0;

  int get samples => _age.n;
  int get dropped => _dropped;

  /// 收一帧。
  /// [capturedAtMs] Java 注入的采集时刻（缺失传 null）；
  /// [receivedAtMs] 本侧接收时刻；
  /// [encodeMs]/[engineMs] Java 侧「整屏 JPEG 编码」与「Python 识别往返」两段耗时。
  /// 返回本帧帧龄（ms）；null 表示不可信，已丢弃且不计入任何窗口。
  int? add(int? capturedAtMs, int receivedAtMs,
      {int? encodeMs, int? engineMs}) {
    if (capturedAtMs == null || capturedAtMs <= 0) {
      _dropped++;
      return null;
    }
    final int age = receivedAtMs - capturedAtMs;
    if (age < 0 || age > maxPlausibleMs) {
      _dropped++;
      return null;
    }
    _age.add(age, capacity);
    // 分段耗时独立收：它们由 Java 同帧算出，负值（时钟/逻辑异常）直接不收。
    if (encodeMs != null && encodeMs >= 0 && encodeMs <= maxPlausibleMs) {
      _encode.add(encodeMs, capacity);
    }
    if (engineMs != null && engineMs >= 0 && engineMs <= maxPlausibleMs) {
      _engine.add(engineMs, capacity);
    }
    lastSampleAtMs = receivedAtMs;
    return age;
  }

  void reset() {
    _age.clear();
    _encode.clear();
    _engine.clear();
    _dropped = 0;
    lastSampleAtMs = 0;
  }

  bool get hasData => _age.n > 0;

  int? get p50 => _age.percentile(0.50);
  int? get p95 => _age.percentile(0.95);
  int? get max => _age.max;
  int? get encodeP50 => _encode.percentile(0.50);
  int? get engineP50 => _engine.percentile(0.50);

  /// 上报给主 App 的载荷（悬浮窗 → 主页只有一条 shareData 通道，必须给它一个
  /// 自解释的 Map）。字段全为 int/null/Map<String,int>，跨引擎序列化不依赖任何自定义类型。
  ///
  /// [java] 是采集层心跳自报的计数（原样透传，不在这里加工）：帧龄只说“多久到”，
  /// 它说“到了多少帧、有多少帧没能带时间戳、本轮新增的解析开销多大”—— 两个数摆在一起
  /// 才能自洽对账（样本缺口能不能被 parse_fail + non_finite 解释）。
  Map<String, dynamic> toShare({Map<String, int> java = const <String, int>{}}) {
    return <String, dynamic>{
      'type': 'latency',
      'n': _age.n,
      'dropped': _dropped,
      'p50_ms': p50,
      'p95_ms': p95,
      'max_ms': max,
      'encode_p50_ms': encodeP50,
      'engine_p50_ms': engineP50,
      'sampled_at': lastSampleAtMs,
      'java': Map<String, int>.from(java),
    };
  }
}

/// 有界样本窗口。三段耗时用同一份实现，避免"每段各写一遍分位数"造成的口径漂移。
class _Window {
  final List<int> _v = <int>[];

  int get n => _v.length;

  void add(int value, int capacity) {
    if (capacity <= 0) return;
    _v.add(value);
    if (_v.length > capacity) {
      _v.removeRange(0, _v.length - capacity);
    }
  }

  void clear() => _v.clear();

  /// 分位数口径：排序后取第 ceil(q*n) 个（1-based）。
  /// n=1 时 p50/p95/max 同为那一个样本 —— 样本少就照实少，不做平滑也不外推。
  int? percentile(double q) {
    final int n = _v.length;
    if (n == 0) return null;
    final List<int> sorted = List<int>.from(_v)..sort();
    final int idx = (n * q).ceil() - 1;
    return sorted[idx < 0 ? 0 : (idx >= n ? n - 1 : idx)];
  }

  int? get max {
    final int n = _v.length;
    if (n == 0) return null;
    int m = _v.first;
    for (final int v in _v) {
      if (v > m) m = v;
    }
    return m;
  }
}
