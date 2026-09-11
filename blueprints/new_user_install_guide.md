# ELI v3.0 — New User Installation Guide

> **Updated for v3.0.114 (September 2026).** One hardware-aware GUI wizard for every OS.
> Primary install: [GitHub Releases](https://github.com/ShadowESC95/ELI_v3.0/releases/tag/v3.0.114).
> Full plain-English map: **`first time Installation/INSTALLATION_GUIDE.md`**.

**Version:** 3.0.114  
**Audience:** First-time users on Linux, Windows, or macOS  

---

## 1. What you are installing

ELI is a **local-first AI assistant** (chat, memory, GUI, optional phone web UI, voice). Offline by default — no cloud account.

**Hardware note:** Best on **Linux + NVIDIA**. Windows / macOS / AMD / Intel Arc / Apple Silicon are supported. **Iris Xe / 8 GB** → use AppImage or `./ELI_Setup.sh` (CPU policy), not interactive Vulkan `install.sh`.

---

## 2. Pick a package

| Package | OS | First click |
|---------|-----|-------------|
| AppImage | Linux | `chmod +x` → run |
| `ELI-Setup-*.exe` | Windows | Run Setup |
| Frozen zip | Windows | `ELI\ELI.exe` |
| `.dmg` | macOS | Drag to Applications |
| Portable `.tar.gz` | Linux | `./ELI_Setup.sh` |
| Git clone | All | `./scripts/eli_setup.sh` |

No release includes a **chat model**. Wizard downloads chat + **nomic** + **Piper/Whisper**.

---

## 3. Linux portable

```bash
wget https://github.com/ShadowESC95/ELI_v3.0/releases/download/v3.0.114/ELI_v3-3.0.114-linux-portable.tar.gz
tar -xzf ELI_v3-3.0.114-linux-portable.tar.gz
cd ELI_v3-3.0.114-linux-portable
chmod +x ELI_Setup.sh && ./ELI_Setup.sh
./RUN_ELI.sh
```

Wizard applies `hardware_policy`, runs `install.sh` **once**, then assets.

## 4. Linux AppImage

```bash
chmod +x ELI_v3-3.0.114-x86_64.AppImage
./ELI_v3-3.0.114-x86_64.AppImage
```

## 5. Windows

Run **`ELI-Setup-3.0.114.exe`**. Desktop shortcut is **ELI only** (Server in Start Menu). v3.0.114 blocks a second GUI instance.

Source/scripts zip: **`ELI_Setup.bat`**.

## 6. macOS

Open the `.dmg`, drag to Applications, launch (Metal).

## 7. After setup

| Goal | Action |
|------|--------|
| Chat | Launch GUI |
| Phone | Settings → Web Server (token URL). **Home** tab = MQTT devices, **not** Home Assistant |
| Missing nomic/voice | Wizard Retry / Fetch, or `python -m eli.core.model_download --aux` |

## 8. Model size vs VRAM (NVIDIA)

| VRAM | Suggested class |
|------|-----------------|
| 8 GB | ~7B Q4 |
| 12 GB | ~14B Q4 or 7B higher quant |
| 24 GB+ | Larger quantised models |

## 9. Do not use as separate installers

`scripts/install_eli.sh` and `scripts/eli_one_click_setup.sh` only **redirect** to `eli_setup.sh`.
