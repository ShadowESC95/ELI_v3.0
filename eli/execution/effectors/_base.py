"""Cross-cutting effector primitives shared by every effector family.

The Full Control state (_full_control), the allowlisted subprocess runner (_run),
browser-open (_open_in_browser), artifacts and document text. Leaf module —
imports only stdlib + the logger, never the executor, so effectors can depend on it
without an import cycle. Extracted from executor_enhanced.py (v3 modularisation).
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List
from eli.utils.log import get_logger
log = get_logger(__name__)


def _full_control() -> bool:
    """ELI Full Control state — the GUI toggle / `full_control` setting (single source
    of truth; no environment variable). Never raises."""
    try:
        from eli.core.full_control import is_full_control
        return bool(is_full_control())
    except Exception:
        return False


def _open_in_browser(url: str) -> bool:
    """Open a URL in the default browser. Returns True on success."""
    try:
        from eli.utils.platform_compat import open_url as _open_url
        return bool(_open_url(url))
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return False


# ── app/security primitives (added with apps_windows family) ──
_security_manager = None


# What a SecurityManager reads when it is made. The one kept here is made again when any of these
# changes: it read ELI_ALLOWED_CMDS once, so a command allowed (or taken off the list) while ELI ran
# was judged by the list as it stood at the first command.
_SECURITY_INPUTS = ("ELI_ALLOWED_CMDS", "ELI_ALLOW_ROOTS", "ELI_ALLOWED_APPS", "ELI_ROOT")
_security_inputs_seen = None


def _get_security_manager():
    """The SecurityManager for the allowlists as they are now (imported lazily)."""
    global _security_manager, _security_inputs_seen
    now = tuple(os.environ.get(name, "") for name in _SECURITY_INPUTS)
    if _security_manager is None or now != _security_inputs_seen:
        try:
            from eli.runtime.security import SecurityManager
            _security_manager = SecurityManager()
            _security_inputs_seen = now
        except Exception:
            _security_manager = None  # degrade gracefully if unavailable
    return _security_manager


def _shell_command_allowed_fallback(cmd: str) -> bool:
    """Fail-closed allowlist check used when the SecurityManager can't load.

    Mirrors SecurityManager.is_command_allowed so a degraded manager can never
    become fail-OPEN: with neither ELI Full Control nor ELI_ALLOWED_CMDS set,
    nothing runs.
    """
    if _full_control():
        return True
    raw = (os.environ.get("ELI_ALLOWED_CMDS") or "").strip()
    if not raw:
        return False  # fail-closed
    allowed = {p for p in raw.replace(",", " ").split() if p}
    if "*" in allowed:
        return True
    return os.path.normpath(cmd) in allowed or cmd in allowed


def _run(argv: List[str], timeout: int = 15) -> Dict[str, Any]:
    # Security gate: check command against allowlist before executing.
    # Fail CLOSED — if the SecurityManager can't load, fall back to an inline
    # allowlist check rather than running unchecked.
    if argv:
        sm = _get_security_manager()
        allowed = sm.is_command_allowed(argv[0]) if sm is not None else _shell_command_allowed_fallback(argv[0])
        if not allowed:
            msg = f"Command blocked by security policy: {argv[0]}"
            return {
                "ok": False,
                "returncode": 1,
                "stdout": "",
                "stderr": msg,
                "cmd": argv,
                "content": msg,
                "response": msg,
            }
    try:
        p = subprocess.run(argv, timeout=timeout, capture_output=True, text=True, check=False)
        ok = (p.returncode == 0)
        msg = "OK" if ok else "Command failed"
        return {
            "ok": ok,
            "returncode": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr,
            "cmd": argv,
            "content": msg,
            "response": msg,
        }
    except Exception as e:
        msg = "Command error"
        return {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": repr(e),
            "cmd": argv,
            "content": msg,
            "response": msg,
            "error": repr(e),
        }


def _run_ok(argv, timeout: int = 5) -> bool:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


def _artifacts_dir() -> Path:
    env = os.environ.get("ELI_ARTIFACTS_DIR")
    if env:
        p = Path(env).expanduser()
        if p.is_absolute():
            return p
        from eli.core.paths import project_root
        return Path(project_root()) / p
    from eli.core.paths import project_root
    return Path(project_root()) / "artifacts"


def _save_artifact(content: str, subdir: str, filename: str, fmt: str = "md") -> str:
    """
    Save content to artifacts/{subdir}/{filename}.{fmt}.
    Supported fmt: md, docx, pdf, txt, py, sh, csv, html.
    Returns the absolute path string.
    """
    import re as _re

    root = _artifacts_dir() / subdir
    root.mkdir(parents=True, exist_ok=True)

    # Clean filename
    safe = _re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(". ")[:80] or "output"
    out_path = root / f"{safe}.{fmt}"
    # Avoid collisions
    if out_path.exists():
        import time as _t
        ts = _t.strftime("%Y%m%d_%H%M%S")
        out_path = root / f"{safe}_{ts}.{fmt}"

    if fmt in ("docx", "pdf"):
        # one writer for documents (eli/runtime/rich_document.py): headings, lists, typeset formulas
        try:
            from eli.runtime.rich_document import write_document
            out_path = write_document(content, out_path)
        except Exception:
            log.debug("[ARTIFACT] document not written; saved as text", exc_info=True)
            out_path = out_path.with_suffix(".txt")
            out_path.write_text(content, encoding="utf-8")
    else:
        out_path.write_text(content, encoding="utf-8")

    return str(out_path)

# .docx/.pdf/etc are binary containers: open(path).read() decodes them as mojibake and the model
# confabulates from the garbage. The document_reader plugin has real per-format extraction; both
# SUMMARIZE_FILE and READ_FILE route through it.
_DOC_READER_SUFFIXES = (".pdf", ".docx", ".doc", ".odt", ".epub")


def _read_document_text(path):
    if Path(path).suffix.lower() not in _DOC_READER_SUFFIXES:
        return None
    from eli.plugins.document_reader.plugin import DocumentReaderPlugin
    return DocumentReaderPlugin().read({"path": str(path)})


__all__ = ['_full_control', '_open_in_browser', '_get_security_manager', '_shell_command_allowed_fallback', '_run', '_run_ok', '_artifacts_dir', '_save_artifact', '_DOC_READER_SUFFIXES', '_read_document_text']
