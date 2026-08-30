package com.example.realtime_mahjong_trainer;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import com.example.realtime_mahjong_trainer.R;
import androidx.core.app.NotificationCompat;

public class MediaProjectionService extends Service {

    // public int onStartCommand(Intent intent, int flags, int startId) {
    //     // Start the service in the foreground
    //     startForeground(1, new Notification()); // You may customize the notification
    //     // Your service logic here
    //     return START_STICKY;
    // }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String channelId = createNotificationChannel();

        PendingIntent pendingIntent = PendingIntent.getActivity(this, 0, intent,  PendingIntent.FLAG_MUTABLE);

        //NotificationCompat.Builder notificationBuilder = new NotificationCompat.Builder(this, channelId);
        //Notification notification = notificationBuilder.setOngoing(true)
        Notification notification = new NotificationCompat.Builder(this, channelId)
                .setContentTitle("Filming screen...")
                .setContentText("This is needed to analyse the tiles.")
                .setSmallIcon(R.drawable.stream)
                .setOngoing(true)
                .setPriority(Notification.PRIORITY_MAX)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .build();


        startForeground(1, notification);

        return super.onStartCommand(intent, flags, startId);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private String createNotificationChannel() {
        String channelId = "Default";
        String channelName = "Foreground notification";
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
