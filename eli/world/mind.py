"""
eli/world/mind.py
─────────────────
Which model is ELI's mind right now, which ones he could use, and choosing one,
for a client that gives him a body.

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

from pathlib import Path
from typing import Any, Dict, List, Optional


class MindError(RuntimeError):
    """Why a model could not be chosen. `kind`: unknown_model, model_unavailable."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail or kind


def _found() -> Dict[str, Path]:
    """Every chat model ELI can see, by the name it is shown under. Two files
    with one name are told apart by the folder they are in."""
    from eli.core.hardware_profile import discover_models

    # where ELI keeps models: beside the code, where the user said, and in his data
    folders: List[Optional[Path]] = [None]
    try:
        from eli.core.paths import data_dir, models_dir
        folders += [models_dir(), data_dir() / "models"]
    except Exception:
        pass
    paths: List[Path] = []
    for folder in folders:
        try:
            for model in discover_models(folder):
                path = Path(model["path"])
                if path not in paths:
                    paths.append(path)
        except Exception:
            continue
    current = _resolved()
    if current is not None and current not in paths:
        paths.append(current)           # one chosen from elsewhere on the disk is still his
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
    """Make this model ELI's mind and load it. The same as choosing it in his settings."""
    found = _found()
    path = found.get(str(name or "").strip())
    if path is None:
        raise MindError("unknown_model", f"there is no model called {name!r} here")
    if _loaded() and _resolved() == path:
        return status()                 # already his mind: nothing to load
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
