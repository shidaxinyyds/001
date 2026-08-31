package com.example.realtime_mahjong_trainer;

import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

public class NetworkClient {
    String host;
    int port;

    final String TAG = "NetworkClient";

    public NetworkClient(String host, int port) {
        this.host = host;
        this.port = port;
    }

    static String leftPadZeros(String s, int length) {
        return String.format("%1$" + length + "s", s).replace(' ', '0');
    }

    /**
     * 发送一帧数据到 Dart 悬浮窗监听的本地端口。
     * 返回是否发送成功，让调用方可以统计失败次数（此前失败只进 logcat，
     * 悬浮窗端表现为"没有任何反应"，完全无法区分是识别挂了还是链路断了）。
     */
    public boolean send(byte[] bytes) {
        Socket socket = new Socket();

        int bytesLength = bytes.length;
        try {
            socket.connect(new InetSocketAddress(host, port), 1000);
            DataOutputStream dataOut = new DataOutputStream(socket.getOutputStream());
            dataOut.writeBytes(leftPadZeros(String.valueOf(bytesLength), 8));
            dataOut.write(bytes);
            dataOut.close();
            socket.close();
            return true;
        } catch (IOException e) {
            TimedLog.e(TAG, "Error sending data" + e.toString());
            return false;
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
