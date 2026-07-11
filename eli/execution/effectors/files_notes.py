"""Files & notes effector — file open/read, note write/append, safe screenshot.

Clean leaf lifted from executor_enhanced.py (v3 modularisation).
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from eli.core.paths import resolve_user_repo_path
from eli.utils.platform_compat import open_file
from eli.utils.log import get_logger
log = get_logger(__name__)


def _open_file(path: str) -> None:
    try:
        open_file(path)
    except Exception:
        log.debug("suppressed exception", exc_info=True)


def _write_note_builtin(args: dict) -> dict:
    """Create a note file under ELI_DOC_DIR (default: ./eli_docs)."""
    try:
        title = (args.get("title") or "note").strip()
        content = (args.get("content") or "").strip()
        filename = (args.get("filename") or "").strip()
        open_after = bool(args.get("open_after", False))

        import os, re, time, subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        doc_dir = Path(os.environ.get("ELI_DOC_DIR", str(root / "eli_docs"))).expanduser()
        doc_dir.mkdir(parents=True, exist_ok=True)

        if not filename:
            safe_title = re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", title).strip("_") or "note"
            filename = f"{safe_title}.md"
        if not filename.lower().endswith((".md", ".txt")):
            filename += ".md"

        if not content:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            content = f"# {title}\n\nCreated: {ts}\n\n"

        path = (doc_dir / filename).resolve()
        doc_dir_resolved = doc_dir.resolve()
        if path != doc_dir_resolved and doc_dir_resolved not in path.parents:
            return {"ok": False, "error": "refusing to write outside ELI_DOC_DIR"}

        path.write_text(content, encoding="utf-8")

        if open_after:
            try:
                open_file(str(path))
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        return {"ok": True, "path": str(path), "title": title}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _append_note_builtin(args: dict) -> dict:
    """Append content to a note under ELI_DOC_DIR."""
    try:
        title = (args.get("title") or "").strip()
        content = (args.get("content") or "").strip()
        filename = (args.get("filename") or "").strip()
        open_after = bool(args.get("open_after", False))

        if not title and not filename:
            return {"ok": False, "error": "append_note: title or filename is required"}
        if not content:
            return {"ok": False, "error": "append_note: content is empty"}

        import os, re, time, subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        doc_dir = Path(os.environ.get("ELI_DOC_DIR", str(root / "eli_docs"))).expanduser()
        doc_dir.mkdir(parents=True, exist_ok=True)

        note_file = None
        if filename:
            note_file = (doc_dir / filename)
        else:
            if title.lower().endswith((".md", ".txt")):
                note_file = (doc_dir / title)
            else:
                for ext in (".md", ".txt"):
                    cand = doc_dir / f"{title}{ext}"
                    if cand.exists():
                        note_file = cand
                        break
                if note_file is None:
                    safe_title = re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", title).strip("_") or "note"
                    note_file = doc_dir / f"{safe_title}.md"

        note_file = note_file.resolve()
        doc_dir_resolved = doc_dir.resolve()
        if note_file != doc_dir_resolved and doc_dir_resolved not in note_file.parents:
            return {"ok": False, "error": "refusing to write outside ELI_DOC_DIR"}

        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        blob = f"\n\n---\n\n{ts}\n\n{content.strip()}\n"

        if note_file.exists():
            prev = note_file.read_text(encoding="utf-8", errors="ignore")
        else:
            prev = f"# {title or note_file.stem}\n"

        note_file.write_text(prev.rstrip() + blob, encoding="utf-8")

        if open_after:
            try:
                open_file(str(note_file))
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        return {"ok": True, "path": str(note_file), "title": title or note_file.stem}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _eli_safe_read_file(args=None):
    from pathlib import Path
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


def _eli_safe_write_note(args=None):
    from pathlib import Path
    from datetime import datetime
    _args = args or {}
    txt = str(_args.get("text") or "").rstrip()
    if not txt:
        return {"ok": False, "action": "WRITE_NOTE", "content": "Missing note text."}
    nb = Path("eli_notebook")
    nb.mkdir(parents=True, exist_ok=True)
    daily = nb / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    prefix = "" if not daily.exists() or not daily.read_text(encoding="utf-8", errors="ignore").strip() else "\n"
    stamp = datetime.now().strftime("%H:%M:%S")
    payload = f"{prefix}- [{stamp}] {txt}\n"
    daily.write_text(daily.read_text(encoding="utf-8", errors="ignore") + payload if daily.exists() else payload, encoding="utf-8")
    return {
        "ok": True,
        "action": "WRITE_NOTE",
        "path": str(daily),
        "content": f"Wrote note to {daily}",
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

__all__ = ['_open_file', '_write_note_builtin', '_append_note_builtin', '_eli_safe_read_file', '_eli_safe_write_note', '_eli_safe_screenshot']
