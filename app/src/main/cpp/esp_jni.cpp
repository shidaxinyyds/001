#include <jni.h>
#include <android/native_window.h>
#include <android/native_window_jni.h>
#include <android/asset_manager.h>
#include <android/asset_manager_jni.h>
#include <android/hardware_buffer.h>
#include <android/hardware_buffer_jni.h>
#include <atomic>
#include <memory>
#include <thread>
#include <chrono>
#include <string>
#include <cstdio>
#include <cstdlib>
#include <cstdarg>
#include <cstring>
#include <cmath>
#include <signal.h>
#include <fcntl.h>

// backtrace() lives in <execinfo.h> which is not available / not declared on
// all Android NDK versions. On Android we rely on Java CrashReporter + the
// system tombstone; on hosts that provide it we record a best-effort backtrace.
#if !defined(__ANDROID__)
#include <execinfo.h>
#define HAS_NATIVE_BACKTRACE 1
#else
#define HAS_NATIVE_BACKTRACE 0
#endif
#include <unistd.h>
#include <algorithm>

// Fix for NDK compatibility issue usually caused by NCNN library mismatch
// Defines the missing symbol __libcpp_verbose_abort.
// We intentionally reopen the inline namespace here  -  suppress the Clang warning.
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Winline-namespace-reopened-noninline"
namespace std {
    namespace __ndk1 {
        void __libcpp_verbose_abort(const char* format, ...) {
            va_list args;
            va_start(args, format);
            vfprintf(stderr, format, args);
            va_end(args);
            abort();
        }
    }
}
#pragma clang diagnostic pop

#include "settings.h"
#include "utils/logger.h"
#include "utils/thread.h"
#include "utils/timer.h"
#include "capture/frame_buffer.h"
#include "detector/yolo_detector.h"
#include "renderer/esp_renderer.h"
#include "aimbot/aimbot_controller.h"

/**
 * @file esp_jni.cpp
 * @brief JNI entry point and thread orchestration
 * 
 * Manages the lifecycle of detector, renderer, and worker threads.
 * Provides JNI bindings for Kotlin code.
 * Coordinates frame ingestion, inference, and rendering handoff.
 */

#include <mutex>
#include "input/touch_helper.h"
#include "utils/aimbot_types.h"

// Global unified settings (ALL settings in one place)
UnifiedSettings g_settings;

// Shared render configuration (legacy - now part of g_settings)
ESP::RenderConfig g_renderConfig;

namespace {

    // Global state (managed via RAII)
    std::unique_ptr<ESP::YoloDetector> g_detector;
    std::unique_ptr<ESP::FrameBuffer> g_frameBuffer;
    std::unique_ptr<AimbotController> g_aimbot;
    std::unique_ptr<TouchHelper> g_touchHelper;

    // Path where the native signal handler writes a crash marker. Pushed from
    // Java (CrashReporter) so we write inside the app's external files dir.
    std::string g_nativeCrashPath;

    // Crash handler that PRESERVES Android's own signal handlers. ART installs
    // handlers for SIGSEGV/SIGBUS to implement implicit null checks, GC read
    // barriers, stack-overflow guards, JIT, etc. Replacing those handlers
    // (instead of chaining to them) makes the process terminate on the very
    // first internal ART fault - which presents as a hard "direct crash" at
    // startup with no useful stack. So we record the signal + a backtrace to
    // disk, then forward to whatever handler was installed before us.
    static std::atomic<bool> g_inNativeCrash{false};
    static struct sigaction g_prevSa[64];

    void nativeCrashSignalHandler(int sig, siginfo_t* info, void* ucontext) {
        // Re-entrancy guard: if we crash while handling a crash, drop straight
        // to the default disposition and re-raise so we always terminate.
        if (g_inNativeCrash.exchange(true)) {
            struct sigaction dfl;
            memset(&dfl, 0, sizeof(dfl));
            dfl.sa_handler = SIG_DFL;
            sigaction(sig, &dfl, nullptr);
            raise(sig);
            return;
        }

        if (!g_nativeCrashPath.empty()) {
            int fd = open(g_nativeCrashPath.c_str(),
                          O_WRONLY | O_CREAT | O_TRUNC, 0600);
            if (fd >= 0) {
                const char* head = "AimBuddy native crash\n";
                write(fd, head, static_cast<size_t>(strlen(head)));
                char buf[64];
                int n = snprintf(buf, sizeof(buf), "signal=%d\n", sig);
                if (n > 0) write(fd, buf, static_cast<size_t>(n));
                // Best-effort backtrace (addresses only; symbolise with
                // ndk-stack + the unstripped lib). Disabled on Android because
                // <execinfo.h>/backtrace() is not reliably available across
                // NDK/API levels; we still have Java CrashReporter + tombstones.
#if HAS_NATIVE_BACKTRACE
                void* frames[24];
                int fc = backtrace(frames, 24);
                for (int i = 0; i < fc; ++i) {
                    n = snprintf(buf, sizeof(buf), "0x%p\n", frames[i]);
                    if (n > 0) write(fd, buf, static_cast<size_t>(n));
                }
#endif
                const char* nl = "[end]\n";
                write(fd, nl, static_cast<size_t>(strlen(nl)));
                close(fd);
            }
        }

        // Forward to the previous handler (keeps ART alive).
        struct sigaction* prev = &g_prevSa[sig];
        if (prev->sa_flags & SA_SIGINFO) {
            if (prev->sa_sigaction) prev->sa_sigaction(sig, info, ucontext);
        } else if (prev->sa_handler == SIG_DFL) {
            struct sigaction dfl;
            memset(&dfl, 0, sizeof(dfl));
            dfl.sa_handler = SIG_DFL;
            sigaction(sig, &dfl, nullptr);
            raise(sig);
        } else if (prev->sa_handler != SIG_IGN && prev->sa_handler != nullptr) {
            prev->sa_handler(sig);
        }
        // If the previous handler returned without terminating (rare), just
        // leave; do not force-exit.
    }

    void installNativeCrashHandler() {
        struct sigaction sa;
        memset(&sa, 0, sizeof(sa));
        sa.sa_sigaction = nativeCrashSignalHandler;
        sa.sa_flags = SA_SIGINFO;
        sigemptyset(&sa.sa_mask);
        const int signals[] = { SIGSEGV, SIGBUS, SIGABRT, SIGFPE, SIGILL };
        for (int sig : signals) {
            if (sig > 0 && sig < 64) {
                sigaction(sig, &sa, &g_prevSa[sig]);
            }
        }
    }

    // Bridge availability must outlive TouchHelper instances. Kotlin can report
    // availability BEFORE g_touchHelper exists (refreshAccessibilityState runs
    // in onCreate, TouchHelper is created in nativeInit/nativeInitAimbot).
    // Without caching, the flag was silently dropped and TouchHelper::init()
    // then failed for every non-root backend.
    bool g_shizukuBridgeAvailable = false;
    bool g_accessibilityBridgeAvailable = false;

    // Push cached bridge flags into a freshly created TouchHelper.
    void applyBridgeAvailability() {
        if (!g_touchHelper) return;
        g_touchHelper->setShizukuBridgeAvailable(g_shizukuBridgeAvailable);
        g_touchHelper->setAccessibilityBridgeAvailable(g_accessibilityBridgeAvailable);
    }

    // Threading
    std::unique_ptr<ESP::Thread> g_inferenceThread;
    std::atomic<bool> g_running{false};
    
    // Latest detection result (shared between inference and render threads)
    ESP::DetectionResult g_latestResult;
    std::mutex g_resultMutex;
    
    // Screen dimensions
    int g_screenWidth = 1080;
    int g_screenHeight = 2400;

    // Capture dimensions (match ImageReader config)
    int g_captureWidth = Config::CAPTURE_WIDTH;
    int g_captureHeight = Config::CAPTURE_HEIGHT;
    
    // Cached JNI references
    JavaVM* g_jvm = nullptr;
    std::string g_modelParamPath;
    std::string g_modelBinPath;

    void SyncUnifiedSettingsToRenderConfig() {
        g_renderConfig.boxColorR.store(g_settings.boxColorR, std::memory_order_relaxed);
        g_renderConfig.boxColorG.store(g_settings.boxColorG, std::memory_order_relaxed);
        g_renderConfig.boxColorB.store(g_settings.boxColorB, std::memory_order_relaxed);
        g_renderConfig.boxThickness.store(g_settings.boxThickness, std::memory_order_relaxed);
        g_renderConfig.confidenceThreshold.store(g_settings.confidenceThreshold, std::memory_order_relaxed);
        g_renderConfig.fovRadius.store(g_settings.fovRadius, std::memory_order_relaxed);
        g_renderConfig.showFPS.store(g_settings.showFPS, std::memory_order_relaxed);
        g_renderConfig.showDetectionCount.store(g_settings.showDetectionCount, std::memory_order_relaxed);
        g_renderConfig.showLabels.store(g_settings.showLabels, std::memory_order_relaxed);
        g_renderConfig.drawLine.store(g_settings.drawLine, std::memory_order_relaxed);
        g_renderConfig.drawDot.store(g_settings.drawDot, std::memory_order_relaxed);
        g_renderConfig.enableSmoothing.store(g_settings.enableSmoothing, std::memory_order_relaxed);
        g_renderConfig.smoothingFactor.store(g_settings.smoothingFactor, std::memory_order_relaxed);

        g_renderConfig.aimbotEnabled.store(g_settings.aimbotEnabled, std::memory_order_relaxed);
        g_renderConfig.headOffset.store(g_settings.headOffset, std::memory_order_relaxed);

        if (g_settings.screenWidth > 0 && g_settings.screenHeight > 0) {
            float centerX = g_settings.touchX / static_cast<float>(g_settings.screenWidth);
            float centerY = g_settings.touchY / static_cast<float>(g_settings.screenHeight);
            g_renderConfig.touchCenterX.store(centerX, std::memory_order_relaxed);
            g_renderConfig.touchCenterY.store(centerY, std::memory_order_relaxed);
        }
        g_renderConfig.touchRadius.store(g_settings.touchRadius, std::memory_order_relaxed);
        g_renderConfig.aimDelay.store(g_settings.aimDelay, std::memory_order_relaxed);
    }
    
    // ---------------------------------------------------------------------
    // Temporal confirmation filter
    //
    // Single-cycle phantom boxes are the dominant cause of "it says there is
    // an enemy but nothing is there" - the model occasionally fires on UI
    // panels, lobby backdrops, HUD art, or (with streamer mode off) on the
    // app's own ESP overlay that MediaProjection feeds back into the capture.
    //
    // A REAL target persists across consecutive detector cycles at roughly the
    // same place; a phantom flickers for a single cycle and disappears. So we
    // keep a tiny IoU-associated track table and only publish a box once its
    // track has been seen kConfirmHits cycles in a row. Tracks that go unseen
    // for kMaxMisses cycles are dropped so targets still vanish promptly.
    //
    // TUNING NOTES (YOLO26s, 320×320, mAP50=0.892):
    //   kMatchIoU kept at 0.20: with single-pass full-frame the cycle time is
    //     ~5-15ms, so targets move less between cycles. The lower confidence
    //     threshold (0.38) means more jitter in box positions, so a loose
    //     gate prevents real tracks from failing to associate and resetting.
    //   kMatchIoUConfirmed kept at 0.05: confirmed tracks need a very loose
    //     gate so that fast-moving targets don't break association.
    //   kMaxMisses kept at 3: gives confirmed tracks enough grace to handle
    //     brief detection gaps (occlusion, motion blur) without flickering.
    //   EMA smoothing alpha lowered 0.55→0.45: the YOLO26s model produces
    //     more stable raw detections, so we can afford more smoothing to
    //     eliminate residual per-frame jitter without adding noticeable lag.
    //   Velocity alpha lowered 0.50→0.40: more stable velocity estimation
    //     improves both IoU matching and coasting accuracy.
    //   Coast decay lowered 0.20→0.15: slower confidence decay during brief
    //     occlusion keeps the ESP box visible longer without being misleading.
    //   Coast min confidence raised 0.15→0.20: don't publish very faded
    //     coasting boxes that could confuse the aimbot.
    //   Coast max distance raised 200→300: allows coasting for faster-moving
    //     targets that briefly get occluded.
    // ---------------------------------------------------------------------
    constexpr int   kTrackCapacity = Config::MAX_DETECTIONS;
    constexpr int   kConfirmHits   = 2;      // sightings required before publishing
    constexpr int   kMaxMisses     = 3;      // cycles a track survives unmatched
    constexpr float kMatchIoU          = 0.20f;  // gate for an unconfirmed track
    constexpr float kMatchIoUConfirmed = 0.05f;  // looser gate once confirmed

    // Geometric plausibility limits, applied here (shared by ESP *and* aimbot)
    // rather than only at draw time. Previously the renderer dropped these
    // boxes while the aimbot still locked onto them, so the crosshair could be
    // dragged toward a "target" the user could not even see.
    //
    // ENHANCED:
    //   - Added aspect-ratio gate (0.15–6.0). A player model in an FPS is
    //     taller than wide; extreme slivers (thin horizontal bars or tall
    //     vertical lines) are classic phantom signatures from HUD/UI edges.
    //   - Tightened max fraction 0.85→0.65: a real enemy never occupies more
    //     than ~65% of screen width AND height simultaneously. Lobby panels
    //     and full-screen artifacts are rejected here instead of leaking into
    //     the track table where they'd stay "confirmed" for several cycles.
    //   - Added minimum-area gate: boxes with area < 200px² (e.g. 14×14) are
    //     sub-threshold noise even if both dimensions pass the min check.
    constexpr float kMinBoxPx      = 14.0f;
    constexpr float kMaxBoxFrac    = 0.65f;
    constexpr float kMinBoxArea    = 200.0f;
    constexpr float kMaxAspectRatio = 6.0f;
    constexpr float kMinAspectRatio = 0.15f;

    struct DetTrack {
        float x = 0.0f, y = 0.0f, w = 0.0f, h = 0.0f;
        float vx = 0.0f, vy = 0.0f;   // smoothed velocity (px/cycle)
        float confidence = 0.0f;       // last known detection confidence
        int   classId = 0;             // last known class ID (for coasting)
        int   hits = 0;
        int   misses = 0;
        bool  used = false;
    };

    DetTrack g_tracks[kTrackCapacity];
    int      g_trackCount = 0;

    /// IoU using the track's velocity-predicted next position. When a target
    /// moves quickly between cycles, the raw last-known position is already
    /// stale; predicting one step ahead keeps the IoU gate meaningful.
    inline float TrackIoUPredicted(const DetTrack& t, const ESP::BoundingBox& b) {
        const float px = t.x + t.vx;
        const float py = t.y + t.vy;
        const float ix1 = std::max(px, b.x);
        const float iy1 = std::max(py, b.y);
        const float ix2 = std::min(px + t.w, b.x + b.width);
        const float iy2 = std::min(py + t.h, b.y + b.height);
        const float iw = ix2 - ix1;
        const float ih = iy2 - iy1;
        if (iw <= 0.0f || ih <= 0.0f) return 0.0f;
        const float inter = iw * ih;
        const float uni = t.w * t.h + b.width * b.height - inter;
        return (uni > 0.0f) ? (inter / uni) : 0.0f;
    }

    void ResetDetectionTracks() {
        g_trackCount = 0;
    }

    /// Drop boxes whose geometry cannot plausibly be a player. Runs before the
    /// temporal filter so a fullscreen artefact never even enters the track
    /// table (a static UI panel would otherwise stay "confirmed" forever).
    ///
    /// Checks (all must pass):
    ///   1. Finite coordinates (no NaN/Inf from decode arithmetic).
    ///   2. Min dimension ≥ kMinBoxPx (14px) — degenerate slivers.
    ///   3. Min area ≥ kMinBoxArea (200px²) — catches 14×14 boxes that pass #2.
    ///   4. Max fraction ≤ kMaxBoxFrac (65%) — lobby panels, full-screen artifacts.
    ///   5. Aspect ratio within [0.15, 6.0] — rejects thin bars from HUD edges.
    void DropImplausibleBoxes(ESP::DetectionArray& boxes) {
        const float screenW = static_cast<float>(std::max(1, g_screenWidth));
        const float screenH = static_cast<float>(std::max(1, g_screenHeight));

        ESP::DetectionArray kept;
        for (int i = 0; i < boxes.size(); ++i) {
            const ESP::BoundingBox& b = boxes[i];
            if (!std::isfinite(b.x) || !std::isfinite(b.y) ||
                !std::isfinite(b.width) || !std::isfinite(b.height)) {
                continue;
            }
            // Min dimension
            if (b.width < kMinBoxPx || b.height < kMinBoxPx) continue;
            // Min area
            if (b.width * b.height < kMinBoxArea) continue;
            // Max fraction (both dims must be oversized to reject — a tall
            // enemy near the screen edge can legitimately be > 65% of height)
            if (b.width > screenW * kMaxBoxFrac && b.height > screenH * kMaxBoxFrac) continue;
            // Aspect ratio
            const float ratio = b.width / b.height;
            if (ratio > kMaxAspectRatio || ratio < kMinAspectRatio) continue;

            kept.push(b);
        }
        boxes = kept;
    }

    /// Filter `boxes` in place, keeping only temporally-confirmed detections.
    ///
    /// Improvements over the basic version:
    ///   - Velocity prediction: IoU matching uses the track's predicted next
    ///     position (pos + velocity), so fast-moving targets stay associated.
    ///   - EMA smoothing: confirmed tracks publish a smoothed position instead
    ///     of the raw per-frame detection, eliminating box jitter.
    ///   - Coasting: when a confirmed track is briefly unmatched (occlusion,
    ///     motion blur), its predicted position is still published for up to
    ///     kMaxMisses cycles, preventing the ESP box from flickering off.
    void ApplyTemporalConfirmation(ESP::DetectionArray& boxes) {
        for (int i = 0; i < g_trackCount; ++i) {
            g_tracks[i].used = false;
        }

        ESP::DetectionArray confirmed;

        // EMA smoothing factor for confirmed track positions.
        // 0.45 = 45% new detection, 55% previous smoothed position.
        // Lowered from 0.55 for YOLO26s: the model's raw detections are
        // stable enough that more smoothing eliminates jitter without lag.
        constexpr float kSmoothAlpha = 0.45f;
        // EMA smoothing factor for velocity (lower = more stable velocity est).
        constexpr float kVelAlpha = 0.40f;
        // Confidence decay per coast cycle (0.15 = loses 15% per missed cycle).
        constexpr float kCoastConfidenceDecay = 0.15f;
        // Minimum confidence to keep publishing a coasting track.
        constexpr float kCoastMinConfidence = 0.20f;
        // Maximum coasting distance in pixels (safety: don't coast across screen).
        constexpr float kCoastMaxDistance = 300.0f;

        for (int b = 0; b < boxes.size(); ++b) {
            const ESP::BoundingBox& box = boxes[b];

            // Greedy best-IoU association using PREDICTED position.
            int   best = -1;
            float bestScore = 0.0f;
            for (int i = 0; i < g_trackCount; ++i) {
                if (g_tracks[i].used) continue;
                const float gate = (g_tracks[i].hits >= kConfirmHits)
                                       ? kMatchIoUConfirmed
                                       : kMatchIoU;
                const float iou = TrackIoUPredicted(g_tracks[i], box);
                if (iou > gate && iou > bestScore) {
                    bestScore = iou;
                    best = i;
                }
            }

            if (best >= 0) {
                DetTrack& t = g_tracks[best];

                // Update velocity (smoothed EMA).
                const float rawVx = box.x - t.x;
                const float rawVy = box.y - t.y;
                t.vx = t.vx * (1.0f - kVelAlpha) + rawVx * kVelAlpha;
                t.vy = t.vy * (1.0f - kVelAlpha) + rawVy * kVelAlpha;

                // Update position. For confirmed tracks, apply EMA smoothing
                // to reduce per-frame jitter. For unconfirmed tracks, use raw
                // position (need accuracy for confirmation matching).
                if (t.hits >= kConfirmHits) {
                    t.x = t.x * (1.0f - kSmoothAlpha) + box.x * kSmoothAlpha;
                    t.y = t.y * (1.0f - kSmoothAlpha) + box.y * kSmoothAlpha;
                    t.w = t.w * (1.0f - kSmoothAlpha) + box.width * kSmoothAlpha;
                    t.h = t.h * (1.0f - kSmoothAlpha) + box.height * kSmoothAlpha;
                } else {
                    t.x = box.x; t.y = box.y; t.w = box.width; t.h = box.height;
                }

                t.confidence = box.confidence;
                t.classId = box.classId;
                if (t.hits < kConfirmHits) ++t.hits;
                t.misses = 0;
                t.used = true;

                if (t.hits >= kConfirmHits) {
                    // Publish the smoothed position.
                    ESP::BoundingBox smoothed;
                    smoothed.x = t.x;
                    smoothed.y = t.y;
                    smoothed.width = t.w;
                    smoothed.height = t.h;
                    smoothed.confidence = t.confidence;
                    smoothed.classId = box.classId;
                    confirmed.push(smoothed);
                }
            } else if (g_trackCount < kTrackCapacity) {
                DetTrack& t = g_tracks[g_trackCount++];
                t.x = box.x; t.y = box.y; t.w = box.width; t.h = box.height;
                t.vx = 0.0f; t.vy = 0.0f;
                t.confidence = box.confidence;
                t.classId = box.classId;
                t.hits = 1;
                t.misses = 0;
                t.used = true;
                // First sighting: withheld until confirmed next cycle.
            }
        }

        // Coast: publish predicted positions for briefly-lost confirmed tracks.
        // This prevents ESP boxes from flickering off during brief occlusion or
        // motion blur, making the visual experience much more stable.
        for (int i = 0; i < g_trackCount; ++i) {
            if (g_tracks[i].used) continue;
            if (g_tracks[i].hits < kConfirmHits) continue;

            DetTrack& t = g_tracks[i];
            const int coastSteps = t.misses + 1;
            const float coastDx = t.vx * coastSteps;
            const float coastDy = t.vy * coastSteps;
            const float coastDist = std::sqrt(coastDx * coastDx + coastDy * coastDy);

            // Don't coast if the predicted position is too far (likely lost).
            if (coastDist > kCoastMaxDistance) continue;

            const float decayedConf = t.confidence * (1.0f - kCoastConfidenceDecay * coastSteps);
            if (decayedConf < kCoastMinConfidence) continue;
            if (confirmed.full()) break;

            ESP::BoundingBox coasted;
            coasted.x = t.x + coastDx;
            coasted.y = t.y + coastDy;
            coasted.width = t.w;
            coasted.height = t.h;
            coasted.confidence = decayedConf;
            coasted.classId = t.classId;
            confirmed.push(coasted);
        }

        // Age out tracks that were not matched this cycle.
        for (int i = g_trackCount - 1; i >= 0; --i) {
            if (g_tracks[i].used) continue;
            if (++g_tracks[i].misses > kMaxMisses) {
                g_tracks[i] = g_tracks[g_trackCount - 1];
                --g_trackCount;
            }
        }

        boxes = confirmed;
    }

    /**
     * @brief Inference thread main loop
     * 
     * Consumes frames from ring buffer, runs YOLO inference,
     * and updates shared detection result.
     */
    void inferenceLoop() {
        LOGI("=== Inference thread started ===");
        ResetDetectionTracks();
        
        // Attach thread to JVM for JNI calls
        JNIEnv* env = nullptr;
        if (g_jvm->AttachCurrentThread(&env, nullptr) != 0) {
            LOGE("Failed to attach inference thread to JVM");
            return;
        }
        
        ESP::Frame frame;
        ESP::DetectionResult result;
        float cachedThreshold = -1.0f;

        uint64_t statsSamples = 0;
        uint64_t statsDrainedFrames = 0;  // accumulated in window, reset at report
        double statsInferenceMs = 0.0;
        double statsEndToEndMs = 0.0;
        uint64_t statsEndToEndSamples = 0;
        constexpr uint64_t kStatsWindow = 120;
        constexpr double kEmaAlpha = 0.15;
        constexpr auto kNoFrameSleepMin = std::chrono::microseconds(200);
        constexpr auto kNoFrameSleepMax = std::chrono::microseconds(2000);

        double emaInferMs = 0.0;
        double emaEndToEndMs = 0.0;
        uint32_t noFrameBackoffLevel = 0;
        
        while (g_running.load(std::memory_order_acquire)) {
            // Try to get a frame from buffer
            if (g_frameBuffer && g_frameBuffer->pop(frame)) {
                noFrameBackoffLevel = 0;

                // Drain to latest frame to reduce latency
                ESP::Frame newer;
                uint64_t drainedThisIteration = 0;
                while (g_frameBuffer->pop(newer)) {
                    if (frame.hardwareBuffer) {
                        AHardwareBuffer_release(frame.hardwareBuffer);
                    }
                    frame = newer;
                    drainedThisIteration++;
                }
                statsDrainedFrames += drainedThisIteration;

                if (frame.hardwareBuffer && g_detector) {
                    // Update detector threshold only when changed
                    const float threshold = g_settings.confidenceThreshold;
                    if (std::fabs(threshold - cachedThreshold) > 0.0001f) {
                        g_detector->setConfidenceThreshold(threshold);
                        cachedThreshold = threshold;
                    }

                    const auto inferStart = std::chrono::steady_clock::now();

                    // ----------------------------------------------------------------
                    // Single-pass FULL-FRAME detection (matches training pipeline).
                    //
                    // CRITICAL: The model is trained on full-screen frames
                    // extracted from 1280×720 gameplay footage (see
                    // training/src/extract_frames.py: _preprocess_frame does
                    // scale-to-720p → full-frame letterbox → resize-to-640).
                    //
                    // The runtime MUST use the same FOV: letterbox the entire
                    // 1280×720 capture into the 320×320 model input. Both
                    // training and inference see 100% of the game screen
                    // because games run fullscreen. Using center-crop mode
                    // (fullFrame=false) would only see the central 320×320
                    // region, missing edge enemies the model was trained on.
                    //
                    // Full-frame mode (fullFrame=true) matches training exactly:
                    //   1280×720 → letterbox 320×320 → infer
                    // ----------------------------------------------------------------
                    if (g_detector->detect(frame.hardwareBuffer, result, Config::CROP_SIZE, true)) {
                        const auto inferEnd = std::chrono::steady_clock::now();
                        const double inferMs = std::chrono::duration<double, std::milli>(inferEnd - inferStart).count();
                        statsInferenceMs += inferMs;
                        statsSamples++;

                        if (emaInferMs <= 0.0) {
                            emaInferMs = inferMs;
                        } else {
                            emaInferMs += (inferMs - emaInferMs) * kEmaAlpha;
                        }

                        bool hasEndToEnd = false;
                        double e2eMs = 0.0;

                        if (frame.timestamp > 0) {
                            const int64_t nowNs = std::chrono::duration_cast<std::chrono::nanoseconds>(
                                std::chrono::steady_clock::now().time_since_epoch()).count();
                            if (nowNs > frame.timestamp) {
                                e2eMs = static_cast<double>(nowNs - frame.timestamp) / 1'000'000.0;
                                if (e2eMs >= 0.0 && e2eMs < 2000.0) {
                                    statsEndToEndMs += e2eMs;
                                    statsEndToEndSamples++;
                                    hasEndToEnd = true;
                                }
                            }
                        }

                        if (hasEndToEnd) {
                            if (emaEndToEndMs <= 0.0) {
                                emaEndToEndMs = e2eMs;
                            } else {
                                emaEndToEndMs += (e2eMs - emaEndToEndMs) * kEmaAlpha;
                            }
                        }

                        // Clean the result before anything consumes it, so ESP
                        // rendering AND aimbot targeting always agree on what
                        // counts as a real enemy.
                        DropImplausibleBoxes(result.boxes);      // geometry
                        ApplyTemporalConfirmation(result.boxes); // persistence

                        // Copy result to shared state (Thread-Safe)
                        {
                            std::lock_guard<std::mutex> lock(g_resultMutex);
                            g_latestResult = result;
                        }

                        // Update Aimbot Target Logic (Thread-Safe)
                        if (g_aimbot && g_settings.aimbotEnabled) {
                             g_aimbot->updateTargets(result.boxes.data(), result.boxes.size());
                        }

                        if (statsSamples >= kStatsWindow) {
                            const uint32_t droppedAtPush = g_frameBuffer ? g_frameBuffer->consumeDroppedFrameCount() : 0;
                            const double avgInfer = statsInferenceMs / static_cast<double>(statsSamples);
                            const double avgEndToEnd = (statsEndToEndSamples > 0)
                                ? (statsEndToEndMs / static_cast<double>(statsEndToEndSamples))
                                : 0.0;

                            LOGI("Pipeline stats: avg infer=%.2fms avg e2e=%.2fms ema infer=%.2fms ema e2e=%.2fms drained=%llu dropped_push=%u",
                                 avgInfer,
                                 avgEndToEnd,
                                 emaInferMs,
                                 emaEndToEndMs,
                                 static_cast<unsigned long long>(statsDrainedFrames),
                                 droppedAtPush);

                            statsSamples = 0;
                            statsDrainedFrames = 0;
                            statsInferenceMs = 0.0;
                            statsEndToEndMs = 0.0;
                            statsEndToEndSamples = 0;
                        }
                    }
                }

                if (frame.hardwareBuffer) {
                    AHardwareBuffer_release(frame.hardwareBuffer);
                }
            } else {
                // No frame available, sleep briefly
                const auto sleepDuration = std::min(kNoFrameSleepMin * (1u << noFrameBackoffLevel), kNoFrameSleepMax);
                std::this_thread::sleep_for(sleepDuration);
                if (noFrameBackoffLevel < 4) {
                    ++noFrameBackoffLevel;
                }
            }
        }
        
        g_jvm->DetachCurrentThread();
        LOGI("Inference thread stopped");
    }
}

extern "C" {

// Accessors for GLSurfaceView renderer (imgui_menu.cpp)
ESP::RenderConfig* GetRenderConfig() {
    return &g_renderConfig;
}

AimbotController* GetAimbotController() {
    return g_aimbot.get();
}

void UpdateScreenSize(int width, int height) {
    if (width <= 0 || height <= 0) {
        return;
    }

    g_screenWidth = width;
    g_screenHeight = height;

    g_settings.screenWidth = width;
    g_settings.screenHeight = height;
    g_settings.validate();
    SyncUnifiedSettingsToRenderConfig();

    if (g_detector) {
        g_detector->setScreenSize(width, height);
    }
    if (g_touchHelper) {
        g_touchHelper->setScreenSize(width, height);
    }
    if (g_aimbot) {
        g_aimbot->setScreenSize(width, height);
    }
}

bool GetLatestResultSnapshot(ESP::DetectionResult* out) {
    if (!out) {
        return false;
    }
    // Copy the most recent result (thread-safe copy)
    std::lock_guard<std::mutex> lock(g_resultMutex);
    *out = g_latestResult;
    return true;
}

void GetCaptureSize(int* outWidth, int* outHeight) {
    if (outWidth) *outWidth = g_captureWidth;
    if (outHeight) *outHeight = g_captureHeight;
}

/**
 * @brief JNI_OnLoad - Called when native library is loaded
 */
extern "C" JNIEXPORT void JNICALL
Java_com_aimbuddy_ImGuiGLSurface_nativeSetCrashLogPath(JNIEnv* env, jclass,
                                                       jstring path) {
    if (path) {
        const char* p = env->GetStringUTFChars(path, nullptr);
        if (p) {
            g_nativeCrashPath = p;
            env->ReleaseStringUTFChars(path, p);
        }
    }
}

JNIEXPORT jint JNI_OnLoad(JavaVM* vm, void* reserved) {
    LOGI("Native library loaded");
    g_jvm = vm;

    // Initialize NCNN for Vulkan FIRST so its internal signal usage still sees
    // the system's default handlers, then install our crash handler which
    // CHAINS to whatever was already installed (preserving Android/ART
    // handlers). Replacing ART's SIGSEGV/SIGBUS handlers outright was the cause
    // of the "launch -> instant crash" regression: ART relies on those signals
    // for null checks, GC read barriers, etc., and losing them kills the app.
    ncnn::create_gpu_instance();
    installNativeCrashHandler();

    return JNI_VERSION_1_6;
}

/**
 * @brief JNI_OnUnload - Called when native library is unloaded
 */
JNIEXPORT void JNI_OnUnload(JavaVM* vm, void* reserved) {
    LOGI("Native library unloading");
    ncnn::destroy_gpu_instance();
}

/**
 * @brief Initialize native components
 */
JNIEXPORT jboolean JNICALL
Java_com_aimbuddy_MainActivity_nativeInit(JNIEnv* env, jobject thiz,
                                      jobject assetManager,
                                      jint screenWidth, jint screenHeight) {
    LOGI("nativeInit: screen %dx%d", screenWidth, screenHeight);
    
    g_screenWidth = screenWidth;
    g_screenHeight = screenHeight;
    
    // Load unified settings from disk
    if (g_settings.load()) {
        LOGI("Loaded settings from disk");
        g_settings.validate();
    } else {
        LOGI("Using default settings");
        g_settings.setDefaultTouchPosition(screenWidth, screenHeight);
    }
    
    // Set runtime values
    g_settings.screenWidth = screenWidth;
    g_settings.screenHeight = screenHeight;
    SyncUnifiedSettingsToRenderConfig();
    
    // Get native asset manager
    AAssetManager* mgr = AAssetManager_fromJava(env, assetManager);
    if (!mgr) {
        LOGE("Failed to get AssetManager");
        return JNI_FALSE;
    }
    
    // Create and initialize detector
    g_detector = std::make_unique<ESP::YoloDetector>();
    const char* modelParamPath = g_modelParamPath.empty() ? nullptr : g_modelParamPath.c_str();
    const char* modelBinPath = g_modelBinPath.empty() ? nullptr : g_modelBinPath.c_str();
    if (!g_detector->initialize(mgr, screenWidth, screenHeight, modelParamPath, modelBinPath)) {
        LOGE("Failed to initialize detector");
        g_detector.reset();
        return JNI_FALSE;
    }
    
    // Create TouchHelper and AimbotController (but DON'T init touch yet)
    // Touch init happens in nativeInitAimbot() AFTER root permissions are set
    g_touchHelper = std::make_unique<TouchHelper>();
    g_touchHelper->setJniBridge(g_jvm, env, thiz);
    g_touchHelper->setBackend(g_settings.touchBackend == 2 ? TouchBackend::ACCESSIBILITY :
                              g_settings.touchBackend == 1 ? TouchBackend::SHIZUKU : TouchBackend::UINPUT);
    applyBridgeAvailability();
    g_touchHelper->setScreenSize(screenWidth, screenHeight);
    
    // Create aimbot controller with touch helper
    // NOTE: We don't call init() or start() here - that happens after root
    g_aimbot = std::make_unique<AimbotController>(g_touchHelper.get(), screenWidth, screenHeight);
    
    // Create frame buffer
    g_frameBuffer = std::make_unique<ESP::FrameBuffer>();
    
    LOGI("Native components initialized");
    return JNI_TRUE;
}

/**
 * @brief Start inference thread
 */
JNIEXPORT void JNICALL
Java_com_aimbuddy_MainActivity_nativeStart(JNIEnv* env, jobject thiz) {
    LOGI("nativeStart called");
    
    if (g_running.load(std::memory_order_acquire)) {
        LOGI("Inference already running");
        return;
    }
    
    if (!g_detector || !g_frameBuffer) {
        LOGE("Cannot start: components not initialized");
        return;
    }
    
    // Start inference thread
    g_running.store(true, std::memory_order_release);
    g_inferenceThread = std::make_unique<ESP::Thread>(inferenceLoop, "InferenceThread");
    
    if (!g_inferenceThread->start(Config::INFERENCE_THREAD_CPU_AFFINITY)) {
        LOGE("Failed to start inference thread!");
        g_running.store(false, std::memory_order_release);
        g_inferenceThread.reset();
        return;
    }
    
    LOGI("Inference thread started successfully");
}

/**
 * @brief Stop inference thread
 */
JNIEXPORT void JNICALL
Java_com_aimbuddy_MainActivity_nativeStop(JNIEnv* env, jobject thiz) {
    LOGI("nativeStop called");
    
    if (!g_running.load(std::memory_order_acquire)) {
        LOGI("Inference not running");
        return;
    }
    
    // Stop inference thread
    g_running.store(false, std::memory_order_release);
    
    if (g_inferenceThread) {
        g_inferenceThread->join();
        g_inferenceThread.reset();
    }
    
    LOGI("Inference thread stopped");
}

/**
 * @brief Shutdown and cleanup all native resources
 */
JNIEXPORT void JNICALL
Java_com_aimbuddy_MainActivity_nativeShutdown(JNIEnv* env, jobject thiz) {
    LOGI("Shutting down native components");
    
    // Save unified settings to disk
    if (g_settings.save()) {
        LOGI("Settings saved successfully");
    } else {
        LOGE("Failed to save settings");
    }
    
    // Stop if running
    g_running.store(false, std::memory_order_release);
    if (g_inferenceThread) {
        g_inferenceThread->join();
        g_inferenceThread.reset();
    }
    
    // Stop Aimbot Thread
    if (g_aimbot) {
        g_aimbot->stop();
        g_aimbot.reset();
    }
    
    if (g_touchHelper) {
        g_touchHelper->shutdown();
        g_touchHelper.reset();
    }
    
    // Cleanup 
    g_frameBuffer.reset();
    g_detector.reset();
    
    LOGI("Native shutdown complete");
}

/**
 * @brief Handle incoming frame from screen capture
 * @param hardwareBuffer AHardwareBuffer from ImageReader
 */
JNIEXPORT void JNICALL
Java_com_aimbuddy_ScreenCaptureService_nativeOnFrame(JNIEnv* env, jclass clazz,
                                                 jobject hardwareBuffer,
                                                 jlong timestamp) {
    if (!g_running.load(std::memory_order_acquire)) {
        return;
    }
    
    if (!hardwareBuffer || !g_frameBuffer) {
        return;
    }
    
    // Get native hardware buffer
    AHardwareBuffer* buffer = AHardwareBuffer_fromHardwareBuffer(env, hardwareBuffer);
    if (!buffer) {
        LOGW("Failed to get AHardwareBuffer");
        return;
    }
    
    // Acquire reference (will be released after inference)
    AHardwareBuffer_acquire(buffer);
    
    // Push to frame buffer
    ESP::Frame frame;
    frame.hardwareBuffer = buffer;
    frame.timestamp = timestamp;
    frame.width = g_captureWidth;
    frame.height = g_captureHeight;
    
    if (!g_frameBuffer->push(frame)) {
        // Buffer full - release; drop count is tracked in FrameBuffer for periodic telemetry
        AHardwareBuffer_release(buffer);
    }
}

/**
 * @brief Render one frame (called from render thread)
 */
JNIEXPORT void JNICALL
Java_com_aimbuddy_MainActivity_nativeRender(JNIEnv* env, jobject thiz) {
    (void)env;
    (void)thiz;
    // Rendering handled by GLSurfaceView (imgui_menu.cpp)
}

/**
 * @brief Handle touch event
 * @param action Touch action (0=down, 1=up, 2=move)
 * @param x X coordinate
 * @param y Y coordinate
 * @return true if event was consumed by ImGui
 */
JNIEXPORT jboolean JNICALL
Java_com_aimbuddy_MainActivity_nativeOnTouch(JNIEnv* env, jobject thiz,
                                         jint action, jfloat x, jfloat y) {
    (void)env;
    (void)thiz;
    (void)action;
    (void)x;
    (void)y;
    return JNI_FALSE;
}

/**
 * @brief Get current FPS
 */
JNIEXPORT jfloat JNICALL
Java_com_aimbuddy_MainActivity_nativeGetFPS(JNIEnv* env, jobject thiz) {
    (void)env;
    (void)thiz;
    return 0.0f;
}

/**
 * @brief Check if ESP is running
 */
JNIEXPORT jboolean JNICALL
Java_com_aimbuddy_MainActivity_nativeIsRunning(JNIEnv* env, jobject thiz) {
    return g_running.load(std::memory_order_acquire) ? JNI_TRUE : JNI_FALSE;
}

/**
 * @brief Initialize/Re-initialize aimbot components (called after root grant)
 * This is where TouchHelper.init() actually happens - AFTER root permissions
 */
JNIEXPORT jboolean JNICALL
Java_com_aimbuddy_MainActivity_nativeInitAimbot(JNIEnv* env, jobject thiz) {
    LOGI("nativeInitAimbot called - initializing TouchHelper with root");
    
    if (!g_touchHelper) {
        LOGE("TouchHelper is null, creating new one");
        g_touchHelper = std::make_unique<TouchHelper>();
    }

    g_touchHelper->setJniBridge(g_jvm, env, thiz);
    g_touchHelper->setBackend(g_settings.touchBackend == 2 ? TouchBackend::ACCESSIBILITY :
                              g_settings.touchBackend == 1 ? TouchBackend::SHIZUKU : TouchBackend::UINPUT);
    applyBridgeAvailability();
    g_touchHelper->setScreenSize(g_screenWidth, g_screenHeight);
    
    // THIS is where we actually init the touch device (needs root)
    if (g_touchHelper->init()) {
        LOGI("TouchHelper initialized successfully!");
        LOGI("Touch device opened, uinput created, grab active");
        
        // Now start aimbot controller since touch is working
        if (g_aimbot) {
            g_aimbot->start();
            LOGI("AimbotController started with working touch");
        } else {
            g_aimbot = std::make_unique<AimbotController>(g_touchHelper.get(), g_screenWidth, g_screenHeight);
            g_aimbot->start();
            LOGI("AimbotController created and started");
        }
        return JNI_TRUE;
    }
    
    LOGE("TouchHelper init FAILED for backend=%d", g_settings.touchBackend);
    LOGE("Check: uinput requires root, Shizuku requires active service + permission");
    return JNI_FALSE;
}

JNIEXPORT void JNICALL
Java_com_aimbuddy_MainActivity_nativeSetTouchBackend(JNIEnv* /* env */, jobject /* thiz */, jint backend) {
    const int clamped = std::max(0, std::min(2, static_cast<int>(backend)));
    g_settings.touchBackend = clamped;
    g_settings.validate();

    if (g_touchHelper) {
        g_touchHelper->setBackend(clamped == 2 ? TouchBackend::ACCESSIBILITY :
                                 (clamped == 1 ? TouchBackend::SHIZUKU : TouchBackend::UINPUT));
    }
    LOGI("Touch backend set to %d", clamped);
}

JNIEXPORT jint JNICALL
Java_com_aimbuddy_MainActivity_nativeGetTouchBackend(JNIEnv* /* env */, jobject /* thiz */) {
    return g_settings.touchBackend;
}

/**
 * Push streamer-mode (FLAG_SECURE) state to MainActivity via JNI.
 * Called from imgui_menu.cpp whenever the user toggles the setting.
 */
void NotifyStreamerModeChanged(bool enabled) {
    if (!g_jvm) return;
    JNIEnv* env = nullptr;
    bool attached = false;
    if (g_jvm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK) {
        if (g_jvm->AttachCurrentThread(&env, nullptr) != JNI_OK) {
            return;
        }
        attached = true;
    }
    jclass cls = env->FindClass("com/aimbuddy/MainActivity");
    if (cls) {
        jmethodID method = env->GetStaticMethodID(cls, "nativeApplyStreamerMode", "(Z)V");
        if (method) {
            env->CallStaticVoidMethod(cls, method, static_cast<jboolean>(enabled));
        }
        if (env->ExceptionCheck()) env->ExceptionClear();
        env->DeleteLocalRef(cls);
    }
    if (attached) g_jvm->DetachCurrentThread();
}

JNIEXPORT void JNICALL
Java_com_aimbuddy_MainActivity_nativeSetShizukuBridgeAvailable(JNIEnv* /* env */, jobject /* thiz */, jboolean available) {
    g_shizukuBridgeAvailable = (available == JNI_TRUE);
    if (g_touchHelper) {
        g_touchHelper->setShizukuBridgeAvailable(g_shizukuBridgeAvailable);
    }
}

JNIEXPORT void JNICALL
Java_com_aimbuddy_MainActivity_nativeSetAccessibilityBridgeAvailable(JNIEnv* /* env */, jobject /* thiz */, jboolean available) {
    g_accessibilityBridgeAvailable = (available == JNI_TRUE);
    if (g_touchHelper) {
        g_touchHelper->setAccessibilityBridgeAvailable(g_accessibilityBridgeAvailable);
    }
}

JNIEXPORT void JNICALL
Java_com_aimbuddy_MainActivity_nativeSetModelPaths(JNIEnv* env, jobject thiz,
                                                   jstring paramPath,
                                                   jstring binPath) {
    (void)thiz;

    g_modelParamPath.clear();
    g_modelBinPath.clear();

    if (paramPath != nullptr) {
        const char* chars = env->GetStringUTFChars(paramPath, nullptr);
        if (chars != nullptr) {
            g_modelParamPath.assign(chars);
            env->ReleaseStringUTFChars(paramPath, chars);
        }
    }

    if (binPath != nullptr) {
        const char* chars = env->GetStringUTFChars(binPath, nullptr);
        if (chars != nullptr) {
            g_modelBinPath.assign(chars);
            env->ReleaseStringUTFChars(binPath, chars);
        }
    }

    LOGI("Updated model paths: param='%s' bin='%s'",
         g_modelParamPath.c_str(),
         g_modelBinPath.c_str());
}

} // extern "C"
