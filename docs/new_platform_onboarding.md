# 新牌面平台接入流程（可复用 SOP）

首个完整案例：**蜀山四川麻将（红中血流）**，全程工具链已落地在本仓库，
接新平台时按本文照抄改名即可。目标：手牌识别 100%、旧平台零回退、
每帧识别开销不随平台数增长。

## 架构速览（识别侧多平台挂载点）

- 模板统一进 `TencentGridDetector._cores`，条目为 `(标签, 风格, 模板)`。
  - 腾讯主库 = `recognition/templates_data.py`（风格 `tencent`）。
  - 新平台 = 新增一个 `recognition/templates_<platform>.py`（导出
    `TEMPLATES_BGR`，键 `label` 或 `label#变体`），并在
    `tencent_grid_detector.py::_load_templates` 的登记表中加一行
    `(mod_name, style)` —— 这是接新平台唯一的引擎侧改动点。
- **变体键约定**：同一张牌的不同视觉形态用 `#` 后缀共存于同标签，分类按
  标签聚合取最高分。已用过的变体：`7s#b`（蓝「缺」角标态）、`7z#s4`
  （发牌动画小尺度态）。模板对尺度敏感：新截图若牌高与既有样本差 >20%，
  通常需要补该尺度变体，而不是改算法。
- **风格探针路由**（`_probe_style` + `detect_hand_strip`）：每帧取手牌主块中央一枚
  代表牌 + 若干额外代表牌（生产传 3 枚）对全部 bank 打分，**各枚风格分取平均**后
  胜出风格（≥0.60）决定张数候选模型（腾讯=重叠节距 `0.0547*iw`；其余=相邻牌宽
  `bh/1.35`）并把分类限定在该风格 bank 内 → 多平台共库时单帧开销 ≈ 单平台。
  单枚定路由实测只有 39% 命中（选错 bank = 拿别家字模认这家的牌，腾讯底座因此
  从 100% 被拖到 86%），多枚平均后离线 84%、线上整帧实切 49%；未路由的帧退到
  全库兜底重扫（慢但不硬认错牌）。腾讯帧路由回旧候选集，行为与接入前完全等价。
- **探针归因不能当平台真值**：在 4 帧已人工钉风格的素材上，不声明平台的探针
  只判对 **2/4**（`public/0` 的 frame 22 真值 queshen→判 jj、frame 31 真值
  shushan→判 tencent）。所以 `probe_platforms.py` 的风格列只能用来**分组**，
  补库优先级必须人工复核。根因同上：路由靠的是牌面美术相似度，不是平台身份。
- **手牌通道**（`Engine.get_hand_detector`）：主检测器是 `YOLODetector` 时，手牌行
  走模板 NCC —— 前提是该平台已挂本家 bank。清单由 `banked_platforms()` 从
  `_load_templates` 登记表 + `STYLE_PLATFORM_WHITELIST` **推导**，所以第 5 步建完
  bank 后该平台的手牌自动从 YOLO 切到 NCC，不需要再改引擎。牌河/副露/阶段探测
  仍走主检测器；调试页 `hand_grid` 开关可一键退回旧行为。实测依据（同帧对拍，
  7 帧 91 张帧级样本外素材）：NCC 89.0% vs YOLO 75.8%（+13.2pt），代价是手牌行
  中位耗时从 103.6ms 涨到 175.3ms（独占 CPU 复跑，`--channels`）。
- **注入面纪律**：守卫用「覆盖 `eng.get_detector` 塞假货再驱 `process`」的写法，
  手牌通道会尊重这种注入（只接管 YOLO）；新增第二条通道时不要绕过注入点，
  否则 `test_process_stability` / `test_tile_ledger_e2e` 这类套件测不到自己写入的
  检测器（本轮实测：绕过注入会让 4 个套件同时变红）。

## 接入步骤

1. **建帧集**：截图放 `localtest/shots_<platform>/`（命名 `s1.jpg`…）。
   一批混合素材先跑归因+质量门（**SOP 第 1 步的唯一入口，不要靠看缩略图猜平台**）：
   `py -3.10 -X utf8 localtest/probe_platforms.py` → `build/platform_probe.txt`，
   输出逐帧风格归属/分辨率簇/字节重复/视觉近似重复/竖屏·过暗·糊·张数异常标记，
   以及需要人工复核的帧清单（门限是脚本常量 `BLUR_MIN=40`、`DARK_MEAN=24`、
   `HAND_COUNT_OK=(13,14)`，可调可复算）。**遮挡无法自动判定**，脚本只给待复核标记。
2. **诊断**：仿 `localtest/diag_shushan.py`，用现行检测器逐帧打印
   rect/label/score 并导出 `diag_*.jpg`、`strip_*.jpg` 标注图；先回答三问：
   ①牌面美术是否超出旧模板 ②手牌排布节距模型是否失配（张数估计崩→整行
   拒识）③角标（赖/缺/选中）是否污染牌面。
3. **钉 GT**：人工逐图读手牌裁片把手牌多重集（mpsz：`1-9m/1-9p/
   1-9s/7z=红中`）写死进 `eval_<platform>.py` 的 GT 表。任何自动标注不可信。
   批量化做法（已落地，可照抄）：
   `py -3.10 localtest/make_tile_sheets.py` 把检测出的每枚裁片拼成**只编号
   不贴标签**的表（默认 7 枚/张、牌高 300px；拼太多会被查看时等比缩小到
   筒子点数分辨不清），逐张人工读图后把 `序号→标签` 写进 GT json，再交给
   `py -3.10 localtest/eval_new_material.py` 出报告。GT 里必须写明标注方式，
   否则下次没人知道这些标签从哪来。
4. **收割样本**：仿 `localtest/harvest_shushan.py`——已知 GT 的手牌行按实测
   rect 直出高清样本；牌河/明牌区先裁候选拼 `montage.png` 人工读图定标。
5. **建 bank**：仿 `localtest/build_shushan_bank.py`，JOBS 表 =
   `(截图, x, y, w, h, 模板键)`；`extract_face` 去绿边 → 80x120 →
   生成 `recognition/templates_<platform>.py` + PNG 留档。然后在
   `_load_templates` 登记表加 `(recognition.templates_<platform>, "<platform>")`。
6. **过门禁**（全部必跑，顺序不限）：
   - `py -3.10 localtest/eval_<platform>.py` → 新平台手牌 100%（RESULT: PASS）
   - `py -3.10 localtest/eval_base.py` → 腾讯 25 帧 100% 不回退
   - `py -3.10 localtest/test_sichuan_logic.py`（21 项决策逻辑）
   - `py -3.10 localtest/test_engine_phase_guard.py`（牌池物理门控）
   - `py -3.10 localtest/test_hand_channel.py`（手牌通道路由 + rows 缓存来源门 +
     注入面被尊重；收割完 bank 后它会自动要求该平台走 NCC）
   - 一把跑完上面这些并落 UTF-8 报告：`py -3.10 localtest/run_all_tests.py`
     （PowerShell 5.1 的 GBK 会吞掉中文结论行，所以报告由脚本自己写文件；
     该脚本会自动发现新增的 `test_*`/`eval_*`，“加了守卫但没人跑”这条路被堵死）

## 离线评测口径（读任何百分比之前先读这条）

- **必须像生产一样声明平台**。`Engine` 每帧 `load_platform()` 决定风格白名单，
  离线脚本不声明就会读到 cwd 里残留的 `mahjong_platform.json`（或落到默认平台），
  于是“拿别家字模认这家的牌”，测出的数字与面板实际表现没有对应关系。固定写法：
  先 `engine.engine.load_platform = lambda *a, **kw: <平台 key>` 再建 `Engine()`
  （`localtest/layer_cost.py`、`localtest/test_hand_channel.py` 均如此）。
- **样本内/外必须标明**。模板 bank 若来自某批帧，这批帧的手牌准确率是上限而非
  预期（腾讯底座 253 张 100% 就是这种）；可外推的数字只能来自 bank 未收割过的帧
  （`localtest/gt/new_shots.json`）。
- **单帧冷喂 Engine 不等于生产连续帧**。稳定器、warmup、阶段清空都看不到历史：
  用它评手牌会低估，用它评阶段/稳定性会高估失败率。
- **“styles 入参为 None” 不等于“跨全库扫”**。声明平台后 `_resolve_styles(None)`
  返回的是该平台白名单，不是全库。插桩若拿入参判路由，会把“本家候选”报成
  “兜底全扫”，进而把 **bank 缺类**问题误述成**路由**问题（本轮在
  `eval_new_material.py` 上踩过）。要记路由就记 `_resolve_styles` 的**解析结果**。
- 实测样本外精度与 98% 的可行性数学见 `docs/accuracy_report_2026-10.md`。

## 排查顺序（先分病再开药）

低分/认错牌先跑 `py -3.10 localtest/bank_coverage.py` 对照，三种病因法不同：

1. **缺类**（预测标签根本不在该风格 bank 里）→ 补样本建模板（第 4/5 步），
   调算法/阈值无效。判定技巧：若输出的标签不在该 bank 的标签集内，说明这一帧
   探针未路由成功、走了跨全部 bank 兜底重扫，于是会“认出别家平台的牌”；
   用 `py -3.10 localtest/bench_latency.py --per-frame` 可以看到该帧有多少次
   跨全库比对（这些帧也是 p95 到秒级的来源）。
2. **新尺度 / 新角标态**（标签对但分低）→ 给该标签补 `#<变体>` 模板。
3. **真分类失误**（标签在 bank 内、分数也不低，但认错了）→ 才是算法/几何问题，
   参照 `localtest/test_hand_strip_gate.py` 加守卫 + 变异检验后再改。

### 分层归因与工具登记（复现路径不能断链）

| 工具 | 干什么 | 命令 |
| --- | --- | --- |
| `localtest/layer_cost.py` | 同一帧把识别拆成 5 个面（网格·原图 / 主检测器·原图 / 主检测器·Engine图 / 门槛后 / 面板输出），回答“牌是在哪一层丢的” | `py -3.10 -X utf8 localtest/layer_cost.py 20 --set base`；换 `--set new` 用未收割过的新素材 |
| `localtest/bench_latency.py --channels` | 同帧对拍两条手牌通道的耗时与张数（换通道的代价就在这测） | `py -3.10 -X utf8 localtest/bench_latency.py --channels 37` |
| `localtest/mutation_check.py` | 把实现改坏，验证守卫真的会变红；新守卫的断言性质必须登记一行变异 | `py -3.10 -X utf8 localtest/mutation_check.py --only hand_channel` |
| `localtest/run_all_tests.py` | 自动发现并跑完全部 `test_*`（守卫）/`eval_*`（门禁），报告写 `build/all_tests.txt` | `py -3.10 -X utf8 localtest/run_all_tests.py` |
| `localtest/probe_platforms.py` | 一批混合素材的**平台归因 + 质量门**（SOP 第 1 步）：重复图、竖屏/过暗/糊、张数异常、分辨率簇 | `py -3.10 -X utf8 localtest/probe_platforms.py [--src public/0] [--n-crops 3]` → `build/platform_probe.txt` |
| `localtest/eval_new_material.py` | 未收割新素材的逐平台精度/混淆对/bank 缺类/拒识门限曲线（**按帧声明平台**，可用 `--no-declare` 回到旧口径做对照） | `py -3.10 -X utf8 localtest/eval_new_material.py` → `build/new_material_report.txt` |
| `localtest/bench_latency.py --engine` | **面板每帧实付**（走 `Engine.process` 而不是单检测器微基准）+ 内存泄漏与精度衰减 | `py -3.10 -X utf8 localtest/bench_latency.py --engine 1000 --fix localtest/shots --platform tencent` → `build/engine_stream.txt` |

判定“该修哪一层”的顺序：先看 `layer_cost` 的 S1/S2 差（检测器上限差）→ 再看
S2→S3（输入变换：ROI 切掉牌/方向归一）→ S3→S4（置信/牌形/亮度门槛）→
S4→S5（稳定器/同字互斥/阶段门）。实测结论是后三段净损失为 0，瓶颈只在
“手牌行走哪条通道”，所以 98% 的缺口要靠**补 bank 缺类样本**而不是改阈值。

`eval_new_material.py` 会把这三类分开输出（逐张/逐位错对、bank 缺类、分数
分离度与拒识门限曲线），报告默认不判失败，只有加 `--gate` 才按阈值退出。

## 后续补帧的增量迭代（用户持续供数据的标准回路）

新截图先直接跑 `eval_<platform>.py` 临时加 GT：
- 全对 → 只把帧收进帧集，不动代码。
- 低分/误读 → 大概率是**新尺度或新角标态**：走 4→5 步给对应标签补
  `#<变体>` 模板即可，无需算法改动；若几何崩（张数不对）才查
  `detect_hand_strip` 的节距/门槛。
- 每次改动必须双门禁（新+旧平台）同绿才算完成。
- **新帧的 GT 在进门禁前必须过一次"肉眼对照图"**：把帧内裁片与该平台 bank 里同名标签的
  **全部变体**拼成一张带标签的对照图再签字（`localtest/_diag_shushan_visual.py` 就是干这个的，
  帧集用 `SH_FRAMES` 扩）。原因见下面「已知边界」里的 GT 错标案例——**GT 错的时候分数往往是满的**。

## 已知边界（蜀山案例遗留，接下一个平台时留意）

- 阶段探测器 `is_dingque_phase` / `is_swap_phase` / `is_pick_phase` /
  `detect_pick_candidates` 仍是腾讯 UI 专属；蜀山帧靠 `river_locked`
  物理门控兜底不误伤，但蜀山自身的定缺/换牌界面阶段检测尚未适配。
- **GT 写死在源码里 = 一颗延时炸弹**（蜀山门禁实测踩到）：`localtest/eval_shushan.py` 的 GT
  是一个硬编码 dict，没有来源记录。2026-10 那轮全量回归它变红，追下去发现
  `s4~s7` 末三枚标的 `7s7s7s` **实际是 `5s5s5s`**——检测器给的是 1.00/0.99/0.99 满分，
  而"满分错认"在数字上完全隐形。排除法是：声明平台 vs 不声明**结果相同**（不是路由）→
  bank 内 `5s` vs `7s` 峰值 NCC 仅 0.77（不是同形）→ 绕开 Engine 走 raw 通道**同样满分错**
  → 最后只能拼图肉眼判，四帧 12 枚的牌面是四角绿+中心红的五条。改判后门禁 **82/84、
  整帧 6/7 仍红**（剩 2 张是 `3m→2m` 真失误）。教训有两条：① 挂在它上面的
  "蜀山 84/84 = 100%"整条作废，**改 GT 不等于修绿，是把历史声明作废**；
  ② 新平台的 GT 尽量落 json 并记来源，硬要写在源码里就必须配一张肉眼对照图（见上一节）。
- 蜀山 bank 的标签/模板/缺类数**本文不给静态数字**（这里曾写过"18 标签 / 35 模板 / 缺 16 类"，
  现读已是 27 类 / 76 核 / 缺 7 类）：一律以 `py -3.10 -X utf8 localtest/bank_coverage.py`
  与 `… localtest/bank_facts.py` 的现算输出为准。缺类牌会被退到候选集内最高分
  （先实测到的是蜀山 `3p→5p`、雀神 `3z/6z→1p/1s`）。
- 牌河/鸣牌识别在蜀山帧上未进门禁（eval 只握手牌），依赖同一 bank 生效后
  需另行钉 GT。
