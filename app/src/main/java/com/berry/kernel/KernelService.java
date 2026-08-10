package com.berry.kernel;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.IBinder;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.Display;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.WindowManager;

import androidx.core.app.NotificationCompat;

public class KernelService extends Service {

    private static final String TAG = "KernelService";
    private static final String CHANNEL_ID = "kernel_service_channel";
    private static final int NOTIFICATION_ID = 1001;

    private WindowManager windowManager;
    private SurfaceView surfaceView;
    private boolean isRunning = false;
    private int screenWidth, screenHeight, screenRotation;

    // JNI 方法声明
    private native boolean nativeInit(Surface surface, int width, int height, int orientation);
    private native void nativeStop();

    static {
        System.loadLibrary("berry");
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (isRunning) {
            Log.i(TAG, "服务已在运行");
            return START_STICKY;
        }

        Notification notification = new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("BerryKernel")
                .setContentText("服务运行中")
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setOngoing(true)
                .build();

        startForeground(NOTIFICATION_ID, notification);

        createOverlay();
        isRunning = true;

        return START_STICKY;
    }

    private void createOverlay() {
        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);

        // 获取屏幕尺寸和旋转角度
        Display display = windowManager.getDefaultDisplay();
        DisplayMetrics metrics = new DisplayMetrics();
        display.getRealMetrics(metrics);
        screenWidth = metrics.widthPixels;
        screenHeight = metrics.heightPixels;
        screenRotation = display.getRotation();

        Log.i(TAG, "屏幕: " + screenWidth + "x" + screenHeight + " rotation=" + screenRotation);

        // 创建 SurfaceView 用于 EGL 渲染
        surfaceView = new SurfaceView(this);
        surfaceView.setZOrderOnTop(true);
        surfaceView.getHolder().setFormat(PixelFormat.TRANSLUCENT);

        surfaceView.getHolder().addCallback(new SurfaceHolder.Callback() {
            @Override
            public void surfaceCreated(SurfaceHolder holder) {
                Surface surface = holder.getSurface();
                Log.i(TAG, "Surface 创建，调用 nativeInit");
                boolean ok = nativeInit(surface, screenWidth, screenHeight, screenRotation);
                Log.i(TAG, "nativeInit 结果: " + ok);
            }

            @Override
            public void surfaceChanged(SurfaceHolder holder, int format, int width, int height) {
            }

            @Override
            public void surfaceDestroyed(SurfaceHolder holder) {
                Log.i(TAG, "Surface 销毁，调用 nativeStop");
                nativeStop();
            }
        });

        // 悬浮窗参数
        int layoutType;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            layoutType = WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY;
        } else {
            layoutType = WindowManager.LayoutParams.TYPE_SYSTEM_OVERLAY;
        }

        WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                layoutType,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE |
                        WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE |
                        WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS |
                        WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT
        );

        windowManager.addView(surfaceView, params);
        Log.i(TAG, "悬浮窗已添加");
    }

    @Override
    public void onDestroy() {
        Log.i(TAG, "onDestroy");
        if (surfaceView != null && windowManager != null) {
            try {
                windowManager.removeView(surfaceView);
            } catch (Exception e) {
                Log.e(TAG, "移除悬浮窗失败: " + e.getMessage());
            }
            surfaceView = null;
        }
        nativeStop();
        isRunning = false;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "Kernel Service",
                    NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription("Kernel service notification");
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                manager.createNotificationChannel(channel);
            }
        }
    }
}
