package com.example.auto_vision;

public class NativeEngine {
    private static volatile boolean isLoaded = false;

    static {
        try {
            System.loadLibrary("mahjong_native");
            isLoaded = true;
            nativeInit();
        } catch (Throwable t) {
            TimedLog.e("NativeEngine", "Failed to load libmahjong_native.so: " + t);
            isLoaded = false;
        }
    }

    public static boolean isAvailable() {
        return isLoaded;
    }

    public static void init() {
        if (isLoaded) {
            nativeInit();
        }
    }

    public static void reset() {
        if (isLoaded) {
            nativeReset();
        }
    }

    public static void setDingque(int suit) {
        if (isLoaded) {
            nativeSetDingque(suit);
        }
    }

    public static void recordDiscard(int tileIdx, int count) {
        if (isLoaded) {
            nativeRecordDiscard(tileIdx, count);
        }
    }

    public static void recordMeld(int tileIdx, int count) {
        if (isLoaded) {
            nativeRecordMeld(tileIdx, count);
        }
    }

    public static String evaluate(int[] handTiles, int visualDqSuit, boolean isSwap, boolean isDq, boolean isTable) {
        if (!isLoaded) {
            return null;
        }
        return nativeEvaluate(handTiles, visualDqSuit, isSwap, isDq, isTable);
    }

    // --- Native JNI declarations ---
    private static native void nativeInit();
    private static native void nativeReset();
    private static native void nativeSetDingque(int suit);
    private static native void nativeRecordDiscard(int tileIdx, int count);
    private static native void nativeRecordMeld(int tileIdx, int count);
    private static native String nativeEvaluate(int[] handTiles, int visualDqSuit, boolean isSwap, boolean isDq, boolean isTable);
}
