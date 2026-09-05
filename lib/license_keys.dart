// GENERATED FILE — 卡密（license key）内置清单。
// 格式：6 位区分大小写的字母(a-zA-Z) + 6 位数字(0-9)，共 100 个。
// 字母表已剔除易混淆字符 I/l/O/o（避免与 1/0 混淆）。
// 请勿手改；如需更换卡密，请重新运行 localtest/_gen_license_keys.py。
const List<String> kBuiltInLicenseKeys = <String>[
  'AVksyr072161',
  'AaWcpj128391',
  'CryGjc356127',
  'CsUGHv470206',
  'DLGWjc504161',
  'DNjZVk152319',
  'Ddfffc691011',
  'DmbJMh671329',
  'EEyUDY468261',
  'Ebpjce981909',
  'EeiaHY535018',
  'EnHkhe280837',
  'GiFYYU847590',
  'GpqLrg962900',
  'GssaCE062887',
  'JFheDW828052',
  'JuwguJ317499',
  'KDrfvU654863',
  'KhVrad482805',
  'LGWsxX932253',
  'LLRJzK472169',
  'MFJGuZ241151',
  'MUwEnH882085',
  'MudbqM920735',
  'NBELGX243598',
  'PhcwZZ127118',
  'PkfDCy394443',
  'RwawHf487337',
  'SJtSiQ885563',
  'TqNdvt288062',
  'UWEbSt345821',
  'VGBEsB473462',
  'VbrSzk562701',
  'VgDiGv432894',
  'WPmSUN042063',
  'WvzVGE610281',
  'YKmrZi313861',
  'aqNRKS390413',
  'bDyvhr712060',
  'bWjgHe737739',
  'bXAHNF450714',
  'basXFh552955',
  'cCPVzM210600',
  'cFzpzg941040',
  'cMfSXc985922',
  'caqYnm358735',
  'ciemLA715180',
  'ddrHcm172027',
  'efwbxm972951',
  'ejFpkr994801',
  'fBuqxL597609',
  'fhRkYp352650',
  'fkmrte582512',
  'fpuVse590797',
  'hWNuRy158267',
  'iAzaYQ824438',
  'iDkbYa528177',
  'iEzBvw462066',
  'iKJQeN385535',
  'ierUYy842600',
  'jJXMBr457195',
  'kPDrbZ977103',
  'kveSXP583774',
  'mNMWcu237961',
  'mkaJZB326284',
  'nTiQhD740375',
  'naGDFe745831',
  'niAmqi077186',
  'ntQafZ226840',
  'pSxpcM973735',
  'pWWGsS600584',
  'pmALJj089910',
  'qAHmXf860872',
  'qApRmC461409',
  'qMvvtr144405',
  'qwWzvH526128',
  'qwuZGj403351',
  'rMXZrS347659',
  'rPFHJW525842',
  'rRbXBm042008',
  'sFeBQR550822',
  'szctcp438350',
  'uFfSER628660',
  'uSLEya827800',
  'uUzzHA220935',
  'ugLfdk734609',
  'uicNmA954221',
  'vERnyg617083',
  'vhACPT266932',
  'vhqUtC799210',
  'vncNqk433345',
  'vqWBXr117866',
  'whxRnA235232',
  'xDJXmp405455',
  'xdsdjX419328',
  'xsnBHB008886',
  'ytAsAL628101',
  'ywmBND734907',
  'zRzZAa995979',
  'zpxweA728817',
];

final Set<String> _licenseKeySet = Set<String>.from(kBuiltInLicenseKeys);

/// 归一化用户输入：去除空格与连字符（兼容带 '-' 或空格的复制粘贴）。
String normalizeLicenseInput(String raw) =>
    raw.replaceAll(RegExp(r'[\s-]'), '');

/// 卡密校验结果。
enum LicenseCheck { empty, length, format, notFound, ok }

/// 完整校验卡密：
/// 1) 去空格/连字符；2) 长度须为 12；
/// 3) 前 6 位须为字母、后 6 位须为数字；4) 须在内置清单内（区分大小写）。
LicenseCheck checkLicenseKey(String raw) {
  final key = normalizeLicenseInput(raw);
  if (key.isEmpty) return LicenseCheck.empty;
  if (key.length != 12) return LicenseCheck.length;
  final letters = key.substring(0, 6);
  final digits = key.substring(6, 12);
  if (!RegExp(r'^[a-zA-Z]{6}$').hasMatch(letters) ||
      !RegExp(r'^[0-9]{6}$').hasMatch(digits)) {
    return LicenseCheck.format;
  }
  if (!_licenseKeySet.contains(key)) return LicenseCheck.notFound;
  return LicenseCheck.ok;
}
