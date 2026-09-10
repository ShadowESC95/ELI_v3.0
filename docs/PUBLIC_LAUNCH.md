# ELI v3 — public launch copy-paste

> **Current release: v3.0.112 (September 2026).** GPU pack chooser (Vulkan/CUDA) and RAM %
> budget on the load screen (v3.0.109–111); optional **ELI Mobile Companion** thin client
> (`clients/mobile/` — PWA or sideload APK, no Play Store required). Thread-aware web
> grounding, honest model identity, phatic fast path.

## Links (share these)

- **Repo:** https://github.com/ShadowESC95/ELI_v3.0
- **Release v3.0.112:** https://github.com/ShadowESC95/ELI_v3.0/releases/tag/v3.0.112
- **Mobile companion (optional):** [`clients/mobile/README.md`](../clients/mobile/README.md)
- **License:** PolyForm Internal Use (source-available, personal use — not OSI open source)

## Easiest install — Linux

```bash
# AppImage (recommended)
wget https://github.com/ShadowESC95/ELI_v3.0/releases/download/v3.0.112/ELI_v2-3.0.112-x86_64.AppImage
chmod +x ELI_v2-3.0.112-x86_64.AppImage
./ELI_v2-3.0.112-x86_64.AppImage
```

```bash
# Portable tarball (source + voices)
wget https://github.com/ShadowESC95/ELI_v3.0/releases/download/v3.0.112/ELI_v2-3.0.112-linux-portable.tar.gz
tar -xzf ELI_v2-3.0.112-linux-portable.tar.gz && cd ELI_v2-3.0.112-linux-portable
chmod +x ELI_Setup.sh && ./ELI_Setup.sh
```

**Requires:** Linux x86_64 (glibc). NVIDIA GPU recommended; AMD Vulkan and CPU-only supported.

**Phone / tablet (LAN):** after install, run `./scripts/eli_serve.sh --lan` (add `--https` for
mobile-browser mic). Optional native shell: build or sideload from `clients/mobile/` — still a
thin client; inference stays on this PC.

## Easiest install — Windows

1. Download `ELI-Setup-3.0.112.exe` or `ELI_v2-3.0.112-windows-x64.zip` from the release page
2. Install or extract → run **ELI**
3. Set your headset **Chat** mic as the default recording device if voice input is silent

## Easiest install — macOS (Apple Silicon)

1. Download `ELI_v2-3.0.112-macos-arm64.dmg`
2. Drag to Applications → right-click → Open (unsigned)
3. Grant mic access when prompted; AirPods work when set as system input

## Hacker News / Reddit one-liner

> **ELI v3.0.112.** Local-first AI assistant (~232k LOC, 227 capabilities, GGUF, PySide6 GUI, offline-by-default). GPU pack + RAM budget on load screen; optional mobile LAN companion. https://github.com/ShadowESC95/ELI_v3.0/releases/tag/v3.0.112

## What to say it is

ELI is a **local cognitive assistant**: chat, voice, memory, tools, and a desktop GUI — powered by GGUF models on your GPU/CPU. Not a ChatGPT wrapper; the full stack runs on your hardware.

## Honest limits (say these upfront)

- Best human-run-tested: **Linux + NVIDIA** (cross-platform mic auto-resolve, v2.3.44+)
- **Mobile companion** does not run the model on the phone — it connects to ELI on your PC over LAN
