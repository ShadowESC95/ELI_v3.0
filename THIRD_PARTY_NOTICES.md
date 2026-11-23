# Third-Party Notices — ELI v3

ELI (© 2026 Jason Fitzgibbon Bridgeman, GitHub: ShadowESC95, jaybridgeman0095@gmail.com) is proprietary software licensed under
the **ELI End User Licence Agreement** (see `LICENSE` and `COMMERCIAL_EULA.md`). This file lists
**third-party** components used at runtime or shipped in release packages,
with their licenses. It accompanies portable tarballs, wheels, and source
checkouts.

> **LGPL note:** PySide6 and some other components are LGPL-licensed. If you
> redistribute ELI binaries (only the copyright holder may do so under the EULA),
> you must comply with LGPL source/relink requirements for those components; the
> directions to their source are in *Source code of the LGPL components* below.
>
> **GPL note:** ELI's voice is Kokoro-82M (Apache-2.0), with misaki (Apache-2.0) for its
> phonemes, installed without misaki's espeak-ng fallback: neither **piper-tts** nor
> **espeak-ng** (both GPL-3.0-or-later, ELI's voice up to 3.0.172) is in v3 any more.
> Through PyAutoGUI (BSD-3-Clause) the release builds carry **MouseInfo**
> (GPLv3+ per its package metadata), **PyMsgBox** (BSD licence text, GPLv3+ classifier
> in its metadata) and, on Linux, **python3-xlib** (GPLv2). Those components are under
> the GPL in every copy; each release attaches their source (*Source code of the GPL
> components* below).

---

## Source code of the GPL components

The release builds carry these packages under the GPL, at the versions in
`requirements.lock.txt`: PyAutoGUI's helpers **MouseInfo**, **PyMsgBox** and, on Linux,
**python3-xlib**. Their exact source, at the versions a release carries, is attached to that
release on GitHub as **`ELI_v3-<version>-gpl-sources.tar.gz`**, next to the binaries: each
package's source distribution from PyPI (checked against PyPI's digest), the project's
repository at the version's tag where the sdist leaves out the build files of a compiled
part, and what a package's build fetches, listed with where each came from in the
`SOURCES.md` inside (which says so when there is no GPL package at all).
`packaging/gpl_sources.py` makes it in the release workflow, from the packages the build
installed; it may also hold the source of build tools under the GPL (PyInstaller), whose
licences carry no such obligation for the programs they build. Upstream: MouseInfo
<https://github.com/asweigart/mouseinfo>, PyMsgBox <https://github.com/asweigart/pymsgbox>,
python3-xlib <https://pypi.org/project/python3-xlib/>.

---

## Source code of the LGPL components

The release builds also carry packages under the LGPL: **PySide6** with
**PySide6_Essentials**, **PySide6_Addons** and **shiboken6** (Qt for Python), the **Qt**
libraries inside those wheels, **zeroconf**, **python-xlib**, **num2words** (misaki's numbers
as words, LGPL-2.1), and any other LGPL package the build installs. Their source is not attached. GPL-3.0 section 6(d), which LGPL-3.0 section 4
applies, lets it be offered from another server when clear directions next to the object
code say where: **`LGPL_SOURCES.md`**, inside `ELI_v3-<version>-gpl-sources.tar.gz` next to
the binaries on each release, names each of those packages, the version the builds carry
and the address of that version's source (its sdist on PyPI with its sha256, or the source
archive its project publishes, and Qt's source at the Qt version the wheels carry).
`packaging/gpl_sources.py` writes it in the release workflow from the packages the build
installed; an address PyPI does not list is used only once it answers, and a build with a
package whose source it cannot find is not released.

---

## Core Python dependencies (`pip install -e .`; `.[full]` adds the inference engine and image generation)

| Component | License | Notes |
|---|---|---|
| **PySide6** (Qt GUI) | LGPL-3.0 | Canonical Qt binding for packaged ELI |
| **llama-cpp-python** | MIT | GGUF inference; built with llama.cpp (MIT) v0.8.0 by `packaging/llama_source.py` |
| **faiss-cpu** | MIT | Vector memory index |
| **faster-whisper** | MIT | Local speech-to-text |
| **onnxruntime** | MIT | Runs the Kokoro voice (text to speech) |
| **misaki** | Apache-2.0 | Kokoro's text to phonemes; installed **without** its `[en]` extra (espeakng-loader, phonemizer-fork: GPL) |
| **spaCy** (+ thinc, srsly, cymem, preshed, murmurhash, blis, wasabi, weasel, confection, catalogue) | MIT / BSD | misaki's English tagger |
| **num2words** | LGPL-2.1 | misaki's numbers as words (pure Python; see the LGPL section) |
| **python-crfsuite** | MIT (CRFsuite: BSD) | Guesses words misaki's dictionaries lack (with gruut's English model, below) |
| **FastAPI** / **Starlette** / **uvicorn** | MIT | Web server |
| **paho-mqtt** | EPL-2.0 / EDL-1.0 | MQTT device server |
| **zeroconf** | LGPL-2.1+ | mDNS discovery |
| **opencv-python** | Apache-2.0 | Vision / gaze |
| **playwright** | Apache-2.0 | Browser automation plugin |
| **bleak** | MIT | Bluetooth discovery |
| **openwakeword** | Apache-2.0 | Wake word |
| **pychromecast**, **androidtv**, **pyatv** | MIT | Device drivers (Chromecast, Fire TV / Android TV, Apple TV / AirPlay) |
| **adb-shell** | Apache-2.0 | Fire TV / Android TV pairing |
| **yara-python** | Apache-2.0 | The plugin scanner's YARA engine (the user's own rules) |
| **numpy**, **Pillow**, **requests**, **pydantic**, etc. | BSD/MIT/Apache-2.0 | See installed package metadata |

Full pinned versions: `requirements-full.txt`, `requirements.lock.txt`.

---

## Optional extras (built for, or chosen by, the machine's GPU)

| Component | License | Used for |
|---|---|---|
| **diffusers**, **accelerate**, **safetensors** | Apache-2.0 | Local image generation |
| **qwen-tts** (`voice-gpu`) + PyTorch | Apache-2.0 / BSD | The GPU voice: Qwen3-TTS natural voices and voice cloning. Not in the release builds. Its closure adds transformers, accelerate, gradio, librosa, torchaudio, sox (Python wrapper, BSD), einops, all permissive, and **soxr** (LGPL-2.1) |

---

## Native / system dependencies (not bundled)

Installed by `install.sh` or the OS package manager as needed:

- **CUDA / ROCm / Metal** — GPU drivers (vendor terms)
- **ffmpeg**, **pandoc**, **LibreOffice** — system packages (various OSS licenses)
- The operating system's own speech voices (`sys:` voices through pyttsx3, MPL-2.0: SAPI on
  Windows, NSSpeech on macOS, a system-installed espeak on Linux) are used only when one is
  chosen; ELI ships none of them

---

## Do not ship in official builds

| Component | License | Reason |
|---|---|---|
| **PyQt6 / PyQt5** | GPL-3.0 | Would copyleft shipped binaries |
| **QScintilla** (with PyQt) | GPL | Optional dev UI only |

Experimental trees under `experimental/` may reference PyQt6 — they are **not**
part of the supported release.

---

## Model and voice weights

Third-party **model and voice binaries** are **not** ELI code. Licenses are
documented separately in **`models/MODEL_LICENSES.md`**. GGUF models ship via GitHub
Release tags, not in this git repo. The voice's files (Kokoro-82M, Apache-2.0; spaCy's
en_core_web_sm, MIT; gruut's English G2P model, MIT) are fetched at pinned revisions and
checksums by `eli/runtime/voice_assets.py`, and the release builds carry them (`tts_kokoro/`).
Qwen3-TTS's weights (Apache-2.0) are fetched on first use, on a GPU machine only.

---

## Obtaining license texts

- PyPI packages: `pip show <package>` → **License** field; or the package's
  `LICENSE` / `METADATA` in `.venv/lib/.../site-packages/`.
- PySide6 LGPL: https://www.qt.io/licensing/
- This file is included in release tarballs produced by `scripts/package_desktop_app.sh`.

Questions: **jaybridgeman0095@gmail.com**
