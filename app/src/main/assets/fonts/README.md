# ImGui font assets (optional)

This folder is empty on purpose. CJK rendering does not require any file
here on a modern Android device.

## How CJK loading works

At startup the overlay tries to load a CJK font in this order:

1. **System fonts** (works on every modern Android with no APK changes):
   - `/system/fonts/NotoSansCJK-Regular.ttc`
   - `/system/fonts/NotoSerifCJK-Regular.ttc`
   - `/system/fonts/DroidSansFallback.ttf`
   - `/system/fonts/NotoSansSC-Regular.otf`
   - `/system/fonts/MiSans-Regular.ttf` (Xiaomi)
   - `/system/fonts/HarmonyOS_Sans_SC.ttf` (HarmonyOS)
   - `/product/fonts/NotoSansCJK-Regular.ttc`
2. **Bundled fallback in this folder**:
   - `fonts/cjk.ttf`
   - `fonts/cjk.otf`
   - `fonts/NotoSansSC-Regular.ttf`
   - `fonts/NotoSansCJKsc-Regular.otf`

The first match wins. Once a font is loaded the glyphs are merged on top
of the default ImGui font, so English stays crisp and Chinese slots in
only where the base font has no glyph.

## When you would drop a font here

Only if your device is missing the system-bundled CJK font, which is
extremely rare on Android 10+. To check:

```bash
adb shell ls /system/fonts | grep -i cjk
```

If the grep returns nothing, download `NotoSansSC-Regular.otf` from
https://github.com/notofonts/noto-cjk, rename it to `cjk.ttf` and drop
it here. Rebuild the APK.

## License

If you bundle a font, only ship one you have the right to redistribute.
Noto Sans SC is OFL 1.1.
