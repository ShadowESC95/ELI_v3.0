"""File-ops effector — CREATE_FILE/CREATE_FOLDER/LIST_DIR/SUMMARIZE_FILE/CONVERT_DOCUMENT."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from eli.utils.log import get_logger
from eli.execution.effectors._base import _full_control, _save_artifact

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
        from eli.core.paths import project_root as _pr
        repo_root = _pr()  # project root (location-independent; was __file__.parents[2])
        package_root = repo_root / "eli"
        legacy_root = Path.home() / "eli"
        raw = Path(path).expanduser()

        package_toplevel = {
            "api", "brain", "controllers", "core", "gui",
            "integrations", "plugins", "tools", "utils"
        }

        if raw == legacy_root:
            p = package_root.resolve()
        else:
            try:
                rel = raw.relative_to(legacy_root)
                if not rel.parts:
                    p = package_root.resolve()
                elif rel.parts[0] in package_toplevel:
                    p = (package_root / rel).resolve()
                else:
                    p = (repo_root / rel).resolve()
            except Exception:
                p = raw.resolve()

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
        return {"ok": True, "action": a, "path": str(p), "content": content, "response": content}
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
    # Light, safe substitution so "containing today's date / your version" resolves.
    _today = _dt_cf.date.today().isoformat()
    _ver = ""
    try:
        import eli as _eli_mod
        _ver = str(getattr(_eli_mod, "__version__", "") or "")
    except Exception:
        _ver = ""
    _ver = _ver or "unknown"
    for _pat in ("today's date", "todays date", "the current date", "today’s date"):
        content = re.sub(re.escape(_pat), _today, content, flags=re.IGNORECASE)
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
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _readback = p.read_text(encoding="utf-8")
    except Exception as _cf_e:
        msg = f"Could not create {p}: {_cf_e}"
        return {"ok": False, "action": a, "error": str(_cf_e), "content": msg, "response": msg}
    _rb_show = _readback if len(_readback) <= 2000 else _readback[:2000] + "…"
    msg = f"Created {p} ({len(content)} chars). Read back:\n{_rb_show}"
    return {"ok": True, "action": a, "path": str(p), "created": True,
            "bytes": len(content.encode('utf-8')), "readback": _readback,
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
    import os as _os2, subprocess as _sfsp
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
    # Directory: if it contains PDFs, delegate to ANALYZE_PDF_FOLDER for real content
    if _os2.path.isdir(expanded):
        import glob as _glob
        _pdfs = _glob.glob(_os2.path.join(expanded, "**", "*.pdf"), recursive=True)
        if _pdfs:
            return _execute_impl("ANALYZE_PDF_FOLDER", {"folder": expanded, "recursive": True})
        # No PDFs: fall back to ls -R + GGUF description
        try:
            _ls_result = _sfsp.run(
                ["ls", "-R", expanded], capture_output=True, text=True, timeout=10)
            _sf_raw = _ls_result.stdout[:8000]
        except Exception as _dir_err:
            _sf_raw = f"Directory listing failed: {_dir_err}"
        _sf_summary = None
        try:
            from eli.cognition import gguf_inference as _sfgi
            if _sfgi.load_model() is not None:
                _sf_user_msg = (
                    f"{_sf_instruction}\n\nDirectory listing:\n\n{_sf_raw[:6000]}"
                    if _sf_instruction else
                    f"Describe the structure and contents of this directory tree:\n\n{_sf_raw[:6000]}"
                )
                _sf_summary = _sfgi.chat_completion(
                    _sf_user_msg,
                    system="You are a precise technical analyst. Describe the directory structure, key files, and inferred purpose.",
                    max_tokens=700, temperature=0.3)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        _sf_body = _sf_summary if _sf_summary else _sf_raw[:3000]
        return {"ok": True, "action": a, "content": _sf_body, "response": _sf_body}
    # Anchor the conversational "current file" so a later proof-challenge
    # ("prove you read it") audits THIS file rather than the GUI default.
    try:
        from eli.runtime import code_examiner as _ce_sf
        _ce_sf.set_last_file(str(expanded))
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        # Read the WHOLE file — no 8000-byte truncation. The summariser does
        # hierarchical map-reduce sized to the model context, so an arbitrarily
        # large file is fully covered instead of summarising only its first 10%.
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
