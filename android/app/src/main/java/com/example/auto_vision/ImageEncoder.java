package com.example.auto_vision;

import android.graphics.Bitmap;
import android.hardware.HardwareBuffer;
import android.media.Image;
import android.util.Base64;
import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;

public class ImageEncoder {

  private static final String TAG = "ImageEncoder";

  /**
   * 采集热路径的 JPEG 质量（单一口径，不允许多处各写一个 80）。
   * 80 是当前精度基准：识别器的模板匹配对压缩噪声敏感，改这一个数字
   * 等于换了一套识别口径，必须先跑全量回归才能动。
   */
  public static final int DEFAULT_QUALITY = 80;

  public static String encodeImageToBase64(Image image) {
    byte[] bytes = encodeImageToByteArray(image);
    if (bytes == null) return null;
    return Base64.encodeToString(bytes, Base64.DEFAULT);
  }

  /**
   * 采集热路径唯一应该用的入口：**整屏编码**。
   * 不要为了省几毫秒改带去 ROI 的重载：引擎收到的图必须保持整屏坐标系。
   * Java 侧的 roiTop/roiBottom 是用户拖出的「手牌识别区域」，它经 set_roi 传给
   * Python，由 _apply_hand_roi 在**整屏图**上裁；而牌河、副露、对家手牌、
   * 牌桌场景校验用的是另一套整屏几何。若在编码时就裁掉，两套坐标会被叠加
   * 应用（裁过的图 + 按整屏算的 ROI），表现为牌河漏框与手牌错位。
   * 实测收益也不成立：1080x2400 q80 整屏编码 7.8ms / 解码 13.1ms，
   * 裁到 (0.30–0.98) 为 6.4ms / 10.2ms —— 拿 4ms 换坐标系错位，不值。
   */
  public static byte[] encodeImageToByteArray(Image image) {
    return encodeImageToByteArray(image, 0f, 1f, DEFAULT_QUALITY);
  }

  public static byte[] encodeImageToByteArray(Image image, float roiTop, float roiBottom) {
    return encodeImageToByteArray(image, roiTop, roiBottom, DEFAULT_QUALITY);
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
    // 预置容量：默认构造的 ByteArrayOutputStream 从 32 字节起步，写满一帧整屏
    // JPEG（实测 ≈400KB）要翻倍扩容 ≈14 次、累计多拷一次整帧数据，还推高 GC。
    // 估算按像素数 / 4（比实测 /6.5 宽松），宁可略大也不要反复长大。
    int cap = Math.max(32, Math.min(4 << 20, bitmap.getWidth() * bitmap.getHeight() / 4));
    ByteArrayOutputStream stream = new ByteArrayOutputStream(cap);
    bitmap.compress(Bitmap.CompressFormat.JPEG, q, stream);
    return stream.toByteArray();
  }
}
