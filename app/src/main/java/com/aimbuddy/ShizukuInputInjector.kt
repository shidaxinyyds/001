package com.aimbuddy

import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import android.view.InputDevice
import android.view.InputEvent
import android.view.MotionEvent
import rikka.shizuku.Shizuku
import rikka.shizuku.ShizukuBinderWrapper
import rikka.shizuku.SystemServiceHelper

/**
 * Non-root touch injector using hidden InputManager APIs from a Shizuku-authorized process.
 *
 * Design notes for simultaneous-touch correctness:
 *  - The synthetic event uses a high pointer id (AIM_POINTER_ID = 19) so it
 *    cannot collide with pointer ids the OS hands out to real fingers (which
 *    typically start at 0 and grow as fingers are added).
 *  - We pass deviceId = -1 ("virtual") so InputDispatcher treats this as a
 *    distinct input stream from the user's physical touchscreen.
 *  - source is SOURCE_TOUCHSCREEN (the game expects a touch, not a mouse).
 *  - Pressure stays at 1.0 throughout so the game sees a sustained press;
 *    we never send ACTION_CANCEL because that would tell the game the
 *    gesture was invalidated by the system.
 */
class ShizukuInputInjector {

    companion object {
        private const val TAG = "ShizukuInputInjector"
        private const val INJECT_MODE_ASYNC = 0
        // Pointer id high enough to never collide with OS-allocated ids
        // for real fingers (Android usually allocates 0..MAX_POINTERS-1).
        private const val AIM_POINTER_ID = 19
        // Virtual deviceId so InputDispatcher tracks this as a separate
        // stream from the physical touchscreen.
        private const val AIM_DEVICE_ID = -1
    }

    private val inputManagerProxy: Any? by lazy {
        buildPrivilegedInputManagerProxy()
    }

    private val injectMethod by lazy {
        resolveInjectMethod(inputManagerProxy)
    }

    private val pointerProperties = arrayOf(
        MotionEvent.PointerProperties().apply {
            id = AIM_POINTER_ID
            toolType = MotionEvent.TOOL_TYPE_FINGER
        }
    )

    private val pointerCoords = arrayOf(
        MotionEvent.PointerCoords().apply {
            pressure = 1f
            size = 1f
        }
    )

    @Volatile
    private var pointerDown = false
    private var lastX = 0f
    private var lastY = 0f
    private var downTimeMs = 0L

    private fun buildPrivilegedInputManagerProxy(): Any? {
        // Bypass hidden-API reflection restrictions (Android 9+) so the
        // Class.forName("android.hardware.input.IInputManager\$Stub") below can
        // succeed on non-rooted devices. Requires the
        // 'org.lsposed.hiddenapibypass:hiddenapibypass' dependency. Done lazily
        // here (instead of in Application.attachBaseContext) so it only runs
        // when the Shizuku backend is actually first used.
        try {
            val bypassClass = Class.forName("org.lsposed.hiddenapibypass.HiddenApiBypass")
            val exemptMethod = bypassClass.getMethod("addHiddenApiExemptions", String::class.java)
            exemptMethod.invoke(null, "")
        } catch (bypassEx: Throwable) {
            Log.w(TAG, "HiddenApiBypass unavailable (${bypassEx.message}); hidden-API reflection may fail on this device")
        }
        return try {
            if (!Shizuku.pingBinder() || Shizuku.checkSelfPermission() != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                Log.w(TAG, "Shizuku not ready for privileged input manager")
                return null
            }

            val baseBinder = SystemServiceHelper.getSystemService("input")
            if (baseBinder == null) {
                Log.e(TAG, "Failed to get input service binder")
                return null
            }

            val wrappedBinder: IBinder = ShizukuBinderWrapper(baseBinder)
            val stubClass = Class.forName("android.hardware.input.IInputManager\$Stub")
            val asInterface = stubClass.getDeclaredMethod("asInterface", IBinder::class.java)
            asInterface.isAccessible = true
            asInterface.invoke(null, wrappedBinder)
        } catch (t: Throwable) {
            Log.e(TAG, "Failed to build privileged input manager proxy: ${t.message}", t)
            null
        }
    }

    private fun resolveInjectMethod(proxy: Any?): java.lang.reflect.Method? {
        if (proxy == null) {
            return null
        }
        return try {
            val direct = proxy.javaClass.methods.firstOrNull {
                it.name == "injectInputEvent" &&
                    it.parameterTypes.size == 2 &&
                    InputEvent::class.java.isAssignableFrom(it.parameterTypes[0])
            }
            direct?.also { it.isAccessible = true }
        } catch (t: Throwable) {
            Log.e(TAG, "Failed to resolve inject method: ${t.message}", t)
            null
        }
    }

    private fun invokeInject(event: MotionEvent): Boolean {
        val proxy = inputManagerProxy ?: return false
        val method = injectMethod ?: return false
        return try {
            val result = method.invoke(proxy, event, INJECT_MODE_ASYNC)
            when (result) {
                is Boolean -> result
                else -> true
            }
        } catch (t: Throwable) {
            Log.e(TAG, "invokeInject failed: ${t.message}", t)
            false
        }
    }

    private fun buildEvent(action: Int, x: Float, y: Float, eventTime: Long): MotionEvent {
        pointerCoords[0].x = x
        pointerCoords[0].y = y
        pointerCoords[0].pressure = 1f
        pointerCoords[0].size = 1f

        // 15-arg MotionEvent.obtain takes deviceId at position 11.
        // (downTime, eventTime, action, pointerCount, properties, coords,
        //  metaState, buttonState, xPrecision, yPrecision, deviceId,
        //  edgeFlags, source, flags)
        val event = MotionEvent.obtain(
            downTimeMs,
            eventTime,
            action,
            1,
            pointerProperties,
            pointerCoords,
            0,
            0,
            1f,
            1f,
            AIM_DEVICE_ID,
            0,
            InputDevice.SOURCE_TOUCHSCREEN,
            0
        )
        event.source = InputDevice.SOURCE_TOUCHSCREEN
        return event
    }

    @Synchronized
    fun injectAimMove(screenX: Float, screenY: Float, isFirst: Boolean): Boolean {
        return try {
            val now = SystemClock.uptimeMillis()

            // Force a fresh ACTION_DOWN whenever we are starting a new aim gesture.
            val needDown = isFirst || !pointerDown
            if (needDown) {
                downTimeMs = now
                val downEvent = buildEvent(MotionEvent.ACTION_DOWN, screenX, screenY, now)
                val ok = invokeInject(downEvent)
                downEvent.recycle()
                if (!ok) {
                    pointerDown = false
                    return false
                }
                pointerDown = true
                lastX = screenX
                lastY = screenY
                return true
            }

            val moveEvent = buildEvent(MotionEvent.ACTION_MOVE, screenX, screenY, now)
            val ok = invokeInject(moveEvent)
            moveEvent.recycle()

            if (!ok) {
                pointerDown = false
                return false
            }
            lastX = screenX
            lastY = screenY
            true
        } catch (t: Throwable) {
            Log.e(TAG, "injectAimMove failed: ${t.message}", t)
            pointerDown = false
            false
        }
    }

    @Synchronized
    fun releaseAim(): Boolean {
        if (!pointerDown) {
            return true
        }

        return try {
            val now = SystemClock.uptimeMillis()
            val upEvent = buildEvent(MotionEvent.ACTION_UP, lastX, lastY, now)
            val ok = invokeInject(upEvent)
            upEvent.recycle()

            pointerDown = false
            ok
        } catch (t: Throwable) {
            Log.e(TAG, "releaseAim failed: ${t.message}", t)
            pointerDown = false
            false
        }
    }
}
