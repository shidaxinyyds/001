package com.example.realtime_mahjong_trainer;


import android.content.Context;
import android.media.Image;

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
        timer.schedule( new TimerTask() {
            public void run() {
                if (!busy.compareAndSet(false, true)) {
                    return;
                }
                Image image = null;
                try {
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
        }, 0, 800);
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

        PyObject engineResult = engine.callAttr("process_bytes", encoded);
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
