"""Files effector — file open/read and safe screenshot (notes live in notes.py).

Clean leaf lifted from executor_enhanced.py (v3 modularisation).
"""
from __future__ import annotations

from eli.core.paths import resolve_user_repo_path
from eli.utils.platform_compat import open_file
from eli.utils.log import get_logger
log = get_logger(__name__)


def _open_file(path: str) -> None:
    try:
        open_file(path)
    except Exception:
        log.debug("suppressed exception", exc_info=True)


def _eli_safe_read_file(args=None):
    _args = args or {}
    path = str(_args.get("path") or "").strip()
    if not path:
        return {"ok": False, "action": "READ_FILE", "content": "Missing file path."}
    p = resolve_user_repo_path(path)
    if not p.exists():
        return {"ok": False, "action": "READ_FILE", "content": f"File not found: {p}"}
    if p.is_dir():
        # A directory was passed to READ_FILE — LIST it instead of refusing. The user asked
        # to read/list the path; refusing (or letting the model confabulate) is worse than
        # just showing the real contents.
        try:
            _entries = sorted(
                (f"{x.name}/" if x.is_dir() else x.name) for x in p.iterdir())
            _listing = (f"Contents of {p} ({len(_entries)} items):\n" + "\n".join(_entries))
            return {"ok": True, "action": "LIST_DIR", "path": str(p),
                    "content": _listing, "response": _listing}
        except Exception as _ld_err:
            return {"ok": False, "action": "READ_FILE",
                    "content": f"Could not read directory {p}: {_ld_err}"}
    data = None
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            data = p.read_text(encoding=enc)
            break
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    if data is None:
        try:
            data = p.read_bytes().decode("utf-8", errors="replace")
        except Exception as e:
            return {"ok": False, "action": "READ_FILE", "content": f"Could not read file: {e}"}
    return {
        "ok": True,
        "action": "READ_FILE",
        "path": str(p),
        "content": data,
    }


def _eli_safe_screenshot(args=None):
    _args = args or {}
    region = _args.get("region", "full")
    try:
        from controllers.os_controller import take_screenshot
    except Exception:
        try:
            from eli.perception.os_controller import take_screenshot
        except Exception as e:
            return {"ok": False, "action": "SCREENSHOT", "content": f"Screenshot backend unavailable: {e}"}
    try:
        out = take_screenshot(region)
    except TypeError:
        try:
            out = take_screenshot()
        except Exception as e:
            return {"ok": False, "action": "SCREENSHOT", "content": f"Screenshot failed: {e}"}
    except Exception as e:
        return {"ok": False, "action": "SCREENSHOT", "content": f"Screenshot failed: {e}"}

    if isinstance(out, dict):
        ok = bool(out.get("ok", True))
        content = str(out.get("content") or out.get("message") or out.get("path") or "Screenshot completed")
        return {
            "ok": ok,
            "action": "SCREENSHOT",
            "content": content,
            **out,
        }

    return {
        "ok": True,
        "action": "SCREENSHOT",
        "content": str(out or "Screenshot completed"),
    }

__all__ = ['_open_file', '_eli_safe_read_file', '_eli_safe_screenshot']


def _h_read_file(a, args):
    return _eli_safe_read_file(args)


from eli.execution.effectors.registry import register as _register

_register("READ_FILE", _h_read_file)
