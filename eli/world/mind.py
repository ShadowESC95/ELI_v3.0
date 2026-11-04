"""
eli/world/mind.py
─────────────────
Which model is ELI's mind right now, which ones ELI could use, and choosing one,
for a client that gives ELI a body.

No model is assumed anywhere. A body shows what this says and lets the user
choose; ELI VR does that from a panel in the room, or before it starts. Opened
from ELI's own window, the model already loaded there is simply the one that
answers: nothing is chosen again and nothing is loaded twice.

    status()        what is loaded, what would be used if nothing is, what there is
    choose(name)    load that one, the way ELI's own settings do

Names go over the wire, never paths: where a person keeps their models is
theirs. Finding the models and loading one are ELI's own (hardware_profile's
discovery, runtime_settings' apply), so the fit to the machine's memory is the
loader's as always.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional


class MindError(RuntimeError):
    """Why a model could not be chosen. `kind`: unknown_model, model_unavailable."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail or kind


def _named_folders() -> List[Path]:
    """Folders the user has pointed ELI at: where the models its settings name are kept (a person
    who keeps their models together is offered all of them once one is chosen), and the folders
    named outright in ELI_MODEL_DIRS or the `model_dirs` setting (separated as PATH is)."""
    out: List[Path] = []
    try:
        from eli.core.runtime_settings import load_settings
        settings = load_settings() or {}
    except Exception:
        settings = {}
    for key in ("model_path", "custom_model_path", "gguf_model_path"):
        value = str(settings.get(key) or "").strip()
        if value:
            try:
                out.append(Path(value).expanduser().resolve().parent)
            except OSError:
                continue
    named = os.environ.get("ELI_MODEL_DIRS") or str(settings.get("model_dirs") or "")
    for raw in str(named).split(os.pathsep):
        if raw.strip():
            out.append(Path(raw.strip()).expanduser())
    return [p for p in out if p.is_dir()]


def _found() -> Dict[str, Path]:
    """Every chat model ELI can see, by the name it is shown under. Two files
    with one name are told apart by the folder they are in."""
    from eli.core.hardware_profile import discover_models

    # where ELI keeps models: beside the code, where the user said, and in ELI's data; then the
    # folders of the models the user has chosen before, and any they named (ELI VR showed one
    # model to choose from while the user's others sat in another folder)
    folders: List[Optional[Path]] = [None]
    try:
        from eli.core.paths import data_dir, models_dir
        folders += [models_dir(), data_dir() / "models"]
    except Exception:
        pass
    current = _resolved()
    if current is not None:
        folders.append(current.parent)
    folders += _named_folders()
    paths: List[Path] = []
    seen_folders: List[Path] = []
    for folder in folders:
        if folder is not None:
            if folder in seen_folders:
                continue
            seen_folders.append(folder)
        try:
            for model in discover_models(folder):
                path = Path(model["path"])
                if path not in paths:
                    paths.append(path)
        except Exception:
            continue
    current = _resolved()
    if current is not None and current not in paths:
        paths.append(current)           # one chosen from elsewhere on the disk is still ELI's
    out: Dict[str, Path] = {}
    for path in sorted(paths, key=lambda p: p.name.lower()):
        name = path.name
        if name in out:
            name = f"{path.name} ({path.parent.name})"
        out[name] = path
    return out


def _resolved() -> Optional[Path]:
    try:
        from eli.cognition.gguf_inference import get_model_path
        path = get_model_path()
        return Path(path).resolve() if path else None
    except Exception:
        return None


def _loaded() -> bool:
    try:
        from eli.cognition.gguf_inference import is_loaded
        return bool(is_loaded())
    except Exception:
        return False


def _chosen() -> bool:
    """Whether a model has been chosen in ELI's settings, as against one merely used last."""
    try:
        from eli.core.runtime_settings import load_settings
        settings = load_settings() or {}
        return any(settings.get(key) for key in ("model_path", "custom_model_path", "gguf_model_path"))
    except Exception:
        return False


def _size_gb(path: Path) -> float:
    try:
        return round(path.stat().st_size / 1e9, 1)
    except OSError:
        return 0.0


def status() -> Dict[str, Any]:
    """ELI's mind as a body may show it."""
    found = _found()
    current = _resolved()
    name = next((n for n, p in found.items() if current is not None and p == current), "")
    return {
        "type": "eli_mind",
        "loaded": _loaded(),            # a model is in memory now
        "model": name,                  # the one that is, or the one that would be used
        "chosen": _chosen(),            # False: nobody chose it; it is only the one used last
        "models": [{"name": n, "size_gb": _size_gb(p)} for n, p in found.items()],
    }


def choose(name: str) -> Dict[str, Any]:
    """Make this model ELI's mind and load it. The same as choosing it in ELI's settings."""
    found = _found()
    path = found.get(str(name or "").strip())
    if path is None:
        raise MindError("unknown_model", f"there is no model called {name!r} here")
    if _loaded() and _resolved() == path:
        return status()                 # already ELI's mind: nothing to load
    try:
        from eli.core.runtime_settings import apply_runtime_settings
        result = apply_runtime_settings(
            {"model_path": str(path), "gguf_model_path": str(path), "custom_model_path": str(path)},
            do_reload=True)
    except Exception as failure:  # noqa: BLE001 - told apart for the client
        raise MindError("model_unavailable", f"{type(failure).__name__}: {failure}") from failure
    reload_result = (result or {}).get("reload_result") or {}
    if reload_result.get("ok") is False:
        raise MindError("model_unavailable", str(reload_result.get("error") or "the model did not load"))
    return status()
