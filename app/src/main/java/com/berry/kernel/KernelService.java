package com.berry.kernel;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import androidx.core.app.NotificationCompat;

import java.io.BufferedReader;
import java.io.InputStreamReader;

public class KernelService extends Service {

    private static final String TAG = "KernelService";
    private static final String CHANNEL_ID = "kernel_service_channel";
    private static final int NOTIFICATION_ID = 1001;

    private Process process;

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String binaryPath = intent.getStringExtra("binary_path");
        if (binaryPath == null) {
            binaryPath = getApplicationInfo().nativeLibraryDir + "/libberry.so";
        }

        Notification notification = new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("BerryKernel")
                .setContentText("服务运行中")
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setOngoing(true)
                .build();

        startForeground(NOTIFICATION_ID, notification);

        startBinary(binaryPath);

        return START_STICKY;
    }

    private void startBinary(String binaryPath) {
        try {
            File binary = new File(binaryPath);
            if (!binary.exists()) {
                Log.e(TAG, "Binary not found: " + binaryPath);
                return;
            }
            binary.setExecutable(true, false);

            ProcessBuilder pb = new ProcessBuilder(binaryPath);
            pb.redirectErrorStream(true);
            process = pb.start();

            new Thread(() -> {
                try {
                    BufferedReader reader = new BufferedReader(
                            new InputStreamReader(process.getInputStream()));
                    String line;
                    while ((line = reader.readLine()) != null) {
                        Log.i(TAG, line);
                    }
                } catch (Exception e) {
                    Log.e(TAG, "Read error: " + e.getMessage());
                }
            }).start();

            Log.i(TAG, "Binary started: " + binaryPath);
        } catch (Exception e) {
            Log.e(TAG, "Failed to start binary: " + e.getMessage());
        }
    }

    @Override
    public void onDestroy() {
        if (process != null) {
            process.destroy();
            process = null;
        }
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
