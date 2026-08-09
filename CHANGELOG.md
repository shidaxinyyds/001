# Changelog

All notable changes to AimBuddy. Format inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Dates are in ISO-8601 (YYYY-MM-DD).

## [0.3.0-beta.17] - 2026-08-09

在 beta.16 架构基础上强化菜单输入链路的可靠性，修复「开菜单按钮点了没反应」「菜单内交互越用越卡」两类残留问题。

### Fixed
- **开菜单瞬间点击丢失**：`nativeTick()` 此前在 `ProcessPendingTouchEvents()` 之后才从 config 读取 `menuVisible`，导致 `openMenu()` 后第一帧仍按「菜单关闭」处理、把该帧排队的触摸样本全部丢弃，表现为「点了齿轮/通知栏菜单没反应」。现在把 `g_menuVisible` 的同步提前到处理触摸队列之前。
- **多点触控与触摸取消**：`onTouchEvent` 与 `menuInputView` 的触摸监听改用 `event.actionMasked` 并显式处理 `ACTION_POINTER_DOWN/UP` 与 `ACTION_CANCEL`。修复指针索引高位导致多点事件被静默丢弃、以及系统取消触摸序列后 ImGui 卡在「鼠标按下」状态使后续菜单交互全部失效的问题。
- **渲染线程卡死兜底**：新增 `nativeGetLastTickMillis()`，50ms 轮询检测「菜单宣称打开但渲染线程 >1.5s 无 tick」时自动 `forceRestoreTouch()`，防止渲染线程意外挂死导致 `menuInputView` 永久吞掉全屏触摸。
- **`setSecure()` 后补钉 NOT_TOUCHABLE**：`MediaProjection` 的 `setSecure()` 会触发 `SurfaceView` 异步重建并可能悄悄剥离 `FLAG_NOT_TOUCHABLE`，现增加 100ms/500ms 延迟强制重设，配合每 ~2s 周期重设，杜绝 overlay 在屏幕共享瞬间变为可触摸。

## [0.3.0-beta.16] - 2026-08-09

彻底重构触摸/菜单输入架构，从根上消除「开启服务后整屏触摸失效、菜单打不开/点了没反应」的故障模式。

### Fixed
- **架构性根因（屏幕冻结 + 菜单不可用）**：此前菜单打开时，会把全屏 `imguiOverlay` 窗口本身设为可触摸来接收菜单输入。一旦某些国产 ROM 上 `updateViewLayout` 静默失败（不抛异常但窗口状态未改），该全屏窗口就永久卡在「可触摸」状态——既吞掉整屏触摸，又因输入并未真正送到 ImGui 而使菜单按钮失灵，且无法关闭。
- **新架构**：全屏 `imguiOverlay` 现在**永远 `FLAG_NOT_TOUCHABLE`**（只负责画 ESP 与菜单画面，绝不拦截触摸）。菜单输入改由**一个仅在菜单打开时才存在的独立透明全屏窗口**（`menuInputView`）接收；菜单一关闭，该窗口被整体移除，屏幕 100% 恢复，不存在任何可被卡住的全屏拦截窗口。
- 悬浮齿轮图标恢复可点击：点一下即可开关设置菜单（齿轮已带 `FLAG_NOT_TOUCH_MODAL`，点击不会冻结整屏）。通知栏「打开菜单 / 恢复触摸」作为兜底保持不变。
- 50ms 轮询从「切换全屏窗口的触摸 flag」改为「按菜单可见性增删输入窗口 + 周期性重申 overlay 为 NOT_TOUCHABLE」，不再依赖易出错的 flag 往返。

### Note
- 这是针对「卡了两天」的触摸失效问题的一次结构性修复，不再依赖偶发的窗口 flag 行为。

## [0.3.0-beta.15] - 2026-08-09

修复 beta.14 引入的「启动即闪退、无任何原因」回归——原生崩溃信号处理器错误地覆盖了 Android ART 运行时的 SIGSEGV/SIGBUS 处理器，导致进程在第一次内部故障时直接终止。改为链式转发以保留 ART，并捕获回溯地址便于诊断。

### Fixed
- **启动闪退根因**：`JNI_OnLoad` 中原生信号处理器用 `sigaction` 直接覆盖（而非链式调用）了 ART 的 SIGSEGV/SIGBUS 处理。ART 依赖这些信号做隐式空指针检查、GC 读屏障、栈溢出保护与 JIT，覆盖后进程在任意内部故障时立即终止，表现为「打开即崩、无堆栈」。现已改为保存并链式转发到上一个处理器，运行时恢复正常。
- 原生崩溃处理器现在在崩溃时把信号号与回溯地址写入 `native_crash.log`（地址可由 ndk-stack + 未 strip 的 .so 还原），并保持防递归。
- `ncnn::create_gpu_instance()` 在信号处理器安装之前调用，避免干扰其初始化期间的内部信号使用。

### Note
- beta.13 的触摸修复（齿轮不再拦截触摸、菜单改由通知栏进入）保持不变。

## [0.3.0-beta.14] - 2026-08-09

新增崩溃自报告器，定位 beta.13 引入的「启动/使用即闪退」问题。

### Added
- `CrashReporter`：捕获 Java 未处理异常与 native 信号（SIGSEGV/SIGABRT/SIGFPE/SIGILL/SIGBUS），
  写入 `getExternalFilesDir()/crashlog.txt` 与 `native_crash.log`。
- 原生层 `JNI_OnLoad` 安装信号处理器，崩溃时把信号号写入文件（路径由
  `ImGuiGLSurface.nativeSetCrashLogPath` 从 Java 注入）。
- `MainActivity` 启动早期安装报告器；若上次运行崩溃，下次启动弹出对话框展示堆栈，
  并可一键复制到剪贴板，便于无 adb 也能拿到崩溃原因。

### Note
- beta.13 的触摸修复（齿轮不再拦截触摸、菜单改由通知栏进入）保持不变。
- 本版本目标：复现崩溃并拿到堆栈，而非盲目改动。下次启动的崩溃弹窗将指明真正成因。

## [0.3.0-beta.13] - 2026-08-09

兜底修复：即使 `FLAG_NOT_TOUCH_MODAL` 在某些设备上仍不能让齿轮窗口外触摸正常穿透，也彻底消除屏幕冻结。

### Changed
- 悬浮齿轮图标改为**纯视觉指示器**，不再消费任何触摸事件；菜单改从通知栏「打开菜单」按钮进入。
- 通知栏新增「打开菜单」动作（`ACTION_OPEN_MENU`），对应广播由 `MainActivity` 接收并打开菜单。
- 移除 `imguiOverlay` 的 `FLAG_FULLSCREEN`，避免覆盖系统手势区域。
- 启动时 Toast 显示当前版本号（`BuildConfig.AIMBUDDY_VERSION`），方便确认安装是否生效。

## [0.3.0-beta.12] - 2026-08-09

真正根因修复 + CI 自动触发修正。

### Fixed
- **启动服务后整屏无法点击/滑动的真正根因**：悬浮齿轮图标窗口（`setupFloatingIcon`）的
  `WindowManager.LayoutParams` 缺少 `FLAG_NOT_TOUCH_MODAL`。该窗口虽小（44x44），但在服务
  启动时以 modal 方式 `addView`，会吞掉整屏所有触摸事件。补上 `FLAG_NOT_TOUCH_MODAL` 后，
  齿轮只接收自身区域内触摸，其余事件全部穿透到下层游戏/桌面。
- **CI 自动触发分支错误**：`release.yml` 监听的是 `master` 分支 push，但仓库主分支是 `main`，
  导致每次 push 后不会自动构建。已改为 `branches: [main]`。

## [0.3.0-beta.11] - 2026-08-09

内部重发尝试：修正 beta.10 标签已存在导致修复未进包的问题。因 push 后未触发自动构建且
标签随即被创建，未产生可用 APK。

### Fixed
- 包含 `0.3.0-beta.10` 的全部修复：
  - 悬浮齿轮窗口补 `FLAG_NOT_TOUCH_MODAL`（真根因：齿轮 modal 吞掉整屏触摸 → 设备冻结）。
  - `forceOverlayNotTouchable()` 硬重置、500ms 启动兜底、`onTouchEvent` 穿透防御、轮询强制重同步。

## [0.3.0-beta.10] - 2026-08-09

彻底定位并修复「启动服务后屏幕完全无法点击/滑动」的**真正根因**，并补充底层可靠性加固。

### Fixed
- **【根因】悬浮齿轮窗口缺少 `FLAG_NOT_TOUCH_MODAL`，吞掉整屏触摸**：这是该故障自 beta.5 起反复出现的真正元凶。齿轮图标是一个 44×44 的可触摸窗口（需接收点击以切换菜单），但其 `WindowManager.LayoutParams` 从未设置 `FLAG_NOT_TOUCH_MODAL`。按 Android 官方文档，一个可触摸窗口若缺少该标志，会以 modal 方式**消费整屏所有触摸事件**——不论触摸是否落在窗口范围内。齿轮在 `setupOverlay → setupFloatingIcon` 时 `addView`，于是服务一启动，整屏点击与滑动全部被齿轮吞掉，设备表现完全冻结。此前 beta.8/beta.9 的所有修复（菜单内 X、通知栏恢复触摸、`forceOverlayNotTouchable`、`ImGuiGLSurface.onTouchEvent` 穿透）都针对 `imguiOverlay`，而 `imguiOverlay` 在齿轮**之下**且本就带 `FLAG_NOT_TOUCHABLE`，根本不是问题源头——齿轮窗口一直被遗漏。本次给齿轮补上 `FLAG_NOT_TOUCH_MODAL`，使其只接收落在自身 44×44 区域内的触摸，其余触摸穿透到下层游戏。
- 保留 beta.9 的「菜单内 X」「通知栏恢复触摸」逃生路径作为二级保险。

### Changed
- **叠加层触摸穿透可靠性（防卡死兜底）**：
  - 新增 `forceOverlayNotTouchable()` 硬重置：无条件给叠加层补上 `FLAG_NOT_TOUCHABLE`，且 `updateViewLayout` 失败时回滚内存 flags，避免「内存 flag 已改、实际窗口未改」导致轮询后续跳过纠正。
  - 启动 ESP 时立即 + 延迟 500ms 各强制穿透一次，抵消 `addView` / GL _surface 创建路径可能引入的标志错位。
  - 齿轮关闭菜单、通知「恢复触摸」逃生口、启动兜底均走 `forceOverlayNotTouchable()`（关菜单方向不能含糊）。
  - `applyOverlayTouchable()` 与触摸轮询的 `updateViewLayout` 均加 `try/catch` + flags 回滚。
  - 触摸轮询每 ~2s 强制重同步一次，作为内存态与真实窗口态可能脱节的最后兜底。
  - `ImGuiGLSurface.onTouchEvent` 在菜单不可见时直接返回 `false`（深度防御：即使窗口标志在 50ms 轮询间隙短暂可触摸，也不会吞掉触摸）。

## [0.3.0-beta.9] - 2026-08-08

针对 beta.8 之后用户反馈「启动服务后屏幕依旧完全无法点击/滑动」的遗留问题做彻底修复。beta.8 已修掉首帧竞态与 `FLAG_NOT_TOUCH_MODAL` 抢占，但遗漏了另一条同样致命的根因，本次从三个层面堵死该路径。

### Fixed
- **【严重】菜单卡死导致屏幕被锁（彻底解决）**：真正遗漏的根因是**菜单唯一的关闭入口是悬浮齿轮，而齿轮的「点按 vs 拖拽」判定阈值为 6px**——手指正常点按的抖动往往大于 6px，被误判为拖拽，于是齿轮只移动、不切换菜单；菜单便一直开着 → 叠加层保持可触摸 → 整屏被拦截、无法点击也无法滑动。并且菜单打开时叠加层 `setZOrderOnTop(true)` 可能盖在齿轮之上，使齿轮本身就点不到。本次修复：
  - **菜单内新增关闭按钮（X）**：固定在菜单右上角。菜单打开时叠加层本身可触摸，X 按钮属于叠加层 UI，永远可点；点击即把 `menuVisible`/`g_menuVisible` 置 false，下一帧菜单消失、50ms 触摸轮询将叠加层恢复为 `FLAG_NOT_TOUCHABLE`，触摸即刻穿透给游戏。
  - **通知栏「恢复触摸」终极逃生口**：`ScreenCaptureService` 常驻通知新增「恢复触摸」操作（`ACTION_RESTORE_TOUCH`）。无论菜单是否卡死、齿轮是否可达，下拉通知栏点该按钮即经广播触发 `MainActivity.forceRestoreTouch()`，强制 `nativeSetMenuVisible(false)` + `applyOverlayTouchable(false)`，屏幕必定恢复。
  - 未改动核心 `FLAG_NOT_TOUCHABLE` / `g_menuVisible` 触摸穿透机制本身（它是正确的），只补上可靠的「关闭 / 逃生」路径，确保任何情况下都能把触摸交还给游戏。

## [0.3.0-beta.8] - 2026-08-08

针对 beta.7 后仍然存在的五项问题做彻底修复，重点解决「启动后屏幕完全无法点击」与「大厅里也误检测敌人」。

### Fixed
- **【严重】启动服务后屏幕完全无法点击/滑动**：根因是叠加层窗口在创建时机上有竞态——`setupOverlay()` 先于「隐藏菜单」执行，`nativeWantsCapture()` 在首个轮询周期内可能返回 true，导致 `FLAG_NOT_TOUCHABLE` 被移除且再没有恢复；同时 `FLAG_NOT_TOUCH_MODAL` 会让叠加层抢占窗口外的触摸分发。修复方式：
  - `startESP()` 中先 `nativeSetMenuVisible(false)` 并复位 `menuVisible`，**再**创建屏幕采集与叠加层，最后显式 `applyOverlayTouchable(false)`，确保叠加层从诞生第一帧起就是纯穿透的。
  - 叠加层 `LayoutParams` 移除 `FLAG_NOT_TOUCH_MODAL`，只保留 `FLAG_NOT_FOCUSABLE | FLAG_NOT_TOUCHABLE | FLAG_LAYOUT_IN_SCREEN | FLAG_LAYOUT_NO_LIMITS`。
  - `ImGuiGLSurface.onTouchEvent()` 对非 DOWN/UP/MOVE 事件返回 `false`（原先返回 `super.onTouchEvent()` 会消费事件），保证多指、CANCEL 等事件干净地透传给游戏。
- **误检测（大厅无人也报敌人）**：核心根因是**采集自反馈回路**——MediaProjection 会把 AimBuddy 自己绘制的 ESP 叠加层（准星、方框、触摸区）一并录进画面，模型再把这些图形识别成「敌人」，在大厅等空场景尤其明显。修复：
  - **新增时序确认过滤**（`esp_jni.cpp` 的 `ApplyTemporalConfirmation`）：维护一张 IoU 关联的轻量轨迹表，一个框必须在**连续 2 个检测周期**内被稳定看到才会被发布；连续 2 周期未命中的轨迹被丢弃。幽灵框通常只闪现一帧，因此会被直接吃掉，而真实目标只延迟一个周期（约 30~60ms）。ESP 渲染与自瞄共用过滤后的结果。
  - **默认开启主播模式**（`streamerMode = true`），叠加层带 `FLAG_SECURE`，被排除在采集帧之外，从物理上切断自反馈回路；Kotlin 侧 `streamerModeEnabled` 默认值同步改为 `true`，保证叠加层从 `addView` 的第一帧起就是 secure 的。
  - 默认置信度阈值 0.55 → 0.60，NMS IoU 0.45 → 0.35（更激进地合并/丢弃真实框旁边的幽灵框）。
  - 增加屏幕空间几何合理性过滤（`DropImplausibleBoxes`）：丢弃宽或高小于 14px 的碎框、非有限值坐标，以及同时超过屏幕 85% 宽高的整屏框。该过滤位于时序确认之前的**共享路径**上，ESP 与自瞄使用完全相同的结果——此前它只作用于渲染层，会出现「屏幕上看不到框、准星却被拖向一个不可见目标」的情况。
  - 时序确认的关联门限区分新旧轨迹：未确认轨迹沿用严格的 IoU 0.25 防止噪点挂靠，已确认轨迹放宽到 0.10，避免快速甩枪／转视角时同一敌人因位移过大而断开关联、方框闪断一帧。
- **主播模式首帧下发竞态**：`g_streamerModeAppliedState` 原为 `bool` 且初值 `false`，当已保存配置里 `streamerMode` 恰好也是 `false` 时，首帧不会触发下发，Java 侧状态可能与原生不一致。改为三态 `int`（初值 `-1`），保证第一帧一定把真实值同步过去。
- **左侧弹窗无法直接下滑**：真正的原因是旧逻辑里「上一帧有控件被激活（`g_anyItemActiveLastFrame`）就完全禁止滚动」——而菜单里绝大部分区域都是复选框/滑块，手指一按下控件就被激活，于是拖内容区永远滚不动，只能去够那根细滚动条。现在改为：
  - 控件未被激活时触发距离 10px（原 18px），被激活时给 24px 宽限但**不再拥有一票否决权**；
  - 方向判定由 `|dy| > |dx|` 放宽为 `|dy| > |dx| * 1.5`；
  - 判定为滚动的瞬间先把 ImGui 光标移到 `(-FLT_MAX, -FLT_MAX)` 再抬起按键，避免「从复选框上起手的滑动顺手把它勾上」（ImGui 会把按下/抬起分帧下发）；
  - 进入滚动态后持续累计位移，滑动跟手。
- **ESP / 瞄准 / 信息 切页响应慢**：菜单窗口移除多余标题栏（`ImGuiWindowFlags_NoTitleBar`），配合滚动手势判定的收紧，点击不再被误判为拖拽而延迟生效。
- **触摸轮询可能永久停摆**：`startTouchPolling()` 的 `Runnable` 中途 `return`（叠加层为 null、`layoutParams` 类型不符、`WindowManager` 抛异常）时不会重新排队，而 `touchPolling` 仍为 `true`，导致轮询再也无法启动、叠加层卡在当时的触摸状态。改为 `try/finally` 结构，任何情况下都保证续期。
- **构建失败**：ImGui 自 1.91.9 起将 `style.TabMinWidthForCloseButton` 重命名为 `TabCloseButtonMinWidthUnselected` 且未保留兼容别名，导致 NDK 编译中断。菜单标签页本就没有关闭按钮，改为设置 `TabBarBorderSize = 0`（同时去掉标签栏下方的分隔线，更贴合极简主题）。

### Changed
- **启动器精简**：移除「启动服务」按钮下方的「触摸输入后端」标签、Root/Shizuku/无障碍 三个状态芯片，以及「由 1337XCode 打造」署名行；同步删除 `BackendChips` / `BackendChip` 组件与相关常量、跳转函数。
- **ImGui 菜单换肤**：从原来的红/粉暗色主题改为干净的冷灰蓝极简主题（窗口 `rgba(0.045,0.055,0.075,0.96)`、面板 `0.085,0.105,0.140`、强调色 `0.42,0.56,0.78`），圆角统一（窗口 12 / 控件 6 / 子窗口 8 / 滚动条 8），且圆角与尺寸覆盖移动到 `ScaleAllSizes` 之后以免被 DPI 缩放冲掉。
- 配置 magic 升级为 `0xE5BA1009`，旧 `settings.bin` 失效并回落到新默认值（0.60 阈值、主播模式默认开启）。

---

## [0.3.0-beta.7] - 2026-08-08

针对用户反馈的六项问题做修复，并精简为单一开箱即用模型。

### Fixed
- **开启后屏幕无法点击**：ESP 启动时强制隐藏设置菜单并移除叠加层的 `FLAG_NOT_TOUCHABLE` 之外的可触摸状态，保证游戏画面默认可正常点击/滑动；只有点击悬浮齿轮图标打开菜单时叠加层才会捕获触摸。
- **误检测（无敌人却报敌人）**：修复分块检测中输出张量朝向判断的缺陷——当某个瓦片检测结果少于 5 个时，旧的 `numBoxes < numValues` 启发式会误判朝向并读取越界行，产生幽灵框。现改为锁定通道维度（5/6 通道）来判断朝向，彻底消除幽灵检测。
- **弹窗（设置菜单）反应慢**：菜单打开时不再做帧率上限节流，直接以 vsync 满帧渲染，按钮与切页即时响应。
- **弹窗内无法下滑**：为 ImGui 菜单增加触摸拖拽滚动——竖直拖动被转换为滚轮事件驱动子窗口滚动，拖动结束后不会误触发控件。
- **启动慢**：屏幕采集授权后的启动延时从 1000ms 降到 300ms。

### Changed
- 默认置信度阈值由 0.5 提升到 0.55，减少低置信度误报。
- 配置 magic 升级为 `0xE5BA1008`，旧 `settings.bin` 失效并回落到新默认值（更高阈值、无障碍开箱即用后端等）。
- 移除「更换模型」入口与模型商店/导入/多模型逻辑，启动器仅保留内置默认模型，真正开箱即用。

### Removed
- 启动器中的「当前模型」卡片、右上角菜单的「导入模型文件 / 模型商店」项、以及整套模型商店 Compose 界面（相关处理函数改为无引用的闲置代码，可后续清理）。

---

## [0.3.0-beta.6] - 2026-08-09

修复 beta.5 中会导致「开箱即用」后端完全无法工作的若干缺陷。beta.5 的无障碍后端实际上无法注入任何触摸，本版修复。

### Fixed
- **无障碍手势 100% 失败**：`StrokeDescription` 会拒绝零长度路径（`Path has zero length`），而瞄准目标静止时起点与终点相同，导致每一次注入都抛异常。现在对退化路径做 1 像素补偿。
- **手势相互抢占**：同一时刻只允许一个手势在飞行中，逐帧派发会让除首个之外的全部 `dispatchGesture` 返回 false。改为自时钟「手势泵」：在上一段的 `onCompleted` 回调中派发下一段，保证任意时刻只有一个手势。
- **拖动被打断**：独立手势会在每帧之间抬起指针，游戏侧表现为连续点击而非按住拖动。改用 `continueStroke`（`willContinue = true`）保持指针按下，`releaseAim` 时才以非连续段收尾抬起。
- **无障碍配置会破坏正常触摸**：`accessibility_service_config.xml` 误设了 `flagRequestTouchExplorationMode`，该标志会开启 TalkBack 式触摸浏览，使单击失效、整个屏幕无法正常操作。已移除该标志及无用的 `flagRetrieveInteractiveWindows` / `typeAllMask` 事件订阅。
- **桥接标志时序丢失**：`nativeSetAccessibilityBridgeAvailable` 在 `TouchHelper` 尚未创建时被静默丢弃（`refreshAccessibilityState` 在 `onCreate` 即调用，而 `TouchHelper` 在 `nativeInit`/`nativeInitAimbot` 才创建），导致 `TouchHelper::init()` 必然失败。现在标志缓存在原生全局变量中，并在 `TouchHelper` 创建后统一下发。
- **旧配置锁死后端**：`settings.bin` 位于 `/data/local/tmp/`，卸载重装仍保留，升级用户会继续使用 `touchBackend=1`（Shizuku）而进不到开箱即用路径。配置 magic 升级为 `0xE5BA1007` 使旧配置失效并回落到新默认值。
- 手势注入不再经由 `MainActivity` 弱引用转发，改为直接调用服务实例，避免用户切入游戏后 Activity 被回收导致注入失效。
- `setJniBridge` 现在会一并重置无障碍方法 ID 缓存。

### Changed
- 开箱即用路径不再回退到 Shizuku：偏好为无障碍且服务未开启时，直接引导开启无障碍（若设备恰好有 Root 则优先使用 Root 以降低延迟），不会把用户导向安装 Shizuku。
- 无障碍服务描述与摘要改为中文。

---

## [0.3.0-beta.5] - 2026-08-09

方案 A：开箱即用的无障碍（AccessibilityService）输入后端。

### Added
- 第三种触摸输入后端 `ACCESSIBILITY`（值 2）：基于系统无障碍服务的 `dispatchGesture` 注入合成触摸，无需 Root、无需 Shizuku，开启「无障碍」服务即可使用，实现开箱即用。
- 新增 `AimAccessibilityService`（`AccessibilityService` 子类）与 `res/xml/accessibility_service_config.xml`，并在 `AndroidManifest.xml` 中声明并申请 `BIND_ACCESSIBILITY_SERVICE` 权限。
- 原生层 `TouchHelper` 增加 `ACCESSIBILITY` 后端分支与 JNI 桥接（`nativeInjectAccessibilityAimMove` / `nativeInjectAccessibilityAimUp` / `nativeSetAccessibilityBridgeAvailable`）。
- 启动流程新增顶层路由 `requestTouchBackendThenProjection`：按用户偏好解析可用后端（Root / Shizuku / 无障碍）并自动回退；无障碍路径在未开启时弹出引导对话框并跳转到系统无障碍设置。
- 原生菜单「触摸后端」新增第三项「无障碍服务（免 Root）」，并补充对应状态与提示文案（i18n）。
- 启动器新增「无障碍」后端状态指示芯片，状态栏补充无障碍相关文案。

### Changed
- 默认输入后端改为 `2`（无障碍），新安装即走开箱即用路径；已保存的旧配置（值 0/1）保持不变。
- `aimbot_controller` / `esp_jni` 的后端映射扩展支持值 2；`UnifiedSettings.touchBackend` 校验上限同步调整为 2。

### Notes
- `dispatchGesture` 每帧重发手势（合成指针从上一坐标移动到目标坐标并短暂停留），与真实手指并行工作，游戏仍可正常点击/滑动。
- 无障碍手势按屏幕像素坐标注入，与原 Shizuku/uinput 后端坐标系一致。

## [0.3.0-beta.4] - 2026-08-09

Touch passthrough hardening, residual-English cleanup, and ESP box resize.

### Changed
- Overlay only captures touch while the ImGui menu is actually open (`nativeWantsCapture` now keys off `g_menuVisible` only). Previously a transient `io.WantCaptureMouse` kept the full-screen overlay touchable and swallowed all game input, making the screen unclickable/unslidable after starting the service.
- Floating-icon menu toggle now reads the real native menu state and applies the touchable flag immediately, eliminating the ~50 ms polling latency and the stale-toggle desync that forced extra taps to reopen the menu.
- Added a `识别框缩放` (box scale) slider on the ESP tab so detection boxes can be freely enlarged/shrunk on screen; detection still covers the entire screen.
- Removed remaining "open source"/"GitHub" wording from the UI: startup-page credit line, the "View on GitHub" menu item, and the Info-tab title. Translated the last live English overlay strings ("Enemy", "enemy(s)") to Chinese.

### Fixed
- Resolved the root cause of "can't click/slide the screen after enabling" and the delayed menu-button response.

## [0.3.0-beta.3] - 2026-08-08

Full-screen detection, complete Chinese UI, and touch restoration.

### Added
- Full-screen tiled detection: scan the entire 1280×720 capture frame with overlapping 512×512 tiles instead of only the center crop, then merge results with a global NMS.
- DPI-aware ImGui font scaling so menu text is readable on high-density screens; falls back to system CJK fonts or `assets/fonts/cjk.ttf`.

### Changed
- Default language switched to Chinese and all UI text translated:
  - Native ImGui settings menu, tooltips, and combo items (target priority, aim mode, filter type, touch backend).
  - Kotlin system dialogs, Toast messages, and Compose home screen labels.
- Auto-close the settings menu when aim assist is enabled so the overlay stops intercepting touches and the game receives normal tap/slide input.
- Settings binary magic bumped so legacy English configs stored under `/data/local/tmp` are invalidated and fresh installs default to Chinese.

### Removed
- Startup "AimBuddy Notice" AlertDialog about the project being free/open source.

## [0.3.0-beta.2] - 2026-06-04

UX overhaul and critical APK installation fix.

### Added
- Premium Compose-based Model Store UI replacing legacy Alert Dialogs. Features tabs for browsing available models from GitHub and managing installed models with options to download, switch, and delete models.
- Support for deleting downloaded/imported model files through the UI.

### Fixed
- Fixed immersive landscape layout breaking/shifting by implementing an in-window animated menu overlay for the 3-dot dropdown, avoiding system bar visibility toggles caused by system PopupWindows.
- Fixed APK installation failure ("package is invalid") on modern Android devices by automatically signing release builds using the debug signing configuration when no custom release keystore is provided.

### Changed
- Redesigned home screen into a landscape-optimized side-by-side panel layout, grouping Status and Active Model information on the left and primary actions with input backend chips on the right.
- Enhanced launcher status reporting by mapping internal JNI status strings to professional, descriptive titles and subtitles (e.g., translating "Model Loading" to "Initializing Model" with detailed buffer allocation details) for better user feedback.

---

## [0.3.0-beta.1] - 2026-05-31

Tooling, docs, and release-automation pass on top of 0.2.0-beta.1.

### Added
- Beginner-friendly Shizuku setup guide at `docs/ShizukuSetup.md` covering wireless-debugging pairing, ADB start, root start, permission grant, post-reboot behavior, OEM battery-saver workarounds, and troubleshooting.
- GitHub Actions release workflow at `.github/workflows/release.yml`. Triggered on every push to `master` that modifies `CHANGELOG.md`; reads the top non-Unreleased version heading, verifies it matches the `aimbuddy.versionName` Gradle property and is not already tagged, builds the release APK with the Android SDK action, optionally signs with a keystore from repository secrets, and publishes a GitHub Release with the changelog section as the body and the APK attached.
- Centralized version metadata. `aimbuddy.versionName` and `aimbuddy.versionCode` live in the root `gradle.properties` and are consumed by `app/build.gradle` (APK metadata + `BuildConfig.AIMBUDDY_VERSION`), by the native side through a new `-DAIMBUDDY_VERSION=...` CMake define, and by the release workflow when validating that the changelog matches the build. The ImGui menu title and Info tab now read from that single source rather than hardcoded literals.

### Changed
- Updated mermaid diagrams across `docs/Architecture.md`, `docs/Performance.md`, and `docs/Training.md` to reflect the 4-slot ring buffer, event-driven aim loop, Shizuku permission branch, and the new `automate.py` end-to-end pipeline.

---

## [0.2.0-beta.1] - 2026-05-29

Major feature release. Touch parity, streamer mode, Chinese UI, redesigned launcher, full training-pipeline overhaul, and a significant runtime memory/CPU pass.

### Added
- Streamer mode toggle in the ESP tab that sets `FLAG_SECURE` on the overlay and floating-icon windows and calls `SurfaceView.setSecure(true)` on the GL surface so the overlay is stripped from MediaProjection, screen recorders, screenshots, and mirroring while still visible on the user's own screen.
- Chinese (`中文`) UI language alongside English. New `utils/i18n.h` holds the per-key translation table. CJK font loading tries `/system/fonts/NotoSansCJK-Regular.ttc` and other vendor fallbacks first (works on every modern Android with no APK bloat), then falls back to `assets/fonts/cjk.ttf` if a user-bundled font is present. Language is persisted in `UnifiedSettings.language` and chosen from the ESP tab.
- Redesigned launcher screen built on Material3: `CenterAlignedTopAppBar` with an overflow menu (Import model / Model store / GitHub), a status card with a colored state pill (grey idle, amber waiting, green running, red error), backend-readiness `AssistChip`s for Root and Shizuku, and a single primary `FilledTonalButton` that toggles between Start and Stop. New dark palette tuned for the launcher.
- Teacher-driven auto-labelling pipeline (`training/src/auto_label.py` + `scripts/08_auto_label.bat`) that runs a large COCO-pretrained YOLO (default `yolov8x.pt` at `imgsz=1280`) over `raw_frames/` and emits YOLO-format enemy labels - replaces hours of manual box-drawing with minutes of spot-checking.
- Hard-negative mining (`training/src/mine_negatives.py` + `scripts/09_mine_negatives.bat`) for empty-label samples from `raw_frames/negatives/`, used to suppress false positives on menus, vehicles, friendly NPCs, etc.
- Active-learning sweep (`training/src/active_learning.py` + `scripts/10_active_learning.bat`) that scores unlabelled frames by uncertainty and teacher-student disagreement and surfaces the top-N for review.
- Stable hash-based train/valid/test split (`training/src/split_dataset.py`) that never moves a frame between splits, so re-running the pipeline as new data lands stays leak-free.
- End-to-end automation script (`training/src/automate.py` + `scripts/00_automate.bat`) that runs setup -> extract -> auto-label -> mine negatives -> split -> validate -> train -> NCNN export -> active-learning, checkpointing to `outputs/reports/automate_state.json` so a re-run resumes from the last failure.
- Separate train-time and runtime image sizes: train at `imgsz=640` for distant-target recall, export NCNN at `runtime_imgsz=256` for mobile latency. New `[augmentation]` block in `config/config.ini` (mosaic, mixup, copy-paste, HSV, perspective) so the model generalizes across maps/outfits/poses.

### Changed
- Touch backends rebuilt for true user+aim parallelism:
  - **uinput (root):** removed `EVIOCGRAB` on the real touchscreen and the reader thread that mirrored events back through uinput. The aimbot now owns a separate `aimbuddy-virtual-touch` device with its own slot 0 and tracking id (`0x7000`); the kernel reports both devices to InputDispatcher, so finger and aim contact dispatch as parallel streams.
  - **Shizuku (non-root):** synthetic `MotionEvent` carries `deviceId = -1` and pointer id `19`, keeps its own `downTime`, and uses `INJECT_MODE_ASYNC` so InputDispatcher routes the synthetic gesture as a parallel stream rather than merging it into the user's pointer history.
- Aim controller smoothed for less shake: heavier blend when locked, cubic ramp-to-zero below 2 px, derivative gain attenuated near lock, removed `usleep()` calls from `applyMovement`. Velocity-lead lookahead now scales with measured pipeline delay (capped at 80 ms) and the default `velocityLeadFactor`/`velocityLeadClamp` were tripled so running targets are actually predicted.
- Aim loop is now event-driven: condition-variable wakeup from `updateTargets()` triggers the first touch within one inference cycle of acquisition; sleeps for 120 ms when the aimbot is disabled instead of polling.
- Target tracker resets EMA/Kalman filter state when a track is re-acquired after being lost, eliminating ghost-box drag.
- Runtime crop ceiling reduced from 480 px to **320 px** (`Config::CROP_SIZE`). Smaller resize ratio into the model (1.25x vs 1.875x) cuts per-frame CPU; the smaller FOV also matches what `imgsz=256` actually resolves cleanly.
- Frame ring buffer reduced from 8 slots to **4 slots** (`Config::RING_BUFFER_CAPACITY`). Halves peak AHardwareBuffer footprint (~28 MB -> ~14 MB). The inference loop drains-to-latest each iteration, so deeper buffering only added latency.
- ImGui overlay render now capped to 60 FPS (90 FPS while the menu is open), preventing 120/144 Hz panels from redrawing the overlay twice per inference frame.
- Frame extraction default `--source-crop` changed from 480 to 320 to keep train/runtime preprocessing geometrically identical.
- Stronger training augmentations enabled by default (`mosaic=1.0`, `mixup=0.15`, `copy_paste=0.30`, wider `scale=0.6`, larger `hsv_v=0.45`); `close_mosaic` bumped to 15 so mosaic stays active longer in the schedule.
- ImGui Aim/ESP/Info tab labels, presets, sliders, and footer translated through `T(Key::...)`; per-frame language sync so a toggle takes effect immediately.

### Fixed
- User finger + aimbot touch can now run simultaneously on both backends - previously one would cancel or replace the other depending on backend.
- Aim no longer flicks toward stale "ghost" boxes after target re-acquisition.
- `usleep()` calls inside the aim loop no longer stall the controller mid-move.

### Docs
- `CHANGELOG.md` (this file).
- `docs/Architecture.md`: rewrote the Input Injection section to describe the parallel-stream design; added Streamer Mode section; updated the threading diagram to event-driven aim loop.
- `docs/Performance.md`: documented the 320 px crop ceiling, 4-slot ring buffer, runtime memory budget, and overlay render cap.
- `docs/SettingsGuide.md`: updated lead defaults; documented `streamerMode`; refreshed preset tables.
- `docs/Training.md`: added "Skipping Manual Labelling" section covering teacher auto-labelling, negative mining, active learning, and the high-train / low-runtime imgsz split.
- `app/src/main/assets/fonts/README.md`: documents the system-font-first CJK loading strategy and when a user would need to drop their own font.

---

## [0.1.0-beta.1] - 2026-04-25

Public-facing beta tag - corresponds to `bf93b26 feat: add Shizuku support for non-root input backend and enhance touch handling`. This is the first build that runs end-to-end on devices without root.

### Added
- Shizuku non-root input backend (`ShizukuInputInjector.kt`) using the hidden `IInputManager.injectInputEvent` API surfaced through Shizuku's binder wrapper. Adds the second supported touch path beside the root/uinput one.
- Backend toggle in the Aim tab and persisted `touchBackend` setting (0 = uinput, 1 = Shizuku).
- Permission flow: `requestShizukuThenMediaProjection()` mirrors the existing root flow, with binder listeners that auto-detect connect/disconnect.

### Changed
- Touch handler abstracted behind `TouchHelper::setBackend()` so the native aim controller calls a single API and `TouchHelper` dispatches to uinput or the Shizuku JNI bridge.

---

## [0.1.0-alpha.4] - 2026-04-16

Maps to `6223d76 feat: implement model store functionality with GitHub integration and local model management`.

### Added
- In-app model store (`ModelStoreRepository.kt`) that fetches model metadata from a GitHub repo branch, lists available models with size/description, and downloads/unpacks them into `filesDir/models/<id>/`.
- Installed-model catalog (`ModelCatalog.kt`) that tracks bundled, locally-imported, and store-downloaded models; the active model selection is reflected in the main-activity status text.
- Switcher dialog to choose between installed models, with hot-reload via `nativeShutdown` + `nativeInit` when the app is idle.

### Changed
- `nativeSetModelPaths(param, bin)` lets the native detector pick up store/local model files instead of always loading from assets.

---

## [0.1.0-alpha.3] - 2026-04-07

Maps to `933f7d0 feat: enhance model loading and import functionality in MainActivity`.

### Added
- Local model import via `OpenDocument` activity - pick a `.param` then the matching `.bin` and they are copied into the app's private models directory.
- Legacy single-import migration so users who imported a model under the older single-slot scheme are not stranded.

### Changed
- Status text now reflects the active model (`Model: <title> (<source>)`).

---

## [0.1.0-alpha.2] - 2026-04-06

Spans `eb21245`, `84e7627`, `a7b296d`, `b93d8c5` - overlay UX polish, GitHub Pages site, and richer docs.

### Added
- ESP overlay groundwork for the "menu visible" state machine - visibility check that suppresses aim while the menu is open.
- Initial GitHub Pages scaffolding under `site/` and a deploy workflow.

### Changed
- Improved enemy detection / menu-visibility checks in `eb21245` make the aim controller stop cleanly the moment the menu opens.
- Main layout dimensions updated to align the floating icon with the overlay coordinate space.

---

## [0.1.0-alpha.1] - 2026-03-25

Foundational milestone. Five commits land on a single day: `79438a8` (initial), `2669072` (root permission handling), `5f8ccbe` (menu UX + native stability), `9d2dd69` (base model download script + setup), `c0eaae7` (initial aimbot tuning + perf), `946f8de` (initial docs pass).

### Added
- Initial repository: Android app shell (Kotlin + Compose), native C++ runtime, NCNN-Vulkan detector wired to YOLOv26n, ESP overlay with ImGui, basic settings menu, target tracker, aim controller with smooth/snap/magnetic modes, uinput-based touch injection, MediaProjection capture pipeline, training Python pipeline (extract -> validate -> train -> export -> deploy).
- Hardened root permission handling with timeout (`RootUtils`).
- Polished menu UX (tabs, presets, save-on-edit) and native overlay stability fixes.
- Base model download script + setup instructions so `yolo26n.pt` is recoverable when missing.
- Initial documentation set: README, Architecture, Performance, SettingsGuide, Training, Troubleshooting, Contributing.
