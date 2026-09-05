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
            default: return;
        }
        configDirty = true;
    }

    private Timer timer;

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
        sendStatus(NetworkClient.statusJson("engine_ready", null));
        return true;
    }

    // 单帧处理（Python 推理）可能超过 500ms 的采集间隔。
    // Timer 只有一个工作线程，不加保护的话任务会无限堆积，
    // 队列越排越长、结果永远滞后，表现为"识别卡死"。
    // 这里保证同一时刻只处理一帧，处理不完就直接跳过下一帧。
    private final java.util.concurrent.atomic.AtomicBoolean busy =
            new java.util.concurrent.atomic.AtomicBoolean(false);

    public void start() {
        timer = new Timer();
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

    // 下一帧采集间隔。
    // 防封号开启：350–550ms 随机抖动（拟人节奏，避免固定节奏的 bot 特征）；
    // 否则固定 400ms，行为与原先完全一致。
    private long captureDelayMs() {
        if (cfgAntiBan) {
            return 350 + sRng.nextInt(201); // [350, 550]
        }
        return 400;
    }

    // 单帧采集 + 识别（原函数体从 TimerTask.run 抽出，便于自调度复用）。
    private void runCaptureOnce() {
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
                // 收不到画面：屏幕静止（虚拟显示不重复出帧），
                // 或者 VirtualDisplay 建流失败/会话被系统结束。
                // 以前这里直接 return，一旦采集挂掉就是永久静默。
                heartbeat("no_frames");
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

    // 防平台检测：判断当前是否「可识别」状态——即某个麻将 App 在前台。
    // 返回 true = 采帧；false = 暂停（前台是本 App 或桌面启动器）。
    // 无 Context / 无权限 / 取不到前台包名时一律返回 true（降级为常开，绝不阻断识别）。
    private static boolean isTargetAppForeground() {
        if (sContext == null) return true;
        if (!hasUsageStatsPermission()) return true;
        final long now = System.currentTimeMillis();
        final UsageStatsManager usm =
                (UsageStatsManager) sContext.getSystemService(Context.USAGE_STATS_SERVICE);
        if (usm == null) return true;
        final List<UsageStats> stats =
                usm.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, now - 2000, now);
        if (stats == null || stats.isEmpty()) return true;
        String top = null;
        long last = 0;
        for (final UsageStats s : stats) {
            if (s.getLastTimeUsed() > last) {
                last = s.getLastTimeUsed();
                top = s.getPackageName();
            }
        }
        if (top == null) return true;
        // 暂停条件：前台是我们自己的 App（用户在设置页），或系统桌面启动器（没在打牌）。
        if (top.equals(sContext.getPackageName())) return false;
        if (isLauncher(top)) return false;
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

    public void stop() {
        if (timer != null) {
            timer.cancel();
            timer = null;
        }
    }

    public void processCapturedImage(Image image) {
        byte[] encoded = ImageEncoder.encodeImageToByteArray(image);
        if (encoded == null || encoded.length == 0) {
            sendStatus(NetworkClient.statusJson("java_error", "帧编码失败（Bitmap为空）"));
            return;
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

        // 把调试页开关（自动旋转/冷启动/严格门槛/防封号/防平台检测）推给引擎（仅在变化时）。
        if (configDirty && engine != null) {
            try {
                engine.callAttr("set_config", "auto_orient", cfgAutoOrient);
                engine.callAttr("set_config", "bootstrap", cfgBootstrap);
                engine.callAttr("set_config", "strict", cfgStrict);
                engine.callAttr("set_config", "anti_ban", cfgAntiBan);
                engine.callAttr("set_config", "anti_detect", cfgAntiDetect);
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
        // 注入采集/处理/发送计数，界面能区分"画面断了"和"识别断了"
        json = json.substring(0, json.length() - 1)
                + ",\"frames\":" + framesAcquired
                + ",\"proc\":" + framesProcessed
                + ",\"send_fail\":" + sendFailures + "}";
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
