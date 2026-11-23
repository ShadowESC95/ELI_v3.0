"""The licence of every model file ELI v3 bundles or an installer step fetches, kept beside the file.

ELI v3 is sold under its own licence, and each model it passes on or fetches for the user is under
its upstream's: the voice (Kokoro-82M, its voices, spaCy's English tagger, gruut's English G2P model),
speech input (the Whisper models faster-whisper fetches, the Silero VAD inside faster-whisper), the
wake word's feature models (Google's speech_embedding, ELI's log-mel frontend), the text embedder, Qwen3-TTS's weights. MODELS names each,
with its licence, where it comes from and the text of its licence (eli/runtime/licence_texts/), and
`place` writes that text into the folder that holds the files, as LICENSE.<key>.txt:

    python -m eli.runtime.model_licences --place KEY DIR      # the licence of KEY beside its files in DIR
    python -m eli.runtime.model_licences --list               # every model, its licence and its source

The release builds (ELI.spec) carry the licence beside each model they bundle and stop on a bundled
model file MODELS does not cover; voice_assets and tts_qwen3 place it beside what they fetch; the model
installer (eli/core/model_download.py) places the licence of a catalog model it downloads with
`write_licence`. models/MODEL_LICENSES.md and THIRD_PARTY_NOTICES.md name them all.
"""
from __future__ import annotations

import fnmatch
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

TEXTS = Path(__file__).with_name("licence_texts")
# The licence of a model file that is ELI's own work (a transform built from its definition).
OWN = "LicenseRef-ELI"
# The files that are models or weights, by suffix: what a build or a package checks for a licence.
MODEL_SUFFIXES = (".onnx", ".gguf", ".safetensors", ".crf", ".tflite", ".bin")


@dataclass(frozen=True)
class ModelLicence:
    """One model ELI passes on or fetches: what it is, its licence (SPDX), where it comes from, the file
    in licence_texts/ that holds the licence's text, and the files it covers (fnmatch patterns over the
    path under the folder it is kept in). `note` says what is still open about it."""
    key: str
    name: str
    licence: str
    source: str
    text: str
    files: Tuple[str, ...]
    note: str = ""


MODELS: Dict[str, ModelLicence] = {m.key: m for m in (
    ModelLicence(
        "kokoro-82m", "Kokoro-82M v1.0, its ONNX export, its tokenizer and its 28 English and 8 Mandarin voices",
        "Apache-2.0",
        "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX (exported from https://huggingface.co/hexgrad/Kokoro-82M)",
        "Apache-2.0.txt", tuple(f"{root}/{f}" for root in ("tts_kokoro", "tts/kokoro")
                                for f in ("model.onnx", "tokenizer.json", "voices/*.bin"))),
    ModelLicence(
        "en-core-web-sm", "spaCy's English tagger en_core_web_sm 3.8.0 (its own LICENSE is inside its folder)",
        "MIT", "https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0",
        "", ("en_core_web_sm-3.8.0/*",)),
    ModelLicence(
        "gruut-en-g2p", "gruut's English grapheme-to-phoneme model (g2p/model.crf of gruut-lang-en 2.0.1, "
        "trained on the CMU Pronouncing Dictionary)",
        "MIT", "https://github.com/rhasspy/gruut/tree/a2187f9dfe472e4aafffcf043bd1a220c057ee9d/gruut-lang-en "
        "(the same file is in gruut-lang-en 2.0.1 on PyPI)",
        "MIT-gruut.txt", ("g2p_en.crf",)),
    ModelLicence(
        "nomic-embed-text-v1.5", "nomic-embed-text v1.5, the text embedder for memory and search (GGUF)",
        "Apache-2.0", "https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF",
        "Apache-2.0.txt", ("nomic-embed-text-v1.5*.gguf",)),
    ModelLicence(
        "speech-embedding", "Google's speech_embedding/1, the wake word's feature model "
        "(wake_features/embedding_model.onnx), converted to ONNX by ELI from Google's own release "
        "(packaging/wake_features.py; provenance in PROVENANCE.md beside it)",
        "Apache-2.0", "https://tfhub.dev/google/speech_embedding/1 (served from "
        "https://www.kaggle.com/models/google/speech-embedding)",
        "Apache-2.0.txt", ("wake_features/embedding_model.onnx",)),
    ModelLicence(
        "eli-melspectrogram", "ELI's log-mel frontend for the wake word (wake_features/melspectrogram.onnx): "
        "a fixed transform with no trained weights, built from its definition by packaging/wake_features.py",
        OWN, "packaging/wake_features.py (part of ELI)",
        "", ("wake_features/melspectrogram.onnx",),
        note="ELI's own file, under ELI's own licence; no third-party work is in it."),
    ModelLicence(
        "silero-vad", "Silero VAD, the voice-activity model inside faster-whisper's package",
        "MIT", "https://github.com/snakers4/silero-vad (silero-vad on PyPI)",
        "MIT-Silero-VAD.txt", ("silero_vad*.onnx",)),
    ModelLicence(
        "faster-whisper", "OpenAI's Whisper models, converted to CTranslate2 for faster-whisper (Systran)",
        "MIT", "https://huggingface.co/Systran (converted from https://github.com/openai/whisper)",
        "MIT-OpenAI-Whisper.txt", ("models--Systran--faster-whisper-*/*",)),
    ModelLicence(
        "pocketsphinx-en-us", "CMU Sphinx's US English acoustic model, language model and dictionary, inside "
        "SpeechRecognition's package (ELI does not use them; its own LICENSE.txt is in their folder)",
        "BSD-2-Clause", "https://github.com/cmusphinx/pocketsphinx (SpeechRecognition on PyPI)",
        "", ("pocketsphinx-data/en-US/*", "pocketsphinx-data/en-US/*/*")),
    ModelLicence(
        "qwen3-tts", "Qwen3-TTS 12Hz 1.7B and 0.6B, CustomVoice and Base, with their speech tokenizer",
        "Apache-2.0", "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice (and the 0.6B and Base models)",
        "Apache-2.0.txt", ("models--Qwen--Qwen3-TTS-*/*",)),
)}


def licence_file(key: str) -> str:
    return f"LICENSE.{key}.txt"


def text_of(model: ModelLicence) -> str:
    """The licence notice placed beside the model's files: what they are, their licence and source, and
    the licence's text (or where the model's own copy is)."""
    lines = [model.name, f"Licence: {model.licence}", f"Source: {model.source}"]
    if model.note:
        lines.append(f"Note: {model.note}")
    if model.licence == OWN:
        return "\n".join(lines) + "\n\nThis file is part of ELI v3 and under its licence (LICENSE, COMMERCIAL_EULA.md).\n"
    lines.append("Placed beside the files by ELI v3 (eli/runtime/model_licences.py); ELI is licensed separately, "
                 "under its own licence, and this licence covers these files only.")
    body = (TEXTS / model.text).read_text(encoding="utf-8") if model.text else \
        "The licence's own text is with the model: in its folder (LICENSE), or at its source above.\n"
    return "\n".join(lines) + "\n\n" + body


def _write(model: ModelLicence, folder) -> Optional[Path]:
    target = Path(folder) / licence_file(model.key)
    try:
        body = text_of(model)
        if not (target.is_file() and target.read_text(encoding="utf-8") == body):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        return target
    except OSError:
        log.warning("model_licences: could not write %s", target, exc_info=True)
        return None


def place(key: str, folder) -> Optional[Path]:
    """Write MODELS[key]'s licence notice into `folder`, the folder that holds its files (unchanged when
    it is there already). None, logged, when it cannot be written (a read-only bundle)."""
    return _write(MODELS[key], folder)


def write_licence(folder, key: str, name: str, licence: str, source: str, text: str = "",
                  note: str = "") -> Optional[Path]:
    """Write a licence notice for any model (a catalog model the installer downloads, the package's
    starter) into `folder`: `text` names a file in licence_texts/, by default the standard text of
    `licence` when there is one (Apache-2.0). None, logged, when it cannot be written."""
    text = text or (f"{licence}.txt" if (TEXTS / f"{licence}.txt").is_file() else "")
    if not text:
        log.warning("model_licences: no licence text kept for %s (%s); its notice points to its source", name, licence)
    return _write(ModelLicence(key, name, licence, source, text, (), note), folder)


def for_file(rel: str) -> Optional[ModelLicence]:
    """The model a file belongs to, by its path under the folder it is kept in (or by its name alone)."""
    rel = str(rel).replace("\\", "/")
    for model in MODELS.values():
        for pattern in model.files:
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, f"*/{pattern}"):
                return model
    return None


def licensed_in(rel: str, key: str, names) -> bool:
    """Whether `names` (paths under the same root as `rel`) hold KEY's licence beside `rel` or in a
    folder above it."""
    parent = Path(rel).parent
    return any((up / licence_file(key)).as_posix() in names for up in [parent, *parent.parents])


def _main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--place"] and len(args) == 3 and args[1] in MODELS:
        return 0 if place(args[1], Path(args[2])) else 1
    if args == ["--list"]:
        for m in MODELS.values():
            print(f"{m.key}: {m.licence} — {m.name} — {m.source}" + (f" [{m.note}]" if m.note else ""))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
