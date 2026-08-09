package com.aimbuddy

import android.content.Context
import android.os.Build
import android.util.Log
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter

/**
 * CrashReporter - captures both Java (uncaught exception) and native (signal)
 * crashes to on-disk files so the cause can be surfaced to the user on the next
 * launch, without needing a debugger or adb access.
 *
 * - Java crashes are handled by Thread.setDefaultUncaughtExceptionHandler and
 *   written to crashlog.txt.
 * - Native crashes (SIGSEGV/SIGABRT/...) are written by a signal handler in the
 *   native library to native_crash.log (path pushed from Java via
 *   ImGuiGLSurface.nativeSetCrashLogPath).
 */
object CrashReporter {

    private const val FILE_JAVA = "crashlog.txt"
    private const val FILE_NATIVE = "native_crash.log"

    fun crashDir(context: Context): File {
        return context.getExternalFilesDir(null) ?: context.filesDir
    }

    fun install(context: Context) {
        val dir = crashDir(context)
        val nativePath = File(dir, FILE_NATIVE).absolutePath
        try {
            ImGuiGLSurface.nativeSetCrashLogPath(nativePath)
        } catch (t: Throwable) {
            // Native bridge not ready yet is fine; the handler still installs.
            Log.w(TAG, "nativeSetCrashLogPath failed: ${t.message}")
        }

        val defaultHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                val sw = StringWriter()
                sw.append("=== AimBuddy Crash Report ===\n")
                sw.append("Time : ${System.currentTimeMillis()}\n")
                sw.append("Android: ${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT})\n")
                sw.append("Device : ${Build.MANUFACTURER} ${Build.MODEL}\n")
                sw.append("Thread: ${thread.name}\n\n")
                throwable.printStackTrace(PrintWriter(sw))
                File(dir, FILE_JAVA).writeText(sw.toString())
            } catch (_: Throwable) {
                // Best-effort only.
            }
            defaultHandler?.uncaughtException(thread, throwable)
        }
    }

    /**
     * Returns the combined crash report (native + java) if one is pending from a
     * previous run, and clears the files. Returns null if there was no crash.
     */
    fun consumePendingCrash(context: Context): String? {
        val dir = crashDir(context)
        val javaFile = File(dir, FILE_JAVA)
        val nativeFile = File(dir, FILE_NATIVE)
        if (!javaFile.exists() && !nativeFile.exists()) return null

        val sb = StringBuilder()
        if (nativeFile.exists()) {
            sb.append("[NATIVE CRASH - signal captured by native handler]\n")
            sb.append(nativeFile.readText())
            sb.append("\n\n")
            nativeFile.delete()
        }
        if (javaFile.exists()) {
            sb.append("[JAVA/KOTLIN CRASH - uncaught exception]\n")
            sb.append(javaFile.readText())
            sb.append("\n\n")
            javaFile.delete()
        }
        return sb.toString().takeIf { it.isNotEmpty() }
    }

    private const val TAG = "CrashReporter"
}
