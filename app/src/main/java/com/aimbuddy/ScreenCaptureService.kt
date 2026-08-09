package com.aimbuddy

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.hardware.HardwareBuffer
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat

/**
 * ScreenCaptureService - Foreground Service for MediaProjection
 *
 * Manages the MediaProjection foreground service requirement.
 * Also acts as the JNI bridge for frame data.
 */
class ScreenCaptureService : Service() {

    companion object {
        private const val CHANNEL_ID = "ESP_Capture_Channel"
        private const val NOTIFICATION_ID = 12345

        /** Broadcast action that asks MainActivity to force-restore touch
         *  passthrough (dismiss the menu and re-enable FLAG_NOT_TOUCHABLE). */
        const val ACTION_RESTORE_TOUCH = "com.aimbuddy.action.RESTORE_TOUCH"

        // JNI bridge method
        @JvmStatic
        external fun nativeOnFrame(hardwareBuffer: HardwareBuffer, timestamp: Long)
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(NOTIFICATION_ID, createNotification())
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Service runs until explicitly stopped
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? {
        return null
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val serviceChannel = NotificationChannel(
                CHANNEL_ID,
                "ESP Capture Service",
                NotificationManager.IMPORTANCE_DEFAULT
            )
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(serviceChannel)
        }
    }

    private fun createNotification(): Notification {
        // "Restore touch" action: broadcast to MainActivity, which force-closes
        // the menu and re-enables FLAG_NOT_TOUCHABLE. This is the ultimate
        // escape hatch if the menu ever traps screen input.
        val restoreIntent = Intent(ACTION_RESTORE_TOUCH).apply { setPackage(packageName) }
        val restorePending = PendingIntent.getBroadcast(
            this,
            0,
            restoreIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("AimBuddy ESP 运行中")
            .setContentText("点击「恢复触摸」可解除菜单对屏幕的拦截")
            .setSmallIcon(R.mipmap.ic_launcher)
            .addAction(
                android.R.drawable.ic_menu_close_clear_cancel,
                "恢复触摸",
                restorePending
            )
            .setOngoing(true)
            .build()
    }
}
