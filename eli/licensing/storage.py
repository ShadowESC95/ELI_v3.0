from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from eli.licensing.types import ActivationState


def license_dir() -> Path:
    from eli.core.paths import license_dir as _license_dir

    return _license_dir()


def activation_state_path() -> Path:
    return license_dir() / "activation.json"


def ensure_license_dir() -> Path:
    d = license_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_activation_state() -> ActivationState | None:
    path = activation_state_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return ActivationState.from_dict(raw)
    except Exception:
        return None


def save_activation_state(state: ActivationState) -> Path:
    ensure_license_dir()
    path = activation_state_path()
    payload = json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="eli-license-", suffix=".json", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def clear_activation_state() -> bool:
    path = activation_state_path()
    if path.is_file():
        path.unlink()
        return True
    return False
