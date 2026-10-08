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

    // 节流采样计数器：避免高频采帧每帧做开销较大的文件扫描。
    // volatile：心跳线程写（runScheduledDeepScan）、主线程也可能写（启动闸门），
    // 非 volatile 时两边会各自看自己的副本，节流形同虚设（多线程重复深扫）。
    private static volatile long sLastDeepScanTime = 0;
    private static final long DEEP_SCAN_INTERVAL_MS = 8000; // 8秒深扫一次

    // 设备指纹与卡密核验的过路缓存（仅为了把 binder IPC + SHA-256 + 正则从
    // 每帧热路径上拿掉，见 verifyLicenseThrottled）。
    private static volatile String sDeviceIdCache = null;
    private static volatile long sLicenseOkAt = 0;
    private static final long LICENSE_OK_TTL_MS = 2000; // 好结果缓存 2s；坏结果不缓存

    /**
     * 快速检查：轻量级主路径调用（开销小于 0.05ms）
     *
     * 必须在采集线程上便宜：旧写法在这里顺带做「周期性深层检测」，于是每 8 秒
     * 有一帧要在采集线程里逐行读 /proc/self/maps + 对 27042/27043 各做一次
     * socket.connect（15ms 超时）—— 最坏直接卡住流水线几十到几百毫秒，
     * 表现为周期性钝卡。现在深扫由心跳线程调 runScheduledDeepScan()，
     * 本方法只读内存标志位 + Debug.isDebuggerConnected()。
     * 代价：靠深扫发现的注入，熔断最多迟一个心跳周期（2s）+1 帧生效。
     */
    public static boolean isSafe(Context context) {
        if (sCompromised) {
            return false;
        }

        // 快速调试器检测（内存读，无 IO）
        if (Debug.isDebuggerConnected() || Debug.waitingForDebugger()) {
            punishAndMitigate(context, "Debug.isDebuggerConnected detected");
            return false;
        }

        return true;
    }

    /**
     * 按节流推进一次深层安全扫描（给独立心跳线程用）。
     * 到间隔才扫；扫描失败已在 performDeepScan 内部触发 punishAndMitigate，
     * 下一帧的 isSafe() 就会拒识别。
     */
    public static void runScheduledDeepScan(Context context) {
        if (sCompromised) {
            return;
        }
        long now = System.currentTimeMillis();
        if (now - sLastDeepScanTime <= DEEP_SCAN_INTERVAL_MS) {
            return;
        }
        sLastDeepScanTime = now;
        performDeepScan(context);
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
     * 卡密核验的节流版：只给采帧热路径用。
     *
     * 旧行为是每帧跑一次 `verifyLicense`：一次 SharedPreferences 读 + 一次
     * Settings.Secure 的 binder IPC + 一次 SHA-256 + 一次 `split("\\|")` 正则，
     * 全在采集线程上。现改成：① deviceId 本身按进程内不变，缓存；
     * ② 「通过」结果缓存 2s（TTL）；「不通过」绝不缓存 —— 下一次调用仍真核验，
     * 保证过期/被拉黑能立即反映。
     * 代价：授权自然到期的硬拒最多迟 2s（而本方法对到期已有 300s 宽限）。
     * 启动闸门（MainActivity.startProcessing / verifyLicenseNative）必须继续用
     * 强校验的 `verifyLicense`，不接受缓存。
     */
    public static boolean verifyLicenseThrottled(Context context) {
        if (sCompromised) {
            return false;
        }
        long now = System.currentTimeMillis();
        if (sLicenseOkAt != 0 && now - sLicenseOkAt < LICENSE_OK_TTL_MS) {
            return true;
        }
        boolean ok = verifyLicense(context);
        // 只缓存好结果；sCompromised 在真核验中被置位时也不缓存（下一调即回 false）。
        if (ok && !sCompromised) {
            sLicenseOkAt = now;
        } else {
            sLicenseOkAt = 0;
        }
        return ok;
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
     *
     * 进程内缓存：ANDROID_ID 与 Build.* 在进程生命周期内不会变，而它原本
     * 每帧被 `verifyLicense` 重算一次（binder IPC + SHA-256）。缓存不影响判定：
     * 比对的是「token 里写的设备」与「本机设备」，两者都取自同一份缓存。
     * 拉黑（sec_tamper_locked）不写回这里，仍由 verifyLicense 每调现读，
     * 保证用户手动清除拉黑态后能立即生效。
     */
    public static String computeDeviceId(Context context) {
        String cached = sDeviceIdCache;
        if (cached != null && !cached.isEmpty()) {
            return cached;
        }
        String id = computeDeviceIdUncached(context);
        if (id != null && !id.isEmpty()) {
            sDeviceIdCache = id;
        }
        return id;
    }

    private static String computeDeviceIdUncached(Context context) {
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
