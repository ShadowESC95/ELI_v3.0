"""Stable machine identity for license binding.

v2 fingerprints deliberately exclude two inputs the v1 scheme used:

* ``uuid.getnode()`` — resolves to whichever NIC the runtime happens to pick.
  A frozen bundle (libuuid via the ``_uuid`` extension) and a source checkout
  (pure-python fallback) can choose *different* interfaces on the same host, and
  the answer also moves when Docker/VPN/VM bridges appear or wifi MAC
  randomisation kicks in. Binding to it produced spurious
  "this machine is not activated" failures on legitimately licensed machines.
* ``platform.node()`` — the hostname, which users rename freely.

What remains is the OS-assigned install identity, which survives NIC changes,
renames and CPU upgrades. ``legacy_machine_fingerprints()`` reproduces the v1
values so activations created before this change keep working and can be
migrated in place.
"""
from __future__ import annotations

import hashlib
import logging
import platform
import subprocess
import sys
import uuid
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_V2_PREFIX = "eliv3-machine-v2"


def _read_first_line(path: Path) -> str:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        log.debug("unreadable identity file %s", path, exc_info=True)
    return ""


def _read_machine_id_file() -> str:
    for candidate in (
        Path("/etc/machine-id"),
        Path("/var/lib/dbus/machine-id"),
    ):
        value = _read_first_line(candidate)
        if value:
            return value
    return ""


def _windows_machine_guid() -> str:
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
        return str(value).strip()
    except OSError:
        return ""


def _macos_platform_uuid() -> str:
    try:
        out = subprocess.run(
            ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if "IOPlatformUUID" in line and '"' in line:
            return line.rsplit('"', 2)[-2].strip()
    return ""


def _persisted_install_id() -> str:
    """Last-resort identity: a UUID generated once and kept on disk.

    Only reached when the OS exposes no install id (hardened containers, some
    minimal images). Stored beside the activation state so it shares that
    directory's lifetime.
    """
    try:
        from eli.licensing.storage import ensure_license_dir

        path = ensure_license_dir() / "machine.id"
    except Exception:
        path = Path.home() / ".eli-v3-machine-id"

    existing = _read_first_line(path)
    if existing:
        return existing

    value = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        # Unwritable: fall back to a value stable for this boot at least, so a
        # single session behaves consistently even if it cannot be persisted.
        return f"ephemeral-{uuid.getnode():012x}"
    return value


def os_install_id() -> str:
    """OS-assigned install identity, or a persisted UUID if unavailable."""
    if sys.platform == "win32":
        value = _windows_machine_guid()
    elif sys.platform == "darwin":
        value = _macos_platform_uuid()
    else:
        value = _read_machine_id_file()
    return value or _persisted_install_id()


def _machine_id_override() -> str:
    """ELI_MACHINE_ID, read only when working from source: in a sold build every new value
    would be a new machine, and so a fresh trial."""
    from eli.licensing.activation import developer_override

    return developer_override("ELI_MACHINE_ID")


@lru_cache(maxsize=1)
def machine_fingerprint() -> str:
    """Stable, privacy-conscious machine fingerprint (SHA-256 hex)."""
    override = _machine_id_override()
    parts = [
        _V2_PREFIX,
        platform.system(),
        platform.machine(),
        os_install_id(),
        override,
    ]
    blob = "|".join(parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(blob).hexdigest()


def _nic_nodes() -> list[int]:
    """Every MAC-derived node value this host could yield, for v1 replay."""
    nodes: list[int] = []
    try:
        nodes.append(uuid.getnode())
    except Exception:
        log.debug("uuid.getnode() unavailable for legacy replay", exc_info=True)
    try:
        for addr in sorted(Path("/sys/class/net").glob("*/address")):
            mac = _read_first_line(addr)
            if not mac:
                continue
            try:
                nodes.append(int(mac.replace(":", ""), 16))
            except ValueError:
                continue
    except OSError:
        log.debug("could not enumerate /sys/class/net for legacy replay", exc_info=True)
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(nodes))


@lru_cache(maxsize=1)
def legacy_machine_fingerprints() -> tuple[str, ...]:
    """v1 fingerprints for this host, for migrating existing activations.

    The v1 formula was
    ``sha256(system|machine|node|machine-id|getnode()|ELI_MACHINE_ID)``. Because
    ``getnode()`` was build-dependent, every plausible NIC value is replayed so
    a bundle-created binding is recognised by a checkout and vice versa.
    """
    override = _machine_id_override()
    machine_id_file = _read_machine_id_file()
    out: list[str] = []
    for node in _nic_nodes():
        parts = [
            platform.system(),
            platform.machine(),
            platform.node() or "",
            machine_id_file,
            str(node),
            override,
        ]
        blob = "|".join(parts).encode("utf-8", errors="ignore")
        out.append(hashlib.sha256(blob).hexdigest())
    return tuple(dict.fromkeys(out))


def machine_label() -> str:
    return (platform.node() or "unknown-host").strip()[:128]
