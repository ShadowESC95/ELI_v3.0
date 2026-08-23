"""Input-control effector — keyboard, mouse, and clipboard."""
from __future__ import annotations

import shutil
from typing import Any, Dict

from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_keyboard(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from eli.perception.os_controller import press_key, type_text
        if args.get("type"):
            return type_text(str(args["type"]))
        elif args.get("key"):
            return press_key(str(args["key"]))
        return {"ok": False, "action": a, "error": "No key or text specified", "content": "No key or text specified", "response": "No key or text specified"}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_mouse_control(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    # Rewritten after a live session in which "click enter", "click enter
    # button" and "move cursor right" moved nothing at all while the
    # executor reported every one of them as a success. Four faults:
    #   * an unrecognised action fell into an `else` that set
    #     "Mouse action '<x>' performed" and returned ok=True having
    #     touched nothing;
    #   * both click branches required x AND y, so a click without
    #     coordinates matched nothing and hit that same else;
    #   * relative movement was never implemented, so a direction-only
    #     request ("move cursor right") had no branch to land in;
    #   * "enter" arrived in the *button* slot. A key is not a mouse
    #     button and can never be clicked, so it could only ever fail.
    # Nothing below reports success unless a backend actually acted.
    import subprocess as _sp
    import sys as _sys

    _mc_action = str(args.get("action") or args.get("command") or "").strip().lower()
    _mc_x, _mc_y = args.get("x"), args.get("y")
    _mc_button = str(args.get("button") or "left").strip().lower()
    _mc_double = bool(args.get("double", False))
    _mc_dir = str(args.get("direction") or "").strip().lower()
    try:
        _mc_amount = int(args.get("amount") or args.get("distance") or args.get("pixels") or 0)
    except Exception:
        _mc_amount = 0

    # Keys that turn up in the button/action slot when the user says
    # "click enter" — route them to the keyboard instead of failing.
    _MC_KEYNAMES = {
        "enter", "return", "escape", "esc", "space", "spacebar", "tab",
        "backspace", "delete", "del", "insert", "home", "end",
        "pageup", "page up", "pagedown", "page down", "capslock",
        "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    }
    _mc_key = None
    if _mc_button in _MC_KEYNAMES:
        _mc_key = _mc_button
    elif _mc_action in _MC_KEYNAMES:
        _mc_key = _mc_action
    if _mc_key:
        from eli.perception.os_controller import press_key
        _kr = press_key(_mc_key)
        if isinstance(_kr, dict):
            _kr.setdefault("action", "KEYBOARD")
            _kr["rerouted_from"] = "MOUSE_CONTROL"
            if _kr.get("ok"):
                _km = f"Pressed {_mc_key} (that is a key, not a mouse button)."
                _kr["content"] = _kr["response"] = _km
            return _kr

    if _mc_button not in {"left", "right", "middle"}:
        _mm = (f"'{_mc_button}' is not a mouse button. Use left, right or middle — "
               f"or name a key and I will press it instead.")
        return {"ok": False, "action": a, "error": _mm, "content": _mm, "response": _mm}

    if not _mc_action:
        _mc_action = "move" if (_mc_dir or _mc_x is not None) else ""
    if _mc_action in {"move", "click"} and not _mc_dir and _mc_x is None and _mc_action == "move":
        _mm = "Tell me where to move the pointer — coordinates, or a direction like 'right'."
        return {"ok": False, "action": a, "error": _mm, "content": _mm, "response": _mm}
    if _mc_action not in {"move", "click", "scroll"}:
        _mm = (f"I don't have a mouse action called '{_mc_action or 'nothing'}'. "
               f"I can move, click or scroll.")
        return {"ok": False, "action": a, "error": _mm, "content": _mm, "response": _mm}

    # Relative movement: a direction plus a step, defaulting to 100px.
    _mc_step = _mc_amount if _mc_amount > 0 else 100
    _mc_dx = {"right": _mc_step, "left": -_mc_step}.get(_mc_dir, 0)
    _mc_dy = {"down": _mc_step, "up": -_mc_step}.get(_mc_dir, 0)
    if _mc_action == "move" and _mc_x is None and not (_mc_dx or _mc_dy):
        _mm = f"I don't know which way '{_mc_dir}' is — try left, right, up or down."
        return {"ok": False, "action": a, "error": _mm, "content": _mm, "response": _mm}
    if _mc_action == "scroll" and _mc_dir not in {"up", "down"}:
        _mc_dir = "down"
    _mc_scroll_n = _mc_amount if _mc_amount > 0 else 3

    def _mc_run(cmd) -> bool:
        try:
            return _sp.run(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                           timeout=8, check=False).returncode == 0
        except Exception:
            return False

    def _mc_native():
        """xdotool (X11) / ydotool (Wayland). Subprocesses, so no X11
        display handles are leaked the way the pyautogui path leaks them."""
        tool = ("ydotool" if shutil.which("ydotool")
                else ("xdotool" if shutil.which("xdotool") else None))
        if tool is None:
            return None
        wayland = tool == "ydotool"
        if _mc_action == "move":
            if _mc_x is not None and _mc_y is not None:
                cmd = ([tool, "mousemove", "-a", "--", str(int(_mc_x)), str(int(_mc_y))]
                       if wayland else
                       [tool, "mousemove", str(int(_mc_x)), str(int(_mc_y))])
                ok = _mc_run(cmd)
                return (ok, f"Moved the pointer to ({int(_mc_x)}, {int(_mc_y)})." if ok
                        else "The pointer did not move.")
            cmd = ([tool, "mousemove", "--", str(_mc_dx), str(_mc_dy)] if wayland
                   else [tool, "mousemove_relative", "--", str(_mc_dx), str(_mc_dy)])
            ok = _mc_run(cmd)
            return (ok, f"Moved the pointer {_mc_dir} by {_mc_step}px." if ok
                    else "The pointer did not move.")
        if _mc_action == "click":
            if _mc_x is not None and _mc_y is not None:
                _mc_run([tool, "mousemove", "-a", "--", str(int(_mc_x)), str(int(_mc_y))]
                        if wayland else
                        [tool, "mousemove", str(int(_mc_x)), str(int(_mc_y))])
            if wayland:
                # ydotool click takes <flags|button>: 0x40 down, 0x80 up,
                # buttons left/right/middle = 0/1/2. The previous codes
                # (0x40001, 0x40002) sent a press with no release — a
                # stuck mouse button — and named the wrong buttons.
                code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}[_mc_button]
                cmd = [tool, "click", code]
            else:
                num = {"left": "1", "middle": "2", "right": "3"}[_mc_button]
                cmd = [tool, "click", "--clearmodifiers"]
                if _mc_double:
                    cmd += ["--repeat", "2", "--repeat-delay", "80"]
                cmd += [num]
            ok = _mc_run(cmd)
            if ok and wayland and _mc_double:
                _mc_run(cmd)
            where = (f" at ({int(_mc_x)}, {int(_mc_y)})"
                     if _mc_x is not None and _mc_y is not None else " where the pointer is")
            return (ok, (f"{'Double-c' if _mc_double else 'C'}licked {_mc_button}{where}."
                         if ok else "The click did not register."))
        # scroll
        if wayland:
            cmd = [tool, "scroll", "--", "0",
                   str(-_mc_scroll_n if _mc_dir == "down" else _mc_scroll_n)]
        else:
            cmd = [tool, "click", "--clearmodifiers", "--repeat", str(_mc_scroll_n),
                   "--repeat-delay", "50", "4" if _mc_dir == "up" else "5"]
        ok = _mc_run(cmd)
        return (ok, f"Scrolled {_mc_dir} by {_mc_scroll_n}." if ok else "The scroll did not register.")

    def _mc_pyautogui():
        """Cross-platform path — the only one available on Windows/macOS."""
        try:
            import pyautogui
        except Exception:
            return None
        try:
            if _mc_action == "move":
                if _mc_x is not None and _mc_y is not None:
                    pyautogui.moveTo(int(_mc_x), int(_mc_y), duration=0.2)
                    return (True, f"Moved the pointer to ({int(_mc_x)}, {int(_mc_y)}).")
                pyautogui.moveRel(_mc_dx, _mc_dy, duration=0.15)
                return (True, f"Moved the pointer {_mc_dir} by {_mc_step}px.")
            if _mc_action == "click":
                if _mc_x is not None and _mc_y is not None:
                    pyautogui.moveTo(int(_mc_x), int(_mc_y), duration=0.1)
                    where = f" at ({int(_mc_x)}, {int(_mc_y)})"
                else:
                    where = " where the pointer is"
                if _mc_double:
                    pyautogui.doubleClick(button=_mc_button)
                else:
                    pyautogui.click(button=_mc_button)
                return (True, f"{'Double-c' if _mc_double else 'C'}licked {_mc_button}{where}.")
            pyautogui.scroll(_mc_scroll_n if _mc_dir == "up" else -_mc_scroll_n)
            return (True, f"Scrolled {_mc_dir} by {_mc_scroll_n}.")
        except Exception as _pe:
            return (False, f"Pointer control failed: {_pe}")

    # On Linux the native tools are both more reliable and leak-free; the
    # pyautogui/Xlib path left unclosed display sockets and file handles
    # behind on every single call. Elsewhere pyautogui is the only option.
    _mc_order = ((_mc_native, _mc_pyautogui) if _sys.platform.startswith("linux")
                 else (_mc_pyautogui, _mc_native))

    _mc_last = ""
    for _backend in _mc_order:
        _res = _backend()
        if _res is None:          # backend unavailable — try the next
            continue
        _ok, _msg = _res
        if _ok:
            return {"ok": True, "action": a, "content": _msg, "response": _msg}
        _mc_last = _msg

    if not _mc_last:
        # Advice has to match the machine it is printed on. This used to
        # tell Windows and macOS users to `apt install xdotool`.
        if _sys.platform.startswith("win"):
            _mc_last = ("No pointer-control backend is available. Install one with "
                        "`pip install pyautogui`.")
        elif _sys.platform == "darwin":
            _mc_last = ("No pointer-control backend is available. Install one with "
                        "`pip install pyautogui`, then allow ELI under System Settings ▸ "
                        "Privacy & Security ▸ Accessibility — macOS blocks synthetic "
                        "input until that is granted.")
        else:
            from eli.system.portable_app_control import display_server as _ds
            if _ds() == "wayland":
                _mc_last = ("No pointer-control backend is available for this Wayland "
                            "session. xdotool and pyautogui drive X11 only and cannot "
                            "reach native Wayland windows; install ydotool (and run "
                            "ydotoold with access to /dev/uinput), or log in on X11.")
            else:
                _mc_last = ("No pointer-control backend is available. Install one: "
                            "`pip install pyautogui`, or your distribution's xdotool "
                            "package (X11) / ydotool (Wayland).")
    return {"ok": False, "action": a, "error": _mc_last,
            "content": _mc_last, "response": _mc_last}



def _h_set_clipboard(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from eli.perception.os_controller import set_clipboard
        txt = str(args.get("text") or "").strip()
        return set_clipboard(txt)
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_get_clipboard(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from eli.perception.os_controller import get_clipboard
        txt = get_clipboard() or ""
        msg = f"Clipboard: {txt[:200]}" if txt else "Clipboard is empty"
        return {"ok": True, "action": a, "content": msg, "response": msg, "text": txt}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "KEYBOARD": _h_keyboard,
    "MOUSE_CONTROL": _h_mouse_control,
    "SET_CLIPBOARD": _h_set_clipboard,
    "GET_CLIPBOARD": _h_get_clipboard,
}.items():
    _register(_action, _fn)
