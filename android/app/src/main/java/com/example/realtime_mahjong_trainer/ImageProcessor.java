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

    private NetworkClient client = new NetworkClient("127.0.0.1", 55555);
    private NetworkClient debugClient = new NetworkClient("192.168.1.1", 55555);

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

    public void start() {
        timer = new Timer();
        timer.schedule( new TimerTask() {
            public void run() {
                Image image = callback.get();
                if (image == null) {
                    return;
                }
                processCapturedImage(image);
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
        image.close();
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

        bytes = engineResult.callAttr("dumps").toJava(byte[].class);
//        bytes = Arrays.copyOfRange(bytes, 0, 1000);
        TimedLog.i(TAG, "Sending bytes to debug server:" + bytes.length);
        debugClient.send(bytes);

        return;
    }
}
