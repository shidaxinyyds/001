package com.aimbuddy

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Path
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import kotlin.math.abs

/**
 * AimAccessibilityService - no-root, no-Shizuku touch injection ("开箱即用").
 *
 * The user enables AimBuddy once in system Accessibility settings; from then on
 * the aimbot can drive a synthetic pointer without root or Shizuku.
 *
 * ## Why a "gesture pump" instead of one gesture per frame
 *
 * The naive approach (build a fresh [GestureDescription] every frame and
 * dispatch it) does not work on Android, for three framework reasons:
 *
 *  1. `StrokeDescription` rejects zero-length paths ("Path has zero length").
 *     When the aim target is stationary the start and end points are identical,
 *     so every dispatch would throw.
 *  2. Only ONE gesture may be in flight at a time. Dispatching every frame makes
 *     `dispatchGesture` return false for all but the first one.
 *  3. Independent gestures lift the pointer between frames, which breaks
 *     drag-style aiming (the game sees tap-tap-tap instead of a held drag).
 *
 * Instead we run a self-clocking pump on a dedicated thread:
 *
 *  - Each segment is a *continued* stroke (`willContinue = true`), so the
 *    synthetic pointer stays DOWN across segment boundaries.
 *  - The next segment is dispatched from the previous segment's `onCompleted`
 *    callback, guaranteeing exactly one gesture in flight.
 *  - Each segment walks from the previous end point to the latest target, so
 *    the pointer always chases the newest aim position.
 *  - Degenerate (zero-length) segments are nudged by one pixel to satisfy the
 *    framework while staying visually stationary.
 *  - `onCancelled` (e.g. the system preempted us) restarts the pump, so the
 *    backend self-heals instead of silently dying.
 */
class AimAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "AimA11yService"

        /** Duration of each continued segment. Shorter = lower aim latency. */
        private const val SEGMENT_MS = 50L

        /** Duration of the final segment that lifts the pointer. */
        private const val RELEASE_MS = 16L

        /** Minimum path length accepted by the framework. */
        private const val MIN_PATH_LEN = 1f

        @Volatile
        var instance: AimAccessibilityService? = null
            private set

        /** True once the service is bound and able to inject. */
        @JvmStatic
        fun isReady(): Boolean = instance != null

        /** Called from native via MainActivity's static JNI bridge. */
        @JvmStatic
        fun aimMove(screenX: Float, screenY: Float, isFirst: Boolean): Boolean =
            instance?.dispatchAim(screenX, screenY, isFirst) ?: false

        /** Called from native via MainActivity's static JNI bridge. */
        @JvmStatic
        fun aimUp(): Boolean = instance?.releaseAim() ?: false
    }

    private var pumpThread: HandlerThread? = null
    private var pump: Handler? = null

    // Shared with the aimbot thread.
    @Volatile private var wantDown = false
    @Volatile private var targetX = 0f
    @Volatile private var targetY = 0f

    // Pump-thread-only state. Never touch these from other threads.
    private var running = false
    private var lastStroke: GestureDescription.StrokeDescription? = null
    private var curX = 0f
    private var curY = 0f

    override fun onCreate() {
        super.onCreate()
        val thread = HandlerThread("aim-a11y-pump").also { it.start() }
        pumpThread = thread
        pump = Handler(thread.looper)
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "Accessibility service connected")
    }

    override fun onUnbind(intent: Intent?): Boolean {
        instance = null
        wantDown = false
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        wantDown = false
        try {
            pumpThread?.quitSafely()
        } catch (_: Throwable) {
        }
        pumpThread = null
        pump = null
        super.onDestroy()
    }

    override fun onInterrupt() {
        // No-op: this service never observes UI, it only injects gestures.
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // No-op: this service never observes UI, it only injects gestures.
    }

    /**
     * Move the synthetic pointer toward [screenX] / [screenY] (display pixels).
     * Starts the pump on the first call of a press. Returns false only when the
     * service is not usable at all, so the native layer can fall back.
     */
    fun dispatchAim(screenX: Float, screenY: Float, isFirst: Boolean): Boolean {
        val handler = pump ?: return false
        targetX = clampX(screenX)
        targetY = clampY(screenY)

        if (isFirst || !wantDown) {
            wantDown = true
            handler.post { startPump() }
        }
        return true
    }

    /**
     * Lift the synthetic pointer. The pump finishes the current segment with a
     * non-continued stroke, which produces the ACTION_UP.
     */
    fun releaseAim(): Boolean {
        if (!wantDown) return true
        wantDown = false
        return true
    }

    // ---------------------------------------------------------------- pump

    private fun startPump() {
        if (running || !wantDown) return
        running = true
        lastStroke = null
        curX = targetX
        curY = targetY
        step(first = true)
    }

    private fun step(first: Boolean) {
        if (!wantDown) {
            finishStroke()
            return
        }

        val startX = curX
        val startY = curY
        var endX = targetX
        var endY = targetY

        // The framework rejects zero-length paths, which is exactly what a
        // stationary aim target produces. Nudge by one pixel, keeping the point
        // inside the display bounds.
        if (abs(endX - startX) < MIN_PATH_LEN && abs(endY - startY) < MIN_PATH_LEN) {
            endX = if (startX + MIN_PATH_LEN <= maxX()) {
                startX + MIN_PATH_LEN
            } else {
                startX - MIN_PATH_LEN
            }
            endY = startY
        }

        val path = Path().apply {
            moveTo(startX, startY)
            lineTo(endX, endY)
        }

        val stroke = try {
            val previous = lastStroke
            if (first || previous == null) {
                GestureDescription.StrokeDescription(path, 0L, SEGMENT_MS, true)
            } else {
                // Continued strokes MUST start at the previous stroke's end
                // point, which curX/curY always holds.
                previous.continueStroke(path, 0L, SEGMENT_MS, true)
            }
        } catch (t: Throwable) {
            Log.e(TAG, "stroke build failed: ${t.message}")
            stopPump()
            return
        }

        val dispatched = try {
            dispatchGesture(
                GestureDescription.Builder().addStroke(stroke).build(),
                object : AccessibilityService.GestureResultCallback() {
                    override fun onCompleted(gestureDescription: GestureDescription?) {
                        pump?.post { step(first = false) }
                    }

                    override fun onCancelled(gestureDescription: GestureDescription?) {
                        pump?.post { restartAfterCancel() }
                    }
                },
                pump
            )
        } catch (t: Throwable) {
            Log.e(TAG, "dispatchGesture threw: ${t.message}")
            false
        }

        if (dispatched) {
            lastStroke = stroke
            curX = endX
            curY = endY
        } else {
            // Could not dispatch (e.g. another gesture still in flight).
            // Drop the chain and retry shortly so aiming self-heals.
            stopPump()
            if (wantDown) {
                pump?.postDelayed({ startPump() }, 16L)
            }
        }
    }

    /** The system cancelled our stroke; drop the chain and start a fresh one. */
    private fun restartAfterCancel() {
        stopPump()
        if (wantDown) {
            startPump()
        }
    }

    /** Emit a final non-continued segment so the pointer actually lifts. */
    private fun finishStroke() {
        val previous = lastStroke
        if (previous != null) {
            try {
                val startX = curX
                val startY = curY
                var endX = startX + MIN_PATH_LEN
                if (endX > maxX()) endX = startX - MIN_PATH_LEN
                val path = Path().apply {
                    moveTo(startX, startY)
                    lineTo(endX, startY)
                }
                val last = previous.continueStroke(path, 0L, RELEASE_MS, false)
                dispatchGesture(
                    GestureDescription.Builder().addStroke(last).build(),
                    null,
                    pump
                )
            } catch (t: Throwable) {
                Log.e(TAG, "release failed: ${t.message}")
            }
        }
        stopPump()
    }

    private fun stopPump() {
        lastStroke = null
        running = false
    }

    // ------------------------------------------------------------- helpers

    private fun maxX(): Float {
        val w = resources.displayMetrics.widthPixels
        return if (w > 1) (w - 1).toFloat() else 1f
    }

    private fun maxY(): Float {
        val h = resources.displayMetrics.heightPixels
        return if (h > 1) (h - 1).toFloat() else 1f
    }

    private fun clampX(value: Float): Float = value.coerceIn(0f, maxX())

    private fun clampY(value: Float): Float = value.coerceIn(0f, maxY())
}
