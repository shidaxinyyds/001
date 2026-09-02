package com.example.ace_mahjong;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.hardware.HardwareBuffer;
import android.media.Image;
import android.util.Base64;
import java.io.ByteArrayOutputStream;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;

public class ImageEncoder {

  private static final String TAG = "ImageEncoder";

  public static String encodeImageToBase64(Image image) {
    byte[] bytes = encodeImageToByteArray(image);
    return Base64.encodeToString(bytes, Base64.DEFAULT);
  }

  public static byte[] encodeImageToByteArray(Image image) {
    Bitmap bitmap = imageToBitmap(image);
    return bitmapToByteArray(bitmap);
  }

  private static Bitmap imageToBitmap(Image image) {
    int width = image.getWidth();
    int height = image.getHeight();

    // This class is hardcoded to only able to accept this format.
    if (image.getFormat() != HardwareBuffer.RGBA_8888) {
      return null;
    }

    Image.Plane[] planes = image.getPlanes();
    ByteBuffer buffer = planes[0].getBuffer();
    int pixelStride = planes[0].getPixelStride();
    int rowStride = planes[0].getRowStride();
    int rowPadding = rowStride - pixelStride * width;
    Bitmap bitmap = Bitmap.createBitmap(
      width + rowPadding / pixelStride,
      height,
      Bitmap.Config.ARGB_8888
    );
    bitmap.copyPixelsFromBuffer(buffer);
    bitmap = Bitmap.createBitmap(bitmap, 0, 0, width, height);
    return bitmap;
  }

  private static byte[] bitmapToByteArray(Bitmap bitmap) {
    // 关键改动：不再用 Assert.assertNotNull。Android 的 Assert 在 release 包里默认
    // 不抛异常（ENABLE_ASSERTIONS 为 false），会变成对 null 直接 bitmap.compress → NPE；
    // 而在 debug 包里又可能抛 AssertionError。两种行为都不受控。
    // 这里显式判空：bitmap 为 null（例如 ImageReader 给出的格式不是 RGBA_8888）
    // 就直接返回 null，让上层（ImageProcessor.processCapturedImage）走"编码失败"
    // 的错误处理分支，而不是崩溃。
    if (bitmap == null) {
      return null;
    }
    ByteArrayOutputStream stream = new ByteArrayOutputStream();
    // 质量 95：之前用 50，整屏（2712x1220 级）高压缩把麻将牌面细小数字/花色糊成
    // 一团，模板匹配几乎必然误判——这是"识别不准"的头号画质元凶。95 在画质与体积
    // 之间取得平衡，localhost 传输不是瓶颈。
    bitmap.compress(Bitmap.CompressFormat.JPEG, 95, stream);
    return stream.toByteArray();
  }
}
