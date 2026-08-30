package com.example.realtime_mahjong_trainer;

import android.content.Intent;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.util.DisplayMetrics;
import androidx.core.util.Consumer;
import com.example.realtime_mahjong_trainer.Assert;
import java.util.concurrent.CompletableFuture;

public class ScreenStreamer {

  private static final String TAG = "ScreenStreamer";

  private DisplayMetrics metrics;
  private MediaProjectionManager mMediaProjectionManager;
  private MediaProjection mMediaProjection;
  private ImageReader mImageReader;
  private VirtualDisplay mVirtualDisplay;

  private int width = 0;
  private int height = 0;
  private int density = 0;


  private static int MAX_RATE = 5000;


  public ScreenStreamer(
    int width,
    int height,
    int density,
    MediaProjectionManager mMediaProjectionManager
  ) {
    this.width = width;
    this.height = height;
    this.density = density;
    this.mMediaProjectionManager = mMediaProjectionManager;
  }

  public void startStream(int mResultCode, Intent mResultData) {
    mMediaProjection =
      mMediaProjectionManager.getMediaProjection(mResultCode, mResultData);
    Assert.assertNotNull(mMediaProjection);
    setupResources();
  }

  public void restartStream(int width, int height, int density) {
    teardownResources();
    this.width = width;
    this.height = height;
    this.density = density;
    setupResources();
  }

  public void stopStream(){
    teardownResources();
  }

  private void teardownResources() {
    mVirtualDisplay.release();
    mImageReader.close();
  }

  private void setupResources() {
    TimedLog.i(
      TAG,
      "Setting up a VirtualDisplay: " +
      width +
      "x" +
      height +
      " (" +
      density +
      ")"
    );
    mImageReader =
      ImageReader.newInstance(
        width,
        height,
        PixelFormat.RGBA_8888,
        60
      );

//     mImageReader.setOnImageAvailableListener(
//       (ImageReader reader) -> {
//         TimedLog.i(TAG, "Creating a future now to process image");
//         CompletableFuture.runAsync(() -> {
//           Image image = reader.acquireLatestImage();
//           if (image == null) {
//             TimedLog.i(TAG, "Image is null");
//           }
//           try {
//             this.callback.accept(image);
//           } catch (Exception e) {
//             TimedLog.i(TAG, "Exception occured" + e.toString());
//             e.printStackTrace();
//           } finally {
// //            image.close(); // Release resources
//           }
//           // try {
//           //   Thread.sleep(100);
//           // } catch (Exception e) {
//           //   TimedLog.i(TAG, "Exception occured" + e.toString());
//           // }
//         });
//     }, null);

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
  }

  public Image acquireLatestImage() {
    return mImageReader.acquireLatestImage();
  }
}