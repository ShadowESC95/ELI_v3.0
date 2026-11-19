"""Cross-platform system helpers shared by executor and effectors (v3).

Leaf module — no imports from executor_enhanced to avoid circular dependencies.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict

from eli.utils.log import get_logger

log = get_logger(__name__)


def open_app_with_timeout(argv, timeout=30):
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=0.7)
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": out,
                "stderr": err,
                "argv": argv,
            }
        except subprocess.TimeoutExpired:
            return {"ok": True, "spawned": True, "pid": proc.pid, "argv": argv}
    except Exception as e:
        return {"ok": False, "error": repr(e), "argv": argv}


def open_file_system(path: str = "~") -> Dict[str, Any]:
    try:
        from eli.utils.platform_compat import open_file
        target = os.path.expanduser(path)
        if open_file(target):
            msg = f"Opened folder: {target}"
            return {"ok": True, "action": "OPEN_FILE_SYSTEM", "path": target,
                    "content": msg, "response": msg}
        msg = f"Could not open folder: {target}"
        return {"ok": False, "action": "OPEN_FILE_SYSTEM", "path": target,
                "content": msg, "response": msg}
    except Exception as e:
        msg = "Failed to open file system."
        return {"ok": False, "action": "OPEN_FILE_SYSTEM", "error": repr(e),
                "content": msg, "response": msg}


def open_browser(url: str = "https://duckduckgo.com", urls: list | None = None) -> Dict[str, Any]:
    try:
        from eli.utils.platform_compat import open_url
        targets = [str(u) for u in (urls or []) if str(u).strip()]
        if not targets:
            targets = [str(url)]
        opened = 0
        for target in targets:
            if open_url(target):
                opened += 1
        if opened == 0:
            msg = (
                "Couldn't open the browser — it may be hung or missing. "
                "On Linux set ELI_BROWSER=chromium or close Firefox and try again."
            )
            return {"ok": False, "action": "OPEN_BROWSER", "content": msg, "response": msg}
        if len(targets) == 1:
            msg = f"Opened browser: {targets[0]}"
            return {"ok": True, "action": "OPEN_BROWSER", "url": targets[0],
                    "content": msg, "response": msg}
        msg = f"Opened {opened} browser tab(s)."
        return {"ok": True, "action": "OPEN_BROWSER", "urls": targets,
                "content": msg, "response": msg}
    except Exception as e:
        msg = f"Failed to open browser: {e}"
        return {"ok": False, "action": "OPEN_BROWSER", "error": repr(e),
                "content": msg, "response": msg}


def _run_ok(argv, timeout: int = 5) -> bool:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


def _volume_done(said: str, asked: int | None = None) -> Dict[str, Any]:
    """A volume change that went through, with the level read back after it: the reply and the turn's
    record hold the real figure. Asked "current volume?" later, the model had only "Volume down" to
    go on and said 50% for a level of 45%."""
    try:
        from eli.utils.platform_compat import get_volume
        level = get_volume()
    except Exception:
        log.debug("volume not read back", exc_info=True)
        level = None
    msg = said if level is None or (asked is not None and int(level) == int(asked)) else f"{said} — now {int(level)}%"
    out = {"ok": True, "action": "VOLUME", "content": msg, "response": msg}
    if level is not None:
        out["level"] = int(level)
    return out


def volume_fallback(direction: str, delta: int = 10, level: int | None = None) -> Dict[str, Any]:
    """Volume on every OS: platform_compat first (Windows audio API or volume keys, macOS osascript,
    Linux), then Linux's own tools. No direction and no level is a question: the level is read."""
    direction = (direction or "").strip().lower()
    if not direction and level is None:
        direction = "get"
    if direction == "set" and level is None:
        direction = "get"
    try:
        from eli.utils.platform_compat import adjust_volume, get_volume, set_volume, set_muted
        if direction == "set" and level is not None:
            if set_volume(int(level)):
                return _volume_done(f"Volume set to {int(level)}%", int(level))
        if direction in ("up", "raise"):
            if adjust_volume(int(delta)):
                return _volume_done("Volume up")
        if direction in ("down", "lower"):
            if adjust_volume(-int(delta)):
                return _volume_done("Volume down")
        if direction == "mute":
            if set_muted(True):
                return _volume_done("Muted")
        if direction == "unmute":
            if set_muted(False):
                return _volume_done("Unmuted")
        if direction == "get":
            vol = get_volume()
            if vol is not None:
                msg = f"Volume is {int(vol)}%"
                return {"ok": True, "action": "VOLUME", "level": int(vol), "content": msg, "response": msg}
            msg = "I couldn't read the volume: no audio tool on this system answered."
            return {"ok": False, "action": "VOLUME", "error": "volume_unreadable", "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": "VOLUME", "error": str(e),
                "content": str(e), "response": str(e)}

    # Legacy direct-tool path (kept for environments where platform_compat misses)
    try:
        if direction == "set" and level is not None:
            if shutil.which("wpctl") and _run_ok(
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{int(level)}%"]
            ):
                return _volume_done(f"Volume set to {int(level)}%", int(level))
            if shutil.which("pactl") and _run_ok(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{int(level)}%"]
            ):
                return _volume_done(f"Volume set to {int(level)}%", int(level))
        if direction in ("up", "raise"):
            for tool, argv in (
                ("wpctl", ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{int(delta)}%+"]),
                ("pactl", ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{int(delta)}%"]),
                ("amixer", ["amixer", "-D", "pulse", "sset", "Master", f"{int(delta)}%+"]),
            ):
                if shutil.which(tool) and _run_ok(argv):
                    return _volume_done("Volume up")
        if direction in ("down", "lower"):
            for tool, argv in (
                ("wpctl", ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{int(delta)}%-"]),
                ("pactl", ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{int(delta)}%"]),
                ("amixer", ["amixer", "-D", "pulse", "sset", "Master", f"{int(delta)}%-"]),
            ):
                if shutil.which(tool) and _run_ok(argv):
                    return _volume_done("Volume down")
    except Exception as e:
        return {"ok": False, "action": "VOLUME", "error": str(e),
                "content": str(e), "response": str(e)}

    from eli.utils import platform_compat as _pc_vol
    if _pc_vol.WINDOWS:
        msg = "Volume control failed — Windows' audio API and the volume keys both refused"
    elif _pc_vol.MACOS:
        msg = "Volume control failed — macOS did not accept the change (osascript)"
    else:
        msg = "Volume control failed — no wpctl, pactl, or amixer available"
    return {"ok": False, "action": "VOLUME", "content": msg, "response": msg}
