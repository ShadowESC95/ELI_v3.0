"""This machine's identity, the same in every ELI product.

A trial key, and a local trial, is bound to one machine. The fingerprint is a SHA-256 of the
identity the operating system assigns to this installation of itself (``/etc/machine-id``,
Windows' ``MachineGuid``, the Mac's ``IOPlatformUUID``), never of a network card or the host
name, which change under people's feet. It is the formula ELI v3 binds its activations with, so
a fingerprint taken here matches the one ELI v3 records on the same machine.

Where the system offers no identity (some containers), an id made once is kept in this user's
own folder, found from the account itself rather than from environment variables.
"""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
import uuid
from functools import lru_cache
from pathlib import Path

_PREFIX = "eliv3-machine-v2"


def account_home() -> Path:
    """This account's home folder as the system records it, not as $HOME says: changing an
    environment variable must not make the same person on the same machine look new."""
    if os.name == "nt":
        known = _windows_known_folder("{5E6C858F-0E22-4760-9AFE-EA3317B67173}")  # Profile
        if known is not None:
            return known
        return Path(os.path.expanduser("~"))
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        return Path(os.path.expanduser("~"))


def _windows_known_folder(guid: str) -> Path | None:
    """SHGetKnownFolderPath: the folder Windows has on record, whatever %APPDATA% says."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

        raw = uuid.UUID(guid)
        g = GUID(raw.fields[0], raw.fields[1], raw.fields[2],
                 (wintypes.BYTE * 8).from_buffer_copy(raw.bytes[8:]))
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            return Path(out.value) if out.value else None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]
    except Exception:
        return None


def windows_folder(name: str) -> Path | None:
    """%APPDATA% (``roaming``) or %LOCALAPPDATA% (``local``) as Windows records them."""
    guids = {"roaming": "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}",
             "local": "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}"}
    return _windows_known_folder(guids[name]) if os.name == "nt" else None


def _first_line(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip() if path.is_file() else ""
    except OSError:
        return ""


def _linux_machine_id() -> str:
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        value = _first_line(candidate)
        if value:
            return value
    return ""


def _windows_machine_guid() -> str:
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
        return str(value).strip()
    except OSError:
        return ""


def _mac_platform_uuid() -> str:
    try:
        out = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                             capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if "IOPlatformUUID" in line and '"' in line:
            return line.rsplit('"', 2)[-2].strip()
    return ""


def _kept_install_id() -> str:
    """Last resort: an id made once and kept in this account's own folder."""
    path = account_home() / ".eli-machine-id"
    existing = _first_line(path)
    if existing:
        return existing
    value = uuid.uuid4().hex
    try:
        path.write_text(value + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        return f"ephemeral-{uuid.getnode():012x}"
    return value


def install_id() -> str:
    """The identity the operating system gives this installation of itself."""
    if sys.platform == "win32":
        value = _windows_machine_guid()
    elif sys.platform == "darwin":
        value = _mac_platform_uuid()
    else:
        value = _linux_machine_id()
    return value or _kept_install_id()


@lru_cache(maxsize=1)
def fingerprint() -> str:
    """This machine, as a SHA-256 hex digest: what a machine-bound key or trial names."""
    parts = [_PREFIX, platform.system(), platform.machine(), install_id(), ""]
    return hashlib.sha256("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()
