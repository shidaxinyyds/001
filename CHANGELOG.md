# Changelog

All notable changes to AimBuddy. Format inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Dates are in ISO-8601 (YYYY-MM-DD).

## [Unreleased]

Pre-release work staged for the next tag.

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
