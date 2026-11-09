"""Shared policy for GitHub model/voice asset releases and restore."""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

# Run as `python3 scripts/<script>.py` only scripts/ is on the path; the checkout's own eli package
# holds the voice list and the model catalog this policy reads.
_ROOT = Path(__file__).resolve().parents[1]
if (_ROOT / "eli").is_dir() and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DEFAULT_ASSET_REPO = "ShadowESC95/ELI_v3.0"
DEFAULT_ASSET_TAG = "local-assets-v3.0"

# Voices excluded from public restore/upload (NC-SA ryan, uncleared Lessac, Cori
# under review). The licence attaches to the DATASET, so exclusion is by voice
# name — every quality variant (-low/-medium/-high) is covered, not just the
# files we happened to have locally. eli.runtime.voice_assets owns this list;
# the literal below is the fallback for when this script runs outside the package
# (portable builds copy scripts/ standalone).
try:  # pragma: no cover - trivial import shim
    from eli.runtime.voice_assets import RESTRICTED_VOICE_NAMES
except Exception:
    RESTRICTED_VOICE_NAMES = frozenset({"ryan", "lessac", "cori"})

# Kept for callers that want the exact known-file list (upload manifests/logs).
EXCLUDED_VOICE_BASENAMES = frozenset({
    "en_US-ryan-high",
    "en_US-ryan-medium",
    "en_US-lessac-high",
    "en_US-lessac-medium",
    "en_GB-cori-high",
})


def is_excluded_voice_filename(name: str) -> bool:
    stem = Path(name).name
    if stem.endswith(".onnx.json"):
        stem = stem[:-10]
    elif stem.endswith(".onnx"):
        stem = stem[:-5]
    if stem in EXCLUDED_VOICE_BASENAMES:
        return True
    parts = stem.split("-")
    return len(parts) >= 3 and "-".join(parts[1:-1]) in RESTRICTED_VOICE_NAMES


# ── what may be published from models/ ──────────────────────────────────────────
# ELI keeps beside the models what it made from the person's own voice and conversations: a cloned
# voice's reference recordings (voice_profiles), their speaker profile (voice_profile), wake-word
# models trained on their voice (wakeword), adapters, merges and data fine-tuned on their
# conversations (lora*, eli-*, training_data) and the cache that training leaves. Those, and any
# recording, log or partial download under models/ or tts_piper/, are never published: no option
# uploads them.
_PRIVATE_MODEL_DIRS = frozenset({"voice_profiles", "voice_profile", "wakeword", "training_data",
                                 "unsloth_compiled_cache"})
_PRIVATE_MODEL_DIR_PREFIXES = ("lora", "eli-")
_PRIVATE_SUFFIXES = (".wav", ".log", ".part")


def is_private_user_asset(rel: str) -> bool:
    """A file under models/ or tts_piper/ that holds the person's own data (see above)."""
    parts = Path(rel).parts
    if not parts or parts[0] not in ("models", "tts_piper"):
        return False
    if Path(rel).name.lower().endswith(_PRIVATE_SUFFIXES):
        return True
    top = parts[1].lower() if parts[0] == "models" and len(parts) >= 2 else ""
    return top in _PRIVATE_MODEL_DIRS or top.startswith(_PRIVATE_MODEL_DIR_PREFIXES)


@lru_cache(maxsize=1)
def catalog_model_filenames() -> frozenset:
    """Every model file ELI's model catalog (eli/core/model_download.py) names with a download
    address: the models that may be published, since anyone can fetch them. Empty when the catalog
    cannot be read, and then nothing under models/ is published."""
    try:
        from eli.core import model_download as md
        entries = list(getattr(md, "CATALOG", [])) + list(md.list_catalog()) + list(md.list_aux())
    except Exception:
        return frozenset()
    return frozenset(str(e.get("filename") or "").strip() for e in entries
                     if isinstance(e, dict) and e.get("url") and e.get("filename"))


def flat_restore_destination(root: Path, filename: str) -> Path:
    """Map a flat GitHub Release asset filename to its project path."""
    name = Path(filename).name
    if name.endswith(".gguf"):
        lower = name.lower()
        if any(tag in lower for tag in ("nomic-embed", "embed", "bge-", "embedder")):
            return root / "models" / "embeddings" / name
        return root / "models" / name
    if name.endswith(".onnx") or name.endswith(".onnx.json"):
        return root / "tts_piper" / "piper" / name
    return root / "models" / name
