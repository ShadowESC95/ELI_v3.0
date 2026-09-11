# ELI v3 — cutting a download-and-run release

> Maintainer: **ShadowESC95/ELI_v3.0** on GitHub

Current semver: **`pyproject.toml`** → **3.0.114** (tag **`v3.0.114`**).

**One installer:** `./ELI_Setup.sh` (or `INSTALL_ELI.sh`) → `scripts/eli_setup.sh` → GUI wizard → `install.sh` / `install.ps1` once, hardware-aware via `eli.setup.hardware_policy` (NVIDIA/AMD/Arc/Metal → GPU; Iris Xe / ≤8 GB → CPU). Legacy aliases `install_eli.sh` / `eli_one_click_setup.sh` only redirect there.

## 1. Build portable Linux package

```bash
bash scripts/package_desktop_app.sh
# Output: dist/app_packages/ELI_v3-3.0.114-linux-portable.tar.gz
```

Optional full bundle (local models — very large):

```bash
bash scripts/package_desktop_app.sh --with-assets
```

## 2. Publish GitHub Release

1. [New release](https://github.com/ShadowESC95/ELI_v3.0/releases/new)
2. Tag: `v3.0.114` (semver matches `pyproject.toml`)
3. Attach:
   - `ELI_v3-3.0.114-linux-portable.tar.gz`
   - `.sha256` sidecar

## 3. What users do

```bash
tar -xzf ELI_v3-3.0.114-linux-portable.tar.gz
cd ELI_v3-3.0.114-linux-portable
./ELI_Setup.sh
./RUN_ELI.sh
```

Activate a paid license when prompted (v3 only). **Tested path:** Linux x86_64 + NVIDIA.
