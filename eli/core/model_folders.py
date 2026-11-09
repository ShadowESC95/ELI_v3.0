"""
eli/core/model_folders.py
─────────────────────────
Where ELI looks for models, in one place.

Every kind of model ELI uses (the chat model, the vision pair, the image checkpoint, the
speech model) was found by its own scanner over its own list of folders, and a person who keeps
their models together in one folder had to point each scanner at it separately, or type a path.
This is the one list: ELI's own folders, the folders of the models the settings already name,
the folder of the model loaded now, and the folders the user names outright (the `model_dirs`
setting, `ELI_MODEL_DIRS`). Every scanner reads it; the settings page lists it and lets the
user add to it; a model chosen from a folder nothing knew makes that folder known.

    model_folders()            every folder, in order, each once, existing ones only, never one
                               that holds the home or the temporary folder (refused_as_folder)
    named_model_folders()      the ones the user named (the setting and the environment)
    add_model_folder(path)     name a folder (the setting); True when it was new
    remove_model_folder(path)  forget a named folder; False when it is named by the environment,
                               which this cannot change
    remember_folder_of(path)   a model chosen from an unknown folder makes its folder known
    chat_models(current)       every chat model in every folder, by the name it is shown under
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

SETTING = "model_dirs"               # os.pathsep-separated, as PATH is
ENV = "ELI_MODEL_DIRS"
# the settings whose values are models, whose folders are therefore model folders
MODEL_SETTINGS = ("model_path", "custom_model_path", "gguf_model_path", "bundled_model_path",
                  "vision_model_path", "vision_mmproj_path", "vision_fast_model_path", "image_model_path")


def _settings() -> dict:
    try:
        from eli.core.runtime_settings import load_settings
        return load_settings() or {}
    except (ImportError, OSError, ValueError, TypeError) as failure:   # no settings is an empty list, not a crash
        log.debug("settings unreadable for the model folders: %s", failure)
        return {}


def _anchored(path: Path) -> Path:
    """A folder from a setting, made absolute against ELI's own root (eli.core.paths), never the
    directory ELI happened to be started from: a bare file name in a model setting made the home
    folder a model folder when the app was started from a terminal there."""
    if path.is_absolute():
        return path                 # as given: a link to a model stays in the folder it was put in
    try:
        from eli.core.paths import resolve_runtime_path
        return resolve_runtime_path(path)
    except (ImportError, OSError, RuntimeError, ValueError) as failure:
        log.debug("ELI's root could not anchor %s: %s", path, failure)
        return path


def _folder_of(value: str) -> Optional[Path]:
    """The folder a model setting points into: a file's parent, or a model folder's parent (an
    image model is a folder of weights; the folder that holds such folders is the one to scan)."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _anchored(Path(raw).expanduser()).parent
    except OSError:
        return None


def _split(named: str) -> List[Path]:
    return [Path(part.strip()).expanduser() for part in str(named or "").split(os.pathsep) if part.strip()]


def _held_folders() -> Dict[str, List[Path]]:
    """The folders a model folder must not be or hold, by what they are: every scanner searches a
    model folder and every folder beneath it, and these hold other programs' files by the disk-full
    (in the temporary folder, another program's test files named *.gguf showed as models)."""
    import tempfile
    held: Dict[str, List[Path]] = {"the home folder": [], "the temporary folder": []}
    try:
        held["the home folder"].append(Path.home())
    except (RuntimeError, OSError) as failure:       # no home on this account: nothing to keep out
        log.debug("no home folder to keep out of the model folders: %s", failure)
    temps = [os.environ.get(name, "") for name in ("TMPDIR", "TEMP", "TMP")] + [tempfile.gettempdir()]
    held["the temporary folder"] += [Path(t) for t in temps if str(t or "").strip()]
    return held


def refused_as_folder(folder) -> str:
    """Why `folder` cannot be a model folder, or '' when it can: it is, or holds, the home folder
    or the temporary folder (a drive's root holds both)."""
    try:
        resolved = Path(folder).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as failure:
        return f"it cannot be read ({failure})"
    for what, paths in _held_folders().items():
        for held in paths:
            try:
                held_resolved = held.expanduser().resolve()
            except (OSError, RuntimeError, ValueError):
                continue
            if held_resolved == resolved or resolved in held_resolved.parents:
                return f"it is {what}" if held_resolved == resolved else f"it holds {what}"
    return ""


_REFUSED_LOGGED: set = set()


def named_model_folders(settings: Optional[dict] = None) -> List[Path]:
    """The folders the user named: in the environment first, then in the setting (`settings`, when
    given, instead of the saved ones)."""
    saved = _settings() if settings is None else settings
    out: List[Path] = []
    for folder in _split(os.environ.get(ENV, "")) + _split(str(saved.get(SETTING) or "")):
        if folder not in out:
            out.append(folder)
    return out


def model_folders(settings: Optional[dict] = None, *, include_loaded: bool = True) -> List[Path]:
    """Every folder ELI looks in for models, in order, each once, existing ones only: its own
    folders, the folders of the models the settings name, the folder of the model loaded now, and
    the folders the user named. `settings` stands for the saved settings (the settings loader
    heals a path with this list while it reads them, so it cannot ask itself); `include_loaded`
    False leaves out the loaded model's folder, which asks the settings too. A folder that is or
    holds the home or the temporary folder is left out (refused_as_folder), said once per folder."""
    folders: List[Path] = []
    try:
        from eli.core.paths import data_dir, models_dir, project_root
        folders += [models_dir(), data_dir() / "models", project_root() / "models"]
    except (ImportError, OSError, RuntimeError, ValueError) as failure:   # a path helper that cannot answer leaves its folder out
        log.debug("a model folder of ELI's own could not be named: %s", failure)
    saved = _settings() if settings is None else settings
    for key in MODEL_SETTINGS:
        folder = _folder_of(str(saved.get(key) or ""))
        if folder is not None:
            folders.append(folder)
    if include_loaded:
        try:
            from eli.cognition.gguf_inference import get_model_path
            loaded = get_model_path()
            if loaded:
                folders.append(Path(loaded).expanduser().parent)
        except (ImportError, OSError, RuntimeError, AttributeError, ValueError) as failure:   # nothing loaded, or no inference here
            log.debug("the loaded model's folder could not be read: %s", failure)
    folders += [_anchored(f) for f in named_model_folders(saved)]
    out: List[Path] = []
    seen: set = set()
    for folder in folders:
        try:
            key = folder.resolve()
        except OSError:
            continue
        if key in seen or not key.is_dir():
            continue
        seen.add(key)
        why = refused_as_folder(key)
        if why:
            if key not in _REFUSED_LOGGED:
                _REFUSED_LOGGED.add(key)
                log.warning("[MODELS] %s is not searched for models: %s", key, why)
            continue
        out.append(folder)
    return out


def add_model_folder(path) -> bool:
    """Name a folder in the setting. True when it was not named before; ValueError for a folder
    that is or holds the home or the temporary folder (refused_as_folder)."""
    folder = Path(str(path or "")).expanduser()
    if not folder.is_dir():
        raise NotADirectoryError(str(folder))
    why = refused_as_folder(folder)
    if why:
        raise ValueError(f"{folder} cannot be a model folder: {why}, and every folder beneath it "
                         f"would be searched. Choose the folder the models are in.")
    current = _split(str(_settings().get(SETTING) or ""))
    if folder in current or folder in named_model_folders():
        return False
    from eli.core.runtime_settings import save_settings
    save_settings({SETTING: os.pathsep.join(str(p) for p in current + [folder])})
    return True


def remove_model_folder(path) -> bool:
    """Forget a folder named in the setting. False when it is not there, or is named by the
    environment, which only the shell that set it can change."""
    folder = Path(str(path or "")).expanduser()
    current = _split(str(_settings().get(SETTING) or ""))
    if folder not in current:
        return False
    from eli.core.runtime_settings import save_settings
    save_settings({SETTING: os.pathsep.join(str(p) for p in current if p != folder)})
    return True


def remember_folder_of(model_path) -> bool:
    """A model chosen from a folder ELI did not know: its folder joins the named ones, so the
    models beside it are offered next time. True when a folder was added."""
    folder = _folder_of(str(model_path or ""))
    if folder is None or not folder.is_dir() or refused_as_folder(folder):
        return False
    try:
        known = {f.resolve() for f in model_folders()}
        if folder.resolve() in known:
            return False
    except OSError:
        return False
    return add_model_folder(folder)


def chat_models(current: Optional[Path] = None) -> Dict[str, Path]:
    """Every chat model in every model folder, by the name it is shown under; two files with one
    name are told apart by their folders. `current`, a model loaded from elsewhere on the disk, is
    offered as well: it is ELI's too."""
    import eli.core.hardware_profile as hardware
    paths: List[Path] = []
    for folder in model_folders():
        try:
            for model in hardware.discover_models(folder):
                path = Path(model["path"])
                if path not in paths:
                    paths.append(path)
        except (OSError, ValueError, KeyError, RuntimeError) as failure:   # one unreadable folder does not hide the others
            log.debug("model folder %s could not be scanned: %s", folder, failure)
    if current is not None and current not in paths:
        paths.append(current)
    out: Dict[str, Path] = {}
    for path in sorted(paths, key=lambda p: p.name.lower()):
        name = path.name
        if name in out:
            name = f"{path.name} ({path.parent.name})"
        out[name] = path
    return out


__all__ = ["ENV", "MODEL_SETTINGS", "SETTING", "add_model_folder", "chat_models", "model_folders",
           "named_model_folders", "refused_as_folder", "remember_folder_of", "remove_model_folder"]
