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
> **GPL note:** no release carries a package under the GPL. ELI's voice is Kokoro-82M
> (Apache-2.0), with misaki (Apache-2.0) for its phonemes, installed without misaki's
> espeak-ng fallback: neither **piper-tts** nor **espeak-ng** (both GPL-3.0-or-later, ELI's
> voice up to 3.0.172) is in v3 any more.
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
>
> **Nor anything else a closed, paid product may not pass on.** Each package a build carries
> is sorted into a licence class (permissive, vendor terms, weak copyleft, unknown, strong
> copyleft, non-commercial) from its SPDX expression, License field, classifiers, licence file
> and the native libraries inside it (`packaging/gpl_sources.py`); `ELI.spec` stops on strong
> copyleft, non-commercial and unknown, and each build checks what it built (`--check-bundle`).
> That found and took out: **PyAV**, whose wheels link **x264 and x265 (GPL-2.0)** into their
> FFmpeg on every platform (faster-whisper declares it for opening files; ELI decodes audio
> itself); SpeechRecognition's **FLAC programs (GPL-2.0)**; **GNU Readline (GPL-3.0)** and
> **Berkeley DB 5.3 (Sleepycat)**, which the Linux build took from its build machine (Python's
> `readline` module, and Ubuntu's PortAudio through JACK); **OpenCV on macOS**, whose wheels
> link a GPL FFmpeg; openWakeWord's **non-commercial wake-word models**; Coqui **XTTS-v2**
> (non-commercial model) and **Chatterbox** (pykakasi, GPL-3.0).

---

## Source code of the GPL components

The release builds carry these packages under the GPL, at the versions in
`requirements.lock.txt`: none. Should one ever be added, its exact source, at the versions a
release carries, is attached to that release on GitHub as
**`ELI_v3-<version>-gpl-sources.tar.gz`**, next to the binaries: each package's source
distribution from PyPI (checked against PyPI's digest), the project's repository at the
version's tag where the sdist leaves out the build files of a compiled part, and what a
package's build fetches, listed with where each came from in the `SOURCES.md` inside, which
says so plainly when the builds carry no package under the GPL (as now).
`packaging/gpl_sources.py` makes it in the release workflow, from the packages the build
installed. The build's own tools are not among them: PyInstaller, whose bootloader every
frozen app starts through, is under the GPL with an exception that lets the bootloader go with
any program, and nothing of it is owed.

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
archive its project publishes, and Qt's source at the Qt version the wheels carry), and each
native LGPL library inside the AppImage's bundle: FFmpeg, Qt 5 and libsndfile inside permissive
wheels, at the version each library reports, and the libraries the build machine gave it, by their
Ubuntu source package. `packaging/gpl_sources.py` writes it in the release workflow from the packages
the build installed and the bundle it built; an address PyPI does not list is used only once it
answers, and a build with a package or library whose source it cannot find is not released.

---

## Core Python dependencies (`pip install -e .`; `.[full]` adds the inference engine and image generation)

| Component | License | Notes |
|---|---|---|
| **PySide6** (Qt GUI) | LGPL-3.0 | Canonical Qt binding for packaged ELI |
| **llama-cpp-python** | MIT | GGUF inference; built with llama.cpp (MIT) v0.8.0 by `packaging/llama_source.py` |
| **faiss-cpu** | MIT | Vector memory index |
| **faster-whisper** (+ ctranslate2, tokenizers) | MIT | Local speech-to-text. It declares **PyAV**, which no release carries and no installer keeps: PyAV's wheels (BSD-3-Clause) link **x264 and x265 (GPL-2.0)** into their FFmpeg on every platform. ELI decodes a recording itself (WAV, FLAC, Ogg, MP3 through libsndfile; anything else through the system's own `ffmpeg` program when it has one) and hands faster-whisper the samples |
| **SpeechRecognition** | BSD-3-Clause; its package also holds **FLAC encoder programs (GPL-2.0)** and CMU Sphinx's English model (BSD-style) | The microphone and the end of a phrase. The FLAC programs run only for its online recognisers, which ELI never calls; every build, wheelhouse and installer leaves them out (`packaging/gpl_sources.py`, LEFT_OUT_FILES) |
| **onnxruntime** | MIT | Runs the Kokoro voice (text to speech) |
| **misaki** | Apache-2.0 | Kokoro's text to phonemes; installed **without** its `[en]` extra (espeakng-loader, phonemizer-fork: GPL) |
| **spaCy** (+ thinc, srsly, cymem, preshed, murmurhash, blis, wasabi, weasel, confection, catalogue) | MIT / BSD | misaki's English tagger |
| **num2words** | LGPL-2.1 | misaki's numbers as words (pure Python; see the LGPL section) |
| **python-crfsuite** | MIT (CRFsuite: BSD) | Guesses words misaki's dictionaries lack (with gruut's English model, below) |
| **langdetect** (+ six) | Apache-2.0 (six: MIT) | The language of a reply in the Latin script (`eli/perception/reply_language.py`) |
| **jieba**, **pypinyin**, **cn2an** (+ proces), **ordered-set** | MIT | misaki's Chinese G2P for Kokoro's Mandarin voices |
| **FastAPI** / **Starlette** / **uvicorn** | MIT | Web server |
| **paho-mqtt** | EPL-2.0 OR EDL-1.0 (used under EDL-1.0, a BSD-3-Clause licence) | MQTT device server |
| **soundfile** | BSD-3-Clause; its wheels carry **libsndfile** (LGPL-2.1) with its codecs (libogg, libvorbis, libFLAC, libopus: BSD; libmpg123, libmp3lame: LGPL) | Reading and writing sound files |
| **pyttsx3**, **certifi**, **tqdm** | MPL-2.0 (tqdm: MPL-2.0 AND MIT) | System voices, CA certificates, progress bars; unmodified files |
| **zeroconf** | LGPL-2.1+ | mDNS discovery |
| **opencv-python** (Linux and Windows only) | Apache-2.0; its wheels carry FFmpeg built under the **LGPL-2.1** and, on Linux, Qt 5 (**LGPL-3.0**) | Gaze cursor. Not installed or bundled on macOS, where its wheels (and the headless ones) link an FFmpeg with **x264 and x265 (GPL)** into cv2 |
| **playwright** | Apache-2.0 | Browser automation plugin |
| **bleak** | MIT | Bluetooth discovery |
| **openwakeword** | Apache-2.0 code; the **pre-trained models in its package are CC BY-NC-SA 4.0** (non-commercial), per its own licence note | Wake word. Its code runs ELI's own two feature models (`eli/perception/wake_features/`: Google's speech_embedding/1, Apache-2.0, converted from Google's release, and ELI's log-mel frontend) and ELI trains its own detector head; every build, wheelhouse and installer leaves all the package's model files out (`packaging/gpl_sources.py`, LEFT_OUT_FILES) |
| **pychromecast**, **androidtv**, **pyatv** | MIT | Device drivers (Chromecast, Fire TV / Android TV, Apple TV / AirPlay) |
| **adb-shell** | Apache-2.0 | Fire TV / Android TV pairing |
| **PyAutoGUI** | BSD-3-Clause | Keyboard and mouse where no native tool does; its GPL dependencies are left out (see the GPL note above) |
| **python-xlib** | LGPLv2+ | The Xlib PyAutoGUI drives on Linux |
| **yara-python** | Apache-2.0 | The plugin scanner's YARA engine (the user's own rules) |
| **numpy**, **Pillow**, **requests**, **pydantic**, etc. | BSD/MIT/Apache-2.0 | See installed package metadata |

Full pinned versions: `requirements-full.txt`, `requirements.lock.txt`.

---

## Weak copyleft kept, and why that is fine

These are under the LGPL, the MPL or the EPL, which let a closed program use them as long as they stay
separate, replaceable files (a Python package, a shared library loaded at run time) and their source is
offered: none is changed, linked statically into ELI's code or merged into it.

- **LGPL Python packages** (whole packages, replaceable in place; directions to each one's source in
  `LGPL_SOURCES.md` on every release): PySide6, PySide6_Essentials, PySide6_Addons and shiboken6 (Qt for
  Python, LGPL-3.0, with the Qt libraries inside them), zeroconf (LGPL-2.1+), python-xlib (LGPL-2.1+,
  Linux), num2words (LGPL-2.1); soxr (LGPL-2.1) only in Qwen3-TTS's own runtime, never in a release.
- **LGPL shared libraries inside permissive wheels**: opencv-python's FFmpeg (LGPL-2.1) and, on Linux, Qt 5
  (LGPL-3.0); soundfile's libsndfile (LGPL-2.1, with mpg123 and LAME compiled in). `LGPL_SOURCES.md` lists
  each at the version the library itself reports (FFmpeg's `av_version_info`, libsndfile's
  `sf_version_string`, Qt's own file name) with that version's source (`NATIVE_LIBRARIES` in
  `packaging/gpl_sources.py`, read from the AppImage's bundle).
- **LGPL system libraries** the Linux AppImage takes from its Ubuntu build machine (GLib, alsa-lib,
  libgcrypt, libgpg-error, libsystemd, libquadmath and any other whose package's copyright names the LGPL
  or the GPL): `LGPL_SOURCES.md` lists each Ubuntu source package at the version the build machine had,
  with Ubuntu's page for that version's source (found by dpkg, not by a list).
- **MPL-2.0 files** (certifi, tqdm, pyttsx3): file-level copyleft, kept as their own unmodified files.

## Vendor runtimes (their own licence lets an application carry them)

- The **CUDA runtime** libraries inside the GPU packs (`cudart`, `cublas`, `cublasLt`, beside the
  CUDA build of llama-cpp-python): NVIDIA's CUDA Toolkit EULA lists them as redistributable with an
  application (Attachment A). The release builds' own Python environments carry no NVIDIA package (the
  Linux build fails on an `nvidia-*` wheel).
- The **Microsoft Visual C++ runtime** DLLs in the Windows build and its installer's `vc_redist`:
  Microsoft's redistributable code.

---

## Optional extras (built for, or chosen by, the machine's GPU)

| Component | License | Used for |
|---|---|---|
| **diffusers**, **accelerate**, **safetensors** | Apache-2.0 | Local image generation |
| **qwen-tts** 0.1.1 + PyTorch, in Qwen3-TTS's own runtime | Apache-2.0 / BSD | The GPU voice: Qwen3-TTS natural voices and voice cloning. Not in the release builds and not in ELI's environment: on a machine whose GPU has room ELI fetches a pinned **uv** (Astral, MIT or Apache-2.0) and makes a runtime with its own Python, PyTorch for the card's CUDA, transformers 4.57.3, accelerate 1.12.0, librosa, soundfile, torchaudio, sox (Python wrapper, BSD), einops and onnxruntime, all permissive but **soxr** (LGPL-2.1), and qwen-tts without its demo's gradio (`eli/perception/qwen3_runtime.py`) |

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
| **MouseInfo**, **PyMsgBox**, **python3-xlib** | GPL | PyAutoGUI declares them; ELI never uses what needs them, and python-xlib (LGPL) is the Xlib it drives (see the GPL note above) |
| **PyAV** (`av`) | BSD-3-Clause, with **x264 and x265 (GPL-2.0)** linked into its FFmpeg | faster-whisper declares it for opening files; ELI hands it samples it decoded itself (`packaging/gpl_sources.py`, LEFT_OUT) |
| **GNU Readline**, **Berkeley DB**, any library or program under the GPL | GPL-3.0, Sleepycat | What a Linux build machine would add: `ELI.spec` leaves Python's `readline` module out, the Linux build compiles PyAudio against a PortAudio built without JACK (which links Berkeley DB), and every build's `--check-bundle` step stops on such a library or program (`NATIVE_LIBRARIES`) |
| **opencv-python** on macOS | Apache-2.0, with **x264, x265 and other GPL parts** in the FFmpeg its macOS wheels link into cv2 | Not installed or bundled on macOS (the gaze cursor says why); its Linux and Windows wheels carry an LGPL FFmpeg |
| **coqui-tts** (XTTS-v2) | MPL-2.0 code; the model is under the **Coqui Public Model License** (non-commercial) | No engine of v3: ELI agreed to that licence for the user and fetched the model |
| **chatterbox-tts** | MIT, but it brings **pykakasi (GPL-3.0)** | No extra and no engine of v3 |
| openWakeWord's **pre-trained models**, its feature models among them | CC BY-NC-SA 4.0 | Left out of every build, wheelhouse and install (`LEFT_OUT_FILES`); ELI ships its own feature models |
| SpeechRecognition's **FLAC programs** | GPL-2.0 | Left out of every build, wheelhouse and install (`LEFT_OUT_FILES`) |

Experimental trees under `experimental/` may reference PyQt6 — they are **not**
part of the supported release.

---

## Model and voice weights

Third-party **model and voice files** are **not** ELI code. Each one ELI bundles or fetches is named,
with its licence and source, in **`models/MODEL_LICENSES.md`**, and carries its own licence beside its
files wherever they are kept (`LICENSE.<key>.txt`, written by `eli/runtime/model_licences.py`). The
release builds carry the voice's files (Kokoro-82M, Apache-2.0; spaCy's en_core_web_sm, MIT; gruut's
English G2P model, MIT) in `tts_kokoro/`, the embedder (nomic-embed-text v1.5, Apache-2.0), the wake word's
two feature models (Google's speech_embedding/1, Apache-2.0, and ELI's own log-mel frontend), Silero's VAD (MIT, inside
faster-whisper) and CMU Sphinx's English model (BSD-2-Clause, inside SpeechRecognition). Whisper's models
(MIT) and Qwen3-TTS's weights (Apache-2.0, on a GPU machine only) are fetched on the user's machine. GGUF
chat models are downloads, never bundled in a release build; a portable package bundles a starter only
when its licence is permissive.

---

## Obtaining license texts

- PyPI packages: `pip show <package>` → **License** field; or the package's
  `LICENSE` / `METADATA` in `.venv/lib/.../site-packages/`.
- PySide6 LGPL: https://www.qt.io/licensing/
- This file is included in release tarballs produced by `scripts/package_desktop_app.sh`.

Questions: **jaybridgeman0095@gmail.com**
