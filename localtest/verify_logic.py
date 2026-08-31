"""验证向听/进张/推荐打法逻辑的正确性（不依赖截图，用已知手牌对拍）。

策略：以「和牌 = -1」作为已知 oracle，其余用内部自洽性 + 教科书单钓案例验证。
（标准 mahjong 库的 shanten 公式是公认正确的，手算易错，故不直接对拍抽象数字。）
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYROOT = os.path.join(REPO, "android", "app", "src", "main", "python")
sys.path.insert(0, PYROOT)

from trainer.trainer import Trainer  # noqa: E402
from trainer.objects.tile_collection import TileCollection  # noqa: E402
from trainer.objects.tile import Tile  # noqa: E402
from trainer.utils.ukeire import calculate_ukeire  # noqa: E402

ok = True


def check(name, cond, detail=""):
    global ok
    mark = "PASS" if cond else "FAIL"
    if not cond:
        ok = False
    print(f"[{mark}] {name} {detail}")


# 1) 和牌 oracle：4 面子 + 1 对 -> -1
h_agari = "123456789m123p11s"
check("agari shanten == -1", Trainer(TileCollection.from_mpsz(h_agari)).get_shanten() == -1,
      f"(got {Trainer(TileCollection.from_mpsz(h_agari)).get_shanten()})")

# 2) 单钓听牌（唯一浮牌 9p）：4 面子 + 1 浮牌 -> 0，进张 = 剩余 9p = 3
h_tenpai = "123456789m123p9p"
tc = TileCollection.from_mpsz(h_tenpai)
tr = Trainer(tc)
s_ten = tr.get_shanten()
uk = calculate_ukeire(tc)
check("tanki shanten == 0", s_ten == 0, f"(got {s_ten})")
check("tanki ukeire == 3", uk == 3, f"(got {uk})")

# 3) 和牌去掉一张 -> 13 张必为听牌(0)；再去掉一张 -> 必 >= 0 且通常更高
ag = TileCollection.from_mpsz(h_agari)
h13 = ag.remove_tile(Tile("1s"))
check("agari minus 1 tile -> tenpai(0)", Trainer(h13).get_shanten() == 0,
      f"(got {Trainer(h13).get_shanten()})")

# 4) 推荐打法自洽性（14 张手牌：4 面子 + 浮 9p + 多一张无关 5m）
h14 = "123456789m123p9p5m"
t14 = TileCollection.from_mpsz(h14)
trainer = Trainer(t14)
base = trainer.get_shanten()
dd = trainer.calculate_discards()
check("14-hand base shanten computed", base >= 0, f"(base={base})")
for tile, uke in dd.items():
    rem = t14.remove_tile(tile)
    s_rem = Trainer(rem).get_shanten()
    u_re = calculate_ukeire(rem)
    check(f"discard {tile} keeps shanten (== base)", s_rem == base,
          f"(rem {s_rem}, base {base})")
    check(f"discard {tile} ukeire consistent", u_re == uke,
          f"(recompute {u_re}, returned {uke})")
    check(f"discard {tile} ukeire > 0", uke > 0, f"(uke={uke})")
# 反向：恶化向听的牌不应出现在结果里
for tile in t14.unique:
    s_rem = Trainer(t14.remove_tile(tile)).get_shanten()
    in_dd = tile in dd
    if s_rem > base:
        check(f"worse discard {tile} excluded", not in_dd,
              f"(rem {s_rem} > base {base})")
    else:
        check(f"non-worse discard {tile} included", in_dd,
              f"(rem {s_rem} <= base {base})")

# 5) 截图真实手牌演示（补一张成 14 张，给出推荐打法）
hand13 = "3m9m1s6s7s9s9s1p3p3p4p4p5p"
for extra in ["1m", "2p", "9s"]:
    h = hand13 + extra
    trb = Trainer(TileCollection.from_mpsz(h))
    b = trb.get_shanten()
    d = trb.calculate_discards()
    best = sorted(d.items(), key=lambda kv: -kv[1])[:3]
    print(f"  +{extra}: shanten={b} top_discards={[(str(t), u) for t, u in best]}")

print("\nALL CHECKS PASSED" if ok else "\nSOME CHECKS FAILED")
assert ok, "LOGIC VERIFICATION FAILED"
