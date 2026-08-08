# Changelog

All notable changes to AimBuddy. Format inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Dates are in ISO-8601 (YYYY-MM-DD).

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
