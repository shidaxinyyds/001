package com.example.realtime_mahjong_trainer;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.res.Configuration;
import android.media.projection.MediaProjectionManager;
import android.os.Bundle;
import android.util.DisplayMetrics;
import android.view.WindowMetrics;
import androidx.annotation.NonNull;
import io.flutter.embedding.android.FlutterActivity;
import io.flutter.embedding.engine.FlutterEngine;
import io.flutter.plugin.common.EventChannel;
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

    WindowMetrics wm = getActivity().getWindowManager().getCurrentWindowMetrics();
    DisplayMetrics dm = new DisplayMetrics();
    getActivity().getWindowManager().getDefaultDisplay().getMetrics(dm);

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

    WindowMetrics wm = activity.getWindowManager().getCurrentWindowMetrics();
    DisplayMetrics dm = new DisplayMetrics();
    activity.getWindowManager().getDefaultDisplay().getMetrics(dm);

    mMediaProjectionManager =
      (MediaProjectionManager) activity.getSystemService(
        Context.MEDIA_PROJECTION_SERVICE
      );

    // 记下分辨率，startStream() 里重建 ScreenStreamer 时要用
    mStreamWidth = wm.getBounds().width();
    mStreamHeight = wm.getBounds().height();
    mStreamDpi = dm.densityDpi;

    // Android 14(API 34) 硬性要求：mediaProjection 类型的前台服务，
    // 必须在 startForeground() 之前先调用 createScreenCaptureIntent()，
    // 否则系统会抛 SecurityException。所以这里先拿到 intent 再起服务。
    Intent captureIntent = mMediaProjectionManager.createScreenCaptureIntent();

    TimedLog.i(TAG, "Start foreground service");
    startForegroundService(new Intent(this, MediaProjectionService.class));

    TimedLog.i(TAG, "Requesting confirmation");
    // This initiates a prompt dialog for the user to confirm screen projection.
    // Looks like this is the legacy approach, the recommended one is
    // https://developer.android.com/guide/topics/large-screens/media-projection
    startActivityForResult(captureIntent, REQUEST_MEDIA_PROJECTION);

    return 0;
  }

  private void startStream(int resultCode, Intent intent) {
    // 先清理上一次会话：否则重复点“开始识别”会残留多个 500ms 采集 Timer，
    // 队列越排越长，表现为识别结果严重滞后甚至看起来“卡死”。
    stopStream();

    streamer =
      new ScreenStreamer(
        mStreamWidth,
        mStreamHeight,
        mStreamDpi,
        mMediaProjectionManager
      );
    streamer.startStream(resultCode, intent);
    processor = new ImageProcessor(() -> streamer.acquireLatestImage());
    processor.prepare(getContext());
    processor.start();
  }

  private void stopStream() {
    // 悬浮窗的“停止”、主界面的“停止识别”、以及用户没点授权就取消，
    // 都会走到这里。此前没有判空，未开始识别就点停止会直接 NPE 崩掉。
    if (processor != null) {
      processor.stop();
      processor = null;
    }
    if (streamer != null) {
      streamer.stopStream();
      streamer = null;
    }
    stopService(new Intent(this, MediaProjectionService.class));
  }

}
