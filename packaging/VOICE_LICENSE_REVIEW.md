# ELI Voice License Review

This report is packaging guidance, not a replacement for formal legal review.

## The voice the release builds carry

ELI v3's voice is **Kokoro-82M v1.0** (hexgrad), run on the CPU through onnxruntime. Every file is
pinned to one revision and checked against its SHA-256 in `eli/runtime/voice_assets.py`; the release
workflow fetches them with that module into `tts_kokoro/`, and `ELI.spec` bundles that folder.

| File(s) | Upstream | Licence | Classification |
|---|---|---|---|
| `model.onnx` (fp32), `tokenizer.json` | onnx-community/Kokoro-82M-v1.0-ONNX @ `1939ad2a`, exported from hexgrad/Kokoro-82M | Apache-2.0 | ship |
| `voices/{af,am,bf,bm}_*.bin` (the 28 English voices) | same | Apache-2.0 | ship |
| `en_core_web_sm-3.8.0/` (spaCy's English tagger) | explosion/spacy-models release `en_core_web_sm-3.8.0` | MIT (OntoNotes 5 licensed commercially by Explosion; WordNet 3.0 licence) | ship |
| `g2p_en.crf` (gruut's English grapheme-to-phoneme CRF) | rhasspy/gruut @ `a2187f9d`, `gruut-lang-en/gruut_lang_en/g2p/model.crf` | MIT (CMU Pronouncing Dictionary data, BSD-2-Clause-style) | ship |

What is deliberately **not** used, because it is GPL:

- **espeak-ng** in every form: misaki's `[en]` extra (`espeakng-loader`, `phonemizer-fork`), kokoro-onnx
  (which imports both), phonemizer, piper-tts (espeak-ng compiled in), and the `espeak/` lexicon and model
  inside gruut-lang-en (generated with espeak-ng; ELI fetches only the CMU-trained `g2p/model.crf`).
- **g2p_en** (Apache-2.0 itself) depends on `distance`, which is GPL.

Kokoro's other languages (Spanish, French, Hindi, Italian, Portuguese) are read through espeak-ng by
misaki, so ELI offers Kokoro's American and British English voices only. On a GPU, Qwen3-TTS (below)
speaks ten languages.

## The GPU voice (optional, not in the release builds)

**Qwen3-TTS** 12Hz 1.7B and 0.6B, CustomVoice (nine built-in speakers) and Base (voice cloning), Apache-2.0
weights and code (`qwen-tts` 0.1.1). Installed with the `voice-gpu` extra; the weights are fetched at a pinned
revision on first use and each weight file is checked against its SHA-256 (`eli/perception/tts_qwen3.py`).
Classification: **ship-safe**, but never bundled (GPU-bound, 2.5-4.5 GB).

## Voice cloning

A cloned voice is made from a recording the user supplies (`CREATE_VOICE`); the user must have the right
to use it. XTTS-v2 (Coqui) stays a legacy, user-installed engine only: its model's licence (CPML) is
non-commercial, and ELI's builds never carry it.

## Retired: the Piper voices (up to 3.0.172)

piper-tts 1.4 (GPL-3.0-or-later, espeak-ng compiled in) and its voices are no longer used. The earlier review's
exclusions (`en_US-ryan-*`: CC BY-NC-SA 4.0; `en_US-lessac-*`: Blizzard/Lessac terms; `en_GB-cori-high`:
LibriVox-derived) no longer apply to anything ELI ships. A voice id saved for Piper maps to its nearest Kokoro
voice (`eli/perception/tts_kokoro.voice_for_legacy`).
