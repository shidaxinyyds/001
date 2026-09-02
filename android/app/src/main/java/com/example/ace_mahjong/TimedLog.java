package com.example.ace_mahjong;

import io.flutter.Log;
import java.time.Instant;

public class TimedLog {
    public static void i(String tag, String message) {
        message = String.format("%s: %s", Instant.now().toString(), message);
        Log.i(tag, message);
    }

    public static void e(String tag, String message) {
        message = String.format("%s: %s", Instant.now().toString(), message);
        Log.e(tag, message);
    }
}