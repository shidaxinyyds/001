package com.example.realtime_mahjong_trainer;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.res.Configuration;
import android.media.projection.MediaProjectionConfig;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Bundle;
import android.util.DisplayMetrics;
import android.view.WindowMetrics;
import androidx.annotation.NonNull;
import io.flutter.embedding.android.FlutterActivity;
import io.flutter.embedding.engine.FlutterEngine;
import io.flutter.plugin.common.MethodChannel;

import java.util.concurrent.CompletableFuture;


public class MainActivity extends FlutterActivity {

  // For logging
  private static final String TAG = "MainActivity";

  // For calls from Flutter
  private static final String CHANNEL_NAME =
    "com.example.realtime_mahjong_trainer/channel";

  private static final int REQUEST_MEDIA_PROJECTION = 1;

  private MethodChannel channel;

  private MediaProjectionManager mMediaProjectionManager;
  private ScreenStreamer streamer;
  private ImageProcessor processor;

  // 最近一次请求录屏时的屏幕参数，startStream() 重建 ScreenStreamer 时复用
  private int mStreamWidth;
  private int mStreamHeight;
  private int mStreamDpi;

  // 采集层错误/状态上报通道（发到悬浮窗监听的同一端口）
  private final NetworkClient statusClient = new NetworkClient("127.0.0.1", 12345);


  @Override
  public void onCreate(Bundle savedInstanceState) {
    super.onCreate(savedInstanceState);
  }

  @Override
  public void configureFlutterEngine(@NonNull FlutterEngine flutterEngine) {
    super.configureFlutterEngine(flutterEngine);

    channel =
      new MethodChannel(
        flutterEngine.getDartExecutor().getBinaryMessenger(),
        CHANNEL_NAME
      );
    channel.setMethodCallHandler((call, result) -> {
      // This method is invoked on the main thread.

      Runnable toRun = null;
      if (call.method.equals("startProcessing")) {
        toRun = () -> {
          result.success(prepareStream());
        };
      }
      if (call.method.equals("stopProcessing")) {
        toRun = () -> {
          stopStream();
          result.success(0);
        };
      }

      if (toRun == null) {
        result.notImplemented();
        return;
      }

      CompletableFuture.runAsync(toRun).exceptionally(ex -> {
        TimedLog.e(TAG, "Error: " + ex.getMessage());
        ex.printStackTrace();
        return null;
      });
    });
  }

  @Override
  public void onConfigurationChanged(Configuration newConfig) {
    super.onConfigurationChanged(newConfig);
    if (streamer == null) {
      return;
    }

    WindowMetrics wm = maximumWindowMetrics();
    if (wm == null) {
      return;
    }
    DisplayMetrics dm = new DisplayMetrics();
    getActivity().getWindowManager().getDefaultDisplay().getMetrics(dm);

    // 旋转时用 resize + setSurface 更新虚拟显示（ScreenStreamer 内部处理），
    // 不能二次 createVirtualDisplay（Android 14 起会 SecurityException）。
    streamer.restartStream(
            wm.getBounds().width(),
            wm.getBounds().height(),
            dm.densityDpi
    );
  }

  @Override
  public void onActivityResult(int requestCode, int resultCode, Intent intent) {
    TimedLog.i(
      TAG,
      String.format(
        "onActivityResult: requestCode %d, resultCode %d, intent %s",
        requestCode,
        resultCode,
        intent != null ? intent.toString() : "null"
      )
    );

    if (requestCode == REQUEST_MEDIA_PROJECTION) {
      // 用户在系统弹窗里点了“取消”时，resultCode 不是 RESULT_OK，
      // 此时硬去建 MediaProjection 会抛异常
      if (resultCode != Activity.RESULT_OK || intent == null) {
        TimedLog.i(TAG, "用户取消了录屏授权，未开始识别");
        return;
      }
      startStream(resultCode, intent);
    }
  }


  // Effective entry point from Flutter
  private int prepareStream() {
    Activity activity = getActivity();
    if (activity == null) {
      return -1;
    }

    WindowMetrics wm = maximumWindowMetrics();
    if (wm == null) {
      return -1;
    }
    DisplayMetrics dm = new DisplayMetrics();
    activity.getWindowManager().getDefaultDisplay().getMetrics(dm);

    mMediaProjectionManager =
      (MediaProjectionManager) activity.getSystemService(
        Context.MEDIA_PROJECTION_SERVICE
      );

    // 采集尺寸必须用「设备最大显示区域」，且固定为横屏方向（w >= h）：
    // 授权时 App 在前台是竖屏，按当前窗口建流会把游戏画面压成竖条；
    // 麻将游戏全是横屏，本 App 的识别基准几何也是横屏（GT 截图 2712x1220）。
    // 旋转到游戏后，若需要精确匹配，onConfigurationChanged 会 resize 校正。
    int bw = wm.getBounds().width();
    int bh = wm.getBounds().height();
    mStreamWidth = Math.max(bw, bh);
    mStreamHeight = Math.min(bw, bh);
    mStreamDpi = dm.densityDpi;

    // Android 14(API 34) 硬性要求：mediaProjection 类型的前台服务，
    // 必须在 startForeground() 之前先调用 createScreenCaptureIntent()，
    // 否则系统会抛 SecurityException。所以这里先拿到 intent 再起服务。
    Intent captureIntent;
    if (Build.VERSION.SDK_INT >= 34) {
      // Android 14 起系统弹窗默认允许"只共享单个应用"，用户极易误选成
      // 本 App 自己（授权时本 App 正在前台），导致采集到的永远是自家界面。
      // 这里显式限定为"整屏共享"，从根上规避该失败模式。
      captureIntent = mMediaProjectionManager.createScreenCaptureIntent(
        MediaProjectionConfig.createConfigForDefaultDisplay()
      );
    } else {
      captureIntent = mMediaProjectionManager.createScreenCaptureIntent();
    }

    TimedLog.i(TAG, "Start foreground service");
    startForegroundService(new Intent(this, MediaProjectionService.class));

    TimedLog.i(TAG, "Requesting confirmation");
    startActivityForResult(captureIntent, REQUEST_MEDIA_PROJECTION);

    return 0;
  }

  // 注意：整个启动流程放在后台线程执行。
  // 以前 startStream 在主线程里同步做 Python.start() + 全量 import
  // （cv2/numpy/mahjong 一百多个模块，真机上要 10~40 秒），
  // 必然触发 ANR（应用无响应）甚至被系统杀掉——表现就是
  // "点完授权没有任何反应"。
  private void startStream(int resultCode, Intent intent) {
    // 只清理上一次的采集资源（Timer/VirtualDisplay），不动前台服务：
    // getMediaProjection 要求 mediaProjection 类型的前台服务处于运行状态，
    // 以前的写法是先 stopStream() 把服务杀掉再取 projection，顺序是错的。
    stopCaptureOnly();

    CompletableFuture.runAsync(() -> {
      try {
        streamer =
          new ScreenStreamer(
            mStreamWidth,
            mStreamHeight,
            mStreamDpi,
            mMediaProjectionManager,
            this::onCaptureError
          );
        streamer.startStream(resultCode, intent);
        processor = new ImageProcessor(() -> {
          ScreenStreamer s = streamer;
          return s == null ? null : s.acquireLatestImage();
        });
        boolean ok = processor.prepare(getContext());
        if (ok) {
          processor.start();
        } else {
          sendCaptureStatus(NetworkClient.statusJson(
            "start_failed", "识别引擎初始化失败，已中止（详见悬浮窗提示）"));
          stopCaptureOnly();
        }
      } catch (Throwable t) {
        TimedLog.e(TAG, "startStream failed: " + t);
        sendCaptureStatus(NetworkClient.statusJson(
          "start_failed", "启动采集失败: " + t));
        stopCaptureOnly();
      }
    }).exceptionally(ex -> {
      TimedLog.e(TAG, "startStream async error: " + ex);
      return null;
    });
  }

  // ScreenStreamer 的错误/停止回调（含 projection_stopped）
  private void onCaptureError(String message) {
    if ("projection_stopped".equals(message)) {
      sendCaptureStatus(NetworkClient.statusJson(
        "projection_stopped",
        "录屏会话被系统结束（锁屏/状态栏停止共享/被其它录屏抢占），请重新开始识别"));
      stopCaptureOnly();
      stopService(new Intent(this, MediaProjectionService.class));
      return;
    }
    sendCaptureStatus(NetworkClient.statusJson("capture_error", message));
  }

  private void sendCaptureStatus(String json) {
    try {
      statusClient.sendStatus(json);
    } catch (Throwable t) {
      TimedLog.e(TAG, "sendCaptureStatus failed: " + t);
    }
  }

  // 停止采集（Timer + 虚拟显示 + projection），不动前台服务
  private void stopCaptureOnly() {
    if (processor != null) {
      processor.stop();
      processor = null;
    }
    if (streamer != null) {
      streamer.stopStream();
      streamer = null;
    }
  }

  private void stopStream() {
    // 悬浮窗的“停止”、主界面的“停止识别”、以及用户没点授权就取消，
    // 都会走到这里。此前没有判空，未开始识别就点停止会直接 NPE 崩掉。
    stopCaptureOnly();
    stopService(new Intent(this, MediaProjectionService.class));
  }

  // 设备最大显示区域：API 30+ 用 getMaximumWindowMetrics（不受本 App
  // 当前窗口/分屏影响），旧版本回退到当前窗口。
  private WindowMetrics maximumWindowMetrics() {
    Activity activity = getActivity();
    if (activity == null) {
      return null;
    }
    if (Build.VERSION.SDK_INT >= 30) {
      return activity.getWindowManager().getMaximumWindowMetrics();
    }
    return activity.getWindowManager().getCurrentWindowMetrics();
  }

}
