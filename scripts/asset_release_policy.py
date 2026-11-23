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


# ── which catalog models a closed, paid product may pass on ─────────────────────
# A catalog entry's licence, as eli/core/model_download.py keeps it from the upstream model card (the
# model installer's field). An entry without one counts as unknown, and is never published or bundled.
CATALOG_LICENCE_FIELD = "license"


@lru_cache(maxsize=1)
def _licences():
    """packaging/gpl_sources.py, whose licence classes the builds use for packages too: this checkout's,
    else the one beside the eli package this runs with."""
    import importlib.util
    candidates = [_ROOT / "packaging" / "gpl_sources.py"]
    found = importlib.util.find_spec("eli")
    if found is not None and found.origin:
        candidates.append(Path(found.origin).resolve().parents[1] / "packaging" / "gpl_sources.py")
    path = next((c for c in candidates if c.is_file()), candidates[0])
    spec = importlib.util.spec_from_file_location("eli_gpl_sources", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def catalog_licence(entry: dict) -> str:
    """A catalog entry's licence: its own field, else, for a model the release builds carry too (the
    embedder), the one eli/runtime/model_licences.py records for its file; "" when neither says."""
    licence = str((entry or {}).get(CATALOG_LICENCE_FIELD) or "").strip()
    if licence:
        return licence
    try:
        from eli.runtime import model_licences
        known = model_licences.for_file(str((entry or {}).get("filename") or ""))
    except Exception:
        return ""
    return known.licence if known else ""


def redistributable(entry: dict) -> bool:
    """Whether ELI v3, sold under its own licence, may pass this catalog model on (publish it on an
    asset release, bundle it in a package): its licence is in the permissive class. Non-commercial and
    research licences, a custom licence with its own conditions (TII Falcon) and none at all are not."""
    licences = _licences()
    licence = catalog_licence(entry)
    return bool(licence) and licences.licence_text_class(licence) == licences.PERMISSIVE


def _catalog(chat_only: bool = False) -> list:
    try:
        from eli.core import model_download as md
        entries = list(getattr(md, "CATALOG", [])) + list(md.list_catalog()) + ([] if chat_only else list(md.list_aux()))
    except Exception:
        return []
    seen, out = set(), []
    for e in entries:
        name = str((e or {}).get("filename") or "").strip() if isinstance(e, dict) else ""
        if name and e.get("url") and name not in seen:
            seen.add(name)
            out.append(e)
    return out


@lru_cache(maxsize=1)
def catalog_model_filenames() -> frozenset:
    """Every model file ELI's model catalog (eli/core/model_download.py) names with a download
    address under a licence that lets ELI pass it on (redistributable): the models that may be
    published. Empty when the catalog cannot be read, and then nothing under models/ is published."""
    return frozenset(str(e["filename"]).strip() for e in _catalog() if redistributable(e))


def starter_model(folder: Path) -> "dict | None":
    """The catalog's smallest chat model that ELI may pass on (redistributable) and whose file is in
    `folder`: what a package bundles as its starter, since the package goes to every user. None when
    there is none, and the installer downloads one."""
    present = [e for e in _catalog(chat_only=True)
               if redistributable(e) and (Path(folder) / str(e["filename"])).is_file()]
    return min(present, key=lambda e: float(e.get("size_gb") or 0), default=None)


def flat_restore_destination(root: Path, filename: str) -> Path:
    """Map a flat GitHub Release asset filename to its project path."""
    name = Path(filename).name
    if name.endswith(".gguf"):
        lower = name.lower()
        if any(tag in lower for tag in ("nomic-embed", "embed", "bge-", "embedder")):
            return root / "models" / "embeddings" / name
        return root / "models" / name
    return root / "models" / name


def place_licences(root: Path) -> list:
    """The licence notice beside each model file under `root` (a package's staging tree, outside its
    wheelhouse) that has none there or in a folder above it: a catalog model's from its entry, any other
    from eli/runtime/model_licences.py. A model file neither knows is listed in what this returns."""
    from eli.runtime import model_licences
    root = Path(root)
    by_name = {str(e["filename"]): e for e in _catalog()}
    files = [p for p in root.rglob("*") if p.is_file() and "wheelhouse" not in p.relative_to(root).parts]
    names = {p.relative_to(root).as_posix() for p in files}
    unknown = []
    for path in sorted(p for p in files if p.name.lower().endswith(model_licences.MODEL_SUFFIXES)):
        rel = path.relative_to(root).as_posix()
        entry = by_name.get(path.name)
        if entry is not None and redistributable(entry) and str(entry.get(CATALOG_LICENCE_FIELD) or "").strip():
            key = str(entry.get("key") or path.stem)
            if not model_licences.licensed_in(rel, key, names):
                written = model_licences.write_licence(path.parent, key, str(entry.get("name") or path.name),
                                                       catalog_licence(entry), str(entry.get("url") or ""))
                if written:
                    names.add(written.relative_to(root).as_posix())
            continue
        known = model_licences.for_file(rel)
        if known is None:
            unknown.append(rel)
        elif not model_licences.licensed_in(rel, known.key, names):
            written = model_licences.place(known.key, path.parent)
            if written:
                names.add(written.relative_to(root).as_posix())
    return unknown


def main(argv: "list | None" = None) -> int:
    """`--starter DIR`: the file name of the starter model a package bundles from DIR (exit 1: none).
    `--licences ROOT`: the licence beside each model file under ROOT (exit 1: a model no licence covers)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--starter"] and len(args) == 2:
        found = starter_model(Path(args[1]))
        if found:
            print(found["filename"])
        return 0 if found else 1
    if args[:1] == ["--licences"] and len(args) == 2:
        unknown = place_licences(Path(args[1]))
        for rel in unknown:
            print(f"[licences] no licence known for {rel}", file=sys.stderr)
        return 1 if unknown else 0
    print(main.__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
