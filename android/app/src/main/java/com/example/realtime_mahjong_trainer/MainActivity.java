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
      if (intent == null) {
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

    streamer =
      new ScreenStreamer(
        wm.getBounds().width(),
        wm.getBounds().height(),
        dm.densityDpi,
        mMediaProjectionManager
      );


    TimedLog.i(TAG, "Start foreground service");
    startForegroundService(new Intent(this, MediaProjectionService.class));

    TimedLog.i(TAG, "Requesting confirmation");
    // This initiates a prompt dialog for the user to confirm screen projection.
    // Looks like this is the legacy approach, the recommended one is
    // https://developer.android.com/guide/topics/large-screens/media-projection
    startActivityForResult(
      mMediaProjectionManager.createScreenCaptureIntent(),
      REQUEST_MEDIA_PROJECTION
    );

    return 0;
  }

  private void startStream(int resultCode, Intent intent) {
    streamer.startStream(resultCode, intent);
    processor = new ImageProcessor(() -> streamer.acquireLatestImage());
    processor.prepare(getContext());
    processor.start();
  }

  private void stopStream() {
    processor.stop();
    streamer.stopStream();
    streamer = null;
    stopService(new Intent(this, MediaProjectionService.class));
  }

}
