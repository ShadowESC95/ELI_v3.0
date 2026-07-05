from __future__ import annotations

import hashlib
import os
import platform
import uuid
from functools import lru_cache
from pathlib import Path


def _read_machine_id_file() -> str:
    for candidate in (
        Path("/etc/machine-id"),
        Path("/var/lib/dbus/machine-id"),
    ):
        try:
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8", errors="ignore").strip()
                if value:
                    return value
        except OSError:
            continue
    return ""


@lru_cache(maxsize=1)
def machine_fingerprint() -> str:
    """Stable, privacy-conscious machine fingerprint (SHA-256 hex)."""
    parts = [
        platform.system(),
        platform.machine(),
        platform.node() or "",
        _read_machine_id_file(),
        str(uuid.getnode()),
        os.environ.get("ELI_MACHINE_ID", "").strip(),
    ]
    blob = "|".join(parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(blob).hexdigest()


def machine_label() -> str:
    return (platform.node() or "unknown-host").strip()[:128]
