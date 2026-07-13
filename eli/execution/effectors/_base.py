"""Cross-cutting effector primitives shared by every effector family.

Security gate (_allow_or_block/_allowed_cmds_set/_full_control), the audited
subprocess runner (_run_argv), and browser-open (_open_in_browser). Leaf module —
imports only stdlib + the logger, never the executor, so effectors can depend on it
without an import cycle. Extracted from executor_enhanced.py (v3 modularisation).
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
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


def _open_in_browser(url: str) -> None:
    """Open a URL in the default browser, cross-platform (xdg-open on Linux,
    `open` on macOS, the default handler on Windows) via platform_compat —
    raw xdg-open silently no-ops off Linux."""
    try:
        from eli.utils.platform_compat import open_url as _open_url
        _open_url(url)
    except Exception:
        import subprocess as _sp2
        if shutil.which("xdg-open"):
            _sp2.Popen(["xdg-open", url], stdout=_sp2.DEVNULL, stderr=_sp2.DEVNULL,
                       start_new_session=True)


def _allowed_cmds_set():
    """
    Allowed executables for sandboxed run().
    Accept BOTH comma-separated and whitespace-separated formats.
    Also include both './x' and 'x' variants so normpath quirks don't brick you.
    """
    import os
    import os.path
    from pathlib import Path as _Path

    raw = (os.environ.get("ELI_ALLOWED_CMDS") or "").strip()
    if not raw:
        return None

    # commas OR spaces
    raw = raw.replace(",", " ")
    parts = [p for p in raw.split() if p]

    allowed = set()

    def add(tok: str):
        if not tok:
            return
        t = str(tok).strip()
        if not t:
            return

        # keep raw
        allowed.add(t)

        # normpath form
        try:
            n = os.path.normpath(t)
            allowed.add(n)
        except Exception:
            n = t

        # add "./" and no-"./" variants
        try:
            if n.startswith("./"):
                allowed.add(n[2:])
            else:
                allowed.add("./" + n)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    for p in parts:
        add(p)

    # Always allow venv python in common forms (belt + suspenders)
    add("./.venv/bin/python")
    add(".venv/bin/python")
    try:
        add(str((_Path(__file__).resolve().parent.parent / ".venv/bin/python").resolve()))
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    return allowed


def _allow_or_block(argv0: str) -> bool:
    """Allow or block executables based on sandbox rules."""
    import os
    
    # FULL CONTROL MODE: allow everything
    if _full_control():
        return True
    
    allowed = _allowed_cmds_set()
    if allowed is None:
        return True  # No restrictions
    
    # Wildcard allows everything
    if "*" in allowed:
        return True
    
    return argv0 in allowed


def _run_argv(argv, timeout=30):
    # Normalize timeouts (router sometimes sends None)
    # None/"" => env ELI_RUN_TIMEOUT_SEC (default 120s)
    import os as _os
    if timeout is None or timeout == "":
        try:
            timeout = float(_os.environ.get("ELI_RUN_TIMEOUT_SEC", "120"))
        except Exception:
            timeout = 120.0
    else:
        try:
            timeout = float(timeout)
        except Exception:
            timeout = 120.0

    if not argv:
        return {"ok": False, "error": "empty argv"}
    if not _allow_or_block(argv[0]):

        # --- ALLOW_VENV_PYTHON_V1 ---
        # Normalize doubled venv paths and allow our own venv python binary under the allowlist sandbox.
        try:
            import os
            _v = str(argv[0])
            # collapse accidental duplication
            _v = _v.replace("./.venv/bin/./.venv/bin/python", "./.venv/bin/python")
            _v = _v.replace(".venv/bin/.venv/bin/python", ".venv/bin/python")
            _v = os.path.normpath(_v)
            # allow exactly the project venv python
            _venv_py_rel = os.path.normpath("./.venv/bin/python")
            _venv_py_abs = os.path.normpath(str((Path(__file__).resolve().parent / ".venv/bin/python")))
            if _v in (_venv_py_rel, _venv_py_abs) or _v.endswith("/.venv/bin/python"):
                # overwrite the checked value if it's a simple name
                if isinstance(argv[0], str):
                    argv[0] = _v
                # skip raising
                raise SystemExit("__ELI_ALLOW_VENV_PYTHON_SKIP__")
        except SystemExit as _e:
            if str(_e) == "__ELI_ALLOW_VENV_PYTHON_SKIP__":
                pass
            else:
                raise
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # If we didn't early-allow venv python, fall through to the original denial.
        try:
            _ALLOW_VENV_PYTHON
        except NameError:
            _ALLOW_VENV_PYTHON = False
        if not _ALLOW_VENV_PYTHON:
            return {"ok": False, "error": f"command not allowed: {argv[0]} (ELI_ALLOWED_CMDS)"}

    try:
        r = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
        )
        return {
            "ok": (r.returncode == 0),
            "returncode": r.returncode,
            "stdout": (r.stdout or "")[-2000:],
            "stderr": (r.stderr or "")[-2000:],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}



# ── app/security primitives (added with apps_windows family) ──
_security_manager = None


def _get_security_manager():
    """Return the module-level SecurityManager singleton (imported lazily)."""
    global _security_manager
    if _security_manager is None:
        try:
            from eli.runtime.security import SecurityManager
            _security_manager = SecurityManager()
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


def _allowed_apps_set():
    """Return set of allowed apps, or None for no restrictions."""
    import os
    
    # FULL CONTROL MODE: no app restrictions
    if _full_control():
        return None
    
    raw = (os.environ.get("ELI_ALLOWED_APPS") or "").strip()
    if not raw:
        return None  # None => allow common safe apps
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


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
    """Save content to artifacts/{subdir}/{filename}.{fmt}. Returns the path."""
    import re as _re

    root = _artifacts_dir() / subdir
    root.mkdir(parents=True, exist_ok=True)

    safe = _re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(". ")[:80] or "output"
    out_path = root / f"{safe}.{fmt}"
    if out_path.exists():
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = root / f"{safe}_{ts}.{fmt}"

    if fmt == "docx":
        try:
            from docx import Document
            from docx.shared import Pt
            doc = Document()
            style = doc.styles["Normal"]
            style.font.name = "Calibri"
            style.font.size = Pt(11)
            for block in content.split("\n\n"):
                block = block.strip()
                if not block:
                    continue
                if block.startswith("### "):
                    doc.add_heading(block[4:].strip(), level=3)
                elif block.startswith("## "):
                    doc.add_heading(block[3:].strip(), level=2)
                elif block.startswith("# "):
                    doc.add_heading(block[2:].strip(), level=1)
                else:
                    doc.add_paragraph(block)
            doc.save(str(out_path))
        except Exception:
            out_path = out_path.with_suffix(".txt")
            out_path.write_text(content, encoding="utf-8")
    else:
        out_path.write_text(content, encoding="utf-8")

    return str(out_path)

__all__ = ['_full_control', '_open_in_browser', '_allowed_cmds_set', '_allow_or_block', '_run_argv', '_get_security_manager', '_shell_command_allowed_fallback', '_run', '_run_ok', '_artifacts_dir', '_save_artifact', '_allowed_apps_set']
