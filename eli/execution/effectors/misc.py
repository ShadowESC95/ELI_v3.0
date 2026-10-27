"""Misc effector — CHECK_CHRONAL_ALIGNMENT, DATA_FABRICATOR, SHOW_DIFF."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from eli.utils.log import get_logger

log = get_logger(__name__)

def _execute_impl(*a, **k):
    from eli.execution.executor_enhanced import _execute_impl as _f
    return _f(*a, **k)

def chat(*a, **k):
    from eli.execution.executor_enhanced import chat as _f
    return _f(*a, **k)

def open_file(*a, **k):
    from eli.execution.executor_enhanced import open_file as _f
    return _f(*a, **k)


def _h_check_chronal_alignment(a, args):
    import time as _t_mod
    _now = _t_mod.localtime()
    msg = (f"Chronal alignment nominal. Local time: "
           f"{_t_mod.strftime('%Y-%m-%d %H:%M:%S', _now)}.")
    return {"ok": True, "action": a, "content": msg, "response": msg}


def _h_data_fabricator(a, args):
    try:
        # If a topic/prompt is provided, generate a document first then open it.
        topic = str((args or {}).get("topic") or (args or {}).get("query") or (args or {}).get("text") or (args or {}).get("message") or "").strip()
        if topic:
            r = _execute_impl(action="CREATE_DOCUMENT", args={"topic": topic, "format": (args or {}).get("format") or "md"})
            # If generation succeeded, also open it in this OS's editor
            if r.get("ok") and r.get("path"):
                from eli.utils.platform_compat import open_role
                open_role("editor", str(r.get("path"))) or open_file(str(r.get("path")))
            return r

        # Otherwise a new blank file in ELI's own documents folder (not a generic ~/Documents/ELI),
        # opened in this OS's editor.
        from eli.core.paths import documents_dir
        from eli.utils.platform_compat import open_role
        out_dir = Path(documents_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"data_construct_{int(time.time())}.md"
        if not out_path.exists():
            out_path.write_text("# Data Construct\n\n", encoding="utf-8")
        editor = open_role("editor", str(out_path))
        if editor:
            msg = f"Opened editor: {editor}"
            return {"ok": True, "action": a, "path": str(out_path), "content": msg, "response": msg}

        # Fallback: open with default system handler
        open_file(str(out_path))
        msg = f"Opened file: {out_path}"
        return {"ok": True, "action": a, "path": str(out_path), "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_show_diff(a, args):
    return chat("Show me the recent changes or diff in the project.", skip_router=True)


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "CHECK_CHRONAL_ALIGNMENT": _h_check_chronal_alignment,
    "DATA_FABRICATOR": _h_data_fabricator,
    "SHOW_DIFF": _h_show_diff,
}.items():
    _register(_action, _fn)
