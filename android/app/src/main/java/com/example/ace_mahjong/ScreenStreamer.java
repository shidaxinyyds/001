package com.example.ace_mahjong;

import android.content.Intent;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Handler;
import android.os.Looper;
import androidx.core.util.Consumer;

public class ScreenStreamer {

  private static final String TAG = "ScreenStreamer";

  private MediaProjectionManager mMediaProjectionManager;
  private MediaProjection mMediaProjection;
  private ImageReader mImageReader;
  private VirtualDisplay mVirtualDisplay;

  private int width = 0;
  private int height = 0;
  private int density = 0;

  // 采集链路的错误回调（onProjectionStopped / 建流失败等），由 MainActivity
  // 转成状态帧发给悬浮窗。此前这些错误只进 logcat，用户什么都看不到。
  private final Consumer<String> onError;

  public ScreenStreamer(
    int width,
    int height,
    int density,
    MediaProjectionManager mMediaProjectionManager,
    Consumer<String> onError
  ) {
    this.width = width;
    this.height = height;
    this.density = density;
    this.mMediaProjectionManager = mMediaProjectionManager;
    this.onError = onError;
  }

  public synchronized void startStream(int mResultCode, Intent mResultData) {
    mMediaProjection =
      mMediaProjectionManager.getMediaProjection(mResultCode, mResultData);
    if (mMediaProjection == null) {
      reportError("getMediaProjection 返回空：录屏授权会话无效，请停止后重新开始");
      return;
    }

    // Android 14(API 34+) 硬性要求：createVirtualDisplay 之前必须注册
    // MediaProjection.Callback，否则抛 IllegalStateException。
    // onStop 在以下情况触发：用户从状态栏芯片停止共享、锁屏、其它录屏会话
    // 抢占、进程被杀——这些都会让采集悄悄死掉，必须在回调里释放资源并上报。
    mMediaProjection.registerCallback(
      new MediaProjection.Callback() {
        @Override
        public void onStop() {
          TimedLog.i(TAG, "MediaProjection onStop: 录屏会话被系统结束");
          teardownResources();
          if (onError != null) {
            onError.accept("projection_stopped");
          }
        }
      },
      new Handler(Looper.getMainLooper())
    );

    setupResources();
  }

  public synchronized void restartStream(int width, int height, int density) {
    if (mVirtualDisplay == null || mImageReader == null || mMediaProjection == null) {
      this.width = width;
      this.height = height;
      this.density = density;
      setupResources();
      return;
    }

    TimedLog.i(
      TAG,
      "resizing VirtualDisplay: " + width + "x" + height + " (" + density + ")"
    );

    // 旋转/尺寸变化的官方做法是 resize + setSurface。
    // 绝不能在同一 MediaProjection 上二次 createVirtualDisplay：
    // Android 14 起会抛 SecurityException（每个 projection 只允许一次）。
    ImageReader oldReader = mImageReader;
    ImageReader newReader =
      ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 60);
    mVirtualDisplay.resize(width, height, density);
    mVirtualDisplay.setSurface(newReader.getSurface());
    mImageReader = newReader;
    this.width = width;
    this.height = height;
    this.density = density;

    try {
      oldReader.close();
    } catch (Exception ignore) {
    }
  }

  public synchronized void stopStream() {
    teardownResources();
    if (mMediaProjection != null) {
      try {
        mMediaProjection.stop();
      } catch (Exception ignore) {
      }
      mMediaProjection = null;
    }
  }

  // 只释放显示与采集缓冲（供 MediaProjection.onStop 回调使用，
  // 不能在 onStop 里反过来调 projection.stop()）
  private void teardownResources() {
    if (mVirtualDisplay != null) {
      try {
        mVirtualDisplay.release();
      } catch (Exception ignore) {
      }
      mVirtualDisplay = null;
    }
    if (mImageReader != null) {
      try {
        mImageReader.close();
      } catch (Exception ignore) {
      }
      mImageReader = null;
    }
  }

  private void setupResources() {
    TimedLog.i(
      TAG,
      "Setting up a VirtualDisplay: " + width + "x" + height + " (" + density + ")"
    );
    mImageReader =
      ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 60);

    mVirtualDisplay =
      mMediaProjection.createVirtualDisplay(
        "ScreenCapture",
        width,
        height,
        density,
        DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
        mImageReader.getSurface(),
        null,
        null
      );
    // createVirtualDisplay 失败时返回 null 而不是抛异常——
    // 此前完全不检查，结果是 ImageReader 永远收不到画面、
    // acquireLatestImage 永远返回 null、整个程序"没有任何反应"。
    if (mVirtualDisplay == null) {
      reportError("createVirtualDisplay 失败：无法建立屏幕采集，请停止后重试");
      return;
    }
    TimedLog.i(TAG, "VirtualDisplay created OK");
  }

  public Image acquireLatestImage() {
    ImageReader reader = mImageReader;
    if (reader == null) {
      return null;
    }
    try {
      return reader.acquireLatestImage();
    } catch (Exception e) {
      // 采集缓冲被并发关闭（旋转切换瞬间）等情况下会抛异常，
      // 记录但不中断：下一 tick 会用新的 reader。
      TimedLog.e(TAG, "acquireLatestImage: " + e);
      return null;
    }
  }

  private void reportError(String message) {
    TimedLog.e(TAG, message);
    if (onError != null) {
      onError.accept(message);
    }
  }
}
