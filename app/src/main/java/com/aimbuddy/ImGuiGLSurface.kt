package com.aimbuddy

import android.content.Context
import android.graphics.SurfaceTexture
import android.opengl.EGL14
import android.opengl.EGLConfig
import android.opengl.EGLContext
import android.opengl.EGLDisplay
import android.opengl.EGLSurface
import android.opengl.GLES30
import android.os.Process
import android.util.AttributeSet
import android.util.Log
import android.view.MotionEvent
import android.view.Surface
import android.view.TextureView

/**
 * ImGuiGLSurface — TextureView-based ImGui overlay renderer.
 *
 * WHY TEXTUREVIEW, NOT GLSURFACEVIEW:
 * GLSurfaceView extends SurfaceView, which creates a separate compositor
 * surface via setZOrderOnTop(true). That separate surface does NOT inherit
 * FLAG_NOT_TOUCHABLE from the parent window on all Android versions and
 * devices, causing the overlay to silently intercept ALL screen touches —
 * the root cause of the "screen frozen after starting the service" bug.
 *
 * TextureView renders into a SurfaceTexture that lives inside the normal
 * View hierarchy. FLAG_NOT_TOUCHABLE on the parent window works correctly
 * and reliably with TextureView because there is no separate compositor
 * surface that can intercept touches.
 *
 * The EGL context and render thread are managed manually here, replicating
 * what GLSurfaceView did internally.
 */
class ImGuiGLSurface @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null
) : TextureView(context, attrs) {

    companion object {
        private const val TAG = "ImGuiGLSurface"
        private const val EGL_RECORDABLE_ANDROID = 0x3142

        init {
            System.loadLibrary("esp_native")
        }

        // Native methods — unchanged, same JNI signatures.
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

    // ---- EGL state (only touched on the render thread) ----
    private var eglDisplay: EGLDisplay = EGL14.EGL_NO_DISPLAY
    private var eglContext: EGLContext = EGL14.EGL_NO_CONTEXT
    private var eglSurface: EGLSurface = EGL14.EGL_NO_SURFACE
    private var eglConfig: EGLConfig? = null
    private var initSurface: Surface? = null  // kept alive for ANativeWindow

    // ---- Render thread ----
    private var renderThread: Thread? = null
    @Volatile private var rendering = false

    // Pending surface-size change (set on UI thread, consumed on render thread)
    @Volatile private var pendingWidth = 0
    @Volatile private var pendingHeight = 0

    // Guard to prevent double-init / double-shutdown
    @Volatile private var glReady = false

    init {
        // TextureView must be non-opaque for the game underneath to show through.
        isOpaque = false

        surfaceTextureListener = object : SurfaceTextureListener {
            override fun onSurfaceTextureAvailable(
                surface: SurfaceTexture, width: Int, height: Int
            ) {
                Log.i(TAG, "SurfaceTexture available: ${width}x${height}")
                startRenderThread(surface, width, height)
            }

            override fun onSurfaceTextureSizeChanged(
                surface: SurfaceTexture, width: Int, height: Int
            ) {
                Log.i(TAG, "SurfaceTexture size changed: ${width}x${height}")
                pendingWidth = width
                pendingHeight = height
            }

            override fun onSurfaceTextureDestroyed(surface: SurfaceTexture): Boolean {
                Log.i(TAG, "SurfaceTexture destroyed")
                stopRenderThread()
                return true
            }

            override fun onSurfaceTextureUpdated(surface: SurfaceTexture) {
                // Called when a new frame is composited — not needed.
            }
        }

        Log.i(TAG, "ImGuiGLSurface (TextureView) created")
    }

    // ------------------------------------------------------------------
    // Render thread lifecycle
    // ------------------------------------------------------------------

    /**
     * Start (or restart) the render thread. All EGL and native init happens
     * on the render thread so the EGL context is bound to the correct thread.
     */
    private fun startRenderThread(surfaceTexture: SurfaceTexture, width: Int, height: Int) {
        stopRenderThread()  // clean up any previous instance
        rendering = true
        renderThread = Thread({
            Process.setThreadPriority(Process.THREAD_PRIORITY_DISPLAY)
            try {
                initEGL(surfaceTexture)
                initSurface = Surface(surfaceTexture)
                nativeInit(context.assets, initSurface!!)
                nativeSurfaceChanged(width, height)
                glReady = true

                Log.i(TAG, "Render loop starting")
                while (rendering) {
                    // Consume pending size change
                    val pw = pendingWidth
                    val ph = pendingHeight
                    if (pw > 0 && ph > 0) {
                        pendingWidth = 0
                        pendingHeight = 0
                        nativeSurfaceChanged(pw, ph)
                    }

                    // Safety clear: nativeTick() also clears internally, but if
                    // it returns early (e.g. ImGui not yet initialised) this
                    // ensures the surface stays transparent so the app below
                    // shows through.
                    GLES30.glClearColor(0f, 0f, 0f, 0f)
                    GLES30.glClear(GLES30.GL_COLOR_BUFFER_BIT)

                    // Render ImGui frame (ESP + menu). nativeTick() includes
                    // its own FPS limiting (60 FPS cap when menu is hidden,
                    // uncapped/vsync-limited when menu is open).
                    nativeTick()

                    // Display the frame
                    EGL14.eglSwapBuffers(eglDisplay, eglSurface)
                }
            } catch (t: Throwable) {
                Log.e(TAG, "Render thread fatal error", t)
            } finally {
                Log.i(TAG, "Render thread exiting, cleaning up")
                try {
                    if (glReady) {
                        // Ensure EGL context is current for native shutdown
                        if (eglDisplay != EGL14.EGL_NO_DISPLAY && eglContext != EGL14.EGL_NO_CONTEXT) {
                            EGL14.eglMakeCurrent(
                                eglDisplay, eglSurface, eglSurface, eglContext
                            )
                        }
                        nativeShutdown()
                    }
                } catch (t: Throwable) {
                    Log.e(TAG, "nativeShutdown error: ${t.message}")
                }
                glReady = false
                destroyEGL()
            }
        }, "ImGuiRenderThread").also { it.start() }
    }

    /**
     * Signal the render thread to stop and wait for it to finish.
     * Safe to call multiple times.
     */
    private fun stopRenderThread() {
        rendering = false
        try {
            renderThread?.join(3000)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
        renderThread = null
    }

    // ------------------------------------------------------------------
    // EGL management (render thread only)
    // ------------------------------------------------------------------

    private fun initEGL(surfaceTexture: SurfaceTexture) {
        // 1. Get default display
        eglDisplay = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
        if (eglDisplay == EGL14.EGL_NO_DISPLAY) {
            throw RuntimeException("eglGetDisplay failed")
        }

        // 2. Initialize EGL
        val version = IntArray(2)
        if (!EGL14.eglInitialize(eglDisplay, version, 0, version, 1)) {
            throw RuntimeException("eglInitialize failed")
        }

        // 3. Choose EGLConfig: RGBA8888, depth 16, no stencil, ES2/3 renderable
        val configAttribs = intArrayOf(
            EGL14.EGL_RED_SIZE, 8,
            EGL14.EGL_GREEN_SIZE, 8,
            EGL14.EGL_BLUE_SIZE, 8,
            EGL14.EGL_ALPHA_SIZE, 8,
            EGL14.EGL_DEPTH_SIZE, 16,
            EGL14.EGL_STENCIL_SIZE, 0,
            EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
            EGL_RECORDABLE_ANDROID, 1,
            EGL14.EGL_NONE
        )
        val configs = arrayOfNulls<EGLConfig>(1)
        val numConfigs = IntArray(1)
        if (!EGL14.eglChooseConfig(
                eglDisplay, configAttribs, 0,
                configs, 0, 1, numConfigs, 0
            ) || numConfigs[0] == 0
        ) {
            throw RuntimeException("eglChooseConfig failed")
        }
        eglConfig = configs[0]

        // 4. Create EGLContext (OpenGL ES 3.0)
        val contextAttribs = intArrayOf(
            EGL14.EGL_CONTEXT_CLIENT_VERSION, 3,
            EGL14.EGL_NONE
        )
        eglContext = EGL14.eglCreateContext(
            eglDisplay, eglConfig!!, EGL14.EGL_NO_CONTEXT, contextAttribs, 0
        )
        if (eglContext == EGL14.EGL_NO_CONTEXT) {
            throw RuntimeException("eglCreateContext failed: 0x${Integer.toHexString(EGL14.eglGetError())}")
        }

        // 5. Create EGL window surface from the SurfaceTexture
        val surfaceAttribs = intArrayOf(EGL14.EGL_NONE)
        eglSurface = EGL14.eglCreateWindowSurface(
            eglDisplay, eglConfig!!, surfaceTexture, surfaceAttribs, 0
        )
        if (eglSurface == EGL14.EGL_NO_SURFACE) {
            throw RuntimeException("eglCreateWindowSurface failed: 0x${Integer.toHexString(EGL14.eglGetError())}")
        }

        // 6. Make the context current
        if (!EGL14.eglMakeCurrent(eglDisplay, eglSurface, eglSurface, eglContext)) {
            throw RuntimeException("eglMakeCurrent failed: 0x${Integer.toHexString(EGL14.eglGetError())}")
        }

        Log.i(TAG, "EGL initialized successfully")
    }

    private fun destroyEGL() {
        if (eglDisplay != EGL14.EGL_NO_DISPLAY) {
            EGL14.eglMakeCurrent(
                eglDisplay,
                EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE,
                EGL14.EGL_NO_CONTEXT
            )
            if (eglSurface != EGL14.EGL_NO_SURFACE) {
                EGL14.eglDestroySurface(eglDisplay, eglSurface)
            }
            if (eglContext != EGL14.EGL_NO_CONTEXT) {
                EGL14.eglDestroyContext(eglDisplay, eglContext)
            }
            EGL14.eglTerminate(eglDisplay)
        }
        eglDisplay = EGL14.EGL_NO_DISPLAY
        eglContext = EGL14.EGL_NO_CONTEXT
        eglSurface = EGL14.EGL_NO_SURFACE
        eglConfig = null

        initSurface?.release()
        initSurface = null
    }

    // ------------------------------------------------------------------
    // Touch handling
    // ------------------------------------------------------------------
    //
    // When the menu is CLOSED: the parent window has FLAG_NOT_TOUCHABLE, so
    // onTouchEvent is never called — touches pass straight through to the app.
    //
    // When the menu is OPEN: MainActivity removes FLAG_NOT_TOUCHABLE via
    // applyOverlayTouchable(true). The floating gear icon (added after this
    // overlay, so on top) remains tappable via FLAG_NOT_TOUCH_MODAL to toggle
    // the menu closed. All other touches arrive here and are forwarded to
    // ImGui for menu interaction (button clicks, slider drags, scrolling).

    override fun onTouchEvent(event: MotionEvent): Boolean {
        // When the menu is NOT visible, never consume any touch event.
        // The poller in MainActivity should have already set FLAG_NOT_TOUCHABLE
        // on the window, so this method should not even be called. But if it
        // is (e.g. a race between the poller and the touch), returning false
        // avoids swallowing the touch.
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

        return nativeMotionEvent(action, event.x, event.y)
    }

    // ------------------------------------------------------------------
    // Cleanup
    // ------------------------------------------------------------------

    override fun onDetachedFromWindow() {
        super.onDetachedFromWindow()
        Log.i(TAG, "Detached from window, stopping render thread")
        stopRenderThread()
    }

    /**
     * No-op replacement for SurfaceView.setSecure().
     *
     * With GLSurfaceView (SurfaceView), setSecure() was needed because the
     * SurfaceView's surface is separate from the window and doesn't inherit
     * FLAG_SECURE. With TextureView, all content is part of the window, so
     * FLAG_SECURE on the window (set in applyStreamerModeFlag) fully
     * protects the overlay content from MediaProjection capture.
     *
     * This method exists solely so MainActivity.kt can call it without
     * compile errors after the switch from GLSurfaceView to TextureView.
     */
    fun setSecure(secure: Boolean) {
        // Intentionally empty — FLAG_SECURE on the window handles this.
        Log.d(TAG, "setSecure($secure) — no-op for TextureView (FLAG_SECURE on window is sufficient)")
    }
}
