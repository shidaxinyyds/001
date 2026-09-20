"""生成卡密并输出可直接粘贴进 Supabase SQL Editor 的 INSERT 语句。

用法（Windows PowerShell）：
    py -3.10 localtest/gen_cards.py --type month --days 30 --count 20
    py -3.10 localtest/gen_cards.py --type week  --days 7  --count 50 --prefix MJ

产出的明文卡密只显示一次（发客户用），数据库里只存 SHA256(code)，
即使库泄露也无法反推卡密。把打印出来的 INSERT 贴进 Supabase SQL Editor 执行即可。
"""
import argparse
import hashlib
import secrets
import string

# 去掉易混字符 O/0 I/1 L，避免用户手抄出错。
_ALPHABET = "".join(c for c in (string.ascii_uppercase + string.digits) if c not in "OI0L")


def gen_code(prefix: str) -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(16))
    groups = [body[i:i + 4] for i in range(0, 16, 4)]
    code = "-".join(groups)
    return f"{prefix}-{code}" if prefix else code


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", default="month", help="card_type 标签，如 week/month")
    ap.add_argument("--days", type=int, required=True, help="有效天数（激活起算）")
    ap.add_argument("--count", type=int, default=1, help="生成张数")
    ap.add_argument("--prefix", default="MJ", help="卡密前缀，便于区分批次")
    args = ap.parse_args()

    duration_s = args.days * 86400
    rows = []
    print("\n===== 明文卡密（只显示一次，发给客户）=====")
    for _ in range(args.count):
        code = gen_code(args.prefix)
        h = hashlib.sha256(code.encode("utf-8")).hexdigest()
        rows.append(h)
        print(code)

    print("\n===== 粘贴到 Supabase SQL Editor 执行 =====")
    values = ",\n".join(
        f"  ('{h}', '{args.type}', {duration_s})" for h in rows
    )
    print(
        "insert into public.card_keys (code_hash, card_type, duration_s) values\n"
        f"{values};"
    )
    print(f"\n共 {args.count} 张，{args.type} 卡，有效期 {args.days} 天。")


if __name__ == "__main__":
    main()
