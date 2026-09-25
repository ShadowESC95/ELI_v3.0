"""Self-maintenance effector — proactive-daemon control + self-improvement log.

Lifted from executor_enhanced.py (v3 modularisation). Persona-lock + self_test defer to a
later slice (they need the shared _load_state/_save_state/_sha256_file helpers).
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from eli.utils.log import get_logger
log = get_logger(__name__)


def _self_improvement_log_report(limit: int = 5, days: int = 30) -> Dict[str, Any]:
    try:
        from eli.runtime.self_improvement import get_self_improvement
        engine = get_self_improvement()
        rows = engine.analyze_failures(limit=max(1, int(limit)), days=max(1, int(days)), min_cluster_size=1)
    except Exception as exc:
        msg = f"Self-improvement log unavailable: {exc}"
        return {"ok": False, "error": str(exc), "content": msg, "response": msg}

    rows = sorted(rows or [], key=lambda r: float(r.get("timestamp") or 0.0), reverse=True)
    if not rows:
        msg = "No failures are recorded in the self-improvement log for the selected window."
        return {"ok": True, "failures": [], "content": msg, "response": msg}

    last = rows[0]
    ts = float(last.get("timestamp") or 0.0)
    when = datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else "unknown"
    err = str(last.get("error") or "").strip() or "(empty error field)"
    user_input = str(last.get("user_input") or "").strip()
    count = int(last.get("occurrence_count") or 1)

    lines = [
        "Last self-improvement failure:",
        f"- when: {when}",
        f"- occurrences: {count}",
        f"- exact_error: {err}",
    ]
    if user_input:
        preview = user_input.replace("\n", "\\n")
        if len(preview) > 500:
            preview = preview[:500] + "..."
        lines.append(f"- triggering_input_preview: {preview}")

    if len(rows) > 1:
        lines.append("")
        lines.append("Recent failures:")
        for idx, row in enumerate(rows[:limit], 1):
            row_ts = float(row.get("timestamp") or 0.0)
            row_when = datetime.fromtimestamp(row_ts).isoformat(timespec="seconds") if row_ts else "unknown"
            row_err = str(row.get("error") or "").strip() or "(empty error field)"
            lines.append(f"{idx}. {row_when} | x{int(row.get('occurrence_count') or 1)} | {row_err[:240]}")

    msg = "\n".join(lines)
    return {"ok": True, "failures": rows[:limit], "content": msg, "response": msg}


def _proactive_paths():
    base = Path(os.environ.get("ELI_ARTIFACTS_DIR", "artifacts")) / "proactive"
    base.mkdir(parents=True, exist_ok=True)
    return base, base / "daemon.pid", base / "daemon.log"


def proactive_status() -> Dict[str, Any]:
    base, pidf, logf = _proactive_paths()
    pid = None
    if pidf.exists():
        try:
            pid = int(pidf.read_text().strip())
        except Exception:
            pid = None
    alive = False
    if pid:
        try:
            import psutil as _psutil
            alive = _psutil.pid_exists(int(pid))
        except Exception:
            # psutil missing — the signal-0 probe is POSIX-only. On Windows os.kill with
            # ANY signal calls TerminateProcess, so os.kill(pid, 0) would KILL the process.
            if os.name != "nt":
                try:
                    os.kill(pid, 0)
                    alive = True
                except Exception:
                    alive = False
    # In-process daemon: the GUI runs it as a THREAD (no separate PID), so the PID check
    # above misses it and falsely reports STOPPED (observed: agent_bus daemon_running=False
    # while '[PROACTIVE] Daemon started'). Also honour the live in-process signals — the
    # module singleton's running flag and the named daemon thread.
    if not alive:
        try:
            import threading as _th
            from eli.planning import proactive_daemon as _pd
            if getattr(getattr(_pd, "_daemon", None), "running", False):
                alive = True
            elif any(t.name == "eli-proactive-daemon" and t.is_alive()
                     for t in _th.enumerate()):
                alive = True
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    msg = f"Proactive daemon is {'RUNNING' if alive else 'STOPPED'}."
    return {"ok": True, "action": "PROACTIVE_STATUS", "pid": pid, "running": alive, "log": str(logf), "content": msg, "response": msg}


def proactive_start() -> Dict[str, Any]:
    base, pidf, logf = _proactive_paths()
    st = proactive_status()
    if st.get("running"):
        return st
    try:
        py = sys.executable if "sys" in globals() else "python3"
    except Exception:
        py = "python3"
    # run as module so imports work
    argv = [py, "-m", "eli.proactive.proactive_daemon"]
    try:
        lf = open(logf, "a", encoding="utf-8")
        p = subprocess.Popen(argv, stdout=lf, stderr=lf, cwd=str(Path.cwd()))
        pidf.write_text(str(p.pid), encoding="utf-8")
        msg = "Proactive daemon started."
        return {"ok": True, "action": "PROACTIVE_START", "pid": p.pid, "cmd": argv, "log": str(logf), "content": msg, "response": msg}
    except Exception as e:
        msg = "Failed to start proactive daemon."
        return {"ok": False, "action": "PROACTIVE_START", "error": repr(e), "content": msg, "response": msg}


def proactive_stop() -> Dict[str, Any]:
    base, pidf, logf = _proactive_paths()
    pid = None
    if pidf.exists():
        try:
            pid = int(pidf.read_text().strip())
        except Exception:
            pid = None
    if not pid:
        msg = "Proactive daemon not running (no PID)."
        return {"ok": True, "action": "PROACTIVE_STOP", "content": msg, "response": msg}
    try:
        os.kill(pid, 15)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        pidf.unlink(missing_ok=True)  # py3.8+: if not available it will throw
    except Exception:
        try:
            pidf.unlink()
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    msg = "Proactive daemon stopped."
    return {"ok": True, "action": "PROACTIVE_STOP", "pid": pid, "content": msg, "response": msg}

__all__ = ['_self_improvement_log_report', '_proactive_paths', 'proactive_status', 'proactive_start', 'proactive_stop']


# ── dispatch handlers (v3 registry) ──
from eli.execution.effectors.registry import register as _register


def _h_proactive_status(a, args):
    try:
        st = proactive_status()
        return {"ok": True, "action": a, **st}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_proactive_start(a, args):
    try:
        st = proactive_start()
        return {"ok": True, "action": a, **st, "content": "proactive started", "response": "proactive started"}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_proactive_stop(a, args):
    try:
        st = proactive_stop()
        return {"ok": True, "action": a, **st, "content": "proactive stopped", "response": "proactive stopped"}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


_register("PROACTIVE_STATUS", _h_proactive_status)
_register("PROACTIVE_START", _h_proactive_start)
_register("PROACTIVE_STOP", _h_proactive_stop)
