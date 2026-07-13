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
    try:
        import subprocess as _sp
        mouse_action = str(args.get("action") or "move").lower()
        x = args.get("x")
        y = args.get("y")
        button = str(args.get("button") or "left").lower()
        double = bool(args.get("double", False))
        direction = str(args.get("direction") or "down").lower()
        amount = int(args.get("amount", 3))
        try:
            import pyautogui
            if mouse_action == "move" and x is not None and y is not None:
                pyautogui.moveTo(int(x), int(y), duration=0.2)
                msg = f"Mouse moved to ({x}, {y})"
            elif mouse_action == "click" and x is not None and y is not None:
                pyautogui.moveTo(int(x), int(y), duration=0.1)
                if double:
                    pyautogui.doubleClick(button=button)
                else:
                    pyautogui.click(button=button)
                msg = f"{'Double-c' if double else 'C'}licked {button} at ({x}, {y})"
            elif mouse_action == "scroll":
                delta = amount if direction == "up" else -amount
                pyautogui.scroll(delta)
                msg = f"Scrolled {direction} by {amount}"
            else:
                msg = f"Mouse action '{mouse_action}' performed"
            return {"ok": True, "action": a, "content": msg, "response": msg}
        except ImportError:
            log.debug("suppressed exception", exc_info=True)
        tool = "ydotool" if shutil.which("ydotool") else ("xdotool" if shutil.which("xdotool") else None)
        if tool is None:
            return {"ok": False, "action": a,
                    "error": "No mouse control tool found. Install: pip install pyautogui  OR  apt install ydotool",
                    "content": "No mouse tool available.", "response": "No mouse tool available."}
        if mouse_action == "move" and x is not None:
            cmd = [tool, "mousemove", "--", str(x), str(y)] if tool == "ydotool" else [tool, "mousemove", str(x), str(y)]
        elif mouse_action == "click":
            btn_flag = "0x40002" if button == "right" else "0x40001"
            cmd = [tool, "click", btn_flag] if tool == "ydotool" else [tool, "click", "--clearmodifiers", "--button", "3" if button == "right" else "1"]
        elif mouse_action == "scroll":
            if tool == "xdotool":
                btn = "4" if direction == "up" else "5"
                cmd = [tool, "click", "--clearmodifiers", "--repeat", str(amount), "--repeat-delay", "50", btn]
            else:
                cmd = [tool, "scroll", "--", "0", str(-amount if direction == "down" else amount)]
        else:
            cmd = []
        if cmd:
            _sp.run(cmd, check=True)
        msg = f"Mouse {mouse_action} executed"
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


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
