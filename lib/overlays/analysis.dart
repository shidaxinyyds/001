import 'package:flutter/material.dart';

class Analysis extends StatefulWidget {
  late final Map<String, dynamic>? incomingAnalysis;

  Analysis(Map<String, dynamic>? analysis) {
    incomingAnalysis = analysis;
  }

  @override
  State<Analysis> createState() => _AnalysisState();
}

class _AnalysisState extends State<Analysis> {
  Map<String, dynamic>? analysis = null;

  @override
  Widget build(BuildContext context) {
    if (widget.incomingAnalysis != null) {
      setState(() {
        analysis = widget.incomingAnalysis;
      });
      analysis = widget.incomingAnalysis;
    }

    Widget textWidget = Container(height: 10);
    if (analysis != null) {
      int? shanten = analysis!['shanten'];
      String? commentary = analysis!['commentary'];
      textWidget = Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Text("Shanten: ${shanten.toString()}"),
          Text(
            commentary ?? "",
            textAlign: TextAlign.end,
          ),
        ],
      );
    }

    return Container(
      decoration: BoxDecoration(
        color: Colors.lightBlue.withAlpha(200),
        borderRadius: BorderRadius.all(Radius.circular(15)),
      ),
      child: DefaultTextStyle(
        style: TextStyle(
          color: Colors.white,
          fontSize: 15,
        ),
        child: SizedBox(
          width: 200,
          child: Padding(padding: const EdgeInsets.all(8.0), child: textWidget),
        ),
      ),
    );
  }
}
