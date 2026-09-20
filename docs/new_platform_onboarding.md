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
- **风格探针路由**（`_probe_style` + `detect_hand_strip`）：每帧取手牌主块
  中央一枚代表牌对全部 bank 打分，胜出风格（≥0.60）决定张数候选模型
  （腾讯=重叠节距 `0.0547*iw`；其余=相邻牌宽 `bh/1.35`）并把分类限定在该
  风格 bank 内 → 多平台共库时单帧开销 ≈ 单平台。探针失配时自动全量重扫
  兜底；腾讯帧路由回旧候选集，行为与接入前完全等价。

## 接入步骤

1. **建帧集**：截图放 `localtest/shots_<platform>/`（命名 `s1.jpg`…）。
2. **诊断**：仿 `localtest/diag_shushan.py`，用现行检测器逐帧打印
   rect/label/score 并导出 `diag_*.jpg`、`strip_*.jpg` 标注图；先回答三问：
   ①牌面美术是否超出旧模板 ②手牌排布节距模型是否失配（张数估计崩→整行
   拒识）③角标（赖/缺/选中）是否污染牌面。
3. **钉 GT**：人工逐图读 `strip_*.jpg` 把手牌多重集（mpsz：`1-9m/1-9p/
   1-9s/7z=红中`）写死进 `eval_<platform>.py` 的 GT 表。任何自动标注不可信。
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

## 后续补帧的增量迭代（用户持续供数据的标准回路）

新截图先直接跑 `eval_<platform>.py` 临时加 GT：
- 全对 → 只把帧收进帧集，不动代码。
- 低分/误读 → 大概率是**新尺度或新角标态**：走 4→5 步给对应标签补
  `#<变体>` 模板即可，无需算法改动；若几何崩（张数不对）才查
  `detect_hand_strip` 的节距/门槛。
- 每次改动必须双门禁（新+旧平台）同绿才算完成。

## 已知边界（蜀山案例遗留，接下一个平台时留意）

- 阶段探测器 `is_dingque_phase` / `is_swap_phase` / `is_pick_phase` /
  `detect_pick_candidates` 仍是腾讯 UI 专属；蜀山帧靠 `river_locked`
  物理门控兜底不误伤，但蜀山自身的定缺/换牌界面阶段检测尚未适配。
- 蜀山 bank 当前 18 标签 / 35 模板；缺 `1m,2p,5p,6p,1s-4s,8s,9s` 共 10 类
  （截图无正立清晰样本），这些牌出现时会退到跨风格最高分，需后续帧补收。
- 牌河/鸣牌识别在蜀山帧上未进门禁（eval 只握手牌），依赖同一 bank 生效后
  需另行钉 GT。
