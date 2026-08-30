// 将 mpsz 记法（如 "1m"、"5p"、"1z"）的牌名转换为中文显示用名（如 "1万"、"5筒"、"东"）。
// 与 Python 端 trainer/utils/convert.py 中的映射保持一致。

const Map<String, String> _suitCn = {
  'm': '万',
  'p': '筒',
  's': '条',
};

const Map<String, String> _honorCn = {
  '1z': '东',
  '2z': '南',
  '3z': '西',
  '4z': '北',
  '5z': '白',
  '6z': '发',
  '7z': '中',
};

String tileToChinese(String name) {
  if (_honorCn.containsKey(name)) return _honorCn[name]!;
  if (name.length == 2 && _suitCn.containsKey(name[1])) {
    return '${name[0]}${_suitCn[name[1]]}';
  }
  return name;
}

String handToChinese(String mpsz) {
  final buffer = StringBuffer();
  final temp = StringBuffer();
  for (final ch in mpsz.split('')) {
    if (RegExp(r'[0-9]').hasMatch(ch)) {
      temp.write(ch);
    } else if ('mpsz'.contains(ch)) {
      for (final d in temp.toString().split('')) {
        buffer.write(tileToChinese('$d$ch'));
      }
      temp.clear();
    }
  }
  return buffer.toString();
}
