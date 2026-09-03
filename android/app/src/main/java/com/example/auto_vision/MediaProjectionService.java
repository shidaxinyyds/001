package com.example.auto_vision;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import com.example.auto_vision.R;
import androidx.core.app.NotificationCompat;

public class MediaProjectionService extends Service {

    // public int onStartCommand(Intent intent, int flags, int startId) {
    //     // Start the service in the foreground
    //     startForeground(1, new Notification()); // You may customize the notification
    //     // Your service logic here
    //     return START_STICKY;
    // }

    private static final String TAG = "MediaProjectionService";

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String channelId = createNotificationChannel();

        // 服务被系统重启时 intent 可能为 null，直接用会 NPE
        if (intent != null) {
            try {
                PendingIntent pendingIntent = PendingIntent.getActivity(
                        this, 0, intent, PendingIntent.FLAG_MUTABLE);
                mPendingIntent = pendingIntent;
            } catch (Exception e) {
                TimedLog.e(TAG, "创建 PendingIntent 失败: " + e);
            }
        }

        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, channelId)
                .setContentTitle("正在识别牌局…")
                .setContentText("用于在屏幕上分析麻将牌")
                .setSmallIcon(R.drawable.stream)
                .setOngoing(true)
                .setPriority(Notification.PRIORITY_MAX)
                .setCategory(NotificationCompat.CATEGORY_SERVICE);

        if (mPendingIntent != null) {
            builder.setContentIntent(mPendingIntent);
        }

        // Android 14(API 34) 上，若缺少 FOREGROUND_SERVICE_MEDIA_PROJECTION 权限，
        // 或 startForeground 之前没有先调用 createScreenCaptureIntent()，
        // startForeground 会抛 SecurityException。这里兜底，避免整个进程被拖垮
        // （服务起不来最多是没有常驻通知，录屏识别仍由 MediaProjection 正常驱动）。
        try {
            startForeground(1, builder.build());
        } catch (Throwable t) {
            TimedLog.e(TAG, "startForeground 失败（前台通知不可用，不影响识别）: " + t);
        }

        return super.onStartCommand(intent, flags, startId);
    }

    private PendingIntent mPendingIntent;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private String createNotificationChannel() {
        String channelId = "Default";
        String channelName = "前台通知";
        NotificationChannel channel = new NotificationChannel(channelId, channelName, NotificationManager.IMPORTANCE_HIGH);
        NotificationManager manager = getSystemService(NotificationManager.class);

        manager.createNotificationChannel(channel);
        return channelId;
    }

    @Override
    public void onDestroy() {
        stopForeground(true);
        stopSelf();

        super.onDestroy();
    }

}
