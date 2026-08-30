import 'dart:io';

// 在指定端口上监听原生层（ImageProcessor）发来的分析结果。
// 每帧数据格式：8 字节长度前缀（十进制，前补 0）+ 负载（JSON + '\n' + PNG 图片）。
class Server {
  void Function(List<int>) callback;

  Server({
    required this.callback,
    required String host,
    required int port,
  }) {
    print("分析服务已启动，监听 $host:$port");
    // 监听本机回环地址即可（ImageProcessor 发送到 127.0.0.1），无需监听所有网卡。
    // shared: true -> SO_REUSEADDR，悬浮窗关闭后重新打开可复用端口，避免 TIME_WAIT 占用。
    Future<ServerSocket> serverFuture = ServerSocket.bind(
      InternetAddress.loopbackIPv4,
      port,
      shared: true,
    );
    serverFuture.then((ServerSocket server) {
      server.listen((Socket socket) {
        List<int> metadataBuffer = [];
        List<int> dataBuffer = [];
        int length = -1;
        socket.listen((List<int> data) {
          if (length < 0) {
            // 尚未读完 8 字节长度前缀
            if (metadataBuffer.length + data.length < 8) {
              metadataBuffer.addAll(data);
              return;
            }
            int reserve = 8 - metadataBuffer.length;
            metadataBuffer.addAll(data.sublist(0, reserve));
            int? dataLength =
                int.tryParse(String.fromCharCodes(metadataBuffer));
            if (dataLength == null) {
              print("数据长度解析失败");
              return;
            }
            length = dataLength;
            data = data.sublist(reserve);
          }
          dataBuffer.addAll(data);
          // 注意：必须以累计长度 dataBuffer.length 判断，不能用单个分片长度。
          if (dataBuffer.length >= length) {
            callback(dataBuffer.sublist(0, length));
            // 单次连接只发送一帧，重置以便复用（实际上连接随后即关闭）。
            dataBuffer = [];
            metadataBuffer = [];
            length = -1;
          }
        });
      });
    }).catchError((e) {
      print("分析服务启动失败：$e");
    });
  }
}
