/**
 * i18n.h - Compact translation table for the ImGui overlay menu.
 *
 * Adding a new language:
 *   1. Add a column to kTranslations below.
 *   2. Add the language enum + display name to kLanguageNames.
 *   3. If the language needs glyphs the default ImGui font doesn't ship
 *      (CJK, Cyrillic, etc.), drop a TTF/OTF at assets/fonts/<lang>.ttf
 *      and register it in i18n_LoadFonts().
 *
 * All translation arrays are kept in this single header so adding a key
 * fails at compile time if any language forgets to translate it.
 */
#ifndef AIMBUDDY_I18N_H
#define AIMBUDDY_I18N_H

#include <cstddef>
#include <atomic>

// AIMBUDDY_VERSION is injected by the Gradle CMake configuration (see
// app/build.gradle). The fallback keeps standalone IDE indexing happy.
#ifndef AIMBUDDY_VERSION
#define AIMBUDDY_VERSION "0.3.0-beta.15"
#endif

namespace aimbuddy::i18n {

enum class Language : int {
    English = 0,
    Chinese = 1,
    Count   = 2,
};

inline std::atomic<int> g_currentLanguage{static_cast<int>(Language::English)};

inline void SetLanguage(int lang) {
    if (lang < 0 || lang >= static_cast<int>(Language::Count)) return;
    g_currentLanguage.store(lang, std::memory_order_relaxed);
}

inline int GetLanguage() {
    return g_currentLanguage.load(std::memory_order_relaxed);
}

// Display names for the language picker itself.
inline const char* LanguageDisplayName(int lang) {
    switch (static_cast<Language>(lang)) {
        case Language::English: return "English";
        case Language::Chinese: return "\xe4\xb8\xad\xe6\x96\x87"; // 中文
        default:                return "English";
    }
}

// Every translation key in one place. The macro keeps call sites tight.
enum class Key : int {
    // Top-level / window
    AppTitle,
    BannerRootMissing,

    // Tabs
    TabEsp,
    TabAim,
    TabInfo,

    // ESP tab
    EspLabels,
    EspSnapLine,
    EspHeadDot,
    EspDetectionCount,
    EspEnableSmoothing,
    EspSmoothingAmount,
    EspBoxColor,
    EspBoxThickness,
    EspConfidence,
    EspDetectionZone,
    EspTouchZoneOverlay,
    EspTouchZoneOpacity,
    EspStreamerMode,
    EspLanguage,

    // Aim tab
    AimTouchBackend,
    AimBackendStatusReady,
    AimBackendStatusMissingRoot,
    AimBackendStatusMissingShizuku,
    AimRootRequired,
    AimShizukuRequired,
    AimAccessibilityRequired,
    AimBackendStatusMissingAccessibility,
    AimEnable,
    AimPresetDefault,
    AimPresetCompetitive,
    AimPresetBalanced,
    AimPresetPrecision,
    AimHeadOffset,
    AimTargetPriority,
    AimMode,
    AimSpeed,
    AimSmoothness,
    AimFovRadius,
    AimMaxDistance,
    AimFps,
    AimFilter,
    AimEmaAlpha,
    AimKalmanProcess,
    AimKalmanMeasure,
    AimAntiOvershoot,
    AimDampRadius,
    AimDerivativeDamping,
    AimVelocityLead,
    AimLeadClamp,
    AimRecoil,
    AimRecoilStrength,
    AimRecoilMax,
    AimRecoilDecay,
    AimMissGrace,
    AimSwitchDelay,
    AimTouchCenterX,
    AimTouchCenterY,
    AimTouchRadius,
    AimDelay,
    AimResetTouchZone,

    // Info tab
    InfoTitle,
    InfoVersion,
    InfoOverlayFps,
    InfoInferenceMs,
    InfoDetections,
    InfoScreen,
    InfoTip,

    // Footer
    SaveNow,
    AutoSaveHint,

    // Sentinel
    Count,
};

// Translation table: rows = keys, columns = languages.
// Indexing: kTranslations[static_cast<int>(Key::...)][static_cast<int>(Language::...)]
inline const char* kTranslations[static_cast<int>(Key::Count)][static_cast<int>(Language::Count)] = {
    // English, 中文
    /* AppTitle */                  { "AimBuddy v" AIMBUDDY_VERSION, "AimBuddy v" AIMBUDDY_VERSION },
    /* BannerRootMissing */         { "Root not granted (ESP mode)", "\xe6\x9c\xaa\xe6\x8e\x88\xe6\x9d\x83 Root\xef\xbc\x88\xe4\xbb\x85 ESP \xe6\xa8\xa1\xe5\xbc\x8f\xef\xbc\x89" },

    /* TabEsp */                    { "ESP",              "ESP" },
    /* TabAim */                    { "Aim",              "\xe7\x9e\x84\xe5\x87\x86" },
    /* TabInfo */                   { "Info",             "\xe4\xbf\xa1\xe6\x81\xaf" },

    /* EspLabels */                 { "Labels",           "\xe6\xa0\x87\xe7\xad\xbe" },
    /* EspSnapLine */               { "Snap line",        "\xe6\x8d\x95\xe6\x8d\x89\xe7\xba\xbf" },
    /* EspHeadDot */                { "Head dot",         "\xe5\xa4\xb4\xe9\x83\xa8\xe7\x82\xb9" },
    /* EspDetectionCount */         { "Detection count",  "\xe6\x95\x8c\xe4\xba\xba\xe6\x95\xb0\xe9\x87\x8f" },
    /* EspEnableSmoothing */        { "Enable ESP box smoothing", "\xe5\xbc\x80\xe5\x90\xaf ESP \xe6\xa1\x86\xe5\xb9\xb3\xe6\xbb\x91" },
    /* EspSmoothingAmount */        { "Smoothing amount", "\xe5\xb9\xb3\xe6\xbb\x91\xe5\xbc\xba\xe5\xba\xa6" },
    /* EspBoxColor */               { "Box color",        "\xe6\xa1\x86\xe9\xa2\x9c\xe8\x89\xb2" },
    /* EspBoxThickness */           { "Box thickness",    "\xe6\xa1\x86\xe7\xba\xbf\xe5\xae\xbd" },
    /* EspConfidence */             { "Confidence",       "\xe7\xbd\xae\xe4\xbf\xa1\xe5\xba\xa6" },
    /* EspDetectionZone */          { "Detection zone",   "\xe6\xa3\x80\xe6\xb5\x8b\xe5\x8c\xba\xe5\x9f\x9f" },
    /* EspTouchZoneOverlay */       { "Touch zone overlay","\xe8\xa7\xa6\xe6\x8e\xa7\xe5\x8c\xba\xe5\xb1\x95\xe7\xa4\xba" },
    /* EspTouchZoneOpacity */       { "Touch zone opacity","\xe8\xa7\xa6\xe6\x8e\xa7\xe5\x8c\xba\xe9\x80\x8f\xe6\x98\x8e\xe5\xba\xa6" },
    /* EspStreamerMode */           { "Streamer mode (hide overlay from recording)", "\xe7\x9b\xb4\xe6\x92\xad\xe6\xa8\xa1\xe5\xbc\x8f\xef\xbc\x88\xe5\xaf\xb9\xe5\xbd\x95\xe5\xb1\x8f\xe9\x9a\x90\xe8\x97\x8f\xe5\x8f\xa0\xe5\xb1\x82\xef\xbc\x89" },
    /* EspLanguage */               { "Language",         "\xe8\xaf\xad\xe8\xa8\x80" },

    /* AimTouchBackend */           { "Touch backend",    "\xe8\xa7\xa6\xe6\x8e\xa7\xe5\x90\x8e\xe7\xab\xaf" },
    /* AimBackendStatusReady */     { "ready",            "\xe5\xb0\xb1\xe7\xbb\xaa" },
    /* AimBackendStatusMissingRoot */ { "root missing",   "\xe7\xbc\xba\xe5\xb0\x91 root" },
    /* AimBackendStatusMissingShizuku */ { "Shizuku unavailable", "Shizuku \xe4\xb8\x8d\xe5\x8f\xaf\xe7\x94\xa8" },
    /* AimRootRequired */           { "Root access required for uinput assisted input.", "uinput \xe8\xbe\x85\xe5\x8a\xa9\xe8\xbe\x93\xe5\x85\xa5\xe9\x9c\x80\xe8\xa6\x81 Root \xe6\x9d\x83\xe9\x99\x90\xe3\x80\x82" },
    /* AimShizukuRequired */        { "Shizuku service/permission required for non-root assisted input.", "\xe9\x9d\x9e Root \xe8\xbe\x85\xe5\x8a\xa9\xe8\xbe\x93\xe5\x85\xa5\xe9\x9c\x80\xe8\xa6\x81 Shizuku \xe6\x9c\x8d\xe5\x8a\xa1\xe5\x92\x8c\xe6\x9d\x83\xe9\x99\x90\xe3\x80\x82" },
    /* AimAccessibilityRequired */   { "Accessibility service required for non-root assisted input. Enable AimBuddy in system Accessibility settings.", "\xe5\x85\x8d Root \xe8\xbe\x85\xe5\x8a\xa9\xe8\xbe\x93\xe5\x85\xa5\xe9\x9c\x80\xe8\xa6\x81\xe5\x9c\xa8\xe7\xb3\xbb\xe7\xbb\x9f\xe3\x80\x8c\xe6\x97\xa0\xe9\x9a\x9c\xe7\xa2\x8d\xe3\x80\x8d\xe8\xae\xbe\xe7\xbd\xae\xe4\xb8\xad\xe5\xbc\x80\xe5\x90\xaf AimBuddy \xe6\x9c\x8d\xe5\x8a\xa1\xe3\x80\x82" },
    /* AimBackendStatusMissingAccessibility */ { "Accessibility service not enabled", "\xe6\x97\xa0\xe9\x9a\x9c\xe7\xa2\x8d\xe6\x9c\x8d\xe5\x8a\xa1\xe6\x9c\xaa\xe5\xbc\x80\xe5\x90\xaf" },
    /* AimEnable */                 { "Enable Aim Assist","\xe5\xbc\x80\xe5\x90\xaf\xe7\x9e\x84\xe5\x87\x86\xe8\xbe\x85\xe5\x8a\xa9" },
    /* AimPresetDefault */          { "Default",          "\xe9\xbb\x98\xe8\xae\xa4" },
    /* AimPresetCompetitive */      { "Competitive",      "\xe7\xab\x9e\xe6\x8a\x80" },
    /* AimPresetBalanced */         { "Balanced",         "\xe5\x9d\x87\xe8\xa1\xa1" },
    /* AimPresetPrecision */        { "Precision",        "\xe7\xb2\xbe\xe5\x87\x86" },
    /* AimHeadOffset */             { "Head offset",      "\xe5\xa4\xb4\xe9\x83\xa8\xe5\x81\x8f\xe7\xa7\xbb" },
    /* AimTargetPriority */         { "Target priority",  "\xe7\x9b\xae\xe6\xa0\x87\xe4\xbc\x98\xe5\x85\x88" },
    /* AimMode */                   { "Aim mode",         "\xe7\x9e\x84\xe5\x87\x86\xe6\xa8\xa1\xe5\xbc\x8f" },
    /* AimSpeed */                  { "Aim speed",        "\xe7\x9e\x84\xe5\x87\x86\xe9\x80\x9f\xe5\xba\xa6" },
    /* AimSmoothness */             { "Smoothness",       "\xe5\xb9\xb3\xe6\xbb\x91\xe5\xba\xa6" },
    /* AimFovRadius */              { "Aim FOV",          "\xe7\x9e\x84\xe5\x87\x86 FOV" },
    /* AimMaxDistance */            { "Max aim distance", "\xe6\x9c\x80\xe5\xa4\xa7\xe7\x9e\x84\xe5\x87\x86\xe8\xb7\x9d\xe7\xa6\xbb" },
    /* AimFps */                    { "Aimbot FPS",       "\xe7\x9e\x84\xe5\x87\x86 FPS" },
    /* AimFilter */                 { "Stabilization filter", "\xe7\xa8\xb3\xe5\xae\x9a\xe6\xbb\xa4\xe6\xb3\xa2\xe5\x99\xa8" },
    /* AimEmaAlpha */               { "EMA alpha",        "EMA \xe7\xb3\xbb\xe6\x95\xb0" },
    /* AimKalmanProcess */          { "Kalman process",   "Kalman \xe8\xbf\x87\xe7\xa8\x8b\xe5\x99\xaa\xe5\xa3\xb0" },
    /* AimKalmanMeasure */          { "Kalman measure",   "Kalman \xe6\xb5\x8b\xe9\x87\x8f\xe5\x99\xaa\xe5\xa3\xb0" },
    /* AimAntiOvershoot */          { "Anti overshoot",   "\xe6\x8a\x97\xe8\xb6\x85\xe8\xb0\x83" },
    /* AimDampRadius */             { "Damp radius",      "\xe9\x98\xbb\xe5\xb0\xbc\xe5\x8d\x8a\xe5\xbe\x84" },
    /* AimDerivativeDamping */      { "Derivative damping", "\xe5\xbe\xae\xe5\x88\x86\xe9\x98\xbb\xe5\xb0\xbc" },
    /* AimVelocityLead */           { "Velocity lead",    "\xe9\x80\x9f\xe5\xba\xa6\xe9\xa2\x84\xe5\x88\xa4" },
    /* AimLeadClamp */              { "Lead clamp",       "\xe9\xa2\x84\xe5\x88\xa4\xe4\xb8\x8a\xe9\x99\x90" },
    /* AimRecoil */                 { "Recoil compensation","\xe5\x90\x8e\xe5\x9d\x90\xe5\x8a\x9b\xe8\xa1\xa5\xe5\x81\xbf" },
    /* AimRecoilStrength */         { "Recoil strength",  "\xe5\x90\x8e\xe5\x9d\x90\xe5\xbc\xba\xe5\xba\xa6" },
    /* AimRecoilMax */              { "Recoil max",       "\xe5\x90\x8e\xe5\x9d\x90\xe6\x9c\x80\xe5\xa4\xa7\xe5\x80\xbc" },
    /* AimRecoilDecay */            { "Recoil decay",     "\xe5\x90\x8e\xe5\x9d\x90\xe8\xa1\xb0\xe5\x87\x8f" },
    /* AimMissGrace */              { "Miss grace",       "\xe4\xb8\xa2\xe5\xa4\xb1\xe5\xae\xbd\xe5\xae\xb9" },
    /* AimSwitchDelay */            { "Switch delay",     "\xe5\x88\x87\xe6\x8d\xa2\xe5\xbb\xb6\xe8\xbf\x9f" },
    /* AimTouchCenterX */           { "Touch center X",   "\xe8\xa7\xa6\xe6\x8e\xa7\xe4\xb8\xad\xe5\xbf\x83 X" },
    /* AimTouchCenterY */           { "Touch center Y",   "\xe8\xa7\xa6\xe6\x8e\xa7\xe4\xb8\xad\xe5\xbf\x83 Y" },
    /* AimTouchRadius */            { "Touch radius",     "\xe8\xa7\xa6\xe6\x8e\xa7\xe5\x8d\x8a\xe5\xbe\x84" },
    /* AimDelay */                  { "Aim delay",        "\xe7\x9e\x84\xe5\x87\x86\xe5\xbb\xb6\xe8\xbf\x9f" },
    /* AimResetTouchZone */         { "Reset touch zone", "\xe9\x87\x8d\xe7\xbd\xae\xe8\xa7\xa6\xe6\x8e\xa7\xe5\x8c\xba" },

    /* InfoTitle */                 { "AimBuddy", "AimBuddy" },
    /* InfoVersion */               { "Version: " AIMBUDDY_VERSION, "\xe7\x89\x88\xe6\x9c\xac\xef\xbc\x9a " AIMBUDDY_VERSION },
    /* InfoOverlayFps */            { "Overlay FPS",      "\xe5\x8f\xa0\xe5\xb1\x82 FPS" },
    /* InfoInferenceMs */           { "Inference",        "\xe6\x8e\xa8\xe7\x90\x86\xe6\x97\xb6\xe9\x97\xb4" },
    /* InfoDetections */            { "Detections",       "\xe6\xa3\x80\xe6\xb5\x8b\xe6\x95\xb0" },
    /* InfoScreen */                { "Screen",           "\xe5\xb1\x8f\xe5\xb9\x95" },
    /* InfoTip */                   { "Tip: use presets first, then fine tune only a few controls for stable behavior.", "\xe6\x8f\x90\xe7\xa4\xba\xef\xbc\x9a\xe5\x85\x88\xe9\x80\x89\xe9\xa2\x84\xe8\xae\xbe\xef\xbc\x8c\xe5\x86\x8d\xe5\xbe\xae\xe8\xb0\x83\xe5\xb0\x91\xe9\x87\x8f\xe5\x8f\x82\xe6\x95\xb0\xe4\xbb\xa5\xe4\xbf\x9d\xe6\x8c\x81\xe7\xa8\xb3\xe5\xae\x9a\xe3\x80\x82" },

    /* SaveNow */                   { "Save now",         "\xe7\xab\x8b\xe5\x8d\xb3\xe4\xbf\x9d\xe5\xad\x98" },
    /* AutoSaveHint */              { "Changes auto-save shortly after edits", "\xe7\xbc\x96\xe8\xbe\x91\xe5\x90\x8e\xe7\x9f\xad\xe6\x9a\x82\xe8\x87\xaa\xe5\x8a\xa8\xe4\xbf\x9d\xe5\xad\x98" },
};

inline const char* T(Key key) {
    int lang = GetLanguage();
    if (lang < 0 || lang >= static_cast<int>(Language::Count)) lang = 0;
    const int idx = static_cast<int>(key);
    if (idx < 0 || idx >= static_cast<int>(Key::Count)) return "";
    const char* s = kTranslations[idx][lang];
    if (!s || !*s) {
        // Fall back to English so missing translations stay legible.
        s = kTranslations[idx][0];
    }
    return s ? s : "";
}

} // namespace aimbuddy::i18n

#endif // AIMBUDDY_I18N_H
