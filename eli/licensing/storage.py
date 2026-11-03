from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from eli.licensing.products import PLATFORM
from eli.licensing.types import ActivationState


def license_dir() -> Path:
    from eli.core.paths import license_dir as _license_dir

    return _license_dir()


def activation_state_path(product: str = PLATFORM) -> Path:
    """ELI v3's activation is ``activation.json``; each bundle product has its own file
    beside it, so activating a bundle can never disturb ELI v3's licence or trial."""
    if product == PLATFORM:
        return license_dir() / "activation.json"
    return license_dir() / f"activation-{product}.json"


def ensure_license_dir() -> Path:
    d = license_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_activation_state(product: str = PLATFORM) -> ActivationState | None:
    path = activation_state_path(product)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        state = ActivationState.from_dict(raw)
        # A file that names another product is not this product's activation.
        return state if state.product == product else None
    except Exception:
        return None


def save_activation_state(state: ActivationState) -> Path:
    ensure_license_dir()
    path = activation_state_path(state.product)
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


def clear_activation_state(product: str = PLATFORM) -> bool:
    path = activation_state_path(product)
    if path.is_file():
        path.unlink()
        return True
    return False


# ── The keys every ELI product shares ────────────────────────────────────────
# One file per product ("eli.key", "vr.key", ...) in one folder every ELI product reads, so a key
# entered in any of them (ELI v3, ELI VR, ELI Legal) is there for the rest. The same folder and
# rules as ELI VR's licence check and the ELI SDK.

def shared_keys_dir() -> Path:
    named = os.environ.get("ELI_KEYS_DIR", "").strip()
    if named:
        return Path(named).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "ELI Keys"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ELI Keys"
    config = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(config) if config else Path.home() / ".config") / "eli-keys"


def share_key(product: str, key: str) -> Path | None:
    """Keep a key where every ELI product finds it. None when the folder cannot be written."""
    folder = shared_keys_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{product}-", suffix=".key", dir=str(folder))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key.strip() + "\n")
        os.chmod(tmp, 0o600)
        target = folder / f"{product}.key"
        os.replace(tmp, target)
        return target
    except OSError:
        return None


def unshare_key(product: str) -> None:
    """Take a product's key out of the shared folder (deactivated on this computer)."""
    try:
        (shared_keys_dir() / f"{product}.key").unlink()
    except OSError:
        return


def shared_key(product: str) -> str:
    """The key another ELI product kept for `product`, or "" when there is none."""
    try:
        return (shared_keys_dir() / f"{product}.key").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
