package com.example.auto_vision;

import android.graphics.Bitmap;
import android.hardware.HardwareBuffer;
import android.media.Image;
import android.util.Base64;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;

public class ImageEncoder {

  private static final String TAG = "ImageEncoder";

  public static String encodeImageToBase64(Image image) {
    byte[] bytes = encodeImageToByteArray(image);
    if (bytes == null) return null;
    return Base64.encodeToString(bytes, Base64.DEFAULT);
  }

  public static byte[] encodeImageToByteArray(Image image) {
    return encodeImageToByteArray(image, 0f, 1f, 95);
  }

  public static byte[] encodeImageToByteArray(Image image, float roiTop, float roiBottom) {
    return encodeImageToByteArray(image, roiTop, roiBottom, 95);
  }

  public static byte[] encodeImageToByteArray(Image image, float roiTop, float roiBottom, int quality) {
    Bitmap bitmap = imageToBitmap(image, roiTop, roiBottom);
    if (bitmap == null) {
      return null;
    }
    try {
      return bitmapToByteArray(bitmap, quality);
    } finally {
      bitmap.recycle();
    }
  }

  private static Bitmap imageToBitmap(Image image, float roiTop, float roiBottom) {
    if (image == null) return null;
    int width = image.getWidth();
    int height = image.getHeight();

    // This class is hardcoded to only able to accept this format.
    if (image.getFormat() != HardwareBuffer.RGBA_8888) {
      return null;
    }

    Image.Plane[] planes = image.getPlanes();
    if (planes == null || planes.length == 0) return null;
    ByteBuffer buffer = planes[0].getBuffer();
    if (buffer == null) return null;
    int pixelStride = planes[0].getPixelStride();
    int rowStride = planes[0].getRowStride();
    int rowPadding = rowStride - pixelStride * width;

    float t = Math.max(0f, Math.min(1f, roiTop));
    float b = Math.max(t + 0.02f, Math.min(1f, roiBottom));
    boolean shouldCrop = (t > 0f || b < 1f);

    int cropY = (int) (height * t);
    int cropH = Math.min(height - cropY, Math.max(1, (int) (height * (b - t))));

    if (rowPadding == 0) {
      if (!shouldCrop) {
        Bitmap bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        bitmap.copyPixelsFromBuffer(buffer);
        return bitmap;
      } else {
        Bitmap rawBitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
        rawBitmap.copyPixelsFromBuffer(buffer);
        Bitmap cropped = Bitmap.createBitmap(rawBitmap, 0, cropY, width, cropH);
        rawBitmap.recycle();
        return cropped;
      }
    }

    Bitmap rawBitmap = Bitmap.createBitmap(
      width + rowPadding / pixelStride,
      height,
      Bitmap.Config.ARGB_8888
    );
    rawBitmap.copyPixelsFromBuffer(buffer);
    Bitmap cropped;
    if (!shouldCrop) {
      cropped = Bitmap.createBitmap(rawBitmap, 0, 0, width, height);
    } else {
      cropped = Bitmap.createBitmap(rawBitmap, 0, cropY, width, cropH);
    }
    rawBitmap.recycle();
    return cropped;
  }

  private static byte[] bitmapToByteArray(Bitmap bitmap, int quality) {
    if (bitmap == null) {
      return null;
    }
    int q = Math.max(10, Math.min(100, quality));
    ByteArrayOutputStream stream = new ByteArrayOutputStream();
    bitmap.compress(Bitmap.CompressFormat.JPEG, q, stream);
    return stream.toByteArray();
  }
}
