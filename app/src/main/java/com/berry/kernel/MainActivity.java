package com.berry.kernel;

import android.app.Activity;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.IBinder;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.NotificationCompat;

import java.io.File;

public class MainActivity extends AppCompatActivity {

    private Button btnStart;
    private Button btnStop;
    private TextView tvStatus;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        btnStart = findViewById(R.id.btn_start);
        btnStop = findViewById(R.id.btn_stop);
        tvStatus = findViewById(R.id.tv_status);

        btnStop.setEnabled(false);

        btnStart.setOnClickListener(v -> checkAndStart());
        btnStop.setOnClickListener(v -> stopKernel());
    }

    private void checkAndStart() {
        if (!Settings.canDrawOverlays(this)) {
            showOverlayPermissionDialog();
            return;
        }
        startKernel();
    }

    private void showOverlayPermissionDialog() {
        new AlertDialog.Builder(this)
                .setTitle("需要悬浮窗权限")
                .setMessage("本应用需要悬浮窗权限才能正常工作。\n\n点击\"去授权\"后将跳转到系统设置页面，请找到本应用并开启悬浮窗权限。")
                .setPositiveButton("去授权", (dialog, which) -> {
                    Intent intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                            Uri.parse("package:" + getPackageName()));
                    overlayPermissionLauncher.launch(intent);
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private final ActivityResultLauncher<Intent> overlayPermissionLauncher =
            registerForActivityResult(new ActivityResultContracts.StartActivityForResult(), result -> {
                if (Settings.canDrawOverlays(this)) {
                    startKernel();
                } else {
                    Toast.makeText(this, "悬浮窗权限未开启", Toast.LENGTH_SHORT).show();
                }
            });

    private void startKernel() {
        String nativeDir = getApplicationInfo().nativeLibraryDir;
        String binaryPath = nativeDir + "/libberry.so";

        File binary = new File(binaryPath);
        if (!binary.exists()) {
            tvStatus.setText("状态：未找到本地工具文件");
            Toast.makeText(this, "工具文件不存在: " + binaryPath, Toast.LENGTH_LONG).show();
            return;
        }

        binary.setExecutable(true, false);

        Intent serviceIntent = new Intent(this, KernelService.class);
        serviceIntent.putExtra("binary_path", binaryPath);
        startKernelService(serviceIntent);

        btnStart.setEnabled(false);
        btnStop.setEnabled(true);
        tvStatus.setText("状态：运行中");
    }

    private void stopKernel() {
        Intent serviceIntent = new Intent(this, KernelService.class);
        stopService(serviceIntent);

        btnStart.setEnabled(true);
        btnStop.setEnabled(false);
        tvStatus.setText("状态：已停止");
    }

    private void startKernelService(Intent intent) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent);
        } else {
            startService(intent);
        }
    }
}
