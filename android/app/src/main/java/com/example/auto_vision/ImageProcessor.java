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
    private static float roiTop = 0f;
    private static float roiBottom = 1f;
    private static boolean roiDirty = true;

    // ===== 手动方向覆盖（悬浮窗「旋转」按钮）=====
    // 经 MainActivity 的 setOrient 通道写入；下一帧处理前推给 Python 引擎
    // （set_orient），引擎按指定角度旋转后再识别。默认 -1 = 未设置（走自动探测）。
    private static int orientOverride = -1;
    private static boolean orientDirty = false;

    // ===== 防封号 / 防平台检测（调试页开关，经 setConfig 写入，采集循环读取）=====
    // anti_ban：截屏节奏随机抖动（350–550ms）+ 建议延迟显示，避免固定节奏的 bot 特征。
    // anti_detect：仅在目标麻将 App 前台时采帧，切回本 App/桌面自动暂停。
    // 两者默认关闭，打开才改变采集行为；缺权限/缺 Context 时自动降级为常开。
    private static boolean cfgAntiBan = false;
    private static boolean cfgAntiDetect = false;
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
    private static Context sContext = null;
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

    private static int dingqueOverride = -1;
    private static boolean dingqueDirty = false;

    public static void setDingque(int suit) {
        if (suit >= 0 && suit <= 2) {
            dingqueOverride = suit;
        } else {
            dingqueOverride = -1;
        }
        dingqueDirty = true;
    }

    private static volatile boolean resetRequested = false;

    public static void resetMatch() {
        resetRequested = true;
        orientOverride = -1;
        orientDirty = true;
    }

    // 调试页开关：经 MainActivity 的 setConfig 通道写入，下一帧处理前推给 Python 引擎。
    // 用独立布尔而非 Map，避免额外的 import 与 Chaquopy 类型转换摩擦。
    private static boolean cfgAutoOrient = true;
    private static boolean cfgBootstrap = true;
    private static boolean cfgStrict = true;
    private static boolean configDirty = false;

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
    private long framesAcquired = 0;
    private long framesProcessed = 0;
    private long sendFailures = 0;
    private long lastHeartbeatAt = 0;

    public ImageProcessor(Supplier<Image> callback) {
        this.callback = callback;
    }

    public boolean prepare(Context context) {
        // 缓存 ApplicationContext 供前台检测使用（避免持有 Activity 导致泄漏）。
        if (context != null) {
            sContext = context.getApplicationContext();
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
        } catch (Throwable t) {
            TimedLog.e(TAG, "配置目录/模式推入失败（不影响基础运行）: " + t);
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

    // 单帧处理（Python 推理）可能超过 500ms 的采集间隔。
    // Timer 只有一个工作线程，不加保护的话任务会无限堆积，
    // 队列越排越长、结果永远滞后，表现为"识别卡死"。
    // 这里保证同一时刻只处理一帧，处理不完就直接跳过下一帧。
    private final java.util.concurrent.atomic.AtomicBoolean busy =
            new java.util.concurrent.atomic.AtomicBoolean(false);

    public void start() {
        timer = new Timer();
        lastCaptureTickAt = System.currentTimeMillis();
        // 真固定 2s 心跳：无论采集循环是否在跑，每 2s 必发一帧状态；
        // 采集线程自己卡死（超 6s 没路过）时改报 pipeline_stalled。
        heartbeatTimer = new Timer("heartbeat", true);
        heartbeatTimer.schedule(new TimerTask() {
            public void run() {
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
    // 活跃态（有摸牌/出牌动作）：25ms 毫秒级极速跟帧，保证摸打建议瞬时呈现；
    // 防封号开启：在此基础上叠加轻微拟人抖动。
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
        byte[] encoded = ImageEncoder.encodeImageToByteArray(image);
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
        try {
            engineResult = engine.callAttr("process_bytes", encoded);
        } catch (Throwable t) {
            TimedLog.e(TAG, "process_bytes 调用失败: " + t);
            sendStatus(NetworkClient.statusJson("java_error", "识别调用失败: " + t));
            return;
        }
        if (engineResult == null) {
            // Python 端现在保证连异常都返回错误结果（见 engine.py），
            // 走到这里说明链路有未预期的断点，上报而不是静默丢帧。
            sendStatus(NetworkClient.statusJson("py_error", "process_bytes 返回空"));
            return;
        }

        byte[] bytes = engineResult.callAttr("to_bytes").toJava(byte[].class);

        // 提取是否跳帧状态，动态更新巡检降频周期
        try {
            PyObject resObj = engineResult.get("result");
            if (resObj != null) {
                String resStr = resObj.toString();
                if (resStr.contains("\"frame_skipped\": true")) {
                    consecutiveSkips++;
                } else {
                    consecutiveSkips = 0;
                }
            }
        } catch (Throwable ignore) {
            consecutiveSkips = 0;
        }

        if (client.send(bytes)) {
            framesProcessed++;
        } else {
            sendFailures++;
            // 悬浮窗还没把 socket 监听起来（启动竞态）或监听挂了。
            // 心跳里带 send_fail 计数，界面上可见。
            heartbeat("send_error");
        }
    }

    // ===== 心跳与状态上报 =====

    private void heartbeat(String status) {
        long now = System.currentTimeMillis();
        if (now - lastHeartbeatAt < 2000) {
            return;
        }
        lastHeartbeatAt = now;
        String json = NetworkClient.statusJson(status, null);
        // 注入采集/处理/发送计数，界面能区分"画面断了"和"识别断了"。
        // send_fail 取异步 writer 线程累计的真实 socket 失败数（send() 已改非阻塞入队）。
        json = json.substring(0, json.length() - 1)
                + ",\"frames\":" + framesAcquired
                + ",\"proc\":" + framesProcessed
                + ",\"send_fail\":" + client.getSendErrors() + "}";
        sendStatus(json);
    }

    // 固定心跳 Timer 专用：不受 lastHeartbeatAt 节流（它本身就是 2s 节奏），
    // 带同样的采集计数，悬浮窗据此区分"画面静止"与"链路死亡"。
    private void sendHeartbeatJson(String status, String message) {
        String json = NetworkClient.statusJson(status, message);
        json = json.substring(0, json.length() - 1)
                + ",\"frames\":" + framesAcquired
                + ",\"proc\":" + framesProcessed
                + ",\"send_fail\":" + client.getSendErrors() + "}";
        sendStatus(json);
    }

    private void sendStatus(String json) {
        try {
            client.sendStatus(json);
        } catch (Throwable t) {
            TimedLog.e(TAG, "sendStatus failed: " + t);
        }
    }
}
