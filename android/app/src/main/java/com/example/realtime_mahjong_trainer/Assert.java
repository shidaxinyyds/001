package com.example.realtime_mahjong_trainer;

public class Assert {

  public static <T> T assertNotNull(T object) {
    if (object == null) throw new AssertionError("Object cannot be null");
    return object;
  }
}
