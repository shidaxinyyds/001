import 'package:flutter/material.dart';

class TilesPainter extends CustomPainter {
  List<dynamic> tiles;
  double devicePixelRatio;

  TilesPainter(this.tiles, this.devicePixelRatio);

  @override
  void paint(Canvas canvas, Size size) {
    final int offsetX = 10;
    final int offsetY = -25;

    Paint paint = Paint()
      // Painting a border around tiles will affect edge detection
      ..color = Colors.blue.withAlpha(50)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5;

    tiles.forEach((tile) {
      var rect = tile[0];
      var tileName = tile[1];
      double x = rect[0] / devicePixelRatio;
      double y = rect[1] / devicePixelRatio;
      double w = rect[2] / devicePixelRatio;
      double h = rect[3] / devicePixelRatio;
      canvas.drawRect(Rect.fromLTWH(x, y, w, h), paint);
      TextSpan span =
          TextSpan(style: TextStyle(color: Colors.white), text: tileName);
      TextPainter tp = TextPainter(
          text: span,
          textAlign: TextAlign.left,
          textDirection: TextDirection.ltr);
      tp.layout();
      tp.paint(canvas, Offset(x + offsetX, y + offsetY));
    });
  }

  @override
  bool shouldRepaint(covariant TilesPainter oldPainter) {
    return true;
  }
}
