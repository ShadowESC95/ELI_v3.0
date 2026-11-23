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
> **GPL note:** the release builds (exe, dmg, AppImage, the portable packages) carry
> **piper-tts 1.4** (GPL-3.0-or-later) with **espeak-ng** (GPL-3.0-or-later) compiled
> into it: ELI's text-to-speech engine, imported in-process (`ELI.spec` collects the
> `piper` package). Those components are under the GPL in every copy; each release
> attaches their source (*Source code of the GPL components* below).
>
> **PyAutoGUI's GPL dependencies are left out.** PyAutoGUI (BSD-3-Clause), which drives
> the keyboard and mouse where no native tool does, declares **MouseInfo** (GPLv3+),
> **PyMsgBox** (GPLv3+ classifier) and, on Linux, **python3-xlib** (GPLv2). No release
> carries them: PyAutoGUI imports the first two only to offer `mouseInfo()` and its
> message boxes, which ELI never calls, and its X11 backend drives **python-xlib**
> (LGPLv2+) instead of python3-xlib. Each build takes them out after installing
> (`packaging/gpl_sources.py --leave-out`), `ELI.spec` stops a frozen build whose
> environment holds any package under the GPL, the wheelhouses are checked the same way,
> and `requirements.lock.txt` pins none of them. ELI's installers take them out of the
> environment they make as well; a `pip install` you run yourself from PyPI still fetches
> them onto your machine, as PyAutoGUI's metadata asks.

---

## Source code of the GPL components

The release builds carry these packages under the GPL, at the versions in
`requirements.lock.txt`: **piper-tts** with **espeak-ng** compiled into it. Their exact source, at
the versions a release carries, is attached to that release on GitHub as
**`ELI_v3-<version>-gpl-sources.tar.gz`**, next to the binaries: each package's source
distribution from PyPI (checked against PyPI's digest), the project's repository at the
version's tag where the sdist leaves out the build files of a compiled part (piper-tts's
does), and the espeak-ng commit piper-tts's build compiles in, listed with where each came
from in the `SOURCES.md` inside, which says so plainly when the builds carry no package under
the GPL. `packaging/gpl_sources.py` makes it in the release workflow, from the packages the
build installed. The build's own tools are not among them: PyInstaller, whose bootloader every
frozen app starts through, is under the GPL with an exception that lets the bootloader go with
any program, and nothing of it is owed. Upstream: piper-tts
<https://github.com/OHF-Voice/piper1-gpl>, espeak-ng <https://github.com/espeak-ng/espeak-ng>.

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

## Core Python dependencies (`pip install -e .`; `.[full]` adds the inference engine and image generation)

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
| **opencv-python** | Apache-2.0 | Vision / gaze |
| **playwright** | Apache-2.0 | Browser automation plugin |
| **bleak** | MIT | Bluetooth discovery |
| **openwakeword** | Apache-2.0 | Wake word |
| **pychromecast**, **androidtv**, **pyatv** | MIT | Device drivers (Chromecast, Fire TV / Android TV, Apple TV / AirPlay) |
| **adb-shell** | Apache-2.0 | Fire TV / Android TV pairing |
| **PyAutoGUI** | BSD-3-Clause | Keyboard and mouse where no native tool does; its GPL dependencies are left out (see the GPL note above) |
| **python-xlib** | LGPLv2+ | The Xlib PyAutoGUI drives on Linux |
| **yara-python** | Apache-2.0 | The plugin scanner's YARA engine (the user's own rules) |
| **numpy**, **Pillow**, **requests**, **pydantic**, etc. | BSD/MIT/Apache-2.0 | See installed package metadata |

Full pinned versions: `requirements-full.txt`, `requirements.lock.txt`.

---

## Optional extras (built for, or chosen by, the machine's GPU)

| Component | License | Used for |
|---|---|---|
| **diffusers**, **accelerate**, **safetensors** | Apache-2.0 | Local image generation |

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
| **MouseInfo**, **PyMsgBox**, **python3-xlib** | GPL | PyAutoGUI declares them; ELI never uses what needs them, and python-xlib (LGPL) is the Xlib it drives (see the GPL note above) |

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
