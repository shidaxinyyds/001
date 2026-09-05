#!/usr/bin/env python3
# 生成 100 个内置卡密，写入 lib/license_keys.dart
# 卡密格式：6 位区分大小写的字母(a-zA-Z) + 6 位数字(0-9)
# 为了"无坑"体验，剔除易混淆字母 I/l/O/o（避免与 1/0 混淆）；
# 数字与字母分处前后两半、且校验时按位置区分，因此不会跨区混淆。
import secrets
import string

# 字母表：去掉视觉易混淆的 I l O o
letters = [c for c in string.ascii_letters if c not in set("IlOo")]
digits = string.digits

keys = set()
while len(keys) < 100:
    head = "".join(secrets.choice(letters) for _ in range(6))
    tail = "".join(secrets.choice(digits) for _ in range(6))
    keys.add(head + tail)

# 排序仅为稳定输出（便于 diff），顺序对校验无影响
keys = sorted(keys)
assert len(keys) == 100
assert len(set(keys)) == 100

lines = []
lines.append("// GENERATED FILE — 卡密（license key）内置清单。")
lines.append("// 格式：6 位区分大小写的字母(a-zA-Z) + 6 位数字(0-9)，共 100 个。")
lines.append("// 字母表已剔除易混淆字符 I/l/O/o（避免与 1/0 混淆）。")
lines.append("// 请勿手改；如需更换卡密，请重新运行 localtest/_gen_license_keys.py。")
lines.append("const List<String> kBuiltInLicenseKeys = <String>[")
for k in keys:
    lines.append(f"  '{k}',")
lines.append("];")
lines.append("")
lines.append("final Set<String> _licenseKeySet = Set<String>.from(kBuiltInLicenseKeys);")
lines.append("")
lines.append("/// 归一化用户输入：去除空格与连字符（兼容带 '-' 或空格的复制粘贴）。")
lines.append("String normalizeLicenseInput(String raw) =>")
lines.append("    raw.replaceAll(RegExp(r'[\\s-]'), '');")
lines.append("")
lines.append("/// 卡密校验结果。")
lines.append("enum LicenseCheck { empty, length, format, notFound, ok }")
lines.append("")
lines.append("/// 完整校验卡密：")
lines.append("/// 1) 去空格/连字符；2) 长度须为 12；")
lines.append("/// 3) 前 6 位须为字母、后 6 位须为数字；4) 须在内置清单内（区分大小写）。")
lines.append("LicenseCheck checkLicenseKey(String raw) {")
lines.append("  final key = normalizeLicenseInput(raw);")
lines.append("  if (key.isEmpty) return LicenseCheck.empty;")
lines.append("  if (key.length != 12) return LicenseCheck.length;")
lines.append("  final letters = key.substring(0, 6);")
lines.append("  final digits = key.substring(6, 12);")
lines.append("  if (!RegExp(r'^[a-zA-Z]{6}$').hasMatch(letters) ||")
lines.append("      !RegExp(r'^[0-9]{6}$').hasMatch(digits)) {")
lines.append("    return LicenseCheck.format;")
lines.append("  }")
lines.append("  if (!_licenseKeySet.contains(key)) return LicenseCheck.notFound;")
lines.append("  return LicenseCheck.ok;")
lines.append("}")
lines.append("")

out_path = r"D:/a/realtime-mahjong-trainer-main/lib/license_keys.dart"
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"WROTE {len(keys)} keys -> {out_path}")
print("SAMPLE:", ", ".join(list(keys)[:5]))
print("ALL_LEN_12:", all(len(k) == 12 for k in keys))
print("NO_CONFUSING:", all(c not in "IlOo" for k in keys for c in k[:6]))
