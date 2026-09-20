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
# 1) 和牌 oracle：4 面子 + 1 对 -> -1 (标准 136 牌规则)
h_agari = "123456789m123p11s"
check("agari shanten == -1", Trainer(TileCollection.from_mpsz(h_agari), mode="std").get_shanten() == -1,
      f"(got {Trainer(TileCollection.from_mpsz(h_agari), mode='std').get_shanten()})")

# 2) 单钓听牌（唯一浮牌 9p）：4 面子 + 1 浮牌 -> 0，进张 = 剩余 9p = 3
h_tenpai = "123456789m123p9p"
tc = TileCollection.from_mpsz(h_tenpai)
tr = Trainer(tc, mode="std")
s_ten = tr.get_shanten()
uk = calculate_ukeire(tc)
check("tanki shanten == 0", s_ten == 0, f"(got {s_ten})")
check("tanki ukeire == 3", uk == 3, f"(got {uk})")

# 3) 和牌去掉一张 -> 13 张必为听牌(0)；再去掉一张 -> 必 >= 0 且通常更高
ag = TileCollection.from_mpsz(h_agari)
h13 = ag.remove_tile(Tile("1s"))
check("agari minus 1 tile -> tenpai(0)", Trainer(h13, mode="std").get_shanten() == 0,
      f"(got {Trainer(h13, mode='std').get_shanten()})")

# 4) 推荐打法自洽性（14 张手牌：4 面子 + 浮 9p + 多一张无关 5m）
h14 = "123456789m123p9p5m"
t14 = TileCollection.from_mpsz(h14)
trainer = Trainer(t14, mode="std")
base = trainer.get_shanten()
dd = trainer.calculate_discards()
check("14-hand base shanten computed", base >= 0, f"(base={base})")
for tile, uke in dd.items():
    rem = t14.remove_tile(tile)
    s_rem = Trainer(rem, mode="std").get_shanten()
    u_re = calculate_ukeire(rem)
    check(f"discard {tile} keeps shanten (== base)", s_rem == base,
          f"(rem {s_rem}, base {base})")
    check(f"discard {tile} ukeire consistent", u_re == uke,
          f"(recompute {u_re}, returned {uke})")
    check(f"discard {tile} ukeire > 0", uke > 0, f"(uke={uke})")
# 反向：恶化向听的牌不应出现在结果里
for tile in t14.unique:
    s_rem = Trainer(t14.remove_tile(tile), mode="std").get_shanten()
    in_dd = tile in dd
    if s_rem > base:
        check(f"worse discard {tile} excluded", not in_dd,
              f"(rem {s_rem} > base {base})")
    else:
        check(f"non-worse discard {tile} included", in_dd,
              f"(rem {s_rem} <= base {base})")

# 5) 四川麻将规则验证（缺一门，万条两门手牌，0=听/和）
h_sc_agari = "123456789m12344s"
tr_sc = Trainer(TileCollection.from_mpsz(h_sc_agari), mode="sc")
check("sichuan agari shanten == 0", tr_sc.get_shanten() == 0,
      f"(got {tr_sc.get_shanten()})")

# 6) 截图真实手牌演示（补一张成 14 张，给出推荐打法）
hand13 = "3m9m1s6s7s9s9s1p3p3p4p4p5p"
for extra in ["1m", "2p", "9s"]:
    h = hand13 + extra
    trb = Trainer(TileCollection.from_mpsz(h), mode="std")
    b = trb.get_shanten()
    d = trb.calculate_discards()
    best = sorted(d.items(), key=lambda kv: -kv[1])[:3]
    print(f"  +{extra}: shanten={b} top_discards={[(str(t), u) for t, u in best]}")

# 7) 阶段1：川麻决策引擎——13 张预摸牌期望 / 定缺全链约束 / 3n 向听正确定义
from sichuan import SichuanAnalyzer  # noqa: E402
from sichuan.sichuan_analyzer import pool_remaining_from_visible  # noqa: E402

# 7a) 截图复现场景：334666888999筒+中（13 张），必须给出非零期望进张
c13 = SichuanAnalyzer.counts_from_tiles(
    SichuanAnalyzer.parse_hand_mpsz('334666888999p7z'))
pool28 = pool_remaining_from_visible(c13, None, None)
res13 = SichuanAnalyzer.analyze_discards(c13, pool_remaining=pool28, dingque_suit=0)
check('13张场景产出候选', len(res13) >= 3, f'(got {len(res13)})')
check('13张场景候选均为手牌持有张',
      all(c13[r['tile_idx']] > 0 for r in res13))
check('13张场景最优进张非零', bool(res13) and res13[0]['ukeire'] > 0,
      f"(best={res13[0]['tile']} ukeire={res13[0]['ukeire']})" if res13 else '(empty)')
check('13张场景候选进张之和>0', sum(r['ukeire'] for r in res13) > 0,
      f"(sum={sum(r['ukeire'] for r in res13)})")

# 7b) 定缺全链约束：含定缺牌的手牌无叫口/不胜；干净手叫口不含定缺花色牌
c_wait = SichuanAnalyzer.counts_from_tiles(
    SichuanAnalyzer.parse_hand_mpsz('2233m123p456p789p'))
w_free = SichuanAnalyzer.find_waiting_tiles(list(c_wait), 0, None, None)
w_dq = SichuanAnalyzer.find_waiting_tiles(list(c_wait), 0, None, 0)
check('不传定缺时双对听万子', len(w_free) > 0, f'(got {sorted(w_free.keys())})')
check('缺万花猪手无叫口', w_dq == {}, f'(got {sorted(w_dq.keys())})')
c_win14 = SichuanAnalyzer.counts_from_tiles(
    SichuanAnalyzer.parse_hand_mpsz('22333m123p456p789p'))
check('缺万时含万14张判不胜',
      SichuanAnalyzer.can_win(list(c_win14), 0, 0) is False)
check('不传定缺时正常胡牌成立',
      SichuanAnalyzer.can_win(list(c_win14), 0, None) is True)
c_clean = SichuanAnalyzer.counts_from_tiles(
    SichuanAnalyzer.parse_hand_mpsz('123p456p111s23s99s'))
w_clean = SichuanAnalyzer.find_waiting_tiles(list(c_clean), 0, None, 0)
check('缺万干净手叫口非空且不含万子',
      bool(w_clean) and all(t >= 9 for t in w_clean), f'(got {sorted(w_clean.keys())})')
res_clean = SichuanAnalyzer.analyze_discards(
    c_clean, pool_remaining=pool_remaining_from_visible(c_clean, None, None),
    dingque_suit=0)
check('缺万干净手候选无万子',
      all(r['tile_idx'] >= 9 for r in res_clean),
      f"(got {[r['tile'] for r in res_clean]})")

# 7c) 3n（12 张）向听正确定义：「补一张即听」= 0 向听，不再短路成常量 2
c12 = SichuanAnalyzer.counts_from_tiles(
    SichuanAnalyzer.parse_hand_mpsz('123p456p111s23s9s'))
s12 = SichuanAnalyzer.calculate_shanten(c12)
check('12张补一张即听 shanten==0', s12 == 0, f'(got {s12})')
c12b = SichuanAnalyzer.counts_from_tiles(
    SichuanAnalyzer.parse_hand_mpsz('123p456p111s26s9s'))
s12b = SichuanAnalyzer.calculate_shanten(c12b)
check('12张一轮循环可达听 shanten==1', s12b == 1, f'(got {s12b})')

print("\nALL CHECKS PASSED" if ok else "\nSOME CHECKS FAILED")
assert ok, "LOGIC VERIFICATION FAILED"
