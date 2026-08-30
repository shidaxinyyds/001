package com.example.realtime_mahjong_trainer;

import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;

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

    public void send(byte[] bytes) {
        Socket socket = new Socket();

        int bytesLength = bytes.length;
        try {
            socket.connect(new InetSocketAddress(host, port), 1000);
            DataOutputStream dataOut = new DataOutputStream(socket.getOutputStream());
            dataOut.writeBytes(leftPadZeros(String.valueOf(bytesLength), 8));
            dataOut.write(bytes);
            dataOut.close();
            socket.close();
        } catch (IOException e) {
            TimedLog.e(TAG, "Error sending data" + e.toString());
        }
    }
}
