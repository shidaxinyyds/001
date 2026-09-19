package com.example.auto_vision;

import java.io.BufferedOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

public class NetworkClient {
    String host;
    int port;

    final String TAG = "NetworkClient";

    private Socket mSocket = null;
    private DataOutputStream mDataOut = null;
    private final Object mLock = new Object();

    public NetworkClient(String host, int port) {
        this.host = host;
        this.port = port;
    }

    static String leftPadZeros(String s, int length) {
        return String.format("%1$" + length + "s", s).replace(' ', '0');
    }

    private void ensureConnectedLocked() throws IOException {
        if (mSocket != null && mSocket.isConnected() && !mSocket.isClosed()) {
            return;
        }
        closeLocked();
        Socket socket = new Socket();
        socket.setTcpNoDelay(true);
        socket.setKeepAlive(true);
        socket.setSendBufferSize(65536);
        socket.connect(new InetSocketAddress(host, port), 1000);
        mDataOut = new DataOutputStream(new BufferedOutputStream(socket.getOutputStream(), 65536));
        mSocket = socket;
    }

    private void closeLocked() {
        if (mDataOut != null) {
            try { mDataOut.close(); } catch (Exception ignore) {}
            mDataOut = null;
        }
        if (mSocket != null) {
            try { mSocket.close(); } catch (Exception ignore) {}
            mSocket = null;
        }
    }

    public void close() {
        synchronized (mLock) {
            closeLocked();
        }
    }

    /**
     * 发送一帧数据到 Dart 悬浮窗监听的本地端口（通过复用持久长连接实现零延迟传输）。
     */
    public boolean send(byte[] bytes) {
        if (bytes == null || bytes.length == 0) return false;
        synchronized (mLock) {
            try {
                ensureConnectedLocked();
                mDataOut.writeBytes(leftPadZeros(String.valueOf(bytes.length), 8));
                mDataOut.write(bytes);
                mDataOut.flush();
                return true;
            } catch (IOException e1) {
                // 连接断开或异常，关闭旧连接并重试一次
                closeLocked();
                try {
                    ensureConnectedLocked();
                    mDataOut.writeBytes(leftPadZeros(String.valueOf(bytes.length), 8));
                    mDataOut.write(bytes);
                    mDataOut.flush();
                    return true;
                } catch (IOException e2) {
                    TimedLog.e(TAG, "Error sending data over persistent socket: " + e2);
                    closeLocked();
                    return false;
                }
            }
        }
    }

    /**
     * 发送一条状态帧（JSON + '\n'，与 Python 的 EngineResult.to_bytes 协议兼容，
     * Dart 端 parseEngineResult 按 JSON 与 PNG 的分隔符解析，PNG 可省略）。
     */
    public boolean sendStatus(String json) {
        return send((json + "\n").getBytes(StandardCharsets.UTF_8));
    }

    /**
     * 构造一条流水线状态 JSON。字段与 engine.py 输出的结果保持同一 schema，
     * Dart 端无需感知这帧来自 Java 还是 Python。
     */
    public static String statusJson(String status, String message) {
        StringBuilder sb = new StringBuilder();
        sb.append("{\"hand\":\"\",\"count\":0,\"status\":\"").append(status)
          .append("\",\"shanten\":null,\"advice\":[],\"commentary\":null,\"tiles\":[],")
          .append("\"top_score\":0.0,\"screen\":[0,0],\"elapsed\":0.0");
        if (message != null && !message.isEmpty()) {
            String safe = message.replace("\\", "\\\\")
                                 .replace("\"", "'")
                                 .replace("\n", " ")
                                 .replace("\r", " ");
            if (safe.length() > 180) {
                safe = safe.substring(0, 180);
            }
            sb.append(",\"message\":\"").append(safe).append("\"");
        }
        sb.append("}");
        return sb.toString();
    }
}
