# Model & Voice License Manifest — ELI v3

ELI v3 is proprietary software, licensed under the ELI End User Licence Agreement (`LICENSE`,
`COMMERCIAL_EULA.md`). The models it bundles, and the ones it or its installers fetch for you, are
**not** ELI's: each stays under its upstream's licence, named here with where it comes from. ELI v3
passes on (bundles, publishes, installs) only models whose licence lets a closed, paid product do so:
the release builds stop on a bundled model file without an entry in `eli/runtime/model_licences.py`
or in a class no release carries (`packaging/gpl_sources.py`: strong copyleft, non-commercial,
research-only), and the packages and the asset release take a catalog model only when its licence
is permissive (`scripts/asset_release_policy.py`).

**Each model carries its own licence beside it.** Wherever its files are kept (in a release bundle,
`models/` or the folder a download lands in), ELI writes `LICENSE.<key>.txt` next to them: what the
files are, their licence, their source and the licence's own text (`eli/runtime/licence_texts/`).

---

## What the release builds carry

| Files (in the bundle) | Upstream | Licence |
|---|---|---|
| **Kokoro-82M v1.0**, ONNX export `model.onnx` + `tokenizer.json` + the 28 English and 8 Mandarin voices `voices/{af,am,bf,bm,zf,zm}_*.bin` (`tts_kokoro/`; revision `1939ad2a8e416c0acfeecc08a694d14ef25f2231`) | [onnx-community/Kokoro-82M-v1.0-ONNX](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX), from [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | **Apache-2.0** (weights; the model card's licence, no licence file upstream) |
| **en_core_web_sm 3.8.0** (`tts_kokoro/en_core_web_sm-3.8.0/`, spaCy's English tagger misaki reads words with) | [explosion/spacy-models](https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0) | **MIT** (© ExplosionAI GmbH; its own `LICENSE` is in its folder) |
| **gruut English G2P model** (`tts_kokoro/g2p_en.crf`, `g2p/model.crf` of gruut-lang-en 2.0.1, trained on the CMU Pronouncing Dictionary) | [rhasspy/gruut](https://github.com/rhasspy/gruut/tree/a2187f9dfe472e4aafffcf043bd1a220c057ee9d/gruut-lang-en) @ `a2187f9d` (the same file, SHA-256 `7942e8f8…`, is in gruut-lang-en 2.0.1 on PyPI) | **MIT** (© 2020 Michael Hansen); CMUdict: BSD-2-Clause-style |
| **misaki's dictionaries** (`misaki/data/*.json`, inside the package) | [hexgrad/misaki](https://github.com/hexgrad/misaki) | **Apache-2.0** (the package's licence) |
| **nomic-embed-text v1.5** `nomic-embed-text-v1.5.Q4_K_M.gguf` (`models/embeddings/`, the memory and search embedder) | [nomic-ai/nomic-embed-text-v1.5-GGUF](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF) | **Apache-2.0** (the model card's licence) |
| **The wake word's feature models** (`eli/perception/wake_features/`): `embedding_model.onnx`, Google's **speech_embedding/1** converted by ELI from Google's own release, and `melspectrogram.onnx`, ELI's own log-mel frontend built from its definition (no trained weights); `packaging/wake_features.py` rebuilds both, `PROVENANCE.md` beside them records sources, checksums and the check against openWakeWord's outputs | [google/speech_embedding/1](https://tfhub.dev/google/speech_embedding/1) (Kaggle Models) | embedding: **Apache-2.0** (Google); melspectrogram: ELI's own. None of the openwakeword package's model files (CC BY-NC-SA 4.0 by its own note, its two feature models among them) is carried or installed (`packaging/gpl_sources.py`, LEFT_OUT_FILES); openWakeWord's code (Apache-2.0) runs ELI's two |
| **Silero VAD** `silero_vad_v6.onnx` (`faster_whisper/assets/`, inside faster-whisper's package) | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) | **MIT** (© Silero Team) |
| **CMU Sphinx US English** acoustic model, language model and dictionary (`speech_recognition/pocketsphinx-data/en-US/`, inside SpeechRecognition's package; ELI does not use them) | [cmusphinx/pocketsphinx](https://github.com/cmusphinx/pocketsphinx) | **BSD-2-Clause** (© Carnegie Mellon University; its own `LICENSE.txt` is in its folder) |

The release builds carry no chat model, no Whisper model and no Qwen3-TTS weights: those are fetched
on the user's machine (below).

---

## What ELI or an installer step fetches

| Asset | Upstream | Licence | Where its licence is written |
|---|---|---|---|
| **faster-whisper** speech models (`small.en` by default; any size the Models page offers) | [Systran](https://huggingface.co/Systran) (e.g. [faster-whisper-small.en](https://huggingface.co/Systran/faster-whisper-small.en)), converted from [openai/whisper](https://github.com/openai/whisper) | **MIT** (© 2022 OpenAI) | `models/whisper/` (`python -m eli.runtime.voice_assets`, the installers' step) |
| **Kokoro-82M**, its voices, en_core_web_sm and the G2P model (as above), on a source install | as above | as above | `models/tts/kokoro/` |
| **Qwen3-TTS** 12Hz 1.7B / 0.6B, CustomVoice (built-in speakers) and Base (voice cloning) — only on a GPU with room, in the runtime ELI makes for it (`eli/perception/qwen3_runtime.py`) | [Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice), [-0.6B-CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice), [-1.7B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base), [-0.6B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) (pinned revisions in `eli/perception/tts_qwen3.py`) | **Apache-2.0** (the model cards' licence) | the pinned snapshot's folder |
| **nomic-embed-text v1.5** (`model_download --aux`) | as above | **Apache-2.0** | by the model installer (`eli/core/model_download.py`) |
| **Qwen2.5-VL-7B** + its mmproj (`model_download --aux vision`) | [unsloth/Qwen2.5-VL-7B-Instruct-GGUF](https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF) | **Apache-2.0** | by the model installer |
| **SSD-1B** (image generation, fetched on demand) | [segmind/SSD-1B](https://huggingface.co/segmind/SSD-1B) | **Apache-2.0** (the model card's licence) | — |
| **Phi-3-mini-4k-instruct** (the LoRA trainer's base, opt-in) | [microsoft/Phi-3-mini-4k-instruct](https://huggingface.co/microsoft/Phi-3-mini-4k-instruct) | **MIT** (its own `LICENSE` is in the download) | — |

Every Kokoro file is pinned (revision and SHA-256) in `eli/runtime/voice_assets.py`; Qwen3-TTS's
weight files are checked against their SHA-256 before first use. Kokoro's own notice: *"Kokoro is an
open-weight TTS model with 82 million parameters ... With Apache-licensed weights, Kokoro can be
deployed anywhere"* (hexgrad/Kokoro-82M model card).

---

## Download catalogue

Written by the model installer from `eli/core/model_download.py`: each catalog model, its licence and
its source. A package bundles a catalog model as its starter, and the asset release publishes one,
only when its licence (the entry's `license` field) is permissive (`scripts/asset_release_policy.py`).

---

## The asset release (`local-assets-v3.0`)

What `scripts/upload_github_asset_files.py` may publish from `models/` (`scripts/github_asset_manifest.py`):
the catalog's models whose licence lets ELI pass them on, and the embedder. Never a model under a
non-commercial, research or custom licence, and never what ELI made from the person's own voice or
conversations (cloned-voice recordings, the speaker profile, trained wake words, adapters). The voice
is never published there: it comes from its own pinned upstream. Restore with
`./RUN_ELI.sh --with-github-assets` or `scripts/restore_github_asset_files.py`.

---

## openWakeWord's feature models, replaced

openWakeWord's README calls its `embedding_model.onnx` Google's `speech_embedding/1` ("provided by
Google as a TFHub module under an Apache-2.0 license", "manually re-implemented"), and also puts "all of
the included pre-trained models" under CC BY-NC-SA 4.0. Rather than ship a file whose licence is in doubt,
ELI converts Google's own release (Apache-2.0, fetched from Kaggle Models with its sha256) and builds the
log-mel frontend from its definition (`packaging/wake_features.py`). On fixed audio ELI's pair gives what
openWakeWord's gave (the embeddings identical under ONNX Runtime's default optimisation, the log-mel
frames within 1.5e-5 dB), and a wake head trained on the old features scores the same clips within 1e-8
on the new (`tests/test_wakeword_training.py`).

---

## What ELI v3 does not use, and why

| What | Licence | Why it is not used |
|---|---|---|
| openWakeWord's pre-trained models (alexa, hey_jarvis, hey_marvin, hey_mycroft, timer, weather, and its melspectrogram and embedding models) | CC BY-NC-SA 4.0 | non-commercial; ELI trains its own head on its own feature models, and every build and install leaves all of them out |
| Coqui **XTTS-v2** | Coqui Public Model License | non-commercial; ELI had agreed to it on the user's behalf and fetched it wherever coqui-tts was installed. No engine of v3 any more: Qwen3-TTS clones voices |
| **Chatterbox** (Resemble AI) | MIT weights, but `chatterbox-tts` brings **pykakasi** (GPL-3.0) | no extra and no engine of v3 any more |
| **Qwen2.5-3B-Instruct** | Qwen Research License (non-commercial) | it was the portable package's starter by default; never again a starter or a published asset |
| The Piper voices (up to 3.0.172) | various, some CC BY-NC-SA | retired with piper-tts (GPL-3.0); a saved Piper voice maps to its nearest Kokoro voice |

---

## Style presets vs character impersonation (public releases)

ELI ships **five built-in style presets** (`char:calm`, `char:robotic`, `char:energetic`, `char:synthetic`, `char:refined`): Kokoro voices (Apache-2.0) + ffmpeg effects. We **do not** bundle neural clones or marketing labels for copyrighted characters (HAL 9000, JARVIS, Rick Sanchez, TARS, GLaDOS, etc.) in GitHub releases or installers — that would be legally unsafe and technically misleading (the old named presets used unshippable base voices and sounded identical anyway).

Users who want a specific timbre may:
- tune or add their own `char:<name>` preset in `config/voices/characters.json`, or
- use **local voice cloning** (Qwen3-TTS on a GPU, `CREATE_VOICE`) from **reference audio they have rights to use**.

Persona / communication-style commands change **wording**, not the TTS timbre.

---

## Maintainer release checklist

1. Attach **this file** to every model GitHub Release (`MODEL_LICENSES.md`).
2. Do not upload `--include-runtime` or `--include-venv` private data.
3. The default voice is Kokoro's `af_heart`; the voice's files come from their pinned upstream,
   never from an asset release.
4. A model added to a build or a package needs its entry in `eli/runtime/model_licences.py` (and its
   licence's text in `eli/runtime/licence_texts/` when no standard one fits), or the build stops.

Questions: **jaybridgeman0095@gmail.com**
