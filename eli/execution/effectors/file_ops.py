"""File-ops effector — CREATE_FILE/CREATE_FOLDER/LIST_DIR/SUMMARIZE_FILE/CONVERT_DOCUMENT."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from eli.utils.log import get_logger
from eli.execution.effectors._base import _full_control, _read_document_text, _save_artifact

log = get_logger(__name__)


def _execute_impl(*a, **k):
    from eli.execution.executor_enhanced import _execute_impl as _f
    return _f(*a, **k)


def _summarize_long_text(*a, **k):
    from eli.execution.executor_enhanced import _summarize_long_text as _f
    return _f(*a, **k)

def resolve_user_repo_path(*a, **k):
    from eli.execution.executor_enhanced import resolve_user_repo_path as _f
    return _f(*a, **k)


def _h_convert_document(a, args):
    try:
        import subprocess as _sp
        source = str(args.get("source") or "").strip()
        fmt = str(args.get("format") or "pdf").strip().lower()
        # Normalise latex aliases
        if fmt in ("latex", "lualatex"):
            fmt = "pdf"  # pandoc --pdf-engine=lualatex
            engine = "lualatex"
        else:
            engine = None
        if not source:
            return {"ok": False, "action": a, "error": "No source file specified",
                    "content": "No source file specified.", "response": "No source file specified."}
        src_path = Path(source).expanduser().resolve()
        if not src_path.exists():
            return {"ok": False, "action": a, "error": f"Source file not found: {src_path}",
                    "content": f"Source not found: {src_path}", "response": f"Source not found: {src_path}"}
        out_path = src_path.with_suffix(f".{fmt if fmt != 'markdown' else 'md'}")
        # Try pandoc first
        if shutil.which("pandoc"):
            cmd = ["pandoc", str(src_path), "-o", str(out_path)]
            if engine:
                cmd += ["--pdf-engine", engine]
            result = _sp.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                msg = f"Converted {src_path.name} → {out_path.name}"
                return {"ok": True, "action": a, "content": msg, "response": msg,
                        "output_path": str(out_path)}
            else:
                err = result.stderr.strip()
                # Fallback: libreoffice headless
                if shutil.which("libreoffice"):
                    lo_cmd = ["libreoffice", "--headless", "--convert-to", fmt,
                              "--outdir", str(src_path.parent), str(src_path)]
                    lo = _sp.run(lo_cmd, capture_output=True, text=True, timeout=180)
                    if lo.returncode == 0:
                        msg = f"Converted {src_path.name} → {out_path.name} (via LibreOffice)"
                        return {"ok": True, "action": a, "content": msg, "response": msg,
                                "output_path": str(out_path)}
                return {"ok": False, "action": a, "error": f"pandoc failed: {err}",
                        "content": f"Conversion failed: {err}", "response": f"Conversion failed: {err}"}
        # No pandoc — try libreoffice directly
        elif shutil.which("libreoffice"):
            lo_cmd = ["libreoffice", "--headless", "--convert-to", fmt,
                      "--outdir", str(src_path.parent), str(src_path)]
            lo = _sp.run(lo_cmd, capture_output=True, text=True, timeout=180)
            if lo.returncode == 0:
                msg = f"Converted {src_path.name} → {out_path.name} (via LibreOffice)"
                return {"ok": True, "action": a, "content": msg, "response": msg,
                        "output_path": str(out_path)}
            return {"ok": False, "action": a, "error": lo.stderr.strip(),
                    "content": "LibreOffice conversion failed.", "response": "LibreOffice conversion failed."}
        else:
            return {"ok": False, "action": a,
                    "error": "Neither pandoc nor libreoffice found. Install one: apt install pandoc or libreoffice",
                    "content": "No conversion tool available.", "response": "No conversion tool available."}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_list_dir(a, args):
    path = str((args or {}).get("path") or "~")
    try:
        # One reader of "~/eli/...": eli.core.paths.resolve_user_repo_path (the project only where no
        # folder ~/eli of the user's exists). This kept its own copy, which took every path under ~/eli
        # for the project: a folder of the user's named ~/eli was listed from inside ELI instead.
        p = Path(resolve_user_repo_path(path)).resolve()

        if not p.exists():
            return {"ok": False, "action": a, "error": f"Path not found: {p}", "content": f"Path not found: {p}", "response": f"Path not found: {p}"}
        if not p.is_dir():
            # A FILE (not a dir) was handed to LIST_DIR — almost always a
            # mis-route of "read/show <file>" (e.g. a conversation .json or a
            # generated .docx). Redirect to READ_FILE rather than returning an
            # error: erroring here surfaced a "Not a directory" failure that
            # the proactive daemon then replayed as a recurring error forever.
            if p.is_file():
                return _execute_impl("READ_FILE", {"path": str(p)})
            return {"ok": False, "action": a, "error": f"Not a directory: {p}", "content": f"Not a directory: {p}", "response": f"Not a directory: {p}"}

        items = sorted([
            f"{x.name}/" if x.is_dir() else x.name
            for x in p.iterdir()
        ])

        content = f"Contents of {p} ({len(items)} items):\n" + "\n".join(items[:500])
        # Kept open, so "the third one" lists that folder or reads that file (LIST_DIR hands a
        # file to READ_FILE above).
        return {"ok": True, "action": a, "path": str(p), "content": content, "response": content,
                "listed": [{"title": name, "ref": str(p / name.rstrip("/"))} for name in items[:50]],
                "follow_up": {"action": "LIST_DIR", "arg": "path", "field": "ref"}}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_create_file(a, args):
    import os as _os_cf, tempfile as _tf_cf, datetime as _dt_cf
    from pathlib import Path as _P_cf
    path = str(args.get("path") or args.get("file") or args.get("filename") or "").strip()
    content = args.get("content")
    if content is None:
        content = args.get("text") or args.get("body") or ""
    content = str(content)
    if not path:
        msg = "Specify a file path to create (e.g. /tmp/note.txt)."
        return {"ok": False, "action": a, "error": "missing path", "content": msg, "response": msg}
    # What ELI wrote is saved as written ("verbatim"); the substitutions below are for a file
    # described in words ("a file containing today's date"), and would change code.
    _verbatim = bool(args.get("verbatim"))
    if not _verbatim:
        content = _content_from_line_spec(content)
    # Light, safe substitution so "containing today's date / your version" resolves.
    _today = _dt_cf.date.today().isoformat()
    _ver = ""
    try:
        import eli as _eli_mod
        _ver = str(getattr(_eli_mod, "__version__", "") or "")
    except Exception:
        _ver = ""
    _ver = _ver or "unknown"
    for _pat in ("today's date", "todays date", "the current date", "today’s date") if not _verbatim else ():
        content = re.sub(re.escape(_pat), _today, content, flags=re.IGNORECASE)
    if not _verbatim:
        content = content.replace("{date}", _today).replace("{datetime}", _dt_cf.datetime.now().isoformat(timespec="seconds"))
        content = re.sub(r"\byour version number\b|\bversion number\b|\byour version\b|\{version\}", _ver, content, flags=re.IGNORECASE)

    p = _P_cf(_os_cf.path.expanduser(path))
    if not p.is_absolute():
        try:
            from eli.core.paths import get_paths as _gp_cf
            base = _P_cf(_gp_cf().artifacts_dir) / "scratch"
        except Exception:
            base = _P_cf(_tf_cf.gettempdir())
        p = base / p
    try:
        p = p.resolve()
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    # Security: allow under home / project / ELI_ALLOW_ROOTS (SecurityManager)
    # or the system temp dir; refuse elsewhere unless ELI Full Control is on.
    _allowed = False
    try:
        from eli.runtime.security import SecurityManager as _SM_cf
        _allowed, _ = _SM_cf().is_path_allowed(str(p))
    except Exception:
        _allowed = False
    if not _allowed:
        try:
            p.relative_to(_P_cf(_tf_cf.gettempdir()).resolve())
            _allowed = True
        except Exception:
            _allowed = False
    if not _allowed and not _full_control():
        msg = (f"Refused: {p} is outside allowed roots (home, project, or temp). "
               "Enable ELI Full Control to override.")
        return {"ok": False, "action": a, "error": "path_not_allowed", "content": msg, "response": msg}
    # A file already there is not created: it is left as it is unless replacing it was asked for
    # outright ("overwrite"). It used to be written over and reported as "Created", from a typed
    # request and from a re-plan the model proposed alike.
    if p.exists() and not bool(args.get("overwrite") or args.get("replace")):
        _what = "a folder" if p.is_dir() else f"a file ({p.stat().st_size:,} bytes)"
        msg = f"Did not create {p}: {_what} is already there, and I left it as it is."
        return {"ok": False, "action": a, "path": str(p), "error": "exists", "exists": True,
                "created": False, "content": msg, "response": msg}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _readback = p.read_text(encoding="utf-8")
    except Exception as _cf_e:
        msg = f"Could not create {p}: {_cf_e}"
        return {"ok": False, "action": a, "error": str(_cf_e), "content": msg, "response": msg}
    _rb_show = _readback if len(_readback) <= 2000 else _readback[:2000] + "…"
    _lines_written = len(_readback.splitlines())
    _matches = _readback.replace("\r\n", "\n") == content.replace("\r\n", "\n")
    msg = (f"Created {p} ({len(content)} chars, {_lines_written} line{'s' if _lines_written != 1 else ''}"
           f"{'' if _matches else ', read-back differs from what was requested'}). Read back:\n{_rb_show}")
    return {"ok": _matches, "action": a, "path": str(p), "created": True,
            "bytes": len(content.encode('utf-8')), "readback": _readback, "lines": _lines_written,
            "content": msg, "response": msg}


def _h_create_folder(a, args):
    import re as _re
    raw = (args.get("name") or args.get("path") or "").strip()
    if not raw:
        return {"ok": False, "error": "Missing folder name"}
    # If raw contains a full path, extract it; otherwise treat as name under ~
    path_match = _re.search(r"(/(?:home|tmp|var|opt|srv)[^ ]+)", raw)
    if path_match:
        path = str(resolve_user_repo_path(path_match.group(1)))
    elif raw.startswith("~") or raw.startswith("/"):
        path = str(resolve_user_repo_path(raw))
    else:
        # Strip any trailing natural language after the folder name
        clean = _re.split(r"\s+(?:not|inside|in|at|under)\s", raw)[0].strip()
        path = str(resolve_user_repo_path(f"~/{clean}"))
    try:
        os.makedirs(path, exist_ok=True)
        return {"ok": True, "content": f"Folder created at {path}", "response": f"Folder created at {path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _h_summarize_file(a, args):
    import os as _os2
    from pathlib import Path as _SFPP
    path = str(args.get("path") or args.get("file") or "").strip()
    _sf_instruction = str(args.get("instruction") or "").strip()
    if not path:
        return {"ok": False, "action": a, "error": "missing path",
                "content": "Specify file to summarize.", "response": "Specify file to summarize."}
    expanded = _os2.path.expanduser(path)
    if not _os2.path.exists(expanded):
        return {"ok": False, "action": a, "error": f"Path not found: {expanded}",
                "content": f"Path not found: {expanded}", "response": f"Path not found: {expanded}"}
    # Images are binary — never read them as text. Delegate to OCR-based
    # image analysis so we describe them honestly instead of dumping bytes.
    if expanded.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif")):
        return _execute_impl("ANALYZE_IMAGE", {"path": expanded})
    # A folder is read, not described from its names: its PDFs, or for a folder with none the files
    # that define it (eli.runtime.folder_analysis), with a summary that cites the files it read. It
    # used to send `ls -R` to the model and ask for the folder's "inferred purpose".
    if _os2.path.isdir(expanded):
        return _execute_impl("ANALYZE_PDF_FOLDER", {"folder": expanded, "recursive": True,
                                                    "instruction": _sf_instruction})
    # Anchor the conversational "current file" so a later proof-challenge
    # ("prove you read it") audits THIS file rather than the GUI default.
    try:
        from eli.runtime import code_examiner as _ce_sf
        _ce_sf.set_last_file(str(expanded))
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        _sf_doc = _read_document_text(expanded)
        if _sf_doc is not None:
            if not _sf_doc.get("ok"):
                return {"ok": False, "action": a, "error": _sf_doc.get("error"),
                        "content": _sf_doc.get("content"), "response": _sf_doc.get("response")}
            _sf_raw = _sf_doc.get("content", "")
        else:
            # Read the WHOLE file — no 8000-byte truncation. The summariser does
            # hierarchical map-reduce sized to the model context, so an arbitrarily
            # large plain-text file is fully covered instead of just its first 10%.
            with open(expanded, errors="replace") as _sf:
                _sf_raw = _sf.read()
        _sf_summary = None
        try:
            _sf_summary = _summarize_long_text(_sf_raw, _sf_instruction)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        _sf_stem = _SFPP(expanded).stem
        _sf_size = _os2.path.getsize(expanded)
        _sf_body = _sf_summary if _sf_summary else _sf_raw[:3000]
        _sf_doc_content = (
            f"# Summary: {_sf_stem}\n\n"
            f"**Source:** {expanded}\n"
            f"**Size:** {_sf_size:,} bytes\n\n---\n\n{_sf_body}"
        )
        _sf_saved = _save_artifact(_sf_doc_content, "documents", f"{_sf_stem}_summary", fmt="docx")
        try:
            from eli.utils.platform_compat import open_file as _open_file
            _sf_opened = bool(_open_file(_sf_saved))
        except Exception:
            _sf_opened = False
        _sf_chat = (
            f"Document compiled: **{_SFPP(_sf_saved).name}**\n"
            f"Saved to: `{_sf_saved}`"
            + ("\n_(opening…)_" if _sf_opened else "")
        )
        return {"ok": True, "action": a, "content": _sf_chat, "response": _sf_chat,
                "saved_to": _sf_saved}
    except Exception as _se:
        return {"ok": False, "action": a, "error": str(_se), "content": str(_se), "response": str(_se)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "CONVERT_DOCUMENT": _h_convert_document,
    "LIST_DIR": _h_list_dir,
    "CREATE_FILE": _h_create_file,
    "CREATE_FOLDER": _h_create_folder,
    "SUMMARIZE_FILE": _h_summarize_file,
}.items():
    _register(_action, _fn)

def _content_from_line_spec(*a, **k):
    from eli.execution.executor_enhanced import _content_from_line_spec as _f
    return _f(*a, **k)
