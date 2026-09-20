package com.example.auto_vision;

import java.io.BufferedOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.atomic.AtomicLong;

public class NetworkClient {
    String host;
    int port;

    final String TAG = "NetworkClient";

    private Socket mSocket = null;
    private DataOutputStream mDataOut = null;
    private final Object mLock = new Object();

    // ===== 异步发送：采集线程不再被 socket 写/重连阻塞 =====
    // 旧 send() 在采集线程同步写 socket，连接抖动时 socket.connect 超时(1s)+两次
    // 重试可阻塞采集线程最长 ~2s，表现为整条识别链路瞬间卡死。现在 send() 只做
    // 入队（有界，满则丢最旧帧——陈旧帧对实时建议无意义），真正写 socket 在独立
    // 守护线程完成；发送失败累计到 sendErrors，供心跳/Dart 侧观测。
    private static final int QUEUE_CAP = 4;
    private final LinkedBlockingDeque<byte[]> mQueue = new LinkedBlockingDeque<>(QUEUE_CAP);
    private final AtomicLong sendErrors = new AtomicLong(0);
    private volatile boolean mClosed = false;
    private Thread mWriter;

    public NetworkClient(String host, int port) {
        this.host = host;
        this.port = port;
    }

    private void ensureWriterLocked() {
        if (mWriter != null && mWriter.isAlive()) return;
        mClosed = false;
        mWriter = new Thread(this::writerLoop, "net-send");
        mWriter.setDaemon(true);
        mWriter.start();
    }

    private void writerLoop() {
        while (!mClosed) {
            byte[] bytes;
            try {
                bytes = mQueue.take();
            } catch (InterruptedException e) {
                break;
            }
            if (!writeBlocking(bytes)) {
                // 单次失败已在 writeBlocking 内重试过一次并计数；此处不退出，
                // 等下一帧再尝试（连接会在下次 writeBlocking 里自愈重连）。
            }
        }
        synchronized (mLock) {
            closeLocked();
        }
    }

    /** 实际写 socket（仅在 writer 线程调用）：失败重连重试一次。 */
    private boolean writeBlocking(byte[] bytes) {
        synchronized (mLock) {
            try {
                ensureConnectedLocked();
                mDataOut.writeBytes(leftPadZeros(String.valueOf(bytes.length), 8));
                mDataOut.write(bytes);
                mDataOut.flush();
                return true;
            } catch (IOException e1) {
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
                    sendErrors.incrementAndGet();
                    return false;
                }
            }
        }
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
        mClosed = true;
        if (mWriter != null) {
            try { mWriter.interrupt(); } catch (Exception ignore) {}
            mWriter = null;
        }
        synchronized (mLock) {
            closeLocked();
        }
    }

    /** 累计发送失败次数（供 ImageProcessor 心跳读取）。 */
    public long getSendErrors() {
        return sendErrors.get();
    }

    /**
     * 发送一帧数据到 Dart 悬浮窗监听的本地端口：非阻塞入队，满则丢最旧。
     * 返回 true 表示已接受（将由 writer 线程送达），false 仅在本客户端已关闭。
     */
    public boolean send(byte[] bytes) {
        if (bytes == null || bytes.length == 0) return false;
        if (mClosed) return false;
        ensureWriterLocked();
        // 有界队列：满了丢最旧（陈旧帧比丢弃更糟，会拖慢实时建议的更新）。
        while (!mQueue.offerLast(bytes)) {
            if (mQueue.pollFirst() == null) break;
        }
        return true;
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
