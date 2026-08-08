package com.aimbuddy

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.accessibility.AccessibilityEvent

/**
 * AimAccessibilityService - no-root, no-Shizuku touch injection.
 *
 * This is "方案 A": the out-of-box ("开箱即用") input backend. The user only
 * enables AimBuddy once in system Accessibility settings; from then on the app
 * can drive the aimbot pointer without root or Shizuku.
 *
 * How injection works:
 *  - The aimbot pipeline calls [dispatchAim] every frame with the target point.
 *  - Each call builds a single-stroke [GestureDescription] that starts at the
 *    previous point and ends at the new point, held for [GESTURE_HOLD_MS].
 *  - Accessibility gestures are injected as a distinct stream by
 *    InputDispatcher, so the user's real finger keeps working in parallel.
 *  - Because only one gesture can be in flight at a time, each new frame cancels
 *    the previous gesture (ACTION_UP at the old point) and starts a new one
 *    (ACTION_DOWN at the same point). Re-dispatching faster than the hold
 *    duration elapses keeps the synthetic pointer effectively held at the target.
 *  - [releaseAim] dispatches one last short gesture (down+up) to lift the
 *    synthetic pointer.
 */
class AimAccessibilityService : AccessibilityService() {

    companion object {
        private const val TAG = "AimA11yService"

        // Hold each gesture long enough that the next frame (aimbot runs at
        // 30-120 fps, i.e. every ~8-33 ms) replaces it before it completes,
        // avoiding an intermittent up/down gap.
        private const val GESTURE_HOLD_MS: Long = 90

        // Final release gesture duration: down then up quickly.
        private const val RELEASE_HOLD_MS: Long = 24

        @Volatile
        var instance: AimAccessibilityService? = null
            private set
    }

    private val dispatchThread = HandlerThread("aim-a11y-gesture").also { it.start() }
    private val dispatchHandler = Handler(dispatchThread.looper)

    @Volatile
    private var pointerDown = false
    private var lastX = 0f
    private var lastY = 0f

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "Accessibility service connected")
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        instance = null
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        try {
            dispatchThread.quitSafely()
        } catch (_: Throwable) {
        }
        super.onDestroy()
    }

    override fun onInterrupt() {
        // No-op: we do not observe events.
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // No-op: this service exists only to inject gestures.
    }

    /**
     * Inject a synthetic pointer move/down at the given screen coordinates.
     * @param screenX target X in display pixels
     * @param screenY target Y in display pixels
     * @param isFirst true on the first frame of a new gesture (forces a fresh down)
     * @return true if the gesture was accepted by the framework
     */
    fun dispatchAim(screenX: Float, screenY: Float, isFirst: Boolean): Boolean {
        val svc = instance ?: return false
        return try {
            val startX: Float
            val startY: Float
            if (isFirst || !pointerDown) {
                startX = screenX
                startY = screenY
                pointerDown = true
            } else {
                startX = lastX
                startY = lastY
            }
            lastX = screenX
            lastY = screenY

            val path = Path()
            path.moveTo(startX, startY)
            path.lineTo(screenX, screenY)

            val stroke = GestureDescription.StrokeDescription(path, 0, GESTURE_HOLD_MS)
            val gesture = GestureDescription.Builder().addStroke(stroke).build()
            // dispatchGesture runs the binder call on the calling thread; the
            // handler is only used for the (null) callback.
            svc.dispatchGesture(gesture, null, dispatchHandler)
        } catch (t: Throwable) {
            Log.e(TAG, "dispatchAim failed: ${t.message}", t)
            pointerDown = false
            false
        }
    }

    /**
     * Lift the synthetic pointer.
     */
    fun releaseAim(): Boolean {
        val svc = instance ?: run {
            pointerDown = false
            return false
        }
        if (!pointerDown) return true
        return try {
            val path = Path()
            path.moveTo(lastX, lastY)
            path.lineTo(lastX, lastY)
            val stroke = GestureDescription.StrokeDescription(path, 0, RELEASE_HOLD_MS)
            val gesture = GestureDescription.Builder().addStroke(stroke).build()
            pointerDown = false
            svc.dispatchGesture(gesture, null, dispatchHandler)
        } catch (t: Throwable) {
            Log.e(TAG, "releaseAim failed: ${t.message}", t)
            pointerDown = false
            false
        }
    }
}
