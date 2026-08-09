package com.aimbuddy

import android.content.Context
import android.graphics.PixelFormat
import android.opengl.GLSurfaceView
import android.util.AttributeSet
import android.util.Log
import android.view.MotionEvent
import android.view.Surface
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10

/**
 * ImGuiGLSurface - GLSurfaceView for ImGui menu overlay
 *
 * Provides a proper OpenGL context for ImGui rendering separate from
 * the main ESP overlay.
 */
class ImGuiGLSurface @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : GLSurfaceView(context, attrs), GLSurfaceView.Renderer {

    companion object {
        private const val TAG = "ImGuiGLSurface"

        init {
            System.loadLibrary("esp_native")
        }

        // Native methods (GLSurfaceView renderer)
        @JvmStatic
        external fun nativeInit(assetManager: android.content.res.AssetManager, surface: Surface)

        @JvmStatic
        external fun nativeSurfaceChanged(width: Int, height: Int)

        @JvmStatic
        external fun nativeTick()

        @JvmStatic
        external fun nativeShutdown()

        @JvmStatic
        external fun nativeMotionEvent(action: Int, x: Float, y: Float): Boolean

        @JvmStatic
        external fun nativeWantsCapture(): Boolean

        @JvmStatic
        external fun nativeSetMenuVisible(visible: Boolean)

        @JvmStatic
        external fun nativeIsMenuVisible(): Boolean

        /// Returns the epoch-millis timestamp of the most recent nativeTick()
        /// call, or 0 if the render thread has never ticked. Used by the UI
        /// thread poller to detect a stalled render thread.
        @JvmStatic
        external fun nativeGetLastTickMillis(): Long
        
        @JvmStatic
        external fun nativeSetIconPosition(x: Float, y: Float)
        
        @JvmStatic
        external fun nativeSetRootAvailable(available: Boolean)

        @JvmStatic
        external fun nativeSetShizukuAvailable(available: Boolean)

        @JvmStatic
        external fun nativeSetAccessibilityAvailable(available: Boolean)

        @JvmStatic
        external fun nativeSetCrashLogPath(path: String)
    }

    private var screenWidth = 0
    private var screenHeight = 0

    init {
        // Setup OpenGL ES 3.0 context
        setEGLContextClientVersion(3)
        setEGLConfigChooser(8, 8, 8, 8, 16, 0)
        holder.setFormat(PixelFormat.TRANSLUCENT)
        setZOrderOnTop(true) // Draw on top
        setRenderer(this)
        renderMode = RENDERMODE_CONTINUOUSLY

        Log.i(TAG, "ImGuiGLSurface created")
    }

    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        Log.i(TAG, "Surface created")
        nativeInit(context.assets, holder.surface)
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
        Log.i(TAG, "Surface changed: ${width}x${height}")
        screenWidth = width
        screenHeight = height
        nativeSurfaceChanged(width, height)
    }

    override fun onDrawFrame(gl: GL10?) {
        nativeTick()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        // Defense-in-depth: when the menu is NOT visible, never consume any
        // touch event. Returning false here tells the View hierarchy "I did
        // not handle this", which  —  combined with FLAG_NOT_TOUCHABLE on the
        // window  —  ensures touches reach the app underneath.
        //
        // The primary touch pass-through mechanism is FLAG_NOT_TOUCHABLE on
        // the overlay window (managed by the touch poller in MainActivity).
        // This override is a secondary guard: if the window ever briefly
        // becomes touchable (e.g. during the 50ms poll cycle, or after a
        // failed updateViewLayout), we still avoid swallowing touches.
        if (!nativeIsMenuVisible()) {
            return false
        }

        val action = when (event.actionMasked) {
            MotionEvent.ACTION_DOWN,
            MotionEvent.ACTION_POINTER_DOWN -> 0
            MotionEvent.ACTION_UP,
            MotionEvent.ACTION_POINTER_UP,
            MotionEvent.ACTION_CANCEL -> 1
            MotionEvent.ACTION_MOVE -> 2
            else -> return false
        }

        // Only consume the touch when the native menu is open. When the menu
        // is closed we return false so the event falls through to the game
        // underneath (works together with the overlay window's FLAG_NOT_TOUCHABLE).
        return nativeMotionEvent(action, event.x, event.y)
    }

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        Log.i(TAG, "Surface detached, shutting down")
        nativeShutdown()
    }
}
