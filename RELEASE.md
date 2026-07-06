# ELI v3 — cutting a download-and-run release

> Maintainer: **ShadowESC95/ELI_v3.0** on GitHub

## 1. Build portable Linux package

```bash
bash scripts/package_desktop_app.sh
# Output: dist/app_packages/ELI_v3-3.0.8-linux-portable.tar.gz
```

Optional full bundle (local models — very large):

```bash
bash scripts/package_desktop_app.sh --with-assets
```

## 2. Publish GitHub Release

1. [New release](https://github.com/ShadowESC95/ELI_v3.0/releases/new)
2. Tag: `v3.0.8` (semver matches `pyproject.toml`)
3. Attach:
   - `ELI_v3-3.0.8-linux-portable.tar.gz`
   - `.sha256` sidecar

## 3. What users do

```bash
tar -xzf ELI_v3-3.0.8-linux-portable.tar.gz
cd ELI_v3-3.0.8-linux-portable
./ELI_Setup.sh
./RUN_ELI.sh
```

Activate a paid license when prompted (v3 only). **Tested path:** Linux x86_64 + NVIDIA.
