import 'dart:io';

// 在指定端口上监听原生层（ImageProcessor）发来的分析结果。
// 每帧数据格式：8 字节长度前缀（十进制，前补 0）+ 负载（JSON + '\n' + PNG 图片）。
class Server {
  void Function(List<int>) callback;
  ServerSocket? _server;

  Server({
    required this.callback,
    required String host,
    required int port,
  }) {
    print("分析服务已启动，监听 $host:$port");
    // 监听本机回环地址即可（ImageProcessor 发送到 127.0.0.1），无需监听所有网卡。
    // shared: false —— 端口独占绑定。旧实现 shared:true（SO_REUSEADDR）允许
    // 新旧两个悬浮窗 State 同时监听同一端口，Java 的短连接被旧监听器抢走，
    // 造成关窗重开后丢帧。现在残留监听未释放时绑定直接失败报错（可见可诊断），
    // 绝不再静默抢连接。配合 dispose 里的 close()，正常路径不会占用端口。
    Future<ServerSocket> serverFuture = ServerSocket.bind(
      InternetAddress.loopbackIPv4,
      port,
      shared: false,
    );
    serverFuture.then((ServerSocket server) {
      _server = server;
      server.listen((Socket socket) {
        try {
          socket.setOption(SocketOption.tcpNoDelay, true);
        } catch (_) {}

        final List<int> buffer = [];
        int expectedLength = -1;

        socket.listen((List<int> chunk) {
          buffer.addAll(chunk);

          while (true) {
            if (expectedLength < 0) {
              if (buffer.length < 8) {
                // 尚未累积够 8 字节长度头
                break;
              }
              final lenStr = String.fromCharCodes(buffer.sublist(0, 8));
              final parsed = int.tryParse(lenStr);
              if (parsed == null || parsed <= 0) {
                print("数据长度解析失败: $lenStr");
                try {
                  socket.destroy();
                } catch (_) {}
                return;
              }
              expectedLength = parsed;
              buffer.removeRange(0, 8);
            }

            if (buffer.length < expectedLength) {
              // 尚未累积够当前帧完整负载
              break;
            }

            // 完整提取当前帧
            final frameData = buffer.sublist(0, expectedLength);
            buffer.removeRange(0, expectedLength);
            expectedLength = -1;

            try {
              callback(frameData);
            } catch (e) {
              print("悬浮窗 callback 执行异常: $e");
            }
          }
        }, onError: (err) {
          try {
            socket.destroy();
          } catch (_) {}
        }, onDone: () {
          try {
            socket.destroy();
          } catch (_) {}
        }, cancelOnError: true);
      }, onError: (err) {
        print("ServerSocket listen 异常: $err");
      });
    }).catchError((e) {
      print("分析服务启动失败：$e");
    });
  }

  /// 关闭监听（State.dispose 时必须调用）：否则旧 State 的 Server 泄漏，
  /// 端口被占、后续重开绑定失败或旧监听抢走 Java 侧短连接。
  void close() {
    try {
      _server?.close();
    } catch (_) {}
    _server = null;
  }
}
