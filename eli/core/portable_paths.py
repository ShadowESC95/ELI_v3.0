from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

PATH_KEY_RE = re.compile(r"(path|dir|file|folder|home|root|model)", re.I)


def project_root(start: str | Path | None = None) -> Path:
    """
    Resolve the ELI project root dynamically on any user's machine.

    Priority:
    1. ELI_HOME / ELI_PROJECT_ROOT environment variable.
    2. Walk upward from this file or supplied start path.
    3. Current working directory fallback.
    """
    for key in ("ELI_HOME", "ELI_PROJECT_ROOT"):
        raw = os.environ.get(key)
        if raw:
            p = Path(raw).expanduser().resolve()
            if (p / "eli").is_dir():
                return p

    p = Path(start or __file__).resolve()
    if p.is_file():
        p = p.parent

    for cur in (p, *p.parents):
        if (cur / "eli").is_dir() and (cur / "config").exists():
            return cur

    return Path.cwd().resolve()


PROJECT_ROOT = project_root()


def expand_project_vars(value: str, root: str | Path | None = None) -> str:
    """
    Expand portable project placeholders and normal OS variables.

    Supported:
    - ${ELI_HOME}
    - ${ELI_PROJECT_ROOT}
    - ${PROJECT_ROOT}
    - %ELI_HOME%
    - %ELI_PROJECT_ROOT%
    - %PROJECT_ROOT%
    - ~
    - normal environment variables
    """
    root_path = Path(root or PROJECT_ROOT).resolve()
    s = str(value)

    replacements = {
        "${ELI_HOME}": str(root_path),
        "${ELI_PROJECT_ROOT}": str(root_path),
        "${PROJECT_ROOT}": str(root_path),
        "%ELI_HOME%": str(root_path),
        "%ELI_PROJECT_ROOT%": str(root_path),
        "%PROJECT_ROOT%": str(root_path),
    }

    for k, v in replacements.items():
        s = s.replace(k, v)

    return os.path.expandvars(os.path.expanduser(s))


def resolve_path_value(value: Any, root: str | Path | None = None) -> Any:
    """
    Convert a config path value into an absolute path for this machine.

    Non-strings are returned unchanged.
    URLs are returned unchanged.
    Relative paths are resolved against the detected ELI project root.
    """
    if not isinstance(value, str):
        return value

    s = value.strip()
    if not s:
        return value

    if "://" in s:
        return value

    expanded = expand_project_vars(s, root)
    p = Path(expanded)

    if p.is_absolute():
        return str(p)

    return str((Path(root or PROJECT_ROOT).resolve() / p).resolve())


def make_portable_path_value(value: Any, root: str | Path | None = None) -> Any:
    """
    A path inside this install becomes relative to it, so the install can move; every other
    path is the user's and is kept exactly as given.

    Example:
        <ELI_PROJECT_ROOT>/models/x.gguf   ->  models/x.gguf
        /data/llm/models/x.gguf            ->  /data/llm/models/x.gguf

    A path that merely looked like an ELI layout (a 'models/' folder anywhere, a folder named
    ELI-something under a home) used to be cut down to that tail and then looked for inside this
    install: a model kept in another program's models folder, or in another ELI product's, was lost.
    """
    if not isinstance(value, str):
        return value

    s = value.strip()
    if not s or "://" in s:
        return value

    root_path = Path(root or PROJECT_ROOT).resolve()
    root_s = str(root_path).replace("\\", "/").rstrip("/")
    norm = s.replace("\\", "/")
    # Windows compares names without case (normcase); elsewhere case is part of the name.
    same = os.path.normcase

    if same(norm) == same(root_s):
        return "."

    if same(norm).startswith(same(root_s + "/")):
        return norm[len(root_s) + 1:]

    return value


def normalize_settings_obj(obj: Any, root: str | Path | None = None) -> Any:
    """
    Recursively normalize settings dictionaries.

    Path-like keys are made portable.
    Nested dict/list structures are supported.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and PATH_KEY_RE.search(k) and isinstance(v, str):
                out[k] = make_portable_path_value(v, root)
            else:
                out[k] = normalize_settings_obj(v, root)
        return out

    if isinstance(obj, list):
        return [normalize_settings_obj(x, root) for x in obj]

    return obj
