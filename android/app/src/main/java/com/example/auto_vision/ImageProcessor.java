package com.example.auto_vision;


import android.content.Context;
import android.media.Image;

import android.app.AppOpsManager;
import android.app.usage.UsageStats;
import android.app.usage.UsageStatsManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Build;
import android.os.Process;

import java.io.File;
import java.io.FileOutputStream;
import java.util.List;
import java.util.Random;
import java.util.Timer;
import java.util.TimerTask;
import com.chaquo.python.PyObject;
import com.chaquo.python.android.AndroidPlatform;
import com.chaquo.python.Python;

import java.util.function.Supplier;

public class ImageProcessor {
    private static final String TAG = "ImageProcessor" ;
    private PyObject engine;

    // 与 Dart 悬浮窗（MahjongOverlay）监听的端口保持一致
    private NetworkClient client = new NetworkClient("127.0.0.1", 12345);

    private Supplier<Image> callback;

    // ===== 用户可调识别区域（ROI，纵向比例带）=====
    // 悬浮窗里拖动"识别框"时经 MainActivity 的 setRoi 通道写入；下一帧处理前
    // 推给 Python 引擎（set_roi），引擎只识别 [top,bottom] 带内。
    // 默认整屏（0..1）不退化。用静态字段，因为引擎实例在 startStream 里重建，
    // 但 ROI 是用户偏好，应跨重建保留。
    // volatile（同下方 cfgDumpFrames 那条注释讲的同一类缺陷）：setRoi 在主线程（平台通道）
    // 写、采集线程读。非 volatile 时采集线程可以长期读到旧的 roiDirty=false，用户拖完
    // ROI 引擎永远收不到 set_roi；也可能读到 roiDirty=true 却配着旧的边界值（撕裂对）。
    // 值字段一并 volatile：写序为 top→bottom→dirty，采集侧见 dirty=true 即建立
    // happens-before，两个值一起可见。
    private static volatile float roiTop = 0f;
    private static volatile float roiBottom = 1f;
    private static volatile boolean roiDirty = true;

    // ===== 手动方向覆盖（悬浮窗「旋转」按钮）=====
    // 经 MainActivity 的 setOrient 通道写入；下一帧处理前推给 Python 引擎
    // （set_orient），引擎按指定角度旋转后再识别。默认 -1 = 未设置（走自动探测）。
    private static volatile int orientOverride = -1;
    private static volatile boolean orientDirty = false;

    // ===== 防封号 / 防平台检测（调试页开关，经 setConfig 写入，采集循环读取）=====
    // anti_ban：截屏节奏随机抖动（350–550ms）+ 建议延迟显示，避免固定节奏的 bot 特征。
    // anti_detect：仅在目标麻将 App 前台时采帧，切回本 App/桌面自动暂停。
    // 两者默认关闭，打开才改变采集行为；缺权限/缺 Context 时自动降级为常开。
    private static volatile boolean cfgAntiBan = false;
    private static volatile boolean cfgAntiDetect = false;
    // 采集存帧（风格库自举）：开启后把引擎看到的原始帧（encoded JPEG）落盘到
    // app 外部 files/frames/，供后续 harvest→cluster→标注重建模板库。默认关闭。
    // 清空旧帧走显式 clear_frames 信号（仅用户手动拨开开关时由调试页发送），
    // 本开关自身绝不触发清空（见 setConfig 的 dump_frames 分支注释）。
    // volatile：setConfig（主线程）写、采集线程读，保证开关变化对采集线程立即可见；
    // 非 volatile 的 static boolean 在无数据竞争保护时可能长期读到旧值（开关"不生效"）。
    private static volatile boolean cfgDumpFrames = false;
    // 牌河真实帧采集（P4 再训练闭环数据入口）：真正的采帧逻辑在 Python 引擎
    // （它知道牌河内容何时变化），Java 只负责：① 开关转发；② 启动时把
    // 应用外部 files/river_frames/ 目录推给引擎；③ clear_river 信号清目录。
    private static volatile boolean cfgCollectRiver = false;
    // 牌河 YOLO 影子对比：真实采帧逻辑在 Python 引擎（只写 diag/日志不显示），
    // Java 仅转发开关。
    private static volatile boolean cfgYoloRiver = false;
    // volatile long：32 位 ARM 上 long 写非原子（可能读到高低位撕裂的值），必须 volatile。
    // framesDumped 由采集线程（dumpFrame）递增、主线程（clearFramesDir）清零，两线程共享。
    private static volatile long framesDumped = 0;
    // 存帧互斥锁：dumpFrame（采集线程）与 clearFramesDir（主线程，开关打开瞬间）互斥，
    // 避免"边删旧帧边写新帧"的竞态。静态锁，不持有任何 Activity 引用。
    private static final Object sFramesLock = new Object();
    // 「已达上限」只提示一次的标志（与 framesDumped 一样跨线程，volatile 保证可见）。
    private static volatile boolean sLimitLogged = false;
    // 前台检测需要 Context，在 prepare() 里缓存（用 ApplicationContext，避免持有 Activity）。
    private static volatile Context sContext = null;
    // 「解析结果 + 注入时间戳」开销的累计值（本次改动新增的那一笔代价自己读数）。
    // 采集线程写、心跳线程读；volatile long 同时解决可见性与 32 位 ARM 上的撕裂写。
    // 语义是「本轮识别（start() 以来）的均值」，因此 start() 里会清零。
    private static volatile long sStampMsSum = 0;
    private static volatile long sStampFrames = 0;
    // 结果 JSON 解析失败的累计次数。解析失败意味着本帧没有时间戳也不走 native，
    // 必须可观测；但绝不能每帧刷一条日志（旧写法会形成 logcat 风暴，反过来拖慢热路径）。
    private static volatile long sParseFailures = 0;
    // 结果含 NaN/Infinity 而退回原始出帧的累计次数（与 parse_fail 分开计：两者降级方向
    // 一致但成因不同，混在一个数里就没法判断该修 Python 的序列化还是修结果字段）。
    private static volatile long sNonFiniteFrames = 0;
    private static final Random sRng = new Random();

    public static void setRoi(float top, float bottom) {
        float t = Math.max(0f, Math.min(1f, top));
        float b = Math.max(t + 0.02f, Math.min(1f, bottom));
        roiTop = t;
        roiBottom = b;
        roiDirty = true;
    }

    public static void setOrient(int deg) {
        // deg 仅接受 0/90/180/270；其它值视为解除覆盖（传 -1 给引擎）。
        if (deg == 0 || deg == 90 || deg == 180 || deg == 270) {
            orientOverride = deg;
        } else {
            orientOverride = -1;
        }
        orientDirty = true;
    }

    private static volatile int dingqueOverride = -1;
    private static volatile boolean dingqueDirty = false;

    public static void setDingque(int suit) {
        if (suit >= 0 && suit <= 2) {
            dingqueOverride = suit;
        } else {
            dingqueOverride = -1;
        }
        dingqueDirty = true;
        if (NativeEngine.isAvailable()) {
            NativeEngine.setDingque(dingqueOverride);
        }
    }

    private static volatile boolean resetRequested = false;

    public static void resetMatch() {
        resetRequested = true;
        orientOverride = -1;
        orientDirty = true;
        if (NativeEngine.isAvailable()) {
            NativeEngine.reset();
        }
    }

    // 调试页开关：经 MainActivity 的 setConfig 通道写入，下一帧处理前推给 Python 引擎。
    // 用独立布尔而非 Map，避免额外的 import 与 Chaquopy 类型转换摩擦。
    private static volatile boolean cfgAutoOrient = true;
    private static volatile boolean cfgBootstrap = true;
    private static volatile boolean cfgStrict = true;
    private static volatile boolean configDirty = false;

    public static void setConfig(String key, boolean value) {
        if (key == null) return;
        switch (key) {
            case "auto_orient": cfgAutoOrient = value; break;
            case "bootstrap":   cfgBootstrap = value; break;
            case "strict":      cfgStrict = value; break;
            case "anti_ban":    cfgAntiBan = value; break;
            case "anti_detect": cfgAntiDetect = value; break;
            case "dump_frames":
                // 只改开关，不清空目录！清空必须走显式的 clear_frames 信号——
                // 否则 app 重启后调试页 initState 的 apply() 会把持久化的 true
                // 重发一遍，被当成「用户刚打开」的上升沿，把已采集的帧全部清掉
                // （用户打完几局收集的数据一次重启就没了，数据丢失级 bug）。
                cfgDumpFrames = value;
                break;
            case "clear_frames":
                // 仅由调试页开关的 onChanged（用户手动拨开）显式触发，
                // 初始化同步/「确认配置」重发都不会走到这里。
                if (value) clearFramesDir();
                break;
            case "collect_river":
                // 与 dump_frames 同理：只改开关，绝不清空（清空走显式 clear_river）。
                cfgCollectRiver = value;
                break;
            case "clear_river":
                if (value) clearRiverFramesDir();
                break;
            case "yolo_river":
                cfgYoloRiver = value;
                break;
            default: return;
        }
        configDirty = true;
    }

    private Timer timer;
    // 真固定 2s 心跳 Timer：与采集自调度循环完全独立。
    // 旧心跳只在采集循环路过时顺带发——画面静止/采集线程卡死时一条都发不出，
    // 悬浮窗无从区分"画面没变"与"链路死了"，永远假显"实时"。
    private Timer heartbeatTimer;
    private volatile long lastCaptureTickAt = 0;

    // ===== 流水线自诊断 =====
    // 此前所有故障（收不到画面/Python异常/发送失败）都只进 logcat，
    // 用户在界面上看到的就是"没有任何反应"。现在每 2 秒发一次心跳
    // 状态帧到悬浮窗，任何一环断掉都能在界面上直接看到断在哪里。
    // volatile：三个计数都由采集线程自增、由 heartbeat 线程读取汇报。
    // ① 可见性：非 volatile 时心跳可以长期读到旧值，健康度面板上的 frames/proc/send_fail
    //    与实际脱节（排查卡帧时这是假线索）；
    // ② 原子性：32 位 ARM 上 long 写非原子，会读到高低位撕裂的值 —— 与本文件
    //    framesDumped（static volatile long）当初立规矩的同一理由。
    // 自增仍只在采集线程发生，所以不存在丢更新，不需要 AtomicInteger。
    private volatile long framesAcquired = 0;
    private volatile long framesProcessed = 0;
    private volatile long sendFailures = 0;
    private long lastHeartbeatAt = 0;

    public ImageProcessor(Supplier<Image> callback) {
        this.callback = callback;
    }

    public boolean prepare(Context context) {
        // 缓存 ApplicationContext 供前台检测使用（避免持有 Activity 导致泄漏）。
        if (context != null) {
            sContext = context.getApplicationContext();
        }
        if (NativeEngine.isAvailable()) {
            NativeEngine.init();
            TimedLog.i(TAG, "Mahjong NativeEngine C++ core ready");
        }
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(context));
        }
        Python python = Python.getInstance();
        try {
            engine = python.getModule("engine").get("Engine").callThrows();
        } catch (Throwable e) {
            // 关键改动：Python 引擎初始化失败不再裸抛 RuntimeException 把整个
            // App 搞崩（用户看到的是点了"开始"后 App 消失，什么反馈都没有）。
            // 现在把错误上报到悬浮窗，并返回 false 让启动流程优雅终止。
            TimedLog.e(TAG, "Python engine init failed: " + e);
            sendStatus(NetworkClient.statusJson("java_error", "Python引擎启动失败: " + e));
            return false;
        }
        TimedLog.i(TAG, "started Python");
        // 把配置目录与当前持久化模式推给引擎
        try {
            File extDir = context != null ? context.getExternalFilesDir(null) : null;
            if (extDir != null) {
                engine.callAttr("set_config_dir", extDir.getAbsolutePath());
            }
            File intDir = context != null ? context.getFilesDir() : null;
            if (intDir != null && extDir == null) {
                engine.callAttr("set_config_dir", intDir.getAbsolutePath());
            }
            if (pendingMode != null) {
                engine.callAttr("set_mode", pendingMode);
            }
            if (pendingPlatform != null) {
                engine.callAttr("set_platform", pendingPlatform);
            }
        } catch (Throwable t) {
            TimedLog.e(TAG, "配置目录/模式/平台推入失败（不影响基础运行）: " + t);
        }
        // 牌河采集目录推给引擎（仅在配置了「牌河采集」时引擎才会真正写盘）。
        // 引擎用普通文件 IO 写应用私有外部目录，无需存储权限；adb pull 可取回。
        try {
            File rdir = riverFramesDir();
            if (rdir != null) {
                engine.callAttr("set_frame_dump_dir", rdir.getAbsolutePath());
                TimedLog.i(TAG, "牌河采集目录已推给引擎: " + rdir.getAbsolutePath());
            }
        } catch (Throwable t) {
            TimedLog.e(TAG, "set_frame_dump_dir 推送失败（不影响识别）: " + t);
        }
        sendStatus(NetworkClient.statusJson("engine_ready", null));
        return true;
    }

    private volatile String pendingMode = null;
    private volatile String pendingPlatform = null;

    public void setMode(String mode) {
        if (mode == null || mode.trim().isEmpty()) return;
        String m = mode.trim().toLowerCase();
        pendingMode = m;
        if (engine != null) {
            try {
                engine.callAttr("set_mode", m);
                TimedLog.i(TAG, "setMode 即时推送到 Python 引擎: " + m);
            } catch (Throwable t) {
                TimedLog.e(TAG, "setMode 推送失败（引擎将经文件轮询兜底读到新玩法）: " + t);
            }
        }
    }

    public void setPlatform(String platform) {
        if (platform == null || platform.trim().isEmpty()) return;
        String p = platform.trim().toLowerCase();
        pendingPlatform = p;
        if (engine != null) {
            try {
                engine.callAttr("set_platform", p);
                TimedLog.i(TAG, "setPlatform 即时推送到 Python 引擎: " + p);
            } catch (Throwable t) {
                TimedLog.e(TAG, "setPlatform 推送失败（引擎将经文件轮询兜底读到新平台）: " + t);
            }
        }
    }

    // 单帧处理（Python 推理）可能超过 500ms 的采集间隔。
    // Timer 只有一个工作线程，不加保护的话任务会无限堆积，
    // 队列越排越长、结果永远滞后，表现为"识别卡死"。
    // 这里保证同一时刻只处理一帧，处理不完就直接跳过下一帧。
    private final java.util.concurrent.atomic.AtomicBoolean busy =
            new java.util.concurrent.atomic.AtomicBoolean(false);

    public void start() {
        timer = new Timer();
        lastCaptureTickAt = System.currentTimeMillis();
        // 开销均值按「本轮识别」统计：不清零就会把上一轮的噪声算进新一轮的读数里。
        sStampMsSum = 0;
        sStampFrames = 0;
        sParseFailures = 0;
        sNonFiniteFrames = 0;
        // 真固定 2s 心跳：无论采集循环是否在跑，每 2s 必发一帧状态；
        // 采集线程自己卡死（超 6s 没路过）时改报 pipeline_stalled。
        heartbeatTimer = new Timer("heartbeat", true);
        heartbeatTimer.schedule(new TimerTask() {
            public void run() {
                // 深层安全巡检放在本线程（独立于采集循环），而不是像旧写法那样
                // 嵌在 SecurityGuard.isSafe() 里由采集线程顺带跑：那意味着每 8 秒
                // 必有一帧要在采集线程里逐行读 /proc/self/maps + 两次
                // socket.connect(15ms)，把实时性周期性让给安全扫描。
                // 扫到攻击会置 sCompromised，下一帧 isSafe()/verifyLicenseThrottled()
                // 立即拒识别（熔断最多迟一个心跳周期 2s + 1 帧生效）。
                SecurityGuard.runScheduledDeepScan(sContext);
                long silent = System.currentTimeMillis() - lastCaptureTickAt;
                if (silent >= 6000) {
                    sendHeartbeatJson("pipeline_stalled",
                        "采集线程已停止响应（静默 " + (silent / 1000) + "s），请停止后重新开始");
                } else {
                    heartbeat("capturing");
                }
            }
        }, 2000, 2000);
        // 改为自调度（见 scheduleNextCapture）：固定 period 无法逐帧改间隔，
        // 防封号开启时需要随机抖动间隔，故每帧跑完再排下一帧。
        scheduleNextCapture(0);
    }

    // 自调度采集：每跑完一帧再排下一帧，间隔可随「防封号」开关变化。
    // 固定 schedule(...,0,400) 无法逐帧改间隔，故用递归 one-shot 调度。
    private void scheduleNextCapture(long delayMs) {
        final Timer t = timer;
        if (t == null) return;
        t.schedule(new TimerTask() {
            public void run() {
                try {
                    runCaptureOnce();
                } finally {
                    // 无论本帧成功/异常/被跳过，都排下一帧。
                    // stop() 会 cancel 并置 timer=null；若本帧恰好在 stop 之后调度，
                    // schedule 会抛 IllegalStateException，这里静默吞掉，避免 Timer 线程崩溃。
                    if (timer != null) {
                        try {
                            scheduleNextCapture(captureDelayMs());
                        } catch (IllegalStateException ignore) {
                            // Timer 已被 stop() 取消，忽略。
                        }
                    }
                }
            }
        }, delayMs);
    }

    // 两级自适应帧调度 (Frame Governor)：静止态降频巡检，活跃态快速跟帧
    private volatile int consecutiveSkips = 0;

    // 下一帧采集间隔：
    // 静止态（连续跳帧 >= 3）：80~100ms 巡检，快速唤醒；
    // 活跃态：15ms 极速跟帧（旧注释写的 25ms 与代码不符；另有一处 UI 文案
    // 写「350–550ms 随机抖动」也与防封号分支的 80–120ms 不符，两边均已按实际修）。
    // 防封号开启：在此基础上叠加拟人抖动。
    private long captureDelayMs() {
        boolean isIdle = (consecutiveSkips >= 3);
        if (isIdle) {
            return 80 + (cfgAntiBan ? sRng.nextInt(40) : 0);
        }
        if (cfgAntiBan) {
            return 80 + sRng.nextInt(41); // [80, 120]
        }
        return 15;
    }

    // 单帧采集 + 识别（原函数体从 TimerTask.run 抽出，便于自调度复用）。
    private void runCaptureOnce() {
        lastCaptureTickAt = System.currentTimeMillis();
        if (!busy.compareAndSet(false, true)) {
            return;
        }
        Image image = null;
        try {
            // 防平台检测：仅当目标麻将 App 在前台才采帧；否则暂停识别，
            // 避免「本 App 在设置页 / 回到桌面」时仍持续扫描屏幕。
            if (cfgAntiDetect && !isTargetAppForeground()) {
                heartbeat("paused_foreground");
                return;
            }
            image = callback.get();
            if (image == null) {
                // 画面未更新（手机屏幕静止，系统虚拟显示不重复出帧）：
                // 自适应降频巡检，绝不发送清空界面的伪心跳状态，保留当前手牌与建议
                consecutiveSkips++;
                return;
            }
            framesAcquired++;
            processCapturedImage(image);
        } catch (Throwable t) {
            TimedLog.e(TAG, "处理帧时出错: " + t.toString());
            sendStatus(NetworkClient.statusJson("java_error", "处理帧出错: " + t));
        } finally {
            if (image != null) {
                try {
                    image.close();
                } catch (Exception ignore) {
                }
            }
            busy.set(false);
        }
    }

    private static volatile long sLastForegroundCheckTime = 0;
    private static volatile boolean sLastForegroundResult = true;

    // 防平台检测：判断当前是否「可识别」状态——即某个麻将 App 在前台。
    // 返回 true = 采帧；false = 暂停（前台是本 App 或桌面启动器）。
    // 无 Context / 无权限 / 取不到前台包名时一律返回 true（降级为常开，绝不阻断识别）。
    private static boolean isTargetAppForeground() {
        if (sContext == null) return true;
        if (!hasUsageStatsPermission()) return true;
        final long now = System.currentTimeMillis();
        if (now - sLastForegroundCheckTime < 1000) {
            return sLastForegroundResult;
        }
        sLastForegroundCheckTime = now;
        final UsageStatsManager usm =
                (UsageStatsManager) sContext.getSystemService(Context.USAGE_STATS_SERVICE);
        if (usm == null) {
            sLastForegroundResult = true;
            return true;
        }
        final List<UsageStats> stats =
                usm.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, now - 2000, now);
        if (stats == null || stats.isEmpty()) {
            sLastForegroundResult = true;
            return true;
        }
        String top = null;
        long last = 0;
        for (final UsageStats s : stats) {
            if (s.getLastTimeUsed() > last) {
                last = s.getLastTimeUsed();
                top = s.getPackageName();
            }
        }
        if (top == null) {
            sLastForegroundResult = true;
            return true;
        }
        // 暂停条件：前台是我们自己的 App（用户在设置页），或系统桌面启动器（没在打牌）。
        if (top.equals(sContext.getPackageName())) {
            sLastForegroundResult = false;
            return false;
        }
        if (isLauncher(top)) {
            sLastForegroundResult = false;
            return false;
        }
        sLastForegroundResult = true;
        return true;
    }

    // 是否拥有「使用情况访问」权限（PACKAGE_USAGE_STATS）。低于 LOLLIPOP 无此概念，默认放行。
    private static boolean hasUsageStatsPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.LOLLIPOP) return true;
        final AppOpsManager aom =
                (AppOpsManager) sContext.getSystemService(Context.APP_OPS_SERVICE);
        if (aom == null) return true;
        final int mode = aom.checkOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                Process.myUid(), sContext.getPackageName());
        return mode == AppOpsManager.MODE_ALLOWED;
    }

    // 给定包名是否为系统默认桌面启动器。用 resolveActivity 而非维护启动器名单，更稳健。
    private static boolean isLauncher(String pkg) {
        final Intent i = new Intent(Intent.ACTION_MAIN);
        i.addCategory(Intent.CATEGORY_HOME);
        final ResolveInfo ri = sContext.getPackageManager()
                .resolveActivity(i, PackageManager.MATCH_DEFAULT_ONLY);
        return ri != null && ri.activityInfo != null
                && pkg.equals(ri.activityInfo.packageName);
    }

    // ===== 采集存帧（风格库自举）=====

    // 帧存放目录：app 外部 files/frames/。用 getExternalFilesDir 无需额外存储权限，
    // 用户可用 adb pull /sdcard/Android/data/com.example.auto_vision/files/frames/ 取回。
    private static File framesDir() {
        if (sContext == null) return null;
        File dir = sContext.getExternalFilesDir(null);
        if (dir == null) return null;
        return new File(dir, "frames");
    }

    private static void clearFramesDir() {
        synchronized (sFramesLock) {
            File dir = framesDir();
            if (dir == null) return;
            File[] old = dir.listFiles();
            if (old != null) {
                for (File f : old) {
                    try { f.delete(); } catch (Exception ignore) { }
                }
            }
            // 重新创建，确保目录存在。
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
            framesDumped = 0;
            sLimitLogged = false; // 新一轮采集重新允许上限提示
            TimedLog.i(TAG, "帧采集目录已清空: " + dir.getAbsolutePath());
        }
    }

    // 牌河采集目录：app 外部 files/river_frames/（jpg + json 元数据成对）。
    private static File riverFramesDir() {
        if (sContext == null) return null;
        File dir = sContext.getExternalFilesDir(null);
        if (dir == null) return null;
        return new File(dir, "river_frames");
    }

    // 递归删除：采集目录下除顶层 jpg/json 外还有 hand_lowconf/ 子目录（低置信
    // 样本回收），非递归 delete() 对非空目录静默失败 → 只增不减的存储泄漏。
    private static void deleteRecursive(File f) {
        if (f == null) return;
        File[] cs = f.listFiles();
        if (cs != null) {
            for (File c : cs) deleteRecursive(c);
        }
        try { f.delete(); } catch (Exception ignore) { }
    }

    // 清空牌河采集目录（仅用户手动拨开「牌河采集」开关时触发，与 clear_frames
    // 同一防误删约定）。Python 引擎侧重置由 set_frame_dump_dir 被下一次清目录
    // 后的引擎重建完成；即使计数不同步也无害——文件名带时间戳不会碰撞。
    private static void clearRiverFramesDir() {
        synchronized (sFramesLock) {
            File dir = riverFramesDir();
            if (dir == null) return;
            File[] old = dir.listFiles();
            if (old != null) {
                for (File f : old) {
                    deleteRecursive(f);
                }
            }
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
            TimedLog.i(TAG, "牌河采集目录已清空: " + dir.getAbsolutePath());
        }
    }

    private static void dumpFrame(byte[] encoded) {
        if (sContext == null) return;
        synchronized (sFramesLock) {
            // 上限约 6000 帧（400ms 一帧 ≈ 40 分钟），防止无限增长撑爆存储。
            // 只提示一次，避免到达上限后每帧刷一条 log。
            if (framesDumped >= 6000) {
                if (!sLimitLogged) {
                    sLimitLogged = true;
                    TimedLog.i(TAG, "帧采集已达 6000 上限，忽略后续帧");
                }
                return;
            }
            try {
                File dir = framesDir();
                if (dir == null) return;
                if (!dir.exists()) {
                    //noinspection ResultOfMethodCallIgnored
                    dir.mkdirs();
                }
                File f = new File(dir, String.format(java.util.Locale.US,
                        "frame_%05d.jpg", framesDumped++));
                try (FileOutputStream fos = new FileOutputStream(f)) {
                    fos.write(encoded);
                }
            } catch (Throwable t) {
                TimedLog.e(TAG, "写帧失败: " + t);
            }
        }
    }

    public void stop() {
        if (timer != null) {
            timer.cancel();
            timer = null;
        }
        if (heartbeatTimer != null) {
            heartbeatTimer.cancel();
            heartbeatTimer = null;
        }
        if (client != null) {
            client.close();
        }
    }

    public void processCapturedImage(Image image) {
        // 端到端延迟测量的**原点**。System.currentTimeMillis() 与悬浮窗侧
        // DateTime.now().millisecondsSinceEpoch 都是设备 epoch 墙钟（同一时钟源，
        // 与时区无关），所以两个进程的数字可以直接相减得到帧龄，不需要任何
        // 跨进程时钟同步协议。帧龄只有在采集侧打点才可能算出来 —— Python 的
        // _proc_ms 只覆盖引擎一段，且只进 logcat，界面上永远读不到端到端。
        final long capturedAt = System.currentTimeMillis();
        // 运行时多层纵深安全巡检：一旦检测到非法 Hook/调试注入或凭证失效，主动熔断流水线。
        // 卡密核验走节流版：旧写法每帧做一次 binder IPC + SHA-256 + 正则（见 SecurityGuard
        // .verifyLicenseThrottled）；好结果缓存 2s，坏结果不缓存，所以过期/拉黑仍立即生效。
        if (!SecurityGuard.isSafe(sContext) || !SecurityGuard.verifyLicenseThrottled(sContext)) {
            sendStatus(NetworkClient.statusJson("security_alert", "安全防护已触发：未授权运行或检测到非法调试/篡改环境"));
            return;
        }

        final long tEncode0 = System.currentTimeMillis();
        byte[] encoded = ImageEncoder.encodeImageToByteArray(image);
        final long encodeMs = System.currentTimeMillis() - tEncode0;
        if (encoded == null || encoded.length == 0) {
            sendStatus(NetworkClient.statusJson("java_error", "帧编码失败（Bitmap为空）"));
            return;
        }

        // 采集存帧（风格库自举）：把引擎真正看到的原始帧 JPEG 落盘，供后续
        // harvest→cluster→标注重建该风格模板库。仅当调试页开关打开时执行。
        if (cfgDumpFrames) {
            dumpFrame(encoded);
        }

        // 把用户刚拖动的识别区域推给引擎（仅在变化时），默认整屏不退化。
        if (roiDirty && engine != null) {
            try {
                engine.callAttr("set_roi", roiTop, roiBottom);
                roiDirty = false;
            } catch (Throwable t) {
                TimedLog.e(TAG, "set_roi 推送失败（不影响本帧）: " + t);
            }
        }

        // 把悬浮窗「旋转」按钮设置的方向覆盖推给引擎（仅在变化时）。
        if (orientDirty && engine != null) {
            try {
                engine.callAttr("set_orient", orientOverride);
                orientDirty = false;
            } catch (Throwable t) {
                TimedLog.e(TAG, "set_orient 推送失败（不影响本帧）: " + t);
            }
        }

        // 把悬浮窗设置的定缺覆盖推给引擎（仅在变化时）。
        if (dingqueDirty && engine != null) {
            try {
                engine.callAttr("set_dingque_override", dingqueOverride);
                dingqueDirty = false;
            } catch (Throwable t) {
                TimedLog.e(TAG, "set_dingque_override 推送失败: " + t);
            }
        }

        // 处理新对局重置请求：瞬间清空牌池、手牌记忆，牌池恢复满额
        if (resetRequested && engine != null) {
            try {
                engine.callAttr("reset_match");
                resetRequested = false;
                TimedLog.i(TAG, "已成功调用 engine.reset_match() 重置对局");
            } catch (Throwable t) {
                TimedLog.e(TAG, "reset_match 调用失败: " + t);
            }
        }

        // 把待切换的玩法即时推给引擎（仅在变化时）
        if (pendingMode != null && engine != null) {
            try {
                engine.callAttr("set_mode", pendingMode);
                pendingMode = null;
            } catch (Throwable t) {
                TimedLog.e(TAG, "set_mode 推送失败: " + t);
            }
        }

        // 把调试页开关（自动旋转/冷启动/严格门槛/防封号/防平台检测）推给引擎（仅在变化时）。
        if (configDirty && engine != null) {
            try {
                engine.callAttr("set_config", "auto_orient", cfgAutoOrient);
                engine.callAttr("set_config", "bootstrap", cfgBootstrap);
                engine.callAttr("set_config", "strict", cfgStrict);
                engine.callAttr("set_config", "anti_ban", cfgAntiBan);
                engine.callAttr("set_config", "anti_detect", cfgAntiDetect);
                engine.callAttr("set_config", "dump_frames", cfgDumpFrames);
                engine.callAttr("set_config", "collect_river", cfgCollectRiver);
                engine.callAttr("set_config", "yolo_river", cfgYoloRiver);
                configDirty = false;
            } catch (Throwable t) {
                TimedLog.e(TAG, "set_config 推送失败（不影响本帧）: " + t);
            }
        }

        if (engine == null) {
            sendStatus(NetworkClient.statusJson("java_error", "引擎尚未初始化"));
            return;
        }

        PyObject engineResult;
        final long tEngine0 = System.currentTimeMillis();
        try {
            engineResult = engine.callAttr("process_bytes", encoded);
        } catch (Throwable t) {
            TimedLog.e(TAG, "process_bytes 调用失败: " + t);
            sendStatus(NetworkClient.statusJson("java_error", "识别调用失败: " + t));
            return;
        }
        final long engineMs = System.currentTimeMillis() - tEngine0;
        if (engineResult == null) {
            // Python 端现在保证连异常都返回错误结果（见 engine.py），
            // 走到这里说明链路有未预期的断点，上报而不是静默丢帧。
            sendStatus(NetworkClient.statusJson("py_error", "process_bytes 返回空"));
            return;
        }

        // 结果串**只解析一次**：一次拿到 native_ready / frame_skipped 两个布尔，
        // 并就地注入端到端时间戳（见 capturedAt 那条注释）。
        // 为什么不再用子串判据（旧写法）：
        //     pyJsonStr.contains("\"native_ready\": true")
        //     pyJsonStr.contains("\"frame_skipped\": true")
        // 那是把「序列化格式」当成语义契约，有两种真实失效方式：
        //   1) 有人把 json.dumps 改成分隔符紧凑形式（separators=(',',':')，正是压
        //      payload 体积的常规手段）→ 文本变成 "native_ready":true，子串永不命中：
        //      native 全线静默不接管、静默帧不再降频采集，而**没有任何精度测试会变红**；
        //   2) 任意文本字段（message/commentary）里出现同样的字面串 → 误接管，于是给
        //      带鬼牌的玩法用川麻口径覆写了 Python 已经算对的答案。
        // 读 JSON 里的布尔是与序列化格式无关的唯一口径。
        final long tStamp0 = System.currentTimeMillis();
        String pyJsonStr = pythonResultString(engineResult);
        org.json.JSONObject pyObj = null;
        try {
            final String trimmed = pyJsonStr.trim();
            if (trimmed.startsWith("{")) {
                pyObj = new org.json.JSONObject(trimmed);
            }
        } catch (Throwable t) {
            noteParseFailure(t);
        }
        // Python 的 json.dumps 默认 allow_nan=True，会写出 `NaN`/`Infinity` 这种**非标准**
        // JSON 字面量；org.json 把它们读成 Double.NaN 还是字符串 "NaN" 取决于 Android
        // 版本，而 JSONObject.toString() 又会把它们写成非法字面量或带引号的 "NaN"。
        // 本轮起**每一帧**都要过一遍重序列化（不只 native 帧），所以这种帧必须退回
        // 「原样转发 Python 出帧」：宁可本帧没有时间戳，也不把语义改了的结果上屏
        // （数字变字符串会让 Dart 侧的 `as num` 直接抛异常，整帧建议消失）。
        if (pyObj != null && hasNonFiniteValue(pyObj)) {
            noteNonFiniteFrame();
            pyObj = null;
        }

        byte[] bytes;
        boolean frameSkipped = false;
        if (pyObj == null) {
            // 降级：这一帧没有时间戳、不走 native，但**绝不丢帧** —— 面板跟帧比多一个
            // 观测字段重要得多；统计侧会因缺键自动跳过该帧，不污染分位数。
            bytes = engineResult.callAttr("to_bytes").toJava(byte[].class);
        } else {
            frameSkipped = pyObj.optBoolean("frame_skipped", false);
            try {
                // native 是否接管由 Python 侧按玩法能力下发（modes.native_solver_ready）。
                // 旧写法是 mode.startsWith("sc")，而这里的 mode 取自 pendingMode —— 它在
                // 本帧开头推给 engine 后就立刻被置 null，所以恒为兜底值 "sc"：等于对**全部
                // 玩法**（含带红中/白板鬼牌的血流红中、贵阳捉鸡、杭州百搭）都用川麻口径
                // 覆写 Python 的 shanten/advice/hand，且 parseMpszToTiles 会把手牌里所有
                // 字牌静默丢弃。
                if (NativeEngine.isAvailable() && pyObj.optBoolean("native_ready", false)) {
                    applyNativeSolver(pyObj);
                }
                // 时间戳在 native 覆写之后注入：两条出帧路径带同一组键。
                pyObj.put("captured_at_ms", capturedAt);
                pyObj.put("encode_ms", encodeMs);
                pyObj.put("engine_ms", engineMs);
                bytes = (pyObj.toString() + "\n").getBytes(java.nio.charset.StandardCharsets.UTF_8);
            } catch (Throwable t) {
                // pyObj 可能已被 native 改到一半（put 了几个键），半覆写的结果比「不接管」
                // 更糟：C++ 的部分状态会和 Python 的部分状态拼成一帧两边都不成立的建议。
                // 所以这里必须回退到 Python 的原始出帧（不带时间戳，但语义完整）。
                TimedLog.e(TAG, "NativeEngine integration failed, fallback to python: " + t);
                bytes = engineResult.callAttr("to_bytes").toJava(byte[].class);
            }
        }

        // 动态更新巡检降频周期：静默帧（frame_skipped）累计到阈值后把采集间隔从活跃档
        // 抬到巡检档，省电也降低平台侧的持续截图特征。
        if (frameSkipped) {
            consecutiveSkips++;
        } else {
            consecutiveSkips = 0;
        }
        stampCost(System.currentTimeMillis() - tStamp0);

        if (client.send(bytes)) {
            framesProcessed++;
        } else {
            sendFailures++;
            // 悬浮窗还没把 socket 监听起来（启动竞态）或监听挂了。
            // 心跳里带 send_fail 计数，界面上可见。
            heartbeat("send_error");
        }
    }

    /**
     * native(C++) 求解器接管本帧：就地改写 pyObj。
     *
     * 不接管的情形都原样返回，pyObj 保持 Python 口径：玩法没有 native 求解器
     * （调用方按 native_ready 判）、手牌里混进 native 表达不了的牌、native 没给出结果。
     * 中途抛异常由调用方回退到 Python 原始出帧（半覆写的对象不可信）。
     */
    private static void applyNativeSolver(org.json.JSONObject pyObj)
            throws org.json.JSONException {
        String handStr = pyObj.optString("hand", "");
        int dqSuit = pyObj.isNull("dingque_suit") ? -1 : pyObj.optInt("dingque_suit", -1);
        boolean isSwap = pyObj.optBoolean("swap_phase", false);
        boolean isDq = pyObj.optBoolean("dingque_phase", false);
        String pyStatus = pyObj.optString("status", "");
        boolean isTable = !"waiting".equals(pyStatus) || !handStr.isEmpty();

        int[] handTiles = parseMpszToTiles(handStr);
        // MPSZ 是定长两字符一码，native 只认 m/p/s：一旦解出来的张数
        // 对不上串长，说明手牌里混进了 native 表达不了的牌（字牌/鬼牌）
        // 或识别串残缺。这种帧绝不接管 —— 少喂一张牌算出的向听是错的，
        // 而它的结果会覆写 Python 已经算对的正确答案。
        boolean handMappable = handTiles.length * 2 == handStr.length();
        if (!handMappable) {
            TimedLog.e(TAG, "手牌含 native 无法映射的牌张，本帧回退 Python: " + handStr);
            return;
        }

        org.json.JSONArray opDqArr = pyObj.optJSONArray("opponents_dingque");
        if (opDqArr != null) {
            for (int i = 0; i < opDqArr.length(); ++i) {
                int s = opDqArr.optInt(i, -1);
                if (s >= 0 && s <= 2) {
                    NativeEngine.setOpponentDingque(i + 1, s);
                }
            }
        }

        String nativeJsonStr = NativeEngine.evaluate(handTiles, dqSuit, isSwap, isDq, isTable);
        if (nativeJsonStr == null || nativeJsonStr.isEmpty() || !nativeJsonStr.startsWith("{")) {
            return;
        }
        org.json.JSONObject nativeObj = new org.json.JSONObject(nativeJsonStr);
        pyObj.put("native_active", true);
        pyObj.put("state", nativeObj.optString("state", "playing"));
        pyObj.put("status", nativeObj.optString("status", pyStatus));
        pyObj.put("shanten", nativeObj.optInt("shanten", 0));
        pyObj.put("remaining", nativeObj.optInt("remaining", 108));
        if (nativeObj.has("remaining_matrix")) {
            pyObj.put("remaining_matrix", nativeObj.optJSONObject("remaining_matrix"));
        }
        if (nativeObj.has("mood")) {
            pyObj.put("mood", nativeObj.optJSONObject("mood"));
        }
        if (nativeObj.has("win_equity")) {
            pyObj.put("win_equity", nativeObj.optDouble("win_equity", 0.5));
        }
        if (nativeObj.has("ev_gauge")) {
            pyObj.put("ev_gauge", nativeObj.optJSONObject("ev_gauge"));
        }
        if (nativeObj.has("hand_ranges")) {
            pyObj.put("hand_ranges", nativeObj.optJSONArray("hand_ranges"));
        }
        if (nativeObj.has("danger_flow")) {
            pyObj.put("danger_flow", nativeObj.optJSONObject("danger_flow"));
        }
        pyObj.put("inferred_discard", nativeObj.optInt("inferred_discard", -1));

        org.json.JSONArray nativeAdvice = nativeObj.optJSONArray("advice");
        if (nativeAdvice != null) {
            pyObj.put("advice", nativeAdvice);
            pyObj.put("best", nativeObj.optString("best", ""));
        }
        if (nativeObj.has("hand")) {
            pyObj.put("hand", nativeObj.optString("hand", ""));
            pyObj.put("count", nativeObj.optInt("count", 0));
        }
    }

    // ===== 心跳与状态上报 =====

    private void heartbeat(String status) {
        long now = System.currentTimeMillis();
        if (now - lastHeartbeatAt < 2000) {
            return;
        }
        lastHeartbeatAt = now;
        sendStatus(withHeartbeatCounters(NetworkClient.statusJson(status, null)));
    }

    // 固定心跳 Timer 专用：不受 lastHeartbeatAt 节流（它本身就是 2s 节奏），
    // 带同样的采集计数，悬浮窗据此区分"画面静止"与"链路死亡"。
    private void sendHeartbeatJson(String status, String message) {
        sendStatus(withHeartbeatCounters(NetworkClient.statusJson(status, message)));
    }

    /**
     * 心跳帧统一附加采集/处理/发送计数，界面能区分"画面断了"和"识别断了"。
     * send_fail 取异步 writer 线程累计的真实 socket 失败数（send() 已改非阻塞入队）。
     * stamp_avg_ms 是「解析结果 + 注入时间戳」这一笔的真实开销均值：本轮为了
     * 告别子串判据而改成每帧解析一次 JSON，这笔代价不该靠拍脑袋宣称很小，
     * 而是让它在真机上自己读数。parse_fail = JSON 解析失败的帧数，non_finite =
     * 因含 NaN/Infinity 而退回原始出帧的帧数；两者都是「本帧没有时间戳」的已知来源，
     * 所以悬浮窗侧帧龄样本的缺口可以直接被这两个数解释，不需要猜。
     *
     * statusJson 产出必以 '}' 结尾，去尾拼接合法；不满足该假设时**原样返回**而不是
     * 抛 StringIndexOutOfBounds —— 心跳也在 send_error 路径上被调用，一个拼接异常
     * 会把整帧处理连带打断（可观测性设施绝不能反过来伤到主链路）。
     */
    private String withHeartbeatCounters(String json) {
        if (json == null || json.length() < 2 || !json.endsWith("}")) {
            TimedLog.e(TAG, "心跳拼接跳过：statusJson 产出不是完整对象");
            return json;
        }
        long n = sStampFrames;
        long avg = n > 0 ? sStampMsSum / n : 0;
        return json.substring(0, json.length() - 1)
                + ",\"frames\":" + framesAcquired
                + ",\"proc\":" + framesProcessed
                + ",\"send_fail\":" + client.getSendErrors()
                + ",\"parse_fail\":" + sParseFailures
                + ",\"non_finite\":" + sNonFiniteFrames
                + ",\"stamp_avg_ms\":" + avg + "}";
    }

    /**
     * 解析失败只落第一次与之后每 300 次一行日志，真实次数走心跳计数。
     * 什么情形会走到这里：Python 输出了非法 JSON（典型是 json.dumps 默认允许的
     * NaN/Infinity），或结果串被截断。降级方向是「原样转发 Python 出帧」，
     * 不拿半解析的结果上屏。
     */
    private void noteParseFailure(Throwable t) {
        final long n = sParseFailures + 1;
        sParseFailures = n;
        if (n == 1 || n % 300 == 0) {
            TimedLog.e(TAG, "解析 python 结果 JSON 失败（累计 " + n
                    + " 次），本帧原样转发: " + t);
        }
    }

    /**
     * 结果含 NaN/Infinity 的帧计数（节流日志，理由同上）。这类帧不走重序列化，
     * 因而既不注入时间戳也不让 native 接管 —— 必须能从心跳读数里看到它在不在发生。
     */
    private void noteNonFiniteFrame() {
        final long n = sNonFiniteFrames + 1;
        sNonFiniteFrames = n;
        if (n == 1 || n % 300 == 0) {
            TimedLog.e(TAG, "python 结果含 NaN/Infinity（累计 " + n
                    + " 次），本帧不走重序列化，原样转发");
        }
    }

    /**
     * 累计「解析 + 时间戳注入」开销。只有采集线程写（busy CAS 保证单飞），
     * 心跳线程读 → 两个字段必须 volatile：long 的非原子写会撕裂，普通字段
     * 还会让心跳线程长期读到旧值。
     *
     * 唯一的写-写重叠窗口是 restart 竞态（旧 Timer 上最后一帧还在飞、新 Timer
     * 已开跑）：那最坏只丢一次统计自增（一个均值样本），不丢牌面数据也不影响
     * 建议内容，因此不值得为此上 AtomicLong。
     */
    private static void stampCost(long ms) {
        if (ms < 0 || ms > 60_000) {
            // 墙钟回拨（NTP 校时/用户改时间）会让差值变负或荒谬，直接丢弃这一样本，
            // 绝不把噪声写进统计 —— 一个假的均值比没有均值更坏。
            return;
        }
        sStampMsSum = sStampMsSum + ms;
        sStampFrames = sStampFrames + 1;
    }

    private void sendStatus(String json) {
        try {
            client.sendStatus(json);
        } catch (Throwable t) {
            TimedLog.e(TAG, "sendStatus failed: " + t);
        }
    }

    /**
     * 结果里是否含 NaN/Infinity（含嵌套对象与数组）。见调用处注释。
     * 递归不设深度上限：载荷由我们自己生成，嵌套不超过三层，而一次递归扫描的
     * 开销远小于刚刚那次 JSON 解析；反过来若中途放弃，就等于把没扫到的分支
     * 默认判为安全 —— 那是比漏判更坏的口径。
     */
    private static boolean hasNonFiniteValue(Object v) {
        if (v instanceof Number) {
            double d = ((Number) v).doubleValue();
            return Double.isNaN(d) || Double.isInfinite(d);
        }
        if (v instanceof String) {
            // org.json 在部分 Android 版本上把非标准字面量读成字符串，这三种是它的全部产出。
            String s = (String) v;
            return s.equals("NaN") || s.equals("Infinity") || s.equals("-Infinity");
        }
        if (v instanceof org.json.JSONObject) {
            org.json.JSONObject o = (org.json.JSONObject) v;
            java.util.Iterator<String> it = o.keys();
            while (it.hasNext()) {
                if (hasNonFiniteValue(o.opt(it.next()))) {
                    return true;
                }
            }
            return false;
        }
        if (v instanceof org.json.JSONArray) {
            org.json.JSONArray a = (org.json.JSONArray) v;
            for (int i = 0; i < a.length(); ++i) {
                if (hasNonFiniteValue(a.opt(i))) {
                    return true;
                }
            }
        }
        return false;
    }

    /** 取 Python 结果里的 result 字段（JSON 串）；任何异常都退化成空串，即不走 native。 */
    private static String pythonResultString(PyObject engineResult) {
        try {
            PyObject r = engineResult.get("result");
            return (r != null) ? r.toString() : "";
        } catch (Throwable t) {
            TimedLog.e(TAG, "读取 python result 字段失败，本帧不走 native: " + t);
            return "";
        }
    }

    public static int[] parseMpszToTiles(String mpsz) {
        if (mpsz == null || mpsz.length() < 2) {
            return new int[0];
        }
        java.util.List<Integer> list = new java.util.ArrayList<>();
        for (int i = 0; i < mpsz.length() - 1; i += 2) {
            char numChar = mpsz.charAt(i);
            char suitChar = mpsz.charAt(i + 1);
            if (numChar >= '1' && numChar <= '9') {
                int num = numChar - '1';
                int offset = -1;
                if (suitChar == 'm') offset = 0;
                else if (suitChar == 'p') offset = 9;
                else if (suitChar == 's') offset = 18;
                if (offset >= 0) {
                    list.add(offset + num);
                }
            }
        }
        int[] arr = new int[list.size()];
        for (int i = 0; i < list.size(); i++) {
            arr[i] = list.get(i);
        }
        return arr;
    }
}
