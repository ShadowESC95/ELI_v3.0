"""Window-control effector — minimise/maximise/focus/close/cycle/workspace."""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Dict

from eli.execution.effectors._base import _run_ok


def _h_minimise_all(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if shutil.which("wmctrl"):
        ok = _run_ok(["wmctrl", "-k", "on"])
        msg = "Showed desktop." if ok else "Failed to minimise all windows."
        return {"ok": ok, "action": a, "content": msg, "response": msg}
    if shutil.which("xdotool"):
        ok = _run_ok(["xdotool", "key", "super+d"])
        msg = "Triggered show-desktop shortcut." if ok else "Failed to minimise all windows."
        return {"ok": ok, "action": a, "content": msg, "response": msg}
    return {"ok": False, "action": a, "error": "wmctrl/xdotool not installed",
            "content": "wmctrl or xdotool required to minimise all windows.",
            "response": "wmctrl or xdotool required to minimise all windows."}


def _h_minimize_app(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from eli.system.portable_app_control import minimize_app
    name = str(args.get("name") or args.get("target") or args.get("app") or "").strip()
    return minimize_app(name)


def _h_restore_windows(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if shutil.which("wmctrl"):
        ok = _run_ok(["wmctrl", "-k", "off"])
        msg = "Restored windows." if ok else "Failed to restore windows."
        return {"ok": ok, "action": a, "content": msg, "response": msg}
    return {"ok": False, "action": a, "error": "wmctrl not installed",
            "content": "wmctrl required to restore windows.",
            "response": "wmctrl required to restore windows."}


def _h_maximise_window(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    # The router extracts the target correctly ("maximise prime video" →
    # window_name="prime video"); this branch used to discard it and
    # maximise :ACTIVE: regardless, so it always acted on whatever window
    # happened to be focused. A named target is now honoured, and when the
    # named window cannot be found we say so instead of falling back to
    # the active window — acting on a different window than the one asked
    # for is the defect, not a graceful degradation.
    _mx_name = str(
        args.get("name") or args.get("target") or args.get("app")
        or args.get("window_name") or args.get("window") or ""
    ).strip()
    from eli.system.portable_app_control import BARE_WINDOW_TARGETS as _BWT
    if _mx_name.lower() in _BWT:
        _mx_name = ""
    if _mx_name:
        from eli.system.portable_app_control import maximize_app
        _mx = maximize_app(_mx_name)
        _mx.setdefault("action", a)
        return _mx

    if shutil.which("wmctrl"):
        ok = _run_ok(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"])
        msg = "Maximised current window." if ok else "Failed to maximise window."
        return {"ok": ok, "action": a, "content": msg, "response": msg}
    if shutil.which("xdotool"):
        ok = _run_ok(["xdotool", "key", "super+Up"])
        msg = "Triggered maximise shortcut." if ok else "Failed to maximise window."
        return {"ok": ok, "action": a, "content": msg, "response": msg}
    return {"ok": False, "action": a, "error": "wmctrl/xdotool not installed",
            "content": "wmctrl or xdotool required to maximise.",
            "response": "wmctrl or xdotool required to maximise."}



def _h_next_window(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if shutil.which("xdotool"):
        ok = _run_ok(["xdotool", "key", "alt+Tab"])
        msg = "Switched to next window." if ok else "Failed to switch window."
        return {"ok": ok, "action": a, "content": msg, "response": msg}
    return {"ok": False, "action": a, "error": "xdotool not installed",
            "content": "xdotool required to cycle windows.",
            "response": "xdotool required to cycle windows."}


def _h_previous_window(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if shutil.which("xdotool"):
        ok = _run_ok(["xdotool", "key", "alt+shift+Tab"])
        msg = "Switched to previous window." if ok else "Failed to switch window."
        return {"ok": ok, "action": a, "content": msg, "response": msg}
    return {"ok": False, "action": a, "error": "xdotool not installed",
            "content": "xdotool required to cycle windows.",
            "response": "xdotool required to cycle windows."}


def _h_switch_workspace(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    direction = str(args.get("direction") or "right").lower()
    if shutil.which("xdotool"):
        key = "ctrl+alt+Right" if direction == "right" else "ctrl+alt+Left"
        ok = _run_ok(["xdotool", "key", key])
        msg = f"Switched workspace {direction}." if ok else "Failed to switch workspace."
        return {"ok": ok, "action": a, "content": msg, "response": msg}
    return {"ok": False, "action": a, "error": "xdotool not installed",
            "content": "xdotool required to switch workspaces.",
            "response": "xdotool required to switch workspaces."}


def _h_focus_app(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args.get("name") or args.get("app") or "").strip()
    if not name:
        return {"ok": False, "action": a, "error": "missing app name",
                "content": "Specify app to focus.", "response": "Specify app to focus."}
    if shutil.which("wmctrl"):
        ok = _run_ok(["wmctrl", "-a", name])
        msg = f"Focused {name}." if ok else f"Could not focus {name} — window not found."
        return {"ok": ok, "action": a, "content": msg, "response": msg}
    if shutil.which("xdotool"):
        try:
            proc = subprocess.run(
                ["xdotool", "search", "--name", name],
                capture_output=True, text=True, timeout=4
            )
            ids = [w for w in (proc.stdout or "").split() if w.strip()]
            if ids:
                ok = _run_ok(["xdotool", "windowactivate", ids[0]])
                msg = f"Focused {name}." if ok else f"Could not focus {name}."
                return {"ok": ok, "action": a, "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e),
                    "content": str(e), "response": str(e)}
    return {"ok": False, "action": a, "error": "wmctrl/xdotool not installed",
            "content": "wmctrl or xdotool required to focus apps.",
            "response": "wmctrl or xdotool required to focus apps."}


def _h_close_app(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    # This is the fallback path: portable_app_control.close_app runs first
    # and only reaches here when it could not find a matching window. It
    # used to end in a bare `pkill -f <name>` loop, which matches the full
    # command line of every process as a regex — "close file" matched
    # `dbus-daemon --session ... --nopidfile` and logged the user out of
    # their desktop, while still reporting the close as a success.
    # Every kill now goes through the verified guard, which dry-runs the
    # pattern and refuses anything that would signal a protected process.
    from eli.system.process_guard import safe_pkill

    name = str(args.get("name") or args.get("app") or args.get("target") or "").strip()
    if not name:
        return {"ok": False, "action": a, "error": "missing app name", "content": "Specify app to close.", "response": "Specify app to close."}

    # Window-scoped close first — Linux only; portable_app_control handles all OSes upstream.
    from eli.utils import platform_compat as _pc_close
    if _pc_close.LINUX and shutil.which("wmctrl") and _run_ok(["wmctrl", "-c", name]):
        msg = f"Closed {name} via wmctrl."
        return {"ok": True, "action": a, "content": msg, "response": msg}

    # Then process kills, exact name before command line, both verified.
    refusals = []
    for full_cmdline in (False, True):
        res = safe_pkill(name, full_cmdline=full_cmdline)
        if res.get("ok"):
            how = "command-line match" if full_cmdline else "process name"
            msg = f"Closed {name} ({res.get('killed')} process(es), matched by {how})."
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "kill_plan": res.get("plan")}
        reason = str(res.get("reason") or "").strip()
        if reason and reason not in refusals:
            refusals.append(reason)

    # Nothing was closed. Say why, rather than reporting a phantom success.
    detail = f" ({refusals[0]})" if refusals else ""
    msg = f"Could not close {name}{detail}."
    return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg,
            "refusals": refusals}



def _h_tile_windows(a, args):
    from eli.system.portable_app_control import tile_windows
    out = tile_windows()
    out.setdefault("action", a)
    return out


def _h_open_in_ide(a, args):
    path = str(args.get("path") or "").strip()
    for editor in ["code", "codium", "subl", "gedit", "kate"]:
        try:
            cmd = [editor] + ([path] if path else [])
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            msg = f"Opened {path or 'IDE'} in {editor}"
            return {"ok": True, "action": a, "content": msg, "response": msg}
        except FileNotFoundError:
            continue
    return {"ok": False, "action": a, "error": "No IDE found", "content": "No IDE found", "response": "No IDE found"}


from eli.execution.effectors.registry import register as _register

_MIN_APP_ALIASES = ("MINIMIZE_APP", "MINIMISE_APP", "HIDE_APP", "MINIMIZE_WINDOW", "MINIMISE_WINDOW")

_HANDLERS = {
    "MINIMISE_ALL": _h_minimise_all,
    "RESTORE_WINDOWS": _h_restore_windows,
    "MAXIMISE_WINDOW": _h_maximise_window,
    "NEXT_WINDOW": _h_next_window,
    "PREVIOUS_WINDOW": _h_previous_window,
    "SWITCH_WORKSPACE": _h_switch_workspace,
    "FOCUS_APP": _h_focus_app,
    "CLOSE_APP": _h_close_app,
}
for _alias in _MIN_APP_ALIASES:
    _HANDLERS[_alias] = _h_minimize_app

for _action, _fn in _HANDLERS.items():
    _register(_action, _fn)

_register("TILE_WINDOWS", _h_tile_windows)
_register("OPEN_IN_IDE", _h_open_in_ide)
