# ELI v3 — cutting a download-and-run release

> Maintainer: **ShadowESC95/ELI_v3.0** on GitHub  
> **New users:** **`first time Installation/INSTALLATION_GUIDE.md`**

Current semver: **`pyproject.toml`** → **3.0.132** (tag **`v3.0.132`**).

## 1. Build packages

**Local Linux portable:**

```bash
bash scripts/package_desktop_app.sh
# Output: dist/app_packages/ELI_v3-3.0.132-linux-portable.tar.gz
```

Optional full bundle (local models — very large):

```bash
bash scripts/package_desktop_app.sh --with-assets
```

## 2. What each asset is for

| Asset | Use |
|---|---|
| `ELI-Setup-<v>.exe` | Windows one-click (frozen). Desktop = ELI only; GUI singleton. |
| `ELI_v3-<v>-windows-x64.zip` | Unzip → `ELI\ELI.exe` |
| `ELI_v3-<v>-x86_64.AppImage` | Linux double-click (frozen) |
| `ELI_v3-<v>-linux-portable.tar.gz` | `./ELI_Setup.sh` → hardware-aware wizard |
| `ELI_v3-<v>-macos-arm64.dmg` | macOS Apple Silicon |

**One installer:** `./ELI_Setup.sh` / `ELI_Setup.bat` / `ELI-Setup-*.exe` → GUI wizard → `eli.setup.hardware_policy` → one `install.sh`/`install.ps1`. Core stages (env, DB, nomic, voice, chat GGUF) hard-fail until present. OS tools (mpv, tesseract, …) best-effort.

## 3. Tag and publish

```bash
git tag v3.0.132 && git push origin v3.0.132
```

Activate a paid license when prompted (v3 only). **Tested path:** Linux x86_64 + NVIDIA.
