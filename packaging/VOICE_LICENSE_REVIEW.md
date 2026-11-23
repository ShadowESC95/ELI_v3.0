# ELI Voice License Review

This report is packaging guidance, not a replacement for formal legal review.

## The voice the release builds carry

ELI v3's voice is **Kokoro-82M v1.0** (hexgrad), run on the CPU through onnxruntime. Every file is
pinned to one revision and checked against its SHA-256 in `eli/runtime/voice_assets.py`; the release
workflow fetches them with that module into `tts_kokoro/`, and `ELI.spec` bundles that folder.

| File(s) | Upstream | Licence | Classification |
|---|---|---|---|
| `model.onnx` (fp32), `tokenizer.json` | onnx-community/Kokoro-82M-v1.0-ONNX @ `1939ad2a`, exported from hexgrad/Kokoro-82M | Apache-2.0 | ship |
| `voices/{af,am,bf,bm,zf,zm}_*.bin` (the 28 English and 8 Mandarin voices) | same | Apache-2.0 | ship |
| `en_core_web_sm-3.8.0/` (spaCy's English tagger) | explosion/spacy-models release `en_core_web_sm-3.8.0` | MIT (OntoNotes 5 licensed commercially by Explosion; WordNet 3.0 licence) | ship |
| `g2p_en.crf` (gruut's English grapheme-to-phoneme CRF) | rhasspy/gruut @ `a2187f9d`, `gruut-lang-en/gruut_lang_en/g2p/model.crf` | MIT (CMU Pronouncing Dictionary data, BSD-2-Clause-style) | ship |

What is deliberately **not** used, because it is GPL:

- **espeak-ng** in every form: misaki's `[en]` extra (`espeakng-loader`, `phonemizer-fork`), kokoro-onnx
  (which imports both), phonemizer, piper-tts (espeak-ng compiled in), and the `espeak/` lexicon and model
  inside gruut-lang-en (generated with espeak-ng; ELI fetches only the CMU-trained `g2p/model.crf`).
- **g2p_en** (Apache-2.0 itself) depends on `distance`, which is GPL.

Kokoro's other languages (Spanish, French, Hindi, Italian, Portuguese) are read through espeak-ng by
misaki, so ELI offers Kokoro's American and British English voices and its Mandarin ones (misaki's own Chinese
G2P: jieba, pypinyin, cn2an, ordered-set, all MIT). On a GPU, Qwen3-TTS (below) speaks ten languages; elsewhere a
reply in another language is spoken by the operating system's own voice for it, which ELI does not ship (on Linux
through speech-dispatcher's `spd-say`, a separate program).

Each of these carries its own licence beside it: `voice_assets` writes `LICENSE.kokoro-82m.txt`,
`LICENSE.en-core-web-sm.txt` and `LICENSE.gruut-en-g2p.txt` into `tts_kokoro/` (and `models/tts/kokoro/`),
with the licence's own text (`eli/runtime/model_licences.py`).

## The wake word's models

openWakeWord 0.4.0's two feature models, `melspectrogram.onnx` and `embedding_model.onnx` (Apache-2.0 per
its README; see the open question in `models/MODEL_LICENSES.md`), are all ELI loads of it
(`FEATURE_MODELS` in `eli/perception/wakeword.py`): it trains its own detector head on them. The package's
pre-trained wake-word models (CC BY-NC-SA 4.0, non-commercial) are left out of every build, wheelhouse and
install (`packaging/gpl_sources.py`, LEFT_OUT_FILES). Classification: feature models **ship** (one open
question), pre-trained models **never ship**.

## The GPU voice (optional, not in the release builds)

**Qwen3-TTS** 12Hz 1.7B and 0.6B, CustomVoice (nine built-in speakers) and Base (voice cloning), Apache-2.0
weights and code (`qwen-tts` 0.1.1). It runs in a runtime ELI makes for it on a machine whose GPU has room
(`eli/perception/qwen3_runtime.py`): a pinned uv (MIT or Apache-2.0, SHA-256-checked) fetches a Python, PyTorch for
the card's CUDA and the pinned packages (`qwen3_runtime.txt`: transformers 4.57.3, accelerate 1.12.0, librosa,
soundfile, sox's Python wrapper (BSD; the SoX program is not needed), einops, onnxruntime, all permissive; librosa
brings soxr, LGPL-2.1), and qwen-tts without its demo's gradio. The weights are fetched at a pinned revision on
first use and each weight file is checked against its SHA-256 (`eli/perception/tts_qwen3.py`). Classification:
**ship-safe**, never bundled: everything is fetched onto the user's machine, GPU-bound, a few GB.

## Voice cloning

A cloned voice is made from a recording the user supplies (`CREATE_VOICE`); the user must have the right
to use it. Qwen3-TTS (Base) clones it on a GPU with room. Coqui XTTS-v2 is no engine of v3 (its model's
licence, the CPML, is non-commercial, and ELI had agreed to it on the user's behalf), nor is Chatterbox
(chatterbox-tts brings pykakasi, GPL-3.0).

## Retired: the Piper voices (up to 3.0.172)

piper-tts 1.4 (GPL-3.0-or-later, espeak-ng compiled in) and its voices are no longer used. The earlier review's
exclusions (`en_US-ryan-*`: CC BY-NC-SA 4.0; `en_US-lessac-*`: Blizzard/Lessac terms; `en_GB-cori-high`:
LibriVox-derived) no longer apply to anything ELI ships. A voice id saved for Piper maps to its nearest Kokoro
voice (`eli/perception/tts_kokoro.voice_for_legacy`).
