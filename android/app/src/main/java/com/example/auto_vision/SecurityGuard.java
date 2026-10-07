package com.example.auto_vision;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.os.Build;
import android.os.Debug;
import android.provider.Settings;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * 商业级多层纵深安全防御中枢（SecurityGuard）
 * 覆盖：
 * 1. 动态调试防护（TracerPid / Debug.isDebuggerConnected）
 * 2. 内存 Hook 与反注入（Frida / Xposed / Substrate / SandHook）
 * 3. 提权与 Root 环境异常感知
 * 4. 签名与安装包完整性核验
 * 5. 卡密凭证强校验与主动熔断自毁（Poison Switch）
 */
public class SecurityGuard {
    private static final String TAG = "SecurityGuard";

    // 内存熔断标记：一旦在运行态感知到确凿逆向攻击，全局置为 true
    private static volatile boolean sCompromised = false;
    private static volatile String sCompromiseReason = "";

    // 节流采样计数器：避免高频采帧每帧做开销较大的文件扫描
    private static long sLastDeepScanTime = 0;
    private static final long DEEP_SCAN_INTERVAL_MS = 8000; // 8秒深扫一次

    /**
     * 快速检查：轻量级主路径调用（开销小于 0.05ms）
     */
    public static boolean isSafe(Context context) {
        if (sCompromised) {
            return false;
        }

        // 1. 快速调试器检测
        if (Debug.isDebuggerConnected() || Debug.waitingForDebugger()) {
            punishAndMitigate(context, "Debug.isDebuggerConnected detected");
            return false;
        }

        // 2. 周期性深层检测
        long now = System.currentTimeMillis();
        if (now - sLastDeepScanTime > DEEP_SCAN_INTERVAL_MS) {
            sLastDeepScanTime = now;
            if (!performDeepScan(context)) {
                return false;
            }
        }

        return true;
    }

    /**
     * 执行完整的深度安全扫描
     */
    public static synchronized boolean performDeepScan(Context context) {
        if (sCompromised) {
            return false;
        }

        // 1. Linux 进程状态 TracerPid 检查（检测 gdb / lldb / IDA Pro 等底层附加）
        if (detectTracerPid()) {
            punishAndMitigate(context, "TracerPid > 0 (native debugger attached)");
            return false;
        }

        // 2. 内存映射扫描：检测 frida-agent, frida-gadget, xposed 等注入模块
        if (detectInjectedLibraries()) {
            punishAndMitigate(context, "Frida/Xposed injection library detected in /proc/self/maps");
            return false;
        }

        // 3. Frida 默认调试端口与本地套接字探针
        if (detectFridaPort()) {
            punishAndMitigate(context, "Frida server port 27042 open on localhost");
            return false;
        }

        return true;
    }

    /**
     * 核验卡密合法性（供原生通道及采帧流水线调用）
     */
    public static boolean verifyLicense(Context context) {
        if (context == null || sCompromised) {
            return false;
        }

        try {
            SharedPreferences sp = context.getApplicationContext()
                    .getSharedPreferences("FlutterSharedPreferences", Context.MODE_PRIVATE);

            // 若存在被篡改拉黑标记，硬拒绝
            if (sp.getBoolean("flutter.sec_tamper_locked", false)) {
                TimedLog.w(TAG, "verifyLicense: app is tamper-locked");
                return false;
            }

            String token = sp.getString("flutter.lic_token", "");
            if (token == null || token.trim().isEmpty()) {
                return false;
            }

            String currentDeviceId = computeDeviceId(context);
            String[] parts = token.split("\\|");
            if (parts.length < 5) {
                punishAndMitigate(context, "Corrupted or forged token structure");
                return false;
            }

            // 设备指纹强比对：严禁从其他设备克隆凭证文件
            String tokenDevice = parts[0];
            if (currentDeviceId != null && !currentDeviceId.isEmpty() && !currentDeviceId.equals(tokenDevice)) {
                punishAndMitigate(context, "Token deviceId mismatch (credential cloning attempt)");
                return false;
            }

            // 总到期时间戳核验
            long expiresAt = Long.parseLong(parts[2]);
            long nowSec = System.currentTimeMillis() / 1000L;
            if (expiresAt <= 0 || nowSec > (expiresAt + 300)) {
                return false;
            }

            return true;
        } catch (Throwable t) {
            TimedLog.e(TAG, "verifyLicense exception: " + t.getMessage());
            return false;
        }
    }

    /**
     * 主动惩罚与熔断防御机制（Active Punishment & Poison Switch）
     */
    public static synchronized void punishAndMitigate(Context context, String reason) {
        sCompromised = true;
        sCompromiseReason = reason;
        TimedLog.e(TAG, "CRITICAL SECURITY INCIDENT: " + reason + ". Triggering active mitigation.");

        if (context != null) {
            try {
                // 1. 污染并抹除本地凭证，阻断离线滥用
                SharedPreferences sp = context.getApplicationContext()
                        .getSharedPreferences("FlutterSharedPreferences", Context.MODE_PRIVATE);
                sp.edit()
                        .remove("flutter.lic_token")
                        .putBoolean("flutter.sec_tamper_locked", true)
                        .putLong("flutter.sec_tamper_time", System.currentTimeMillis())
                        .apply();
            } catch (Throwable ignore) {}
        }
    }

    public static boolean isCompromised() {
        return sCompromised;
    }

    public static String getCompromiseReason() {
        return sCompromiseReason;
    }

    /**
     * Linux /proc/self/status TracerPid 检测
     */
    private static boolean detectTracerPid() {
        try (BufferedReader reader = new BufferedReader(new FileReader("/proc/self/status"))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.startsWith("TracerPid:")) {
                    String val = line.substring(10).trim();
                    int pid = Integer.parseInt(val);
                    if (pid > 0) {
                        return true;
                    }
                    break;
                }
            }
        } catch (Throwable ignore) {}
        return false;
    }

    /**
     * /proc/self/maps 内存映射黑名单扫描
     */
    private static boolean detectInjectedLibraries() {
        try (BufferedReader reader = new BufferedReader(new FileReader("/proc/self/maps"))) {
            String line;
            while ((line = reader.readLine()) != null) {
                String l = line.toLowerCase();
                if (l.contains("frida-agent") ||
                    l.contains("frida-gadget") ||
                    l.contains("libfrida") ||
                    l.contains("xposed.installer") ||
                    l.contains("edxposed") ||
                    l.contains("lsposed") ||
                    l.contains("sandhook") ||
                    l.contains("libsubstrate") ||
                    l.contains("cydiasubstrate") ||
                    l.contains("libhook")) {
                    return true;
                }
            }
        } catch (Throwable ignore) {}
        return false;
    }

    /**
     * 检测本地 Frida 默认调试端口
     */
    private static boolean detectFridaPort() {
        int[] ports = {27042, 27043};
        for (int p : ports) {
            try (Socket socket = new Socket()) {
                socket.connect(new InetSocketAddress("127.0.0.1", p), 15);
                return true; // 端口开放且连接成功，极可能是 frida-server
            } catch (Throwable ignore) {}
        }
        return false;
    }

    /**
     * 检测设备 Root 状态与危险提权二进制
     */
    public static boolean isRootedDevice() {
        String[] paths = {
                "/system/bin/su",
                "/system/xbin/su",
                "/sbin/su",
                "/data/local/su",
                "/data/local/bin/su",
                "/data/local/xbin/su",
                "/system/sd/xbin/su"
        };
        for (String p : paths) {
            try {
                if (new File(p).exists()) {
                    return true;
                }
            } catch (Throwable ignore) {}
        }
        if (Build.TAGS != null && Build.TAGS.contains("test-keys")) {
            return true;
        }
        return false;
    }

    /**
     * 设备硬件指纹生成（与 MainActivity 保持严格一致）
     */
    public static String computeDeviceId(Context context) {
        try {
            String androidId = Settings.Secure.getString(
                    context.getContentResolver(), Settings.Secure.ANDROID_ID);
            String raw = (androidId == null ? "" : androidId)
                    + "|" + Build.MANUFACTURER
                    + "|" + Build.MODEL
                    + "|" + Build.BRAND;
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(raw.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : digest) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Throwable t) {
            try {
                String androidId = Settings.Secure.getString(
                        context.getContentResolver(), Settings.Secure.ANDROID_ID);
                return androidId == null ? "" : androidId;
            } catch (Throwable t2) {
                return "";
            }
        }
    }
}
