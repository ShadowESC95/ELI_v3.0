# Third-Party Notices — ELI v3

ELI (© 2026 Jason Fitzgibbon Bridgeman) is proprietary software licensed under
the **ELI End User Licence Agreement** (see `LICENSE` and `COMMERCIAL_EULA.md`). This file lists
**third-party** components used at runtime or shipped in release packages,
with their licenses. It accompanies portable tarballs, wheels, and source
checkouts.

> **LGPL note:** PySide6 and some other components are LGPL-licensed. If you
> redistribute ELI binaries (only the copyright holder may do so under the EULA),
> you must comply with LGPL source/relink requirements for those components; the
> directions to their source are in *Source code of the LGPL components* below.
>
> **GPL note:** the release builds (exe, dmg, AppImage, the portable packages) carry
> **piper-tts 1.4** (GPL-3.0-or-later) with **espeak-ng** (GPL-3.0-or-later) compiled
> into it: ELI's text-to-speech engine, imported in-process (`ELI.spec` collects the
> `piper` package). Through PyAutoGUI (BSD-3-Clause) they also carry **MouseInfo**
> (GPLv3+ per its package metadata), **PyMsgBox** (BSD licence text, GPLv3+ classifier
> in its metadata) and, on Linux, **python3-xlib** (GPLv2). Those components are under
> the GPL in every copy; each release attaches their source (*Source code of the GPL
> components* below).

---

## Source code of the GPL components

The release builds carry these packages under the GPL, at the versions in
`requirements.lock.txt`: **piper-tts** with **espeak-ng** compiled into it, and PyAutoGUI's
helpers **MouseInfo**, **PyMsgBox** and, on Linux, **python3-xlib**. Their exact source, at
the versions a release carries, is attached to that release on GitHub as
**`ELI_v3-<version>-gpl-sources.tar.gz`**, next to the binaries: each package's source
distribution from PyPI (checked against PyPI's digest), the project's repository at the
version's tag where the sdist leaves out the build files of a compiled part (piper-tts's
does), and the espeak-ng commit piper-tts's build compiles in, listed with where each came
from in the `SOURCES.md` inside. `packaging/gpl_sources.py` makes it in the release
workflow, from the packages the build installed; it may also hold the source of build tools
under the GPL (PyInstaller), whose licences carry no such obligation for the programs they
build. Upstream: piper-tts <https://github.com/OHF-Voice/piper1-gpl>, espeak-ng
<https://github.com/espeak-ng/espeak-ng>, MouseInfo <https://github.com/asweigart/mouseinfo>,
PyMsgBox <https://github.com/asweigart/pymsgbox>, python3-xlib
<https://pypi.org/project/python3-xlib/>.

---

## Source code of the LGPL components

The release builds also carry packages under the LGPL: **PySide6** with
**PySide6_Essentials**, **PySide6_Addons** and **shiboken6** (Qt for Python), the **Qt**
libraries inside those wheels, **zeroconf**, **python-xlib**, and any other LGPL package the
build installs. Their source is not attached. GPL-3.0 section 6(d), which LGPL-3.0 section 4
applies, lets it be offered from another server when clear directions next to the object
code say where: **`LGPL_SOURCES.md`**, inside `ELI_v3-<version>-gpl-sources.tar.gz` next to
the binaries on each release, names each of those packages, the version the builds carry
and the address of that version's source (its sdist on PyPI with its sha256, or the source
archive its project publishes, and Qt's source at the Qt version the wheels carry).
`packaging/gpl_sources.py` writes it in the release workflow from the packages the build
installed; an address PyPI does not list is used only once it answers, and a build with a
package whose source it cannot find is not released.

---

## Core Python dependencies (`pip install -e .[full]`)

| Component | License | Notes |
|---|---|---|
| **PySide6** (Qt GUI) | LGPL-3.0 | Canonical Qt binding for packaged ELI |
| **llama-cpp-python** | MIT | GGUF inference; built with llama.cpp (MIT) v0.8.0 by `packaging/llama_source.py` |
| **faiss-cpu** | MIT | Vector memory index |
| **faster-whisper** | MIT | Local speech-to-text |
| **piper-tts** | GPL-3.0-or-later (espeak-ng inside) | Local text-to-speech; see the GPL note above |
| **onnxruntime** | MIT | Runs the Piper voices |
| **FastAPI** / **Starlette** / **uvicorn** | MIT | Web server |
| **paho-mqtt** | EPL-2.0 / EDL-1.0 | MQTT device server |
| **zeroconf** | LGPL-2.1+ | mDNS discovery |
| **numpy**, **Pillow**, **requests**, **pydantic**, etc. | BSD/MIT/Apache-2.0 | See installed package metadata |

Full pinned versions: `requirements-full.txt`, `requirements.lock.txt`.

---

## Optional / feature extras

| Component | License | Used for |
|---|---|---|
| **diffusers**, **accelerate**, **safetensors** | Apache-2.0 | Local image generation |
| **opencv-python** | Apache-2.0 | Vision / gaze |
| **playwright** | Apache-2.0 | Browser automation plugin |
| **bleak** | MIT | Bluetooth discovery |
| **openwakeword** | Apache-2.0 | Wake word |

---

## Native / system dependencies (not bundled)

Installed by `install.sh` or the OS package manager as needed:

- **CUDA / ROCm / Metal** — GPU drivers (vendor terms)
- **Piper CLI** (optional) — GPL-3.0 if installed separately
- **espeak-ng**, **ffmpeg**, **pandoc**, **LibreOffice** — system packages (various OSS licenses)

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
documented separately in **`models/MODEL_LICENSES.md`**. GGUF and Piper assets
ship via GitHub Release tags (e.g. `local-assets-v2.1`), not in this git repo.

---

## Obtaining license texts

- PyPI packages: `pip show <package>` → **License** field; or the package's
  `LICENSE` / `METADATA` in `.venv/lib/.../site-packages/`.
- PySide6 LGPL: https://www.qt.io/licensing/
- This file is included in release tarballs produced by `scripts/package_desktop_app.sh`.

Questions: **jaybridgeman0095@gmail.com**
