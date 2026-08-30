package com.example.realtime_mahjong_trainer;


import android.content.Context;
import android.media.Image;

import java.util.ArrayList;
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

    public ImageProcessor(Supplier<Image> callback) {
        this.callback = callback;
    }

    void prepare(Context context) {
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(context));
        }
        Python python = Python.getInstance();
        try {
            engine = python.getModule("engine").get("Engine").callThrows();
        } catch (Throwable e) {
            TimedLog.i(TAG, e.toString());
            e.printStackTrace();
            throw new RuntimeException();
        }
        TimedLog.i(TAG, "started Python");
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
                    TimedLog.i(TAG, "上一帧尚未处理完，跳过本帧");
                    return;
                }
                Image image = null;
                try {
                    image = callback.get();
                    if (image == null) {
                        return;
                    }
                    processCapturedImage(image);
                } catch (Throwable t) {
                    TimedLog.e(TAG, "处理帧时出错: " + t.toString());
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
        }, 0, 500);
    }

    public void stop() {
        timer.cancel();
        timer = null;
    }

    public void processCapturedImage(Image image) {
        TimedLog.i(TAG, "Got a new image, encoding...");
        byte[] encoded = ImageEncoder.encodeImageToByteArray(image);
        // 注意：Image 的关闭统一由 start() 里的 finally 负责，避免重复 close
        TimedLog.i(TAG, String.format("Length of encoded: %d, begin python processing...", encoded.length));

        byte[] bytes;
        PyObject engineResult = engine.callAttr("process_bytes", encoded);
        TimedLog.i(TAG, String.format("Done python processing"));

        if (engineResult == null) {
            return;
        }

        bytes = engineResult.callAttr("to_bytes").toJava(byte[].class);
        TimedLog.i(TAG, "Sending bytes to localhost:" + bytes.length);
        client.send(bytes);

        return;
    }
}
