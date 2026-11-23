"""Shared policy for GitHub model asset releases and restore.

ELI's voice (Kokoro) is fetched from its own pinned upstream (eli/runtime/voice_assets.py), never
from these releases, so they carry models only."""
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

# ── what may be published from models/ ──────────────────────────────────────────
# ELI keeps beside the models what it made from the person's own voice and conversations: a cloned
# voice's reference recordings (voice_profiles), their speaker profile (voice_profile), wake-word
# models trained on their voice (wakeword), adapters, merges and data fine-tuned on their
# conversations (lora*, eli-*, training_data) and the cache that training leaves. Those, and any
# recording, log or partial download under models/, are never published: no option uploads them.
_PRIVATE_MODEL_DIRS = frozenset({"voice_profiles", "voice_profile", "wakeword", "training_data",
                                 "unsloth_compiled_cache"})
_PRIVATE_MODEL_DIR_PREFIXES = ("lora", "eli-")
_PRIVATE_SUFFIXES = (".wav", ".log", ".part")


def is_private_user_asset(rel: str) -> bool:
    """A file under models/ that holds the person's own data (see above)."""
    parts = Path(rel).parts
    if not parts or parts[0] != "models":
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
    return root / "models" / name
