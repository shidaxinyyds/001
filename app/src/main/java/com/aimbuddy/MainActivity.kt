package com.aimbuddy

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.content.ComponentName
import android.content.BroadcastReceiver
import android.content.IntentFilter
import androidx.core.content.ContextCompat
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjection.Callback
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.provider.Settings
import android.provider.OpenableColumns
import android.util.DisplayMetrics
import android.text.TextUtils
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.Surface
import android.view.View
import android.view.WindowManager
import android.widget.ImageView
import android.widget.Toast
import android.graphics.drawable.Drawable
import android.graphics.drawable.PictureDrawable
import android.content.ActivityNotFoundException
import androidx.activity.result.contract.ActivityResultContracts
import java.io.File
import java.io.FileOutputStream
import com.caverock.androidsvg.SVG
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.clickable
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.lang.ref.WeakReference
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread
import rikka.shizuku.Shizuku
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.OutlinedButton
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.draw.shadow

// Minimal dark palette tuned for the launcher and the small set of
// state colors used on the status pill / backend chips. Kept here as
// top-level so the composables don't recompute them every recomposition.
private val StatusGreen = Color(0xFF34D399)
private val StatusAmber = Color(0xFFFBBF24)
private val StatusRed   = Color(0xFFF87171)
private val StatusGrey  = Color(0xFF6B7280)

private val AimBuddyColors = darkColorScheme(
    primary          = Color(0xFF8AB4F8),
    onPrimary        = Color(0xFF0B1220),
    secondary        = Color(0xFF93C5FD),
    background       = Color(0xFF0B0F17),
    surface          = Color(0xFF111722),
    surfaceVariant   = Color(0xFF1B2230),
    onBackground     = Color(0xFFE5E7EB),
    onSurface        = Color(0xFFE5E7EB),
    onSurfaceVariant = Color(0xFF9CA3AF),
)

/**
 * MainActivity - ESP overlay control interface
 *
 * Handles permissions, MediaProjection setup, and overlay lifecycle.
 * Provides START/STOP buttons for ESP functionality.
 */
class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "ESP_MainActivity"
        private const val REQUEST_MEDIA_PROJECTION = 1001
        private const val REQUEST_OVERLAY_PERMISSION = 1002
        private const val REQUEST_SHIZUKU_PERMISSION = 2001
        private const val REQUEST_ACCESSIBILITY_SETTINGS = 3001

        // Capture resolution (720p for optimal SD888 performance)
        private const val CAPTURE_WIDTH = 1280
        private const val CAPTURE_HEIGHT = 720
        private const val OSS_GITHUB_URL = "https://github.com/1337XCode/AimBuddy"
        private const val PREFS_NAME = "aimbuddy_prefs"
        private const val PREF_MODEL_PARAM_PATH = "model_param_path"
        private const val PREF_MODEL_BIN_PATH = "model_bin_path"
        private const val ASSET_MODEL_PARAM = "models/yolo26n-opt.param"
        private const val ASSET_MODEL_BIN = "models/yolo26n-opt.bin"
        private const val STORE_OWNER = "1337Xcode"
        private const val STORE_REPO = "AimBuddy"
        private const val STORE_BRANCH = "master"

        private var activityRef: WeakReference<MainActivity>? = null

        @JvmStatic
        fun nativeInjectShizukuAimMove(screenX: Float, screenY: Float, isFirst: Boolean): Boolean {
            val activity = activityRef?.get() ?: return false
            return activity.injectShizukuAimMove(screenX, screenY, isFirst)
        }

        @JvmStatic
        fun nativeInjectShizukuAimUp(): Boolean {
            val activity = activityRef?.get() ?: return false
            return activity.injectShizukuAimUp()
        }

        // Accessibility injection goes straight to the service, NOT through the
        // Activity. The user will be inside a game when aiming, so the Activity
        // may be stopped or collected; the service is what stays alive.
        @JvmStatic
        fun nativeInjectAccessibilityAimMove(screenX: Float, screenY: Float, isFirst: Boolean): Boolean {
            return AimAccessibilityService.aimMove(screenX, screenY, isFirst)
        }

        @JvmStatic
        fun nativeInjectAccessibilityAimUp(): Boolean {
            return AimAccessibilityService.aimUp()
        }

        /**
         * Toggle FLAG_SECURE on overlay windows. Called from native code via JNI
         * when the user enables/disables Streamer Mode in the in-game menu.
         */
        @JvmStatic
        fun nativeApplyStreamerMode(enabled: Boolean) {
            val activity = activityRef?.get() ?: return
            activity.runOnUiThread { activity.applyStreamerModeFlag(enabled) }
        }

        init {
            System.loadLibrary("esp_native")
        }
    }

    // UI state
    private var isRunningState by mutableStateOf(false)
    private var statusTextState by mutableStateOf("Status: Model Loading")
    private var activeModelTextState by mutableStateOf("模型：自动")
    private var showStoreState by mutableStateOf(false)
    private var isFetchingStoreState by mutableStateOf(false)
    private var storeModelsState by mutableStateOf<List<StoreModelDefinition>>(emptyList())
    private var downloadingModelIdState by mutableStateOf<String?>(null)
    private var installedModelsState by mutableStateOf<List<InstalledModel>>(emptyList())

    // Overlay components
    private var imguiOverlay: ImGuiGLSurface? = null
    private var windowManager: WindowManager? = null
    private var isOverlayVisible = false
    private val touchHandler = Handler(Looper.getMainLooper())
    private var touchPolling = false
    // Counter for periodic forced re-application of FLAG_NOT_TOUCHABLE. Every
    // ~2 s (40 cycles at 50 ms) we bypass the "flags look fine" optimisation
    // and unconditionally push FLAG_NOT_TOUCHABLE to the WindowManager. This
    // catches desyncs where the in-memory params.flags still show the flag but
    // the actual window state lost it — e.g. after SurfaceView.setSecure()
    // triggers an internal relayout, or an OEM ROM mangled the flags.
    private var touchPollCycle = 0
    // Last render-thread tick timestamp (epoch ms) reported by nativeTick via
    // nativeGetLastTickMillis(). Used by the poller to detect a stalled render
    // thread: if no frame has been produced for > 1.5 s while the menu claims
    // to be open, we force-close the menu to avoid a permanent touch deadlock
    // (menuInputView would otherwise stay alive swallowing every touch).
    private var lastRenderTickMs = 0L
    private val isStopping = AtomicBoolean(false)
    private val isStarting = AtomicBoolean(false)
    private val rootCheckInProgress = AtomicBoolean(false)
    private val rootAvailable = AtomicBoolean(false)
    private val shizukuAvailable = AtomicBoolean(false)
    private val accessibilityAvailable = AtomicBoolean(false)
    private var pendingStartAfterRoot = false
    private var pendingStartAfterShizuku = false
    private var pendingStartAfterAccessibility = false
    private var pendingShizukuPermissionRequest = false
    private var shizukuWaitStartedAt = 0L
    private var shizukuInjector: ShizukuInputInjector? = null
    private val shizukuBinderReceivedListener = Shizuku.OnBinderReceivedListener {
        runOnUiThread {
            refreshShizukuState()
            if (pendingStartAfterShizuku && !isStarting.get()) {
                requestShizukuThenMediaProjection()
            }
        }
    }
    private val shizukuBinderDeadListener = Shizuku.OnBinderDeadListener {
        runOnUiThread {
            refreshShizukuState(forceUnavailable = true)
            if (nativeGetTouchBackend() == 1) {
                showAppToast("Shizuku 已断开，请重新连接后再次点击开始。", true)
            }
        }
    }
    private val shizukuPermissionListener = Shizuku.OnRequestPermissionResultListener { requestCode, grantResult ->
        if (requestCode != REQUEST_SHIZUKU_PERMISSION) {
            return@OnRequestPermissionResultListener
        }
        val granted = grantResult == PackageManager.PERMISSION_GRANTED
        pendingShizukuPermissionRequest = false
        refreshShizukuState()
        if (granted) {
            showAppToast("Shizuku 权限已授予。", false)
        } else {
            showAppToast("Shizuku 权限被拒绝，瞄准辅助仅保留可视化显示。", true)
        }
        if (pendingStartAfterShizuku) {
            pendingStartAfterShizuku = false
            requestMediaProjectionPermission()
        }
    }
    private var pendingImportParamUri: Uri? = null
    private var pendingImportName: String = "local-model"

    private lateinit var modelCatalog: ModelCatalog
    private val storeRepository = ModelStoreRepository(
        owner = STORE_OWNER,
        repo = STORE_REPO,
        branch = STORE_BRANCH
    )

    // Streamer mode (FLAG_SECURE on overlay windows).
    // Defaults to true to match UnifiedSettings::streamerMode so the overlay is
    // already excluded from MediaProjection on its very first frame - otherwise
    // the ESP boxes we draw get captured and re-detected as phantom enemies.
    // Native pushes the authoritative value on the first rendered frame.
    @Volatile
    private var streamerModeEnabled: Boolean = true

    // Floating menu icon overlay
    private var floatingIconView: ImageView? = null
    private var floatingIconParams: WindowManager.LayoutParams? = null
    private var iconDownRawX = 0f
    private var iconDownRawY = 0f
    private var iconStartX = 0
    private var iconStartY = 0
    private var iconMoved = false
    private var menuVisible = false

    // Dedicated transparent input window for the ImGui menu. It is added ONLY
    // while the menu is open and removed the moment it closes. The full-screen
    // imguiOverlay stays FLAG_NOT_TOUCHABLE forever, so the screen can never be
    // blocked by a stuck/pass-through flag on the persistent overlay.
    private var menuInputView: View? = null
    private var menuInputParams: WindowManager.LayoutParams? = null

    // Escape hatch: the persistent notification's "恢复触摸" action broadcasts
    // this intent so the screen can always be freed even if the menu gets
    // stuck open (the only close affordance was the floating gear, which can
    // become unreachable while the overlay is touchable).
    private var restoreTouchReceiver: BroadcastReceiver? = null

    // MediaProjection components
    private var mediaProjectionManager: MediaProjectionManager? = null
    private var mediaProjection: MediaProjection? = null
    private var projectionCallbackRegistered = false
    private val mediaProjectionCallback = object : Callback() {
        override fun onStop() {
            Log.w(TAG, "MediaProjection stopped by system/user")
            runOnUiThread {
                if (isRunningState || isStarting.get()) {
                    showAppToast("屏幕采集已结束，ESP 已停止。", true)
                    stopESP()
                }
            }
        }
    }
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private val imageThread = HandlerThread("esp-image-reader").also { it.start() }
    private val imageHandler = Handler(imageThread.looper)

    // Display metrics
    private var screenWidth = 1080
    private var screenHeight = 2400
    private var screenDensity = 1

    // Rendering
    private val renderHandler = Handler(Looper.getMainLooper())

    // Native methods
    private external fun nativeInit(assetManager: android.content.res.AssetManager,
                                    screenWidth: Int, screenHeight: Int): Boolean
    private external fun nativeStart()
    private external fun nativeStop()
    private external fun nativeShutdown()
    private external fun nativeIsRunning(): Boolean
    private external fun nativeInitAimbot(): Boolean
    private external fun nativeSetModelPaths(paramPath: String?, binPath: String?)
    private external fun nativeSetTouchBackend(backend: Int)
    private external fun nativeGetTouchBackend(): Int
    private external fun nativeSetShizukuBridgeAvailable(available: Boolean)
    private external fun nativeSetAccessibilityBridgeAvailable(available: Boolean)

    private val importParamLauncher = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) {
            return@registerForActivityResult
        }
        pendingImportParamUri = uri
        pendingImportName = getDisplayName(uri).substringBeforeLast('.', "local-model")

        showAppToast(".param 已导入，请选择对应的 .bin 文件。", false)
        importBinLauncher.launch(arrayOf("application/octet-stream", "*/*"))
    }

    private val importBinLauncher = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) {
            return@registerForActivityResult
        }
        val paramUri = pendingImportParamUri
        if (paramUri == null) {
            showAppToast("请先选择 .param，再选择 .bin。", true)
            return@registerForActivityResult
        }

        val modelId = modelCatalog.sanitizeModelId("local-${pendingImportName}-${System.currentTimeMillis()}")
        val installDir = File(filesDir, "models/$modelId")
        val paramFile = File(installDir, "$modelId.param")
        val binFile = File(installDir, "$modelId.bin")

        val paramOk = copyUriToFile(paramUri, paramFile)
        val binOk = copyUriToFile(uri, binFile)
        if (!paramOk || !binOk) {
            showAppToast("导入所选模型文件失败", true)
            return@registerForActivityResult
        }

        val installed = InstalledModel(
            id = modelId,
            title = pendingImportName,
            description = "从本地存储导入",
            source = ModelSource.LOCAL,
            paramPath = paramFile.absolutePath,
            binPath = binFile.absolutePath,
            totalSizeBytes = paramFile.length() + binFile.length()
        )
        modelCatalog.addOrUpdateModel(installed, makeActive = true)
        applyActiveModelSelection()
        showAppToast("已从存储空间导入模型", false)
        reinitializeNativeIfIdle()
        pendingImportParamUri = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        activityRef = WeakReference(this)

        // Install crash reporter as early as possible so even onCreate crashes are
        // captured and can be shown to the user on the next launch.
        CrashReporter.install(this)
        
        // Force landscape orientation
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        
        setContent {
            MaterialTheme(colorScheme = AimBuddyColors) {
                Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    LauncherScreen(
                        isRunning = isRunningState,
                        statusText = statusTextState,
                        onStart = { onStartClicked() },
                        onStop = { onStopClicked() },
                        onOpenGithub = { openGithubUrl() },
                    )
                }
            }
        }
        
        // Show the running version immediately so the user can verify whether
        // the APK they just installed actually matched the release label.
        showAppToast("AimBuddy ${BuildConfig.AIMBUDDY_VERSION}", false)
        
        // Enable immersive fullscreen mode (hide nav bar & status bar)
        enableImmersiveMode()

        // If the previous run crashed, surface the captured report so the user
        // (and the developer) can see exactly what failed instead of guessing.
        reportPendingCrashIfAny()

        Log.i(TAG, "onCreate")

        // Get display metrics (resources.displayMetrics avoids the deprecated
        // WindowManager.getDefaultDisplay()/Display.getRealMetrics() API)
        val displayMetrics = resources.displayMetrics
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        
        // Force Landscape dimensions for game overlay
        // If width < height, swap them
        if (displayMetrics.widthPixels < displayMetrics.heightPixels) {
            screenWidth = displayMetrics.heightPixels
            screenHeight = displayMetrics.widthPixels
        } else {
            screenWidth = displayMetrics.widthPixels
            screenHeight = displayMetrics.heightPixels
        }
        screenDensity = displayMetrics.densityDpi

        Log.i(TAG, "Screen: ${screenWidth}x${screenHeight}, density: $screenDensity")

        setStatus("Status: Model Loading")
        modelCatalog = ModelCatalog(this)

        val hasAssetParam = assetExists(ASSET_MODEL_PARAM)
        val hasAssetBin = assetExists(ASSET_MODEL_BIN)
        if (!hasAssetParam || !hasAssetBin) {
            val missing = buildList {
                if (!hasAssetParam) add(ASSET_MODEL_PARAM)
                if (!hasAssetBin) add(ASSET_MODEL_BIN)
            }.joinToString(", ")
            showAppToast("assets 中缺少模型：$missing", true)
        }

        modelCatalog.ensureDefaultAssetModel(hasAssetParam, hasAssetBin)
        applyActiveModelSelection()

        // Get MediaProjectionManager
        mediaProjectionManager = getSystemService(Context.MEDIA_PROJECTION_SERVICE)
                as MediaProjectionManager

        // Initialize native components FIRST
        if (!nativeInit(assets, screenWidth, screenHeight)) {
            Log.e(TAG, "Failed to initialize native components")
            if (!hasAssetParam || !hasAssetBin) {
                showAppToast("初始化失败，请手动导入模型或添加 assets 模型。", true)
            } else {
                showAppToast("ESP 初始化失败，请检查模型文件。", true)
            }
            setStatus("Status: Init Failed")
        } else {
            setStatus("Status: Ready")
            ImGuiGLSurface.nativeSetRootAvailable(false)
            refreshShizukuState()
            refreshAccessibilityState()
        }

        Shizuku.addRequestPermissionResultListener(shizukuPermissionListener)
        Shizuku.addBinderReceivedListenerSticky(shizukuBinderReceivedListener)
        Shizuku.addBinderDeadListener(shizukuBinderDeadListener)

        // Register the "restore touch" escape-hatch receiver so the persistent
        // notification's action button can always free the screen, even when
        // the menu is stuck open and the overlay is capturing all touches.
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                when (intent?.action) {
                    ScreenCaptureService.ACTION_RESTORE_TOUCH -> {
                        Log.i(TAG, "Restore-touch broadcast received")
                        forceRestoreTouch()
                    }
                    ScreenCaptureService.ACTION_OPEN_MENU -> {
                        Log.i(TAG, "Open-menu broadcast received")
                        openMenu()
                    }
                }
            }
        }
        restoreTouchReceiver = receiver
        val filter = IntentFilter().apply {
            addAction(ScreenCaptureService.ACTION_RESTORE_TOUCH)
            addAction(ScreenCaptureService.ACTION_OPEN_MENU)
        }
        ContextCompat.registerReceiver(
            this,
            receiver,
            filter,
            Context.RECEIVER_NOT_EXPORTED
        )
    }
    
    override fun onDestroy() {
        Log.i(TAG, "onDestroy")

        // Unregister the escape-hatch receiver to avoid leaks.
        try {
            restoreTouchReceiver?.let { unregisterReceiver(it) }
        } catch (ignored: IllegalArgumentException) {
            Log.w(TAG, "restoreTouchReceiver already unregistered")
        }
        restoreTouchReceiver = null
        stopESP()
        Shizuku.removeRequestPermissionResultListener(shizukuPermissionListener)
        Shizuku.removeBinderReceivedListener(shizukuBinderReceivedListener)
        Shizuku.removeBinderDeadListener(shizukuBinderDeadListener)
        activityRef = null
        imageThread.quitSafely()
        nativeShutdown()
        super.onDestroy()
    }
    
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            enableImmersiveMode()
        }
    }

    override fun onResume() {
        super.onResume()
        // Re-evaluate backend availability after returning from system settings
        // (e.g. the user just enabled the accessibility service).
        refreshShizukuState()
        refreshAccessibilityState()
        if (pendingStartAfterAccessibility && accessibilityAvailable.get()) {
            pendingStartAfterAccessibility = false
            requestTouchBackendThenProjection()
        }
    }
    
    @Suppress("DEPRECATION")
    private fun enableImmersiveMode() {
        // Hide navigation bar and status bar for fullscreen
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
            View.SYSTEM_UI_FLAG_FULLSCREEN or
            View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
            View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
            View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
        )
    }

    private fun reportPendingCrashIfAny() {
        val report = CrashReporter.consumePendingCrash(this) ?: return
        Log.e(TAG, "Previous run crashed:\n$report")
        val scroll = android.widget.ScrollView(this).apply {
            val tv = android.widget.TextView(this@MainActivity).apply {
                text = report
                setTextIsSelectable(true)
                textSize = 11f
                setPadding(24, 24, 24, 24)
            }
            addView(tv)
        }
        val dialog = android.app.AlertDialog.Builder(this)
            .setTitle("上次运行崩溃报告")
            .setView(scroll)
            .setPositiveButton("复制并关闭") { _, _ ->
                val cm = getSystemService(Context.CLIPBOARD_SERVICE) as? android.content.ClipboardManager
                cm?.setPrimaryClip(android.content.ClipData.newPlainText("AimBuddy crash", report))
                showAppToast("崩溃信息已复制到剪贴板", false)
            }
            .setNegativeButton("仅关闭", null)
            .create()
        dialog.show()
    }

    private fun onStartClicked() {
        Log.i(TAG, "Start button clicked")

        if (isRunningState || isStarting.get()) {
            Log.i(TAG, "Start ignored: already running or starting")
            return
        }

        if (isStopping.get()) {
            Log.i(TAG, "Start ignored: stop in progress")
            return
        }

        if (statusTextState == "Status: Init Failed") {
            showAppToast("初始化失败，请检查模型文件。", true)
            return
        }

        // Step 1: overlay permission
        if (!Settings.canDrawOverlays(this)) {
            Log.i(TAG, "Requesting overlay permission")
            AlertDialog.Builder(this)
                .setTitle("需要悬浮窗权限")
                .setMessage("AimBuddy 需要「在其他应用上层显示」权限才能绘制 ESP 叠加层。\n\n点击「打开设置」，在列表中找到 AimBuddy 并开启权限，然后返回点击开始。")
                .setPositiveButton("打开设置") { _, _ ->
                    val intent = Intent(
                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:$packageName")
                    )
                    startActivityForResult(intent, REQUEST_OVERLAY_PERMISSION)
                }
                .setNegativeButton("取消", null)
                .setCancelable(true)
                .show()
            return
        }

        requestTouchBackendThenProjection()
    }

    /**
     * Top-level start router. Resolves the best available input backend from the
     * user's preference (persisted touchBackend) and routes to the matching
     * setup path. Backend 2 (AccessibilityService) is the out-of-box default.
     */
    private fun requestTouchBackendThenProjection() {
        val preferred = nativeGetTouchBackend().coerceIn(0, 2)
        val effective = resolveEffectiveTouchBackend(preferred)
        nativeSetTouchBackend(effective)
        when (effective) {
            0 -> requestRootThenMediaProjection()
            1 -> requestShizukuThenMediaProjection()
            2 -> requestAccessibilityThenMediaProjection()
            else -> requestRootThenMediaProjection()
        }
    }

    private fun requestRootThenMediaProjection() {
        val backend = resolveEffectiveTouchBackend(nativeGetTouchBackend().coerceIn(0, 2))
        nativeSetTouchBackend(backend)

        if (backend == 1) {
            requestShizukuThenMediaProjection()
            return
        }
        if (backend == 2) {
            requestAccessibilityThenMediaProjection()
            return
        }

        // Root is optional for ESP runtime. If already granted, continue immediately.
        if (rootAvailable.get()) {
            requestMediaProjectionPermission()
            return
        }

        pendingStartAfterRoot = true
        setStatus("Status: Waiting for Root Permission")
        showAppToast("如需辅助输入，请授予 Root 权限。", false)
        beginAsyncRootCheck { hasRoot ->
            if (!pendingStartAfterRoot) return@beginAsyncRootCheck

            if (hasRoot) {
                setStatus("Status: Root Granted")
                requestMediaProjectionPermission()
            } else {
                pendingStartAfterRoot = false
                // Fall back to a non-root backend if one is available.
                if (shizukuAvailable.get()) {
                    nativeSetTouchBackend(1)
                    showAppToast("Root 不可用，已切换至 Shizuku 免 Root 输入。", false)
                    setStatus("Status: Using Shizuku Backend")
                    requestShizukuThenMediaProjection()
                } else if (accessibilityAvailable.get()) {
                    nativeSetTouchBackend(2)
                    showAppToast("Root 不可用，已切换至无障碍输入。", false)
                    setStatus("Status: Using Accessibility Backend")
                    requestAccessibilityThenMediaProjection()
                } else {
                    setStatus("Status: Ready")
                    showAppToast("Root 不可用；可在系统「无障碍」设置中开启 AimBuddy 实现免 Root 输入。", true)
                }
            }
        }
    }

    /**
     * Out-of-box (no root, no Shizuku) start path. If the accessibility service
     * is already enabled by the user, init the aimbot and continue to screen
     * capture. Otherwise prompt the user to enable it in system settings.
     */
    private fun requestAccessibilityThenMediaProjection() {
        if (!accessibilityAvailable.get()) {
            showAccessibilityEnableDialog()
            return
        }

        setStatus("Status: Using Accessibility Backend")
        nativeSetAccessibilityBridgeAvailable(true)
        if (statusTextState != "Status: Init Failed") {
            nativeInitAimbot()
        }
        requestMediaProjectionPermission()
    }

    private fun showAccessibilityEnableDialog() {
        setStatus("Status: Accessibility Not Enabled")
        AlertDialog.Builder(this)
            .setTitle("开启无障碍服务")
            .setMessage(
                "AimBuddy 的免 Root 输入需要「无障碍」服务权限。\n\n" +
                    "1) 点击「打开设置」\n" +
                    "2) 在已安装应用/已下载应用中找到 AimBuddy\n" +
                    "3) 开启「无障碍」服务开关并返回\n" +
                    "4) 再次点击「启动服务」即可开箱即用"
            )
            .setPositiveButton("打开设置") { _, _ ->
                pendingStartAfterAccessibility = true
                try {
                    startActivityForResult(
                        Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS),
                        REQUEST_ACCESSIBILITY_SETTINGS
                    )
                } catch (e: Throwable) {
                    showAppToast("无法打开无障碍设置：${e.message}", true)
                }
            }
            .setNegativeButton("取消", null)
            .setNeutralButton("仅显示 ESP") { _, _ ->
                requestMediaProjectionPermission()
            }
            .setCancelable(true)
            .show()
    }

    private fun requestShizukuThenMediaProjection() {
        if (!pendingStartAfterShizuku) {
            shizukuWaitStartedAt = System.currentTimeMillis()
        }
        pendingStartAfterShizuku = true
        setStatus("Status: Waiting for Shizuku Permission")

    refreshShizukuState()
    if (!Shizuku.pingBinder()) {
        if (isRootLikelyAvailable()) {
            pendingStartAfterShizuku = false
            nativeSetTouchBackend(0)
            showAppToast("Shizuku 不可用，已切换至 Root uinput 输入。", false)
            setStatus("Status: Using Root Backend")
            requestRootThenMediaProjection()
        } else if (accessibilityAvailable.get()) {
            pendingStartAfterShizuku = false
            nativeSetTouchBackend(2)
            showAppToast("Shizuku 不可用，已切换至无障碍输入。", false)
            setStatus("Status: Using Accessibility Backend")
            requestAccessibilityThenMediaProjection()
        } else {
            showAppToast("正在等待 Shizuku 连接……", false)
            val waitedMs = System.currentTimeMillis() - shizukuWaitStartedAt
            if (waitedMs > 4000) {
                pendingStartAfterShizuku = false
                setStatus("Status: Shizuku Not Connected")
                showShizukuConnectionHelpDialog()
            }
        }
        return
    }

        if (Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED) {
            shizukuAvailable.set(true)
            nativeSetShizukuBridgeAvailable(true)
            ImGuiGLSurface.nativeSetShizukuAvailable(true)
            if (statusTextState != "Status: Init Failed") {
                nativeInitAimbot()
            }
            pendingStartAfterShizuku = false
            requestMediaProjectionPermission()
            return
        }

        if (!pendingShizukuPermissionRequest) {
            pendingShizukuPermissionRequest = true
            try {
                Shizuku.requestPermission(REQUEST_SHIZUKU_PERMISSION)
                showAppToast("请授予 Shizuku 权限请求。", false)
            } catch (e: Throwable) {
                pendingShizukuPermissionRequest = false
                pendingStartAfterShizuku = false
                showAppToast("请求 Shizuku 权限失败：${e.message}", true)
                setStatus("Status: Ready")
            }
        }
    }

    private fun showShizukuConnectionHelpDialog() {
        AlertDialog.Builder(this)
            .setTitle("Shizuku 未连接")
            .setMessage(
                "AimBuddy 无法接收到 Shizuku 服务。\n\n" +
                    "1) 打开 Shizuku 应用并确认服务正在运行\n" +
                    "2) 确认 AimBuddy 已在 Shizuku 应用中被允许\n" +
                    "3) 返回并再次点击开始"
            )
            .setPositiveButton("打开 Shizuku") { _, _ ->
                val launch = packageManager.getLaunchIntentForPackage("moe.shizuku.privileged.api")
                if (launch != null) {
                    startActivity(launch)
                } else {
                    showAppToast("未找到 Shizuku 应用。", true)
                }
            }
            .setNegativeButton("重试") { _, _ ->
                requestShizukuThenMediaProjection()
            }
            .setNeutralButton("Continue Visual Only") { _, _ ->
                requestMediaProjectionPermission()
            }
            .show()
    }

    private fun requestMediaProjectionPermission() {
        pendingStartAfterRoot = false
        // Step 2 (or 3 when root prompt is shown): media projection permission
        Log.i(TAG, "Requesting MediaProjection")
        val captureIntent = mediaProjectionManager?.createScreenCaptureIntent()
            ?: run {
                Log.e(TAG, "MediaProjectionManager is null")
                showAppToast("无法启动屏幕采集", true)
                setStatus("Status: Ready")
                return
            }

        setStatus("Status: Waiting for Screen Capture Permission")

        startActivityForResult(
            captureIntent,
            REQUEST_MEDIA_PROJECTION
        )
    }

    private fun onStopClicked() {
        Log.i(TAG, "Stop button clicked")
        stopESP()
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)

        when (requestCode) {
            REQUEST_OVERLAY_PERMISSION -> {
                if (Settings.canDrawOverlays(this)) {
                    Log.i(TAG, "Overlay permission granted")
                    requestTouchBackendThenProjection()
                } else {
                    Log.w(TAG, "Overlay permission denied")
                    showAppToast("需要悬浮窗权限", true)
                }
            }

            REQUEST_MEDIA_PROJECTION -> {
                if (resultCode == Activity.RESULT_OK && data != null) {
                    Log.i(TAG, "MediaProjection permission granted")
                    
                    // Start Foreground Service FIRST (Required for MediaProjection on Android 10+)
                    val serviceIntent = Intent(this, ScreenCaptureService::class.java)
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        startForegroundService(serviceIntent)
                    } else {
                        startService(serviceIntent)
                    }
                    
                    // Brief delay so the foreground service is promoted before
                    // we bind the MediaProjection. 300ms is enough; the old
                    // 1000ms made startup feel sluggish.
                    Handler(Looper.getMainLooper()).postDelayed({
                        try {
                            mediaProjection = mediaProjectionManager?.getMediaProjection(resultCode, data)
                            registerProjectionCallbackIfNeeded()
                            startESP()
                        } catch (e: Exception) {
                            Log.e(TAG, "Failed to create MediaProjection: ${e.message}")
                            showAppToast("启动采集失败：${e.message}", true)
                        }
                    }, 300)
                    
                } else {
                    Log.w(TAG, "MediaProjection permission denied")
                    showAppToast("需要屏幕采集权限", true)
                }
            }
        }
    }

    private fun startESP() {
        if (!isStarting.compareAndSet(false, true)) {
            Log.i(TAG, "startESP ignored: already starting")
            return
        }

        Log.i(TAG, "Starting ESP")
        setStatus("Status: Starting")
        try {
            if (mediaProjection == null) {
                showAppToast("屏幕采集未初始化", true)
                setStatus("Status: Ready")
                return
            }

            if (!Settings.canDrawOverlays(this)) {
                showAppToast("需要悬浮窗权限", true)
                setStatus("Status: Ready")
                return
            }

            // Force the native menu state to hidden BEFORE adding the overlay so
            // the very first touch-polling cycle sees a closed menu and keeps
            // FLAG_NOT_TOUCHABLE set. Otherwise a stale/persisted menu-visible
            // state can briefly make the overlay capture all screen touches.
            ImGuiGLSurface.nativeSetMenuVisible(false)
            menuVisible = false

            setupScreenCapture()
            setupOverlay()
            nativeStart()

            // Use the hard-reset variant (always writes FLAG_NOT_TOUCHABLE
            // regardless of in-memory state) to guarantee the overlay starts
            // in pass-through mode. The lighter applyOverlayTouchable(false)
            // is a no-op when flags already appear correct, which masks any
            // hidden desync from the addView path.
            forceOverlayNotTouchable()

            // Schedule a delayed second enforcement ~500ms later. The GL
            // surface creation (onSurfaceCreated → nativeInit) happens
            // asynchronously after addView; if anything in that path nudges
            // the window flags, this delayed call catches and corrects it.
            touchHandler.postDelayed({ forceOverlayNotTouchable() }, 500)

            updateButtonStates(true)
            showAppToast("ESP 已启动", false)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start ESP: ${e.message}", e)
            showAppToast("启动 ESP 失败：${e.message}", true)
            stopESP()
        } finally {
            isStarting.set(false)
        }
    }

    private fun stopESP() {
        if (!isStopping.compareAndSet(false, true)) {
            return
        }
        Log.i(TAG, "Stopping ESP")
        setStatus("Status: Stopping")
        try {
            // Stop native processing
            if (nativeIsRunning()) {
                nativeStop()
            }

            // Cleanup screen capture
            imageReader?.setOnImageAvailableListener(null, null)
            virtualDisplay?.release()
            virtualDisplay = null

            imageReader?.close()
            imageReader = null

            if (projectionCallbackRegistered) {
                try {
                    mediaProjection?.unregisterCallback(mediaProjectionCallback)
                } catch (_: Exception) {
                }
            }
            mediaProjection?.stop()
            mediaProjection = null
            projectionCallbackRegistered = false

            // Remove overlay
            removeOverlay()

            updateButtonStates(false)

            // Stop the foreground service
            stopService(Intent(this, ScreenCaptureService::class.java))
        } finally {
            isStopping.set(false)
        }
    }

    private fun setupScreenCapture() {
        Log.i(TAG, "Setting up screen capture at ${CAPTURE_WIDTH}x${CAPTURE_HEIGHT}")

        registerProjectionCallbackIfNeeded()

        // Create ImageReader with HardwareBuffer support
        imageReader = ImageReader.newInstance(
            CAPTURE_WIDTH, CAPTURE_HEIGHT,
            PixelFormat.RGBA_8888,
            3  // Triple buffering to prevent producer stalls (matches native IMAGE_READER_MAX_IMAGES)
        ).apply {
            setOnImageAvailableListener({ reader ->
                val image = reader.acquireLatestImage() ?: return@setOnImageAvailableListener
                try {
                    // Get HardwareBuffer and pass to native code
                    val hardwareBuffer = image.hardwareBuffer
                    if (hardwareBuffer != null) {
                        ScreenCaptureService.nativeOnFrame(hardwareBuffer, image.timestamp)
                        hardwareBuffer.close()
                    }
                } finally {
                    image.close()
                }
            }, imageHandler)
        }

        // Create VirtualDisplay
        virtualDisplay = mediaProjection?.createVirtualDisplay(
            "ESPCapture",
            CAPTURE_WIDTH, CAPTURE_HEIGHT, screenDensity,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader?.surface, null, null
        )

        Log.i(TAG, "Screen capture setup complete")
    }

    private fun registerProjectionCallbackIfNeeded() {
        val projection = mediaProjection ?: return
        if (projectionCallbackRegistered) {
            return
        }
        projection.registerCallback(mediaProjectionCallback, Handler(Looper.getMainLooper()))
        projectionCallbackRegistered = true
    }

    private fun assetExists(assetPath: String): Boolean {
        return try {
            assets.open(assetPath).use { }
            true
        } catch (_: Exception) {
            false
        }
    }

    private fun copyUriToFile(uri: Uri, outFile: File): Boolean {
        return try {
            val modelDir = outFile.parentFile
            if (modelDir == null) {
                return false
            }
            if (!modelDir.exists() && !modelDir.mkdirs()) {
                Log.e(TAG, "Failed to create local model directory")
                return false
            }
            contentResolver.openInputStream(uri)?.use { input ->
                FileOutputStream(outFile).use { output ->
                    input.copyTo(output)
                }
            } ?: return false
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to copy model from uri: ${e.message}", e)
            false
        }
    }

    private fun getDisplayName(uri: Uri): String {
        var name = "model"
        contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (index >= 0 && cursor.moveToFirst()) {
                name = cursor.getString(index) ?: name
            }
        }
        return name
    }

    private fun applyActiveModelSelection() {
        // Out-of-box: always use the built-in asset model. There is no model
        // switching UI anymore, so we never point native at an imported/store
        // model.
        nativeSetModelPaths(null, null)
        activeModelTextState = "模型：内置默认"
    }

    private fun reinitializeNativeIfIdle() {
        if (isRunningState || isStarting.get() || isStopping.get()) {
            showAppToast("新模型将在下次启动时生效。", false)
            return
        }
        nativeShutdown()
        applyActiveModelSelection()
        if (nativeInit(assets, screenWidth, screenHeight)) {
            if (rootAvailable.get() || shizukuAvailable.get()) {
                nativeInitAimbot()
            }
            setStatus("Status: Ready")
            showAppToast("模型已应用", false)
        } else {
            setStatus("Status: Init Failed")
            showAppToast("导入的模型初始化失败", true)
        }
    }

    private fun onImportModelClicked() {
        try {
            importParamLauncher.launch(arrayOf("application/octet-stream", "*/*"))
        } catch (e: ActivityNotFoundException) {
            showAppToast("本设备未找到文件选择器", true)
        }
    }

    private fun onStoreClicked() {
        openModelStore()
    }

    private fun migrateLegacySingleImportedModel() {
        val prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
        val legacyParam = prefs.getString(PREF_MODEL_PARAM_PATH, null)
        val legacyBin = prefs.getString(PREF_MODEL_BIN_PATH, null)
        if (legacyParam.isNullOrBlank() || legacyBin.isNullOrBlank()) {
            return
        }

        val paramFile = File(legacyParam)
        val binFile = File(legacyBin)
        if (!paramFile.exists() || !binFile.exists()) {
            return
        }

        val legacyModel = InstalledModel(
            id = modelCatalog.sanitizeModelId("legacy-local-model"),
            title = "Legacy Imported Model",
            description = "Migrated from previous app version",
            source = ModelSource.LOCAL,
            paramPath = paramFile.absolutePath,
            binPath = binFile.absolutePath,
            totalSizeBytes = paramFile.length() + binFile.length()
        )
        modelCatalog.addOrUpdateModel(legacyModel, makeActive = false)
    }

    private fun openModelStore() {
        showStoreState = true
        if (storeModelsState.isEmpty()) {
            fetchStoreModelsAsync()
        }
    }

    private fun fetchStoreModelsAsync() {
        isFetchingStoreState = true
        thread(start = true, name = "aimbuddy-store-fetch") {
            try {
                val models = storeRepository.fetchAvailableModels()
                runOnUiThread {
                    storeModelsState = models
                    isFetchingStoreState = false
                }
            } catch (e: Exception) {
                runOnUiThread {
                    isFetchingStoreState = false
                    showAppToast("商店获取失败：${e.message}", true)
                }
            }
        }
    }

    private fun downloadStoreModelAsync(model: StoreModelDefinition) {
        downloadingModelIdState = model.id
        showAppToast("正在下载 ${model.title}……", false)
        thread(start = true, name = "aimbuddy-store-download") {
            try {
                val modelId = modelCatalog.sanitizeModelId(model.id)
                val targetDir = File(filesDir, "models/$modelId")
                val downloaded = storeRepository.downloadModel(model, targetDir)

                val installed = InstalledModel(
                    id = modelId,
                    title = model.title,
                    description = model.description,
                    source = ModelSource.STORE,
                    paramPath = downloaded.first.absolutePath,
                    binPath = downloaded.second.absolutePath,
                    totalSizeBytes = downloaded.first.length() + downloaded.second.length()
                )
                modelCatalog.addOrUpdateModel(installed, makeActive = true)

                runOnUiThread {
                    downloadingModelIdState = null
                    applyActiveModelSelection()
                    reinitializeNativeIfIdle()
                    showAppToast("已下载并切换至 ${model.title}", false)
                }
            } catch (e: Exception) {
                runOnUiThread {
                    downloadingModelIdState = null
                    showAppToast("下载失败：${e.message}", true)
                }
            }
        }
    }

    private fun showDeleteModelConfirmationDialog(model: InstalledModel) {
        AlertDialog.Builder(this)
            .setTitle("删除模型")
            .setMessage("确定要永久删除「${model.title}」并释放空间吗？")
            .setPositiveButton("删除") { _, _ ->
                if (modelCatalog.deleteModel(model.id)) {
                    applyActiveModelSelection()
                    reinitializeNativeIfIdle()
                    showAppToast("模型已成功删除", false)
                } else {
                    showAppToast("删除模型失败", true)
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun formatBytes(bytes: Long): String {
        if (bytes < 1024L) {
            return "${bytes} B"
        }
        val kb = bytes / 1024.0
        if (kb < 1024.0) {
            return String.format("%.1f KB", kb)
        }
        val mb = kb / 1024.0
        if (mb < 1024.0) {
            return String.format("%.2f MB", mb)
        }
        val gb = mb / 1024.0
        return String.format("%.2f GB", gb)
    }

    private fun refreshShizukuState(forceUnavailable: Boolean = false) {
        val available = if (forceUnavailable) {
            false
        } else {
            try {
                Shizuku.pingBinder() &&
                    Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
            } catch (_: Throwable) {
                false
            }
        }

        shizukuAvailable.set(available)
        if (available && shizukuInjector == null) {
            shizukuInjector = ShizukuInputInjector()
        }
        if (!available) {
            shizukuInjector = null
        }

        nativeSetShizukuBridgeAvailable(available)
        ImGuiGLSurface.nativeSetShizukuAvailable(available)
    }

    private fun isAccessibilityServiceEnabled(): Boolean {
        val expected = ComponentName(this, AimAccessibilityService::class.java)
        val enabledServices = Settings.Secure.getString(
            contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        val splitter = TextUtils.SimpleStringSplitter(':')
        splitter.setString(enabledServices)
        for (entry in splitter) {
            if (entry.equals(expected.flattenToString(), ignoreCase = true)) {
                return true
            }
        }
        return false
    }

    private fun refreshAccessibilityState() {
        // Settings.Secure is the source of truth, but a live bound service also
        // counts (covers OEMs that report the enabled list differently).
        val available = isAccessibilityServiceEnabled() || AimAccessibilityService.isReady()
        accessibilityAvailable.set(available)
        nativeSetAccessibilityBridgeAvailable(available)
        ImGuiGLSurface.nativeSetAccessibilityAvailable(available)
    }

    private fun isRootLikelyAvailable(): Boolean {
        return rootAvailable.get() || RootUtils.isRootAvailable()
    }

    private fun resolveEffectiveTouchBackend(preferredBackend: Int): Int {
        val rootReady = isRootLikelyAvailable()
        val shizukuReady = shizukuAvailable.get()
        val a11yReady = accessibilityAvailable.get()

        return when (preferredBackend) {
            0 -> if (rootReady) 0 else if (a11yReady) 2 else if (shizukuReady) 1 else 0
            1 -> if (shizukuReady) 1 else if (a11yReady) 2 else if (rootReady) 0 else 1
            // Out-of-box path: never silently divert the user into installing
            // Shizuku. Root is fine if it happens to be there (lower latency),
            // otherwise stay on accessibility and let the enable-dialog guide.
            2 -> if (a11yReady) 2 else if (rootReady) 0 else 2
            else -> 2
        }
    }

    /**
     * Apply or clear FLAG_SECURE on overlay windows.
     * When the flag is set, MediaProjection / screen recorders / screenshots
     * will not capture the overlay  -  only the game underneath.
     */
    fun applyStreamerModeFlag(enabled: Boolean) {
        streamerModeEnabled = enabled
        val wm = windowManager ?: return

        imguiOverlay?.let { view ->
            // GLSurfaceView is a SurfaceView; SurfaceView owns a separate
            // surface composited by SurfaceFlinger that does NOT inherit
            // FLAG_SECURE from the parent window on all Android versions.
            // Call setSecure() explicitly so MediaProjection blanks the GL
            // surface regardless of the host window flag.
            //
            // CRITICAL: setSecure() triggers an internal surface recreation
            // on SurfaceView. This async relayout can silently strip
            // FLAG_NOT_TOUCHABLE from the window's actual compositor state
            // (even though the in-memory params still show it), causing the
            // overlay to intercept ALL touches and freeze the screen. We
            // therefore (a) always preserve FLAG_NOT_TOUCHABLE in params
            // when updating, and (b) schedule delayed re-applications after
            // the surface rebuild settles to catch the post-relayout state.
            try { view.setSecure(enabled) } catch (_: Throwable) {}

            val params = view.layoutParams as? WindowManager.LayoutParams ?: return@let
            if (enabled) {
                params.flags = params.flags or WindowManager.LayoutParams.FLAG_SECURE
            } else {
                params.flags = params.flags and WindowManager.LayoutParams.FLAG_SECURE.inv()
            }
            // ALWAYS preserve FLAG_NOT_TOUCHABLE — the overlay must never
            // become touchable, or it will block all screen interaction.
            params.flags = params.flags or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
            try { wm.updateViewLayout(view, params) } catch (_: IllegalArgumentException) {}

            // Re-apply FLAG_NOT_TOUCHABLE after the surface rebuild settles.
            // setSecure() triggers an async SurfaceView relayout; the
            // updateViewLayout above may not survive that relayout. Posting
            // delayed force-applications catches the post-relayout state at
            // two intervals to cover fast and slow devices.
            touchHandler.postDelayed({ forceOverlayNotTouchable() }, 100)
            touchHandler.postDelayed({ forceOverlayNotTouchable() }, 500)
        }

        floatingIconView?.let { view ->
            val params = floatingIconParams ?: return@let
            if (enabled) {
                params.flags = params.flags or WindowManager.LayoutParams.FLAG_SECURE
            } else {
                params.flags = params.flags and WindowManager.LayoutParams.FLAG_SECURE.inv()
            }
            try { wm.updateViewLayout(view, params) } catch (_: IllegalArgumentException) {}
        }
    }

    private fun injectShizukuAimMove(screenX: Float, screenY: Float, isFirst: Boolean): Boolean {
        if (!shizukuAvailable.get()) {
            return false
        }
        val injector = shizukuInjector ?: return false
        return injector.injectAimMove(screenX, screenY, isFirst)
    }

    private fun injectShizukuAimUp(): Boolean {
        val injector = shizukuInjector ?: return false
        return injector.releaseAim()
    }

    private fun setupOverlay() {
        Log.i(TAG, "Setting up overlay")

        if (!Settings.canDrawOverlays(this)) {
            throw IllegalStateException("Overlay permission is not granted")
        }

        // Create GLSurfaceView for ImGui rendering
        imguiOverlay = ImGuiGLSurface(this)

        // Overlay window parameters
        val layoutParams = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            else
                WindowManager.LayoutParams.TYPE_PHONE,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    // Default to pass-through touch; enable only when menu needs input
                    WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                    WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
                    // NOTE: FLAG_FULLSCREEN was removed because it can place the
                    // overlay over system gesture areas and interfere with normal
                    // touch navigation on some devices.
                    WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            // Extend into display cutout areas (notch, pill, etc.)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
        }

        if (streamerModeEnabled) {
            layoutParams.flags = layoutParams.flags or WindowManager.LayoutParams.FLAG_SECURE
            try { imguiOverlay?.setSecure(true) } catch (_: Throwable) {}
        }

        windowManager?.addView(imguiOverlay, layoutParams)
        isOverlayVisible = true

        startTouchPolling()
        setupFloatingIcon()

        Log.i(TAG, "Overlay added")
    }

    private fun removeOverlay() {
        if (isOverlayVisible) {
            stopTouchPolling()
            removeMenuInputWindow()
            imguiOverlay?.let {
                try {
                    windowManager?.removeView(it)
                } catch (ignored: IllegalArgumentException) {
                    Log.w(TAG, "Overlay view already removed")
                }
            }
            imguiOverlay = null
            removeFloatingIcon()
            isOverlayVisible = false
            menuVisible = false
            Log.i(TAG, "Overlay removed")
        }
    }

    private fun setupFloatingIcon() {
        if (floatingIconView != null) return
        val wm = windowManager ?: return

        val iconSizePx = (44 * resources.displayMetrics.density).toInt()
        val iconView = ImageView(this).apply {
            setImageDrawable(loadSvgDrawable("icons/settings.svg")
                ?: getDrawable(android.R.drawable.ic_menu_manage))
            setLayerType(View.LAYER_TYPE_SOFTWARE, null)
            scaleType = ImageView.ScaleType.CENTER_INSIDE
        }

        val params = WindowManager.LayoutParams(
            iconSizePx,
            iconSizePx,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            else
                WindowManager.LayoutParams.TYPE_PHONE,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    // CRITICAL: without FLAG_NOT_TOUCH_MODAL a touchable
                    // window consumes ALL touch events on the entire screen,
                    // even those outside its 44x44 bounds — which is exactly
                    // why the device froze after starting the service. With
                    // this flag, touches outside the gear pass through to the
                    // game underneath (the imguiOverlay is FLAG_NOT_TOUCHABLE
                    // so it never competes for them).
                    WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                    WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 40
            y = 120
        }

        // Tapping the gear toggles the settings menu. The gear window has
        // FLAG_NOT_TOUCH_MODAL, so a tap here is consumed only within its 44x44
        // bounds and NEVER freezes the rest of the screen. The menu's input is
        // handled by a separate full-screen window that exists only while the
        // menu is open (see applyOverlayTouchable / addMenuInputWindow).
        iconView.setOnTouchListener { _, event ->
            if (event.actionMasked == MotionEvent.ACTION_UP) {
                openMenu()
            }
            true
        }

        if (streamerModeEnabled) {
            params.flags = params.flags or WindowManager.LayoutParams.FLAG_SECURE
        }

        wm.addView(iconView, params)
        floatingIconView = iconView
        floatingIconParams = params
        
        // Sync initial icon position to native code
        ImGuiGLSurface.nativeSetIconPosition(params.x.toFloat(), params.y.toFloat())
    }

    private fun loadSvgDrawable(assetPath: String): Drawable? {
        return try {
            val svg = assets.open(assetPath).use { SVG.getFromInputStream(it) }
            val picture = svg.renderToPicture()
            PictureDrawable(picture)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to load SVG icon: ${e.message}")
            null
        }
    }

    private fun removeFloatingIcon() {
        val wm = windowManager ?: return
        floatingIconView?.let {
            try {
                wm.removeView(it)
            } catch (ignored: IllegalArgumentException) {
                Log.w(TAG, "Floating icon already removed")
            }
        }
        floatingIconView = null
        floatingIconParams = null
    }

    /**
     * Open the ImGui settings menu. The full-screen [imguiOverlay] stays
     * FLAG_NOT_TOUCHABLE at all times; menu input is served by a dedicated
     * transparent full-screen window ([menuInputView]) that is added now and
     * removed automatically when the menu closes. This guarantees the screen
     * can never be blocked by the persistent overlay.
     */
    private fun openMenu() {
        if (!ImGuiGLSurface.nativeIsMenuVisible()) {
            ImGuiGLSurface.nativeSetMenuVisible(true)
            menuVisible = true
        }
        addMenuInputWindow()
    }

    /**
     * Show/hide the dedicated menu input window. When [touchable] is true the
     * menu needs input, so we make sure the input window exists; when false we
     * remove it so all touches fall through to the app underneath.
     *
     * The persistent [imguiOverlay] is NEVER made touchable — it only renders.
     * Keeping it NOT_TOUCHABLE permanently is what makes the "screen frozen
     * after starting the service" failure mode impossible: there is no
     * full-screen window left that can intercept touches.
     */
    private fun applyOverlayTouchable(touchable: Boolean) {
        if (touchable) {
            addMenuInputWindow()
        } else {
            removeMenuInputWindow()
        }
    }

    private fun addMenuInputWindow() {
        val wm = windowManager ?: return
        if (menuInputView != null) return
        val view = View(this)
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            else
                WindowManager.LayoutParams.TYPE_PHONE,
            // Touchable (no FLAG_NOT_TOUCHABLE) but not focusable, so it captures
            // menu input without stealing soft-keyboard focus. Covers the screen
            // only while the menu is open; removed on close.
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                layoutInDisplayCutoutMode =
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
            }
        }

        view.setOnTouchListener { _, event ->
            if (!ImGuiGLSurface.nativeIsMenuVisible()) return@setOnTouchListener false
            // Use actionMasked to strip the pointer index — without this,
            // multi-touch events (ACTION_POINTER_DOWN/UP) carry an encoded
            // pointer index in the upper bits and never match the cases
            // below, silently dropping those events.
            //
            // ACTION_CANCEL must be forwarded as an "up" event: when the
            // system cancels the touch sequence (window removed, config
            // change, parent intercept), failing to send an "up" leaves
            // ImGui permanently in a "mouse button down" state — a sticky
            // click that makes every subsequent menu interaction impossible.
            val action = when (event.actionMasked) {
                MotionEvent.ACTION_DOWN,
                MotionEvent.ACTION_POINTER_DOWN -> 0
                MotionEvent.ACTION_UP,
                MotionEvent.ACTION_POINTER_UP,
                MotionEvent.ACTION_CANCEL -> 1
                MotionEvent.ACTION_MOVE -> 2
                else -> return@setOnTouchListener false
            }
            ImGuiGLSurface.nativeMotionEvent(action, event.x, event.y)
            true
        }

        if (streamerModeEnabled) {
            params.flags = params.flags or WindowManager.LayoutParams.FLAG_SECURE
        }

        try {
            wm.addView(view, params)
            menuInputView = view
            menuInputParams = params
            Log.i(TAG, "Menu input window added")
        } catch (t: Throwable) {
            Log.e(TAG, "addMenuInputWindow failed: ${t.message}")
        }
    }

    private fun removeMenuInputWindow() {
        val wm = windowManager ?: return
        menuInputView?.let {
            try {
                wm.removeView(it)
            } catch (ignored: IllegalArgumentException) {
                Log.w(TAG, "Menu input window already removed")
            }
        }
        menuInputView = null
        menuInputParams = null
        Log.i(TAG, "Menu input window removed")
    }

    /**
     * Force the overlay back to passthrough (NOT_TOUCHABLE), closing the menu in
     * both the Kotlin state and the native renderer so the 50ms poller keeps it
     * transparent to touches. This is the universal escape hatch triggered by
     * the persistent notification's "恢复触摸" action — it works even when the
     * menu is stuck open and the floating gear is unreachable.
     */
    private fun forceRestoreTouch() {
        Log.i(TAG, "forceRestoreTouch() called")
        ImGuiGLSurface.nativeSetMenuVisible(false)
        menuVisible = false
        removeMenuInputWindow()
        forceOverlayNotTouchable()
        runOnUiThread {
            showAppToast("已恢复触摸，菜单已关闭", false)
        }
    }

    /**
     * Unconditionally force FLAG_NOT_TOUCHABLE onto the overlay window.
     *
     * Unlike [applyOverlayTouchable] which only updates the window when the
     * in-memory flags *appear* to differ, this method always writes the flag
     * and catches any WindowManager exception. It is the hard reset for touch
     * passthrough  —  used by [forceRestoreTouch], after startup, and as the
     * ultimate recovery when the poller detects a persistent mismatch.
     */
    private fun forceOverlayNotTouchable() {
        val view = imguiOverlay ?: return
        val params = view.layoutParams as? WindowManager.LayoutParams ?: return
        val oldFlags = params.flags
        params.flags = params.flags or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
        try {
            windowManager?.updateViewLayout(view, params)
            Log.i(TAG, "forceOverlayNotTouchable: FLAG_NOT_TOUCHABLE applied")
        } catch (t: Throwable) {
            params.flags = oldFlags
            Log.e(TAG, "forceOverlayNotTouchable failed: ${t.message}")
        }
    }

    private fun startTouchPolling() {
        if (touchPolling) return
        touchPolling = true
        touchPollCycle = 0
        touchHandler.post(object : Runnable {
            override fun run() {
                if (!touchPolling) return

                // Everything below is best-effort: the loop MUST reschedule
                // itself no matter what. An early failure must never kill the
                // poller permanently, or the menu input window could get stuck.
                try {
                    touchPollCycle++

                    // The full-screen GL overlay must ALWAYS stay NOT_TOUCHABLE.
                    // The in-memory params.flags check below is a fast-path
                    // optimisation, but it CANNOT detect a desync where the
                    // actual WindowManager state lost the flag while the
                    // in-memory copy still has it (this happens after
                    // SurfaceView.setSecure() triggers an internal relayout,
                    // or on OEM ROMs that mangle flags). Every ~2 s (40 cycles
                    // at 50 ms) we therefore bypass the check and unconditionally
                    // force FLAG_NOT_TOUCHABLE onto the window.
                    val view = imguiOverlay
                    val params = view?.layoutParams as? WindowManager.LayoutParams
                    if (view != null && params != null) {
                        val flagsMissing = (params.flags and WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE) == 0
                        if (flagsMissing || touchPollCycle % 40 == 0) {
                            forceOverlayNotTouchable()
                        }
                    }

                    // The menu's input window exists only while the menu is open.
                    // When the menu closes (e.g. via its X button in native code)
                    // we remove the window entirely so every touch falls through
                    // to the app underneath — guaranteed, no flag toggling.
                    val menuOpen = ImGuiGLSurface.nativeIsMenuVisible()
                    if (menuOpen && menuInputView == null) {
                        addMenuInputWindow()
                    } else if (!menuOpen && menuInputView != null) {
                        removeMenuInputWindow()
                    }

                    // --- Render-thread stall detection -------------------------
                    // MUST run AFTER the add/remove logic above. If a stall is
                    // detected, forceRestoreTouch() removes the menu input window
                    // and sets nativeMenuVisible=false. The next polling cycle
                    // will then see menuOpen=false and skip re-adding. If this
                    // ran BEFORE the add/remove logic, the cached menuOpen=true
                    // would cause the window to be immediately re-added right
                    // after forceRestoreTouch() tore it down — defeating the
                    // force-close and leaving the touch deadlock in place.
                    //
                    // If the GL render thread has died or stalled, nativeTick
                    // stops updating g_menuVisible, so the menu's X button can
                    // never be clicked. menuInputView would then trap every
                    // touch on the screen forever. Detect this: if the menu
                    // claims to be open but no render tick has been registered
                    // for > 1.5 s, force-close everything.
                    if (menuOpen) {
                        val tickMs = ImGuiGLSurface.nativeGetLastTickMillis()
                        if (tickMs > 0) lastRenderTickMs = tickMs
                        val nowMs = System.currentTimeMillis()
                        if (lastRenderTickMs > 0 && nowMs - lastRenderTickMs > 1500) {
                            Log.e(TAG, "Render thread stall detected (no tick for ${nowMs - lastRenderTickMs}ms), force-closing menu")
                            forceRestoreTouch()
                        }
                    }
                } catch (t: Throwable) {
                    Log.w(TAG, "Touch polling cycle failed: ${t.message}")
                } finally {
                    if (touchPolling) {
                        touchHandler.postDelayed(this, 50)
                    }
                }
            }
        })
    }

    private fun stopTouchPolling() {
        touchPolling = false
        touchHandler.removeCallbacksAndMessages(null)
    }

    private fun setStatus(status: String) {
        runOnUiThread {
            statusTextState = status
        }
    }

    private fun updateButtonStates(isRunning: Boolean) {
        runOnUiThread {
            if (isRunning) {
                isRunningState = true
                statusTextState = "Status: Running"
            } else {
                isRunningState = false
                statusTextState = "Status: Ready"
            }
        }
    }

    private fun showAppToast(message: String, isError: Boolean) {
        val textView = android.widget.TextView(this).apply {
            text = message
            setPadding(26, 18, 26, 18)
            textSize = 13f
            maxLines = 4
            setTextColor(android.graphics.Color.WHITE)
            setBackgroundColor(if (isError) 0xCC8B1D1D.toInt() else 0xCC1B1F2A.toInt())
            gravity = Gravity.CENTER
        }
        Toast(this).apply {
            duration = Toast.LENGTH_LONG
            view = textView
            setGravity(Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL, 0, 120)
        }.show()
    }

    private fun openGithubUrl() {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(OSS_GITHUB_URL))
        startActivity(intent)
    }

    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    private fun LauncherScreen(
        isRunning: Boolean,
        statusText: String,
        onStart: () -> Unit,
        onStop: () -> Unit,
        onOpenGithub: () -> Unit,
    ) {
        var menuOpen by remember { mutableStateOf(false) }
        val statusColor = statusAccentFor(statusText, isRunning)

        Box(modifier = Modifier.fillMaxSize()) {
            Scaffold(
                containerColor = MaterialTheme.colorScheme.background,
                topBar = {
                    CenterAlignedTopAppBar(
                        title = {
                            Text(
                                text = "AimBuddy",
                                style = MaterialTheme.typography.titleLarge,
                                fontWeight = FontWeight.SemiBold,
                            )
                        },
                        actions = {
                            IconButton(onClick = { menuOpen = true }) {
                                Icon(Icons.Filled.MoreVert, contentDescription = "More")
                            }
                        },
                        colors = TopAppBarDefaults.centerAlignedTopAppBarColors(
                            containerColor = MaterialTheme.colorScheme.background,
                        ),
                    )
                },
            ) { inner ->
                Row(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(inner)
                        .padding(horizontal = 24.dp, vertical = 12.dp),
                    horizontalArrangement = Arrangement.spacedBy(24.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    // Left Panel: Status
                    Column(
                        modifier = Modifier
                            .weight(1.2f)
                            .fillMaxHeight(),
                        verticalArrangement = Arrangement.Center,
                        horizontalAlignment = Alignment.Start
                    ) {
                        StatusCard(
                            statusText = statusText,
                            accent = statusColor,
                            isRunning = isRunning,
                        )
                    }

                    // Right Panel: Primary Toggle
                    Column(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxHeight(),
                        verticalArrangement = Arrangement.Center,
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        PrimaryActionButton(
                            isRunning = isRunning,
                            onClick = if (isRunning) onStop else onStart
                        )
                    }
                }
            }

            // In-window custom Dropdown Menu Overlay
            if (menuOpen) {
                // Click catcher
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color.Black.copy(alpha = 0.2f))
                        .clickable(
                            interactionSource = remember { MutableInteractionSource() },
                            indication = null
                        ) { menuOpen = false }
                )
                
                // Dropdown Card positioned at Top-End
                Card(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(top = 56.dp, end = 16.dp)
                        .width(220.dp)
                        .shadow(12.dp, RoundedCornerShape(14.dp)),
                    shape = RoundedCornerShape(14.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.surfaceVariant,
                    ),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.15f))
                ) {
                    Column(modifier = Modifier.padding(vertical = 4.dp)) {
                        DropdownMenuItemContent(
                            text = "项目主页",
                            icon = Icons.Filled.Info,
                            onClick = { menuOpen = false; onOpenGithub() }
                        )
                    }
                }
            }

            // Model store / import UI removed: the app ships with a single
            // built-in model (out-of-box).
        }
    }

    @Composable
    private fun DropdownMenuItemContent(
        text: String,
        icon: ImageVector,
        onClick: () -> Unit
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { onClick() }
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(20.dp)
            )
            Spacer(modifier = Modifier.width(12.dp))
            Text(
                text = text,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurface,
                fontWeight = FontWeight.Medium
            )
        }
    }

    private fun mapStatusToDisplayTitle(status: String): String {
        val raw = status.removePrefix("Status: ").trim()
        return when (raw) {
            "Model Loading" -> "模型加载中"
            "Init Failed" -> "初始化失败"
            "Ready" -> "系统就绪"
            "Waiting for Root Permission" -> "正在等待 Root 权限"
            "Root Granted" -> "Root 权限已授予"
            "Using Shizuku Backend" -> "Shizuku 后端已启用"
            "Waiting for Shizuku Permission" -> "正在等待 Shizuku 权限"
            "Using Root Backend" -> "Root 后端已启用"
            "Using Accessibility Backend" -> "无障碍后端已启用"
            "Shizuku Not Connected" -> "Shizuku 未连接"
            "Accessibility Not Enabled" -> "无障碍服务未开启"
            "Waiting for Screen Capture Permission" -> "正在等待屏幕采集权限"
            "Starting" -> "正在启动服务"
            "Stopping" -> "正在停止服务"
            "Running" -> "服务运行中"
            else -> raw
        }
    }

    private fun mapStatusToDisplayDescription(status: String): String {
        val raw = status.removePrefix("Status: ").trim()
        return when (raw) {
            "Model Loading" -> "正在映射内存缓冲并初始化权重……"
            "Init Failed" -> "引擎初始化失败，请检查模型文件。"
            "Ready" -> "系统已配置，点击下方「启动服务」开始。"
            "Waiting for Root Permission" -> "请在提示时授予超级用户授权。"
            "Root Granted" -> "已验证超级用户权限，正在挂载触摸输入。"
            "Using Shizuku Backend" -> "合成触摸事件经由 Shizuku 包装转发。"
            "Waiting for Shizuku Permission" -> "请在提示时授权 Shizuku 管理器。"
            "Using Root Backend" -> "合成触摸事件直接注入到 /dev/uinput。"
            "Using Accessibility Backend" -> "合成触摸事件由系统无障碍服务注入，无需 Root。"
            "Shizuku Not Connected" -> "请在 Shizuku 管理应用中启动服务。"
            "Accessibility Not Enabled" -> "请在系统「无障碍」设置中开启 AimBuddy 服务。"
            "Waiting for Screen Capture Permission" -> "请在弹窗中确认屏幕投影权限。"
            "Starting" -> "正在创建虚拟设备并启动采集线程……"
            "Stopping" -> "正在释放图形 Surface 并停止后台任务……"
            "Running" -> "正在采集缓冲上动态绘制 ESP 叠加层。"
            else -> "系统状态：$raw"
        }
    }

    @Composable
    private fun StatusCard(statusText: String, accent: Color, isRunning: Boolean) {
        val displayTitle = mapStatusToDisplayTitle(statusText)
        val displayDescription = mapStatusToDisplayDescription(statusText)
        
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f),
            ),
            border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.12f)),
            elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 20.dp, vertical = 16.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.Start
            ) {
                Box(
                    modifier = Modifier
                        .size(12.dp)
                        .clip(CircleShape)
                        .background(accent)
                        .border(
                            2.dp,
                            accent.copy(alpha = 0.4f),
                            CircleShape
                        )
                )
                Spacer(Modifier.width(16.dp))
                Column {
                    Text(
                        text = displayTitle,
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text = displayDescription,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 2
                    )
                }
            }
        }
    }

    @Composable
    private fun ActiveModelCard(activeModelText: String, onSelectModel: () -> Unit) {
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { onSelectModel() },
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.2f),
            ),
            border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.08f)),
            elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 20.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "当前模型",
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary.copy(alpha = 0.8f)
                    )
                    Spacer(Modifier.height(2.dp))
                    Text(
                        text = activeModelText.removePrefix("模型： "),
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface,
                        maxLines = 1
                    )
                }
                Icon(
                    imageVector = Icons.Filled.FolderOpen,
                    contentDescription = "Manage Models",
                    tint = MaterialTheme.colorScheme.secondary,
                    modifier = Modifier.size(18.dp)
                )
            }
        }
    }

    @Composable
    private fun PrimaryActionButton(isRunning: Boolean, onClick: () -> Unit) {
        val containerColor = if (isRunning) Color(0xFFDC2626) else MaterialTheme.colorScheme.primary
        val contentColor = if (isRunning) Color.White else Color(0xFF070B13)
        val icon = if (isRunning) Icons.Filled.Stop else Icons.Filled.PlayArrow
        val label = if (isRunning) "停止服务" else "启动服务"
        
        Card(
            modifier = Modifier
                .width(220.dp)
                .height(52.dp)
                .clickable { onClick() }
                .shadow(6.dp, RoundedCornerShape(26.dp)),
            shape = RoundedCornerShape(26.dp),
            colors = CardDefaults.cardColors(
                containerColor = containerColor,
                contentColor = contentColor
            )
        ) {
            Row(
                modifier = Modifier.fillMaxSize(),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    imageVector = icon,
                    contentDescription = null,
                    modifier = Modifier.size(22.dp)
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = label,
                    style = MaterialTheme.typography.labelLarge,
                    fontWeight = FontWeight.Bold,
                    fontSize = 14.sp,
                    letterSpacing = 0.8.sp
                )
            }
        }
    }

    @Composable
    private fun ModelStoreScreen(
        onClose: () -> Unit,
        onImportModel: () -> Unit
    ) {
        var selectedTab by remember { mutableStateOf(0) }

        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(MaterialTheme.colorScheme.background)
        ) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(horizontal = 24.dp, vertical = 16.dp)
            ) {
                // Header
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        IconButton(onClick = onClose) {
                            Icon(Icons.Filled.ArrowBack, contentDescription = "Back")
                        }
                        Spacer(Modifier.width(8.dp))
                        Text(
                            text = "模型商店",
                            style = MaterialTheme.typography.headlineSmall,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onSurface
                        )
                    }
                    
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (selectedTab == 0) {
                            IconButton(
                                onClick = { fetchStoreModelsAsync() },
                                enabled = !isFetchingStoreState
                            ) {
                                if (isFetchingStoreState) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(20.dp),
                                        strokeWidth = 2.dp,
                                        color = MaterialTheme.colorScheme.primary
                                    )
                                } else {
                                    Icon(Icons.Filled.Refresh, contentDescription = "Refresh")
                                }
                            }
                        }
                        
                        Spacer(Modifier.width(12.dp))
                        
                        OutlinedButton(
                            onClick = onImportModel,
                            contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp),
                            shape = RoundedCornerShape(12.dp),
                            border = BorderStroke(1.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.5f))
                        ) {
                            Icon(Icons.Filled.FolderOpen, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("本地导入", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }

                Spacer(Modifier.height(12.dp))

                // Tab Selector
                TabRow(
                    selectedTabIndex = selectedTab,
                    containerColor = Color.Transparent,
                    divider = { HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.08f)) },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Tab(
                        selected = selectedTab == 0,
                        onClick = { selectedTab = 0 },
                        text = { Text("商店目录", fontWeight = FontWeight.Bold) }
                    )
                    Tab(
                        selected = selectedTab == 1,
                        onClick = { selectedTab = 1 },
                        text = { Text("我的模型 (${installedModelsState.size})", fontWeight = FontWeight.Bold) }
                    )
                }

                Spacer(Modifier.height(16.dp))

                // Tab Content
                Box(modifier = Modifier.weight(1f)) {
                    if (selectedTab == 0) {
                        CatalogTabContent()
                    } else {
                        InstalledTabContent()
                    }
                }
            }
        }
    }

    @Composable
    private fun CatalogTabContent() {
        if (isFetchingStoreState && storeModelsState.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
                    Spacer(Modifier.height(12.dp))
                    Text("正在从 GitHub 获取目录……", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        } else if (storeModelsState.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("商店中暂无可用模型。", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(12.dp))
                    OutlinedButton(onClick = { fetchStoreModelsAsync() }) {
                        Text("重新获取")
                    }
                }
            }
        } else {
            LazyColumn(
                verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxSize()
            ) {
                items(storeModelsState) { model ->
                    val isInstalled = installedModelsState.any { it.id == model.id }
                    val isDownloading = downloadingModelIdState == model.id
                    val isActive = modelCatalog.getActiveModel()?.id == model.id
                    
                    CatalogModelCard(
                        model = model,
                        isInstalled = isInstalled,
                        isActive = isActive,
                        isDownloading = isDownloading,
                        onDownload = { downloadStoreModelAsync(model) },
                        onUse = {
                            if (modelCatalog.setActiveModel(model.id)) {
                                applyActiveModelSelection()
                                reinitializeNativeIfIdle()
                            }
                        }
                    )
                }
            }
        }
    }

    @Composable
    private fun CatalogModelCard(
        model: StoreModelDefinition,
        isInstalled: Boolean,
        isActive: Boolean,
        isDownloading: Boolean,
        onDownload: () -> Unit,
        onUse: () -> Unit
    ) {
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(14.dp),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.3f)
            ),
            border = BorderStroke(
                1.dp,
                if (isActive) MaterialTheme.colorScheme.primary.copy(alpha = 0.4f)
                else MaterialTheme.colorScheme.outline.copy(alpha = 0.08f)
            )
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp)
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                text = model.title,
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                            if (isActive) {
                                Spacer(Modifier.width(8.dp))
                                Box(
                                    modifier = Modifier
                                        .clip(RoundedCornerShape(4.dp))
                                        .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.15f))
                                        .padding(horizontal = 6.dp, vertical = 2.dp)
                                ) {
                                    Text(
                                        text = "使用中",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.primary,
                                        fontWeight = FontWeight.Bold
                                    )
                                }
                            }
                        }
                        Spacer(Modifier.height(4.dp))
                        Text(
                            text = model.description,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    
                    Spacer(Modifier.width(16.dp))
                    
                    Box {
                        if (isDownloading) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(24.dp),
                                color = MaterialTheme.colorScheme.primary,
                                strokeWidth = 2.5.dp
                            )
                        } else if (isActive) {
                            // Already active
                        } else if (isInstalled) {
                            OutlinedButton(
                                onClick = onUse,
                                shape = RoundedCornerShape(10.dp),
                                contentPadding = PaddingValues(horizontal = 14.dp, vertical = 6.dp)
                            ) {
                                Text("使用", style = MaterialTheme.typography.labelMedium)
                            }
                        } else if (model.isDownloadable) {
                            FilledTonalButton(
                                onClick = onDownload,
                                shape = RoundedCornerShape(10.dp),
                                contentPadding = PaddingValues(horizontal = 14.dp, vertical = 6.dp)
                            ) {
                                Text("下载", style = MaterialTheme.typography.labelMedium)
                            }
                        } else {
                            Text(
                                text = "演示",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                fontWeight = FontWeight.Bold
                            )
                        }
                    }
                }
                
                Spacer(Modifier.height(10.dp))
                
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.Start,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    val sizeStr = if (model.totalSizeBytes > 0L) formatBytes(model.totalSizeBytes) else "仅元数据"
                    Text(
                        text = "大小：$sizeStr",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
                    )
                    Spacer(Modifier.width(16.dp))
                    Text(
                        text = if (model.isDownloadable) "Downloadable" else "Metadata only",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
                    )
                }
            }
        }
    }

    @Composable
    private fun InstalledTabContent() {
        if (installedModelsState.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    text = "尚未安装模型，请导入模型文件或从商店下载。",
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        } else {
            LazyColumn(
                verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxSize()
            ) {
                items(installedModelsState) { model ->
                    val isActive = modelCatalog.getActiveModel()?.id == model.id
                    InstalledModelCard(
                        model = model,
                        isActive = isActive,
                        onUse = {
                            if (modelCatalog.setActiveModel(model.id)) {
                                applyActiveModelSelection()
                                reinitializeNativeIfIdle()
                            }
                        },
                        onDelete = {
                            showDeleteModelConfirmationDialog(model)
                        }
                    )
                }
            }
        }
    }

    @Composable
    private fun InstalledModelCard(
        model: InstalledModel,
        isActive: Boolean,
        onUse: () -> Unit,
        onDelete: () -> Unit
    ) {
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(14.dp),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.3f)
            ),
            border = BorderStroke(
                1.dp,
                if (isActive) MaterialTheme.colorScheme.primary.copy(alpha = 0.4f)
                else MaterialTheme.colorScheme.outline.copy(alpha = 0.08f)
            )
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp)
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                text = model.title,
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                            if (isActive) {
                                Spacer(Modifier.width(8.dp))
                                Box(
                                    modifier = Modifier
                                        .clip(RoundedCornerShape(4.dp))
                                        .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.15f))
                                        .padding(horizontal = 6.dp, vertical = 2.dp)
                                ) {
                                    Text(
                                        text = "使用中",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.primary,
                                        fontWeight = FontWeight.Bold
                                    )
                                }
                            }
                        }
                        Spacer(Modifier.height(4.dp))
                        Text(
                            text = model.description,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    
                    Spacer(Modifier.width(16.dp))
                    
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (!isActive) {
                            OutlinedButton(
                                onClick = onUse,
                                shape = RoundedCornerShape(10.dp),
                                contentPadding = PaddingValues(horizontal = 14.dp, vertical = 6.dp)
                            ) {
                                Text("使用", style = MaterialTheme.typography.labelMedium)
                            }
                            
                            Spacer(Modifier.width(8.dp))
                        }
                        
                        if (model.id != "asset-default") {
                            IconButton(onClick = onDelete) {
                                Icon(
                                    imageVector = Icons.Filled.Delete,
                                    contentDescription = "Delete Model",
                                    tint = MaterialTheme.colorScheme.error
                                )
                            }
                        }
                    }
                }
                
                Spacer(Modifier.height(10.dp))
                
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.Start,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    val sizeStr = if (model.totalSizeBytes > 0L) formatBytes(model.totalSizeBytes) else "无"
                    Text(
                        text = "大小：$sizeStr",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
                    )
                    Spacer(Modifier.width(16.dp))
                    Text(
                        text = "来源：${model.source.name.lowercase()}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
                    )
                }
            }
        }
    }

    @Composable
    private fun statusAccentFor(statusText: String, isRunning: Boolean): Color {
        val lower = statusText.lowercase()
        return when {
            isRunning || lower.contains("running") -> StatusGreen
            lower.contains("fail") -> StatusRed
            lower.contains("wait") || lower.contains("loading") || lower.contains("starting") || lower.contains("stopping") -> StatusAmber
            else -> StatusGrey
        }
    }

    private fun beginAsyncRootCheck(onCompleted: ((Boolean) -> Unit)? = null) {
        if (!rootCheckInProgress.compareAndSet(false, true)) {
            Log.i(TAG, "Root check already in progress")
            return
        }

        Log.i(TAG, "Starting async root check")
        thread(start = true, name = "aimbuddy-root-check") {
            val hasRoot = RootUtils.ensureRoot(this, timeoutSeconds = 90)
            runOnUiThread {
                rootCheckInProgress.set(false)
                rootAvailable.set(hasRoot)
                ImGuiGLSurface.nativeSetRootAvailable(hasRoot)

                if (hasRoot) {
                    Log.i(TAG, "Root available  -  initializing aimbot")
                    if (statusTextState != "Status: Init Failed") {
                        if (nativeInitAimbot()) {
                            Log.i(TAG, "Aimbot initialized successfully")
                            if (nativeGetTouchBackend() == 0) {
                                showAppToast("Root 已授予！瞄准辅助已开启。", false)
                            }
                        } else {
                            Log.w(TAG, "Aimbot init failed after root grant")
                            showAppToast("Root 已授予，但瞄准辅助初始化失败，请检查 /dev/uinput。", true)
                        }
                    }
                } else {
                    Log.w(TAG, "Root denied or unavailable")
                }
                onCompleted?.invoke(hasRoot)
            }
        }
    }
}
