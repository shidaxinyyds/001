package com.example.ace_mahjong;

public class Assert {

  public static <T> T assertNotNull(T object) {
    if (object == null) throw new AssertionError("Object cannot be null");
    return object;
  }
}
