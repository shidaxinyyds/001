import 'dart:io';


class Server {
  void Function(List<int>) callback;

  Server({
    required this.callback,
    required String host,
    required int port,
  }) {
    print("server started");
    Future<ServerSocket> serverFuture = ServerSocket.bind('0.0.0.0', 55555);
    serverFuture.then((ServerSocket server) {
      server.listen((Socket socket) {
        List<int> metadataBuffer = [];
        List<int> dataBuffer = [];
        int length = -1;
        socket.listen((List<int> data) {
          if (length < 0) {
            if (metadataBuffer.length + data.length < 8) {
              metadataBuffer.addAll(data);
              return;
            }
            int reserve = 8 - metadataBuffer.length;
            metadataBuffer.addAll(data.sublist(0, reserve));
            int? dataLength =
                int.tryParse(String.fromCharCodes(metadataBuffer));
            if (dataLength == null) {
              print("Invalid data length");
              return;
            }
            length = dataLength;
            data = data.sublist(reserve);
            print("Incoming connection of $dataLength");
          }
          dataBuffer.addAll(data);
          if (data.length == length) {
            callback(dataBuffer);
          }
        });
      });
    });
  }
}
