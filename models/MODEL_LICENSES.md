# Model & Voice License Manifest — ELI v3

ELI distributes **third-party** GGUF, embedding and vision files separately from source
(GitHub Release assets — tag `local-assets-v2.1`) because they exceed git size limits, and
fetches its voice's files from their upstream. **You are responsible for complying with
each upstream license** when you download or use these files.

This manifest covers the installer catalog (`eli/core/model_download.py`), the
**`local-assets-v2.1` GitHub Release pack**, and the voice's files
(`eli/runtime/voice_assets.py`, `eli/perception/tts_qwen3.py`).

---

## GitHub Release pack (`local-assets-v2.1`)

Files at the release root (flat layout). Restore via:

`./RUN_ELI.sh --with-github-assets` or `scripts/restore_github_asset_files.py`

### Required auxiliary

| File | Upstream | License |
|---|---|---|
| `nomic-embed-text-v1.5.Q4_K_M.gguf` | [nomic-ai/nomic-embed-text-v1.5-GGUF](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF) | Apache-2.0 |

**Required for memory / RAG / research vector search.** Also auto-fetched by `install.sh`
from Hugging Face when network is allowed.

### Starter chat GGUF (pick one or more)

| File | Upstream | License | Notes |
|---|---|---|---|
| `tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf` | [TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF](https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF) | Apache-2.0 | TinyLlama base |
| `SmolLM2-1.7B-Instruct-Q4_K_M.gguf` | [HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF](https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct-GGUF) | Apache-2.0 | |
| `DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf` | [deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B-GGUF](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B-GGUF) | [DeepSeek License](https://github.com/deepseek-ai/DeepSeek-R1/blob/main/LICENSE) | Read upstream terms |
| `ministral-3b-instruct-q4_k_m.gguf` | [mistralai/Ministral-3B-Instruct-GGUF](https://huggingface.co/mistralai/Ministral-3B-Instruct-GGUF) | Apache-2.0 | Mistral AI |
| `Palmyra-mini-Q8_0.gguf` | [Writer/palmyra-mini-GGUF](https://huggingface.co/Writer/palmyra-mini-GGUF) | **Writer / Palmyra terms** — read HF model card | Verify before commercial use |

7B+ chat models are **not** on this tag (GitHub 2 GB/file limit). Use `install.sh` or
copy your own `.gguf` into `models/`.

### Voices

No voice is published on this release any more. ELI's voice is Kokoro-82M (see *Speech
models* below): its files are fetched from their own upstream at pinned revisions, each checked
against its SHA-256 (`eli/runtime/voice_assets.py`), and the release builds carry them. The Piper
voices this pack held (and the ones it excluded: ryan, lessac, cori) are no longer used by v3.

---

## Chat GGUF models (installer catalog)

| Key | Upstream | License | Redistribution |
|---|---|---|---|
| `qwen2.5-3b` | [bartowski/Qwen2.5-3B-Instruct-GGUF](https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF) | Apache-2.0 (Qwen2.5) | HF GGUF community repacks — verify model card |
| `qwen2.5-7b` | [bartowski/Qwen2.5-7B-Instruct-GGUF](https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF) | Apache-2.0 | Same |
| `qwen3-8b` | [bartowski/Qwen_Qwen3-8B-GGUF](https://huggingface.co/bartowski/Qwen_Qwen3-8B-GGUF) | Apache-2.0 | Same |
| `falcon3-10b` | [bartowski/Falcon3-10B-Instruct-GGUF](https://huggingface.co/bartowski/Falcon3-10B-Instruct-GGUF) | Apache-2.0 (Falcon3) | Same |
| `phi-4` | [bartowski/phi-4-GGUF](https://huggingface.co/bartowski/phi-4-GGUF) | MIT (Microsoft Phi-4) | Same |
| `qwen3.6-35b-a3b` | [unsloth/Qwen3.6-35B-A3B-GGUF](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF) | Apache-2.0 | Same |
| `falcon-h1-34b` | [tiiuae/Falcon-H1-34B-Instruct-GGUF](https://huggingface.co/tiiuae/Falcon-H1-34B-Instruct-GGUF) | **TII Falcon License** — read upstream model card | Verify before commercial use |

---

## Auxiliary models (auto-downloaded via `model_download --aux`)

| Key | Upstream | License |
|---|---|---|
| `embedder` (nomic-embed-text-v1.5) | [nomic-ai/nomic-embed-text-v1.5-GGUF](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF) | Apache-2.0 |
| `vision` (Qwen2.5-VL-7B) | [unsloth/Qwen2.5-VL-7B-Instruct-GGUF](https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF) | Apache-2.0 |
| `vision-mmproj` | Same repo | Apache-2.0 |

---

## Speech models (runtime fetch)

| Asset | Upstream | License |
|---|---|---|
| **faster-whisper** `small.en` (STT weights) | [Systran/faster-whisper-small.en](https://huggingface.co/Systran/faster-whisper-small.en) | MIT |
| **Kokoro-82M v1.0**, ONNX export `onnx/model.onnx` + `tokenizer.json` + the 28 English and 8 Mandarin voices `voices/{af,am,bf,bm,zf,zm}_*.bin` (ELI's voice; revision `1939ad2a8e416c0acfeecc08a694d14ef25f2231`) | [onnx-community/Kokoro-82M-v1.0-ONNX](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX), from [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | **Apache-2.0** (weights) |
| **en_core_web_sm 3.8.0** (spaCy's English tagger misaki reads words with) | [explosion/spacy-models](https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0) | **MIT** (trained on OntoNotes 5, licensed commercially by Explosion; WordNet 3.0 licence) |
| **gruut English G2P model** `g2p/model.crf` (guesses words misaki's dictionaries lack; trained on the CMU Pronouncing Dictionary) | [rhasspy/gruut](https://github.com/rhasspy/gruut/tree/a2187f9dfe472e4aafffcf043bd1a220c057ee9d/gruut-lang-en) @ `a2187f9d` | **MIT** (© 2020 Michael Hansen); CMUdict: BSD-2-Clause-style |
| **misaki's dictionaries** (`misaki/data/*_gold.json`, `*_silver.json`, inside the package) | [hexgrad/misaki](https://github.com/hexgrad/misaki) | **Apache-2.0** |
| **Qwen3-TTS** 12Hz 1.7B / 0.6B, CustomVoice (built-in speakers) and Base (voice cloning) — only on a GPU with room, in the runtime ELI makes for it (`eli/perception/qwen3_runtime.py`) | [Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice), [-0.6B-CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice), [-1.7B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base), [-0.6B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) (pinned revisions in `eli/perception/tts_qwen3.py`) | **Apache-2.0** |

Every Kokoro file is pinned (revision and SHA-256) in `eli/runtime/voice_assets.py`; Qwen3-TTS's
weight files are checked against their SHA-256 before first use. Kokoro's own notice: *"Kokoro is an
open-weight TTS model with 82 million parameters ... With Apache-licensed weights, Kokoro can be
deployed anywhere"* (hexgrad/Kokoro-82M model card). Copies of the Apache-2.0 and MIT licence texts
are at <https://www.apache.org/licenses/LICENSE-2.0> and <https://opensource.org/license/mit>.

---

## Vendored tokenizer / config (in git, not weights)

| Path | License |
|---|---|
| `models/phi-3-mini-base/` | MIT — see `models/phi-3-mini-base/LICENSE` |

Weight files for Phi-3 are **not** committed; download separately under Microsoft terms.

---

## Image generation (optional)

| Model | Upstream | License |
|---|---|---|
| SSD-1B (fetched on demand) | [segmind/SSD-1B](https://huggingface.co/segmind/SSD-1B) | See HF model card |

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

Questions: **jaybridgeman0095@gmail.com**
