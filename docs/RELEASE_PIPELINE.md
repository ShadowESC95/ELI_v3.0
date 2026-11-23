# ELI v3 — Release Pipeline

Tag-triggered, three-OS release automation built on PyInstaller. Pushing a
version tag builds everything in parallel on GitHub Actions and attaches the
artifacts to the GitHub Release for that tag.

```
git tag v3.0.0
git push origin v3.0.0
```

produces on the Release page:

| Asset | Contents |
|---|---|
| `ELI-Setup-<v>.exe` | Windows installer (Inno Setup, per-user, no admin) |
| `ELI_v3-<v>-windows-x64.zip` | Windows portable — unzip, run `ELI\ELI.exe` |
| `ELI_v3-<v>-macos-arm64.dmg` | macOS Apple Silicon — drag ELI.app to Applications |
| `ELI_v3-<v>-x86_64.AppImage` | Linux — `chmod +x` and run (needs glibc 2.39 or newer; needs libfuse2, or `--appimage-extract-and-run`) |
| `ELI_v3.0-<v>-linux-portable.tar.gz` | classic source tarball incl. voices (previous-release format) |
| `ELI_v3-<v>-gpl-sources.tar.gz` | the exact source of the GPL packages the builds carry (piper-tts with espeak-ng, PyAutoGUI's helpers), listed in its `SOURCES.md`, and `LGPL_SOURCES.md` beside it: each LGPL package the builds carry (Qt for Python and the Qt inside it, zeroconf, python-xlib, ...), its version and the address of that version's source (`packaging/gpl_sources.py`) |
| `SHA256SUMS.txt` | checksums for all of the above |

All frozen bundles include the full Piper voice set (pulled at build time from
the project's `local-assets-v2.1` release) and a second `ELI-Server`
executable (console) for the phone/web server. GGUF chat/vision/image model
weights remain first-run downloads into the per-user `ELI_v3` root.

## Moving parts

| File | Role |
|---|---|
| `ELI.spec` | PyInstaller spec — one-dir build, all platforms. Data files come from `git ls-files` only (personal files/models can never leak). Version read from `pyproject.toml`. |
| `packaging/pyinstaller/eli_entry.py` | Frozen entry script → `eli.gui.app:main`. Also dispatches `ELI-Server`/`--server` → `api.server:main`; ELI's own commands (`--bundle`, `--bundles`, `--mcp-server`, `--mcp-config`, `--headless`, `--trust-agent`) → `eli/__main__.py`, as in a source tree; a python command line (`-c`, `-m`, a `.py` script) runs as python; and `--selftest` (real import-stack boot check CI runs on every built bundle; it fails when an action module under `eli/execution/effectors/` did not import, `effectors.failed()`). Keeps one window at a time with an OS file lock (`_gui_singleton_or_exit`), released by the system however the process ends. |
| `packaging/pyinstaller/rthook_eli_frozen_paths.py` | Runtime hook: pins `ELI_PROJECT_ROOT`/data/config/models to the per-user root — `%LOCALAPPDATA%\ELI_v3`, `~/Library/Application Support/ELI_v3`, `~/.local/share/ELI_v3` — the same place previous ELI releases used, so models/settings carry over on upgrade. `ELI_PROJECT_ROOT` env overrides. |
| `packaging/pyinstaller/gen_version_info.py` + `packaging/windows/version.rc.in` | Generates `build/version.rc` (Windows version resource) from `pyproject.toml`. |
| `packaging/windows/installer.iss` | Inno Setup installer for the frozen bundle (the older `ELI_Setup.iss` still serves the source-portable flow). |
| `packaging/macos/build-dmg.sh` | Ad-hoc signs `dist/ELI.app`, builds the `.dmg`. |
| `packaging/linux/build-appimage-pyinstaller.sh` | Wraps `dist/ELI` into an AppImage (pinned appimagetool 13). Writes the glibc floor into it and the launcher that checks it. |
| `packaging/linux/glibc_floor.py` | Reads the oldest glibc a built bundle starts on out of its binaries. |
| `packaging/gpl_sources.py` | Run in the Linux build: fetches the exact source of every GPL package the build installed (and what their builds compile in) and lists where each LGPL package's source is, for the release's `gpl-sources` archive; a source it cannot get stops the release. |
| `requirements-build.txt` | Pinned build tooling (PyInstaller etc.) for reproducible builds. |
| `.github/workflows/release.yml` | The pipeline: version guard → 3 parallel builds → Release upload. |

## The Linux build image decides who can run the download

Every Linux job in `release.yml` runs on **`ubuntu-24.04`**, by name. A Linux program only starts
where the system library (glibc) is at least as new as the one it was built against, so the
AppImage built there needs **glibc 2.39** or newer. `ubuntu-latest` is not a version: GitHub
repoints it to a newer Ubuntu from time to time, and the same workflow would then produce an
AppImage that no longer starts on the systems it runs on today, with nothing in the repository
to show for it.

- `LINUX_GLIBC_FLOOR` in `release.yml` declares the floor. The Linux job runs
  `packaging/linux/glibc_floor.py dist/ELI --max $LINUX_GLIBC_FLOOR`, which reads what every
  binary in the bundle asks for and fails the build if any asks for more.
- The AppImage ships the measured number (`usr/share/eli/min-glibc`). Its launcher compares it
  with the system and, on one too old, says so in a sentence (and a dialog when there is no
  terminal) instead of the loader's `version 'GLIBC_2.38' not found`.
- The GPU packs are loaded into the app and use the C++ runtime it ships, so they have to be
  built on the same image. A pack built on a newer one would fail to load in every app already
  installed.
- The release notes and `docs/CROSS_PLATFORM.md` state the floor.

To move the floor on purpose, change together: the image in the workflow, `LINUX_GLIBC_FLOOR`,
and the distributions named in `docs/CROSS_PLATFORM.md`. `tests/test_linux_build_baseline.py`
fails when they disagree or when a build job goes back to a `-latest` image. The test matrix in
`cross-platform-smoke.yml` stays on `ubuntu-latest` on purpose: that one should follow the newest
system.

## What is (deliberately) not bundled

* **GGUF models, embeddings, diffusion weights** — up to 100 GB; the app
  downloads what the user picks on first run.
* **Piper voices** — flagged `license_review_required` in
  `packaging/runtime_asset_manifest.json`; downloaded at runtime.
* **PyQt** — GPL; the frozen bundle is PySide6-only (excluded in `ELI.spec`).
* **Everything ELI needs to run its features is bundled.** Voice input and output, the
  window, the server, documents, vision, web search, wake words, the device drivers and
  yt-dlp are `pyproject.toml`'s dependencies, so every build installs them and fails if one
  cannot be installed. The extras are what is built for, or chosen by, the machine's GPU:
  `llm` (the inference engine) is in `REQUIRED_EXTRAS`; `image` (and, on Linux, `chatterbox`)
  in `OPTIONAL_EXTRAS`. `tests/claims/test_runtime_dependencies.py` fails when ELI imports a
  package that is not declared this way.
* **Optional extras without wheels for a platform** — each `OPTIONAL_EXTRAS`
  group in `release.yml` installs independently; a group with no wheels for
  that OS is skipped with a workflow warning and ELI degrades cleanly
  (same behaviour as a source install without that extra).

## Local builds

```
python3.12 -m venv .venv-build && source .venv-build/bin/activate   # the release builds on 3.12 (release.yml PYTHON_VERSION)
pip install -r requirements-build.txt
pip install ".[llm]"      # ELI's dependencies and the engine (install packaging/llama_source.py's build first); + optional extras you want bundled
pyinstaller --noconfirm --clean ELI.spec

# then, per platform:
iscc packaging\windows\installer.iss /DMyAppVersion=<version>   # Windows installer
bash packaging/macos/build-dmg.sh                               # macOS dmg
bash packaging/linux/build-appimage-pyinstaller.sh              # Linux AppImage
```

## Cutting a release

1. Bump `[project].version` in `pyproject.toml` (also
   `_DEFAULT_RELEASE_TAG` in `eli/kernel/self_upgrade.py` and the pin in
   `tests/test_pyproject_packaging.py`).
2. Commit, push, tag `v<version>`, push the tag. The `guard` job fails fast
   if the tag and `pyproject.toml` disagree.
3. Watch: `gh run watch` / Actions tab. The Release is created automatically
   with generated notes + artifacts.

`workflow_dispatch` runs the same builds without publishing a Release
(artifacts land on the workflow run) — use it to dry-run pipeline changes.

## Manual steps that remain (signing)

Unsigned builds work but trip OS trust prompts:

* **Windows**: SmartScreen shows "unknown publisher". To sign: buy an
  OV/EV code-signing certificate, then `signtool sign /fd SHA256 /a
  dist\ELI\ELI.exe` before ISCC and the produced `ELI-Setup-*.exe` after
  (store the cert as a GitHub secret and add a signing step to
  `release.yml`).
* **macOS**: the .app is ad-hoc signed only; Gatekeeper requires
  right-click → Open on first launch. Proper distribution needs an Apple
  Developer ID Application certificate + `codesign --options runtime`, then
  `xcrun notarytool submit … --wait` and `xcrun stapler staple` on the dmg.
* **Linux**: no signing required; verify via `SHA256SUMS.txt`.

## The installers are run, not only the packages

`installer-smoke.yml` runs `install.sh --cpu-only --yes --no-model` on Ubuntu 22.04, Ubuntu 24.04
and macOS, and `install.ps1 -CpuOnly -Yes -NoModel` on Windows, on a clean runner, whenever an
installer, a requirement file or the environment script changes, and once a week (a new Python or
a new wheel upstream can break an installer with no commit here). It then checks the environment
holds ELI and what it cannot run without (`scripts/eli_env.py verify --strict`: also no package
left behind that was built for another version of one installed), starts the window toolkit and
starts the server. Not covered there: a GPU build, a model load, and the packaged apps, which
`release.yml` builds and starts.

## The whole suite on a runner

`cross-platform-smoke.yml` runs a chosen set of test files on three systems. `full-suite.yml`
runs every test on one clean Linux runner (`ubuntu-24.04`, Python 3.12, no display, CPU only) on
each push to `main` that touches more than documents, and on demand. It does not gate a release
yet: it has to be seen passing on a runner first.

On Linux the release removes GTK and GLib's I/O library from the bundle before it is started
and packed (`packaging/linux/trim_bundle.py`), and fails if either plug-in that brings them in
is still there.
