from __future__ import annotations
import contextvars
import hashlib
import shlex
import sqlite3
import subprocess
import sys
import time
import json
import requests
from datetime import datetime

import os
import re
import shutil
from pathlib import Path

from eli.utils.log import get_logger

log = get_logger(__name__)

# Verbose diagnostics knob for optional-dependency fallbacks (TTS etc.).
DEBUG = os.environ.get("ELI_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}

from eli.core.paths import path_get as _eli_path_get


def _eli_generated_scripts_dir() -> Path:
    """Directory for generated scripts.

    Honors ELI_ARTIFACTS_DIR / ELI_DATA_DIR so writes can be redirected (tests
    isolate to a temp dir; installed deployments use the user data dir) instead of
    always landing in the source tree's artifacts/scripts. Default unchanged:
    <project_root>/artifacts/scripts.
    """
    root = Path(__file__).resolve().parents[2] / "artifacts"
    env = os.environ.get("ELI_ARTIFACTS_DIR") or os.environ.get("ELI_DATA_DIR")
    if env:
        base = Path(env).expanduser()
        root = base if base.is_absolute() else (Path(__file__).resolve().parents[2] / base)
    d = root / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _eli_attach_response_mode(result, action=None, args=None, meta=None):
    try:
        if isinstance(result, dict):
            _action = action or result.get("action")
            _args = args or result.get("args") or {}
            _meta = dict(meta or result.get("meta") or {})
            result.setdefault("meta", {})
            result["meta"].setdefault("response_mode", classify_response_mode(_action, _args, _meta))
        return result
    except Exception:
        return result

from eli.runtime.response_policy import classify_response_mode
from eli.core.paths import resolve_user_repo_path
from typing import Any, Dict, List, Optional, Tuple

from eli.core.paths import get_paths
import threading

# ── Security gate ────────────────────────────────────────────────────────────
# One copy, in effectors._base (a leaf module): the SecurityManager rebuilt when the allowlists change,
# its fail-closed fallback, and the gated runner. The executor kept its own, which read
# ELI_ALLOWED_CMDS once for the life of the process.
from eli.execution.effectors._base import (  # noqa: E402,F401
    _full_control, _get_security_manager, _run, _run_ok, _shell_command_allowed_fallback,
)
# --- Memory bridge: route through canonical Memory class (not memory_db) ---
# memory_db has a different schema (model/dim/vec columns) than Memory (kind/source/confidence/weight).
# Using Memory directly avoids the "no such column: model" crash.

def _get_canonical_memory():
    """Lazy import to avoid circular import."""
    from eli.memory import get_memory
    return get_memory()

def add_memory(text, tags=""):
    """Store via canonical Memory.store_memory()."""
    try:
        mem = _get_canonical_memory()
        tag_list = [t.strip() for t in str(tags).split(",") if t.strip()] if tags else []
        return mem.store_memory(text, tags=tag_list)
    except Exception as e:
        return {"ok": False, "error": str(e)}

def search_memory(query="", k=5, **kwargs):
    """Search via canonical Memory.recall_memory(), with conversation fallback."""
    try:
        mem = _get_canonical_memory()
        results = list(mem.recall_memory(query, limit=k))
        if hasattr(mem, "search_conversations") and len(results) < int(k or 5):
            conv = mem.search_conversations(query, limit=max(1, int(k or 5) - len(results)))
            for row in conv:
                results.append({
                    "timestamp": row.get("timestamp") or 0,
                    "text": row.get("content") or "",
                    "tags": "conversation",
                    "kind": "conversation",
                    "role": row.get("role") or "",
                    "session_id": row.get("session_id") or "",
                })
        results = results[: int(k or 5)]
        return {"ok": True, "results": results, "count": len(results)}
    except Exception as e:
        return {"ok": False, "error": str(e), "results": []}

# --- Compatibility shim: some older code expects search_memory_compat() ---
def search_memory_compat(query: str = "", limit: int = 10, q: str = "", k: int = 0, db_path=None):
    """Back-compat wrapper around Memory.recall_memory()."""
    qq = (q or query or "").strip()
    kk = int(k or limit or 10)
    return search_memory(query=qq, k=kk)


def _browser_user_dir() -> Path:
    from eli.execution.effectors.system_helpers import browser_user_dir
    return browser_user_dir()

# === CONVERSATION LOGGING (append-only) ===
def _repo_root():
    try:
        return get_paths().artifacts_dir
    except Exception:
        return None

def _convlog_path():
    try:
        from datetime import datetime, timezone
        art = _repo_root()
        if art is None:
            return None
        d = Path(art) / "conversations"
        d.mkdir(parents=True, exist_ok=True)
        fn = datetime.now(timezone.utc).strftime("%Y%m%d") + ".jsonl"
        return d / fn
    except Exception:
        return None

def _convlog_append(role: str, text: str, meta: dict | None = None) -> None:
    try:
        import json
        from datetime import datetime, timezone

        path = _convlog_path()
        if path is None:
            return

        rec = {
            "ts_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "ts_unix": float(time.time()),
            "role": str(role),
            "text": str(text),
            "meta": meta or {},
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        return
# === END CONVERSATION LOGGING ===

# === MEMORY ===

def _get_memory_path():
    from eli.core.db_paths import get_user_db_path
    return get_user_db_path()


def _sqlite_init(conn):
    # Canonical Memory manages schema; keep as no-op for compatibility.
    return None


def memory_store(text, tags=None, kind="note", source="chat", confidence=0.7, ts=None):
    try:
        from eli.memory import get_memory
        mem = get_memory(db_path=_get_memory_path())
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        result = mem.store_memory(
            text=str(text),
            tags=tags or [],
            source=str(source),
            kind=str(kind),
            confidence=float(confidence),
        )
        result = result if isinstance(result, dict) else {}
        skipped = bool(result.get("skipped"))
        return {"ok": bool(result.get("ok", True)) and not skipped, "path": str(_get_memory_path()),
                "id": result.get("id"), "skipped": skipped, "reason": result.get("reason") or result.get("error") or "",
                "vector_indexed": result.get("vector_indexed")}
    except Exception as e:
        return {"ok": False, "error": repr(e), "path": str(_get_memory_path())}


def memory_recall(query, limit=20):
    try:
        from eli.memory import get_memory
        mem = get_memory(db_path=_get_memory_path())
        results = mem.recall_memory(str(query), limit=int(limit))
        return {"ok": True, "results": results, "path": str(_get_memory_path())}
    except Exception as e:
        return {"ok": False, "error": repr(e), "results": [], "path": str(_get_memory_path())}


def _ollama_http_post_json(url: str, payload: Dict[str, Any], timeout_s: int = 30):
    """POST JSON to an Ollama endpoint.

    Returns (ok, status_code, parsed_json_or_None, error_repr_or_None) so
    callers can distinguish transport failures from HTTP-level failures.
    """
    try:
        resp = requests.post(url, json=payload, timeout=timeout_s)
        try:
            data = resp.json()
        except Exception:
            data = None
        return (200 <= resp.status_code < 300, resp.status_code, data, None)
    except Exception as e:
        return (False, 0, None, repr(e))


def _ollama_warm_load(model: str, ollama_host: str) -> bool:
    """
    Best-effort warm-load so /api/ps can report digest for lockdown verification.
    Uses /api/chat (non-stream) with a tiny prompt.
    Returns True if the request likely caused the model to load (or is already loadable),
    False otherwise.
    """
    try:
        model = (model or "").strip()
        if not model:
            return False
        host = (ollama_host or "http://localhost:11434").rstrip("/")
        url = host + "/api/chat"

        payload = {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": "ping"}],
            "options": {"num_predict": 1},
        }

        ok, status, data, err = _ollama_http_post_json(url, payload, timeout_s=30)
        if not ok:
            return False

        # If we got a response object at all, the model was at least resolvable.
        # Ollama typically loads the model to answer, so this is a decent warm-load signal.
        if isinstance(data, dict):
            if "message" in data or "done" in data or "created_at" in data:
                return True
        return True
    except Exception:
        return False
# ------------------ END OLLAMA WARM LOAD HELPERS ------------------

# ----------------------------
# Core normalization utilities
# ----------------------------

def _normalize_result(res: object) -> Dict[str, Any]:
    """
    Guarantee GUI/voice always has:
      - res['ok'] (bool)
      - res['content'] (str)
      - res['response'] (str)
    """
    if not isinstance(res, dict):
        txt = "" if res is None else str(res)
        return {"ok": False, "error": "handler_returned_non_dict", "content": txt, "response": txt}

    if "ok" not in res:
        res["ok"] = False

    if "response" in res and "content" not in res:
        res["content"] = res.get("response")
    if "content" in res and "response" not in res:
        res["response"] = res.get("content")

    if "content" not in res or res.get("content") is None:
        fallback = res.get("message") or res.get("error") or ""
        res["content"] = str(fallback)
    if "response" not in res or res.get("response") is None:
        res["response"] = str(res.get("content") or "")

    res["content"] = "" if res["content"] is None else str(res["content"])
    res["response"] = "" if res["response"] is None else str(res["response"])
    return res


def _strip_ollama_artifacts(s: str) -> str:
    s = (s or "")
    s = s.replace("<|\nend\n", "").replace("<|end|>", "").replace("<|end|", "")
    s = re.sub(r"\s+$", "", s)
    return s.strip()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------
# Grounded runtime audit helpers
# ----------------------------
def _canonical_runtime_file_map() -> Dict[str, Path]:
    root = get_paths().project_root.resolve()
    # Files live under eli/ subdir
    _eli = root / 'eli'
    root = _eli if _eli.exists() else root
    # Canonical runtime file paths under the live eli/ layout. The legacy brain/cognition/... and
    # tools/automation/... candidates no longer exist and produced false "FAIL" entries in
    # RUNTIME_AUDIT.
    rels = {
        'cognitive_engine': ['kernel/engine.py'],
        'gguf_inference':   ['cognition/gguf_inference.py'],
        'memory':           ['memory/memory.py'],
        'memory_init':      ['memory/__init__.py'],
        'router':           ['execution/router_enhanced.py'],
        'executor':         ['execution/executor_enhanced.py'],
        'gui':              ['gui/main_window/window.py'],
        'runtime_settings': ['core/runtime_settings.py'],
        'paths':            ['core/paths.py'],
        'proactive_daemon': ['planning/proactive_daemon.py'],
        'agent_bus':        ['cognition/agent_bus.py'],
        'orchestrator':     ['cognition/orchestrator.py'],
        'inference_broker': ['cognition/inference_broker.py'],
        'output_governor':  ['cognition/output_governor.py'],
        'vector_store':     ['memory/vector_store.py'],
        # Web server + home/RBAC/audit runtime (api/server.py lives at the project root,
        # one level above eli/, hence the `../`).
        'web_server':       ['../api/server.py'],
        'device_server':    ['runtime/device_server.py'],
        'home_intel':       ['runtime/home_intel.py'],
        'api_users':        ['runtime/api_users.py'],
        'evidence_ledger':  ['runtime/evidence_ledger.py'],
    }
    out: Dict[str, Path] = {}
    for key, candidates in rels.items():
        chosen = None
        for rel in candidates:
            cand = (root / rel).resolve()
            if cand.exists():
                chosen = cand
                break
        out[key] = chosen or (root / candidates[0]).resolve()
    return out

def _scan_merge_markers(lines: List[str]) -> List[Dict[str, Any]]:
    issues = []
    marker_re = re.compile(r'^(<{7}(?:\s+.+)?|={7}|>{7}(?:\s+.+)?)$')
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if marker_re.match(stripped):
            issues.append({'type': 'merge_marker', 'line': idx, 'message': stripped[:120]})
    return issues

def _scan_user_specific_paths(lines: List[str]) -> List[Dict[str, Any]]:
    issues = []
    path_re = re.compile(r"(/home/[^/\s]+(?:/[^\s]+)?)|(C:\\Users\\[^\\]+(?:\\[^\s]+)?)")
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if ('re.compile(' in line or 're.search(' in line or 're.match(' in line or 'path_re =' in line or '/home/...' in line or '/home/\\S+' in line or 'C:\\Users\\[^\\]+' in line):
            continue
        for match in path_re.finditer(line):
            value = match.group(0)
            if value and ('\\S' not in value) and ('[^' not in value) and ('(?:' not in value):
                issues.append({'type': 'hardcoded_user_path', 'line': idx, 'message': value})
    return issues

def _scan_top_level_symbols(path: Path, text: str) -> List[Dict[str, Any]]:
    import ast
    issues: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        issues.append({'type': 'syntax_error', 'line': int(getattr(e, 'lineno', 0) or 0), 'message': str(e)})
        return issues
    seen: Dict[str, List[int]] = {}
    for node in tree.body:
        names: List[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        for name in names:
            seen.setdefault(name, []).append(int(getattr(node, 'lineno', 0) or 0))
    for name, lines in seen.items():
        if len(lines) > 1:
            issues.append({'type': 'duplicate_top_level_symbol', 'line': lines[1], 'message': f'{name} also defined at lines {lines}'})
    return issues

def _audit_python_file(path: Path) -> Dict[str, Any]:
    entry = {'path': str(path), 'status': 'PASS', 'issues': []}
    if not path.exists():
        entry['status'] = 'FAIL'
        entry['issues'].append({'type': 'missing_file', 'line': 0, 'message': 'file not found'})
        return entry
    text = path.read_text(encoding='utf-8', errors='replace')
    lines = text.splitlines()
    issues = []
    issues.extend(_scan_merge_markers(lines))
    issues.extend(_scan_user_specific_paths(lines))
    issues.extend(_scan_top_level_symbols(path, text))
    if not any(i['type'] == 'syntax_error' for i in issues):
        try:
            compile(text, str(path), 'exec')
        except SyntaxError as e:
            issues.append({'type': 'syntax_error', 'line': int(getattr(e, 'lineno', 0) or 0), 'message': str(e)})
    entry['issues'] = sorted(issues, key=lambda x: (int(x.get('line', 0) or 0), x.get('type', '')))
    if entry['issues']:
        entry['status'] = 'FAIL'
    return entry

def _runtime_health_probes() -> List[Dict[str, Any]]:
    """LIVE health checks a static source audit cannot catch: actually EXERCISE
    key subsystems and check DATA INTEGRITY, so real runtime faults surface in an
    audit (a method-level NameError, malformed data, recurring logged failures)
    instead of only appearing once at use time. Each probe: {name, ok, detail}."""
    probes: List[Dict[str, Any]] = []

    def _probe(name, fn):
        try:
            ok, detail = fn()
            probes.append({"name": name, "ok": bool(ok), "detail": str(detail)})
        except Exception as e:
            probes.append({"name": name, "ok": False, "detail": f"{type(e).__name__}: {e}"})

    def _db_path():
        from eli.memory import get_memory
        return getattr(get_memory(), "db_path", None)

    # Plugin manager — instantiate + list (catches the method-level NameError class
    # that import/syntax audits miss: the module imports fine, the fault is at call).
    def _plugins():
        from eli.plugins.manager import get_manager
        return True, f"OK — {len(get_manager().list_installed())} plugin(s) installed"
    _probe("plugin_manager", _plugins)

    # Memory — singleton resolves + db file present.
    def _mem():
        from pathlib import Path as _P
        dbp = _db_path()
        return (bool(dbp and _P(dbp).exists()),
                f"db={_P(dbp).name}" if dbp else "no db path")
    _probe("memory", _mem)

    # Agent bus — registry imports + has agents.
    def _bus():
        from eli.cognition.agent_bus import _ALL_AGENTS
        return bool(_ALL_AGENTS), f"{len(_ALL_AGENTS)} agents registered"
    _probe("agent_bus", _bus)

    # Habit-rule integrity — flag the un-schedulable corruption class.
    def _habits():
        import sqlite3 as _s
        dbp = _db_path()
        if not dbp:
            return True, "no db"
        c = _s.connect(str(dbp))
        try:
            bad = c.execute(
                "SELECT COUNT(*) FROM habit_rules WHERE enabled=1 AND (hour IS NULL "
                "OR minute IS NULL OR TRIM(LOWER(command))=TRIM(LOWER(name)))").fetchone()[0]
            tot = c.execute("SELECT COUNT(*) FROM habit_rules WHERE enabled=1").fetchone()[0]
        finally:
            c.close()
        if bad:
            return False, f"{bad} of {tot} enabled habit rule(s) un-schedulable (NULL time / bare-token command)"
        return True, f"{tot} enabled habit rule(s), all schedulable"
    _probe("habit_integrity", _habits)

    # Recent live failures, so the audit reflects live problems and not just static source. Reads
    # the store the Self-Improve panel uses (resolved entries are filtered by get_recent_failures),
    # so the two agree.
    def _failures():
        from eli.runtime.self_improvement import get_self_improvement
        from eli.runtime.failure_taxonomy import classify, is_actionable
        rows = get_self_improvement().memory.get_recent_failures(limit=20)
        actionable = []
        for r in rows or []:
            err = str(r.get("error") or r.get("user_input") or "")
            info = classify(err, command=str(r.get("command") or ""))
            if not is_actionable(info.get("category", "")):
                continue
            actionable.append(r)
            if len(actionable) >= 5:
                break
        if not actionable:
            return True, "no live failures logged"
        summary = "; ".join(
            f"{(r.get('error') or r.get('user_input') or '')[:70]} (×{r.get('occurrence_count', 1)})"
            for r in actionable)
        return False, f"{len(actionable)} recent live failure(s): {summary}"
    _probe("recent_failures", _failures)

    return probes


def _runtime_audit_report() -> Dict[str, Any]:
    files = _canonical_runtime_file_map()
    entries = []
    for key in ['cognitive_engine', 'gguf_inference', 'memory', 'memory_init', 'router', 'executor', 'gui', 'runtime_settings', 'paths', 'proactive_daemon']:
        for path in (_gui_source_files(files[key]) if key == 'gui' else [files[key]]):
            entries.append(_audit_python_file(path))
    probes = _runtime_health_probes()
    return {'ok': True, 'entries': entries, 'health_probes': probes}

def _format_runtime_audit(report: Dict[str, Any]) -> str:
    lines = []
    for entry in report.get('entries', []):
        lines.append(f"{entry['status']} {entry['path']}")
        for issue in entry.get('issues', []):
            line_no = int(issue.get('line', 0) or 0)
            prefix = f"  - line {line_no}" if line_no else '  - line ?'
            lines.append(f"{prefix} [{issue.get('type')}] {issue.get('message')}")
    probes = report.get('health_probes') or []
    if probes:
        lines.append("")
        lines.append("Live health probes:")
        for p in probes:
            mark = "✅" if p.get("ok") else "❌"
            lines.append(f"  {mark} {p.get('name')}: {p.get('detail')}")
    return '\n'.join(lines) if lines else 'No runtime files audited.'

_LINE_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _content_from_line_spec(content: str) -> str:
    """"exactly three lines: a=1, b=2, c=3" means three lines, not that sentence written into the file."""
    m = re.match(r"^\s*(?:exactly\s+)?(\d+|" + "|".join(_LINE_WORDS) + r")\s+lines?\s*(?:of\s+text\s*)?[:\-]\s*(.+?)\s*$",
                 str(content or ""), re.I | re.S)
    if not m:
        return content
    n = int(m.group(1)) if m.group(1).isdigit() else _LINE_WORDS[m.group(1).lower()]
    body = m.group(2)
    for split in (r"\n", r"\s*[;,]\s*(?:and\s+)?", r"\s+(?=\S+\s*=)", r"\s+and\s+"):
        items = [x.strip() for x in re.split(split, body) if x.strip()]
        if len(items) == n:
            return "\n".join(items) + "\n"
    return content


def _gpu_status_report() -> Dict[str, Any]:
    # get_live_gpu_telemetry() is the one place that answers "what is my GPU doing now":
    # nvidia-smi/rocm-smi dispatch, the cross-vendor identity fallback and honest None for
    # unavailable fields all live there.
    runtime_snapshot: Dict[str, Any] = {}
    try:
        snap_path = get_paths().artifacts_dir / "runtime_snapshot.json"
        if snap_path.exists():
            runtime_snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    except Exception:
        runtime_snapshot = {}

    def _runtime_snapshot_lines() -> list:
        # Independent of any GPU vendor tool: it comes from the loader's own record of what it
        # loaded with. A failing live GPU query must never suppress it (it did, and "what are your
        # settings" was answered "not specified in the evidence").
        if not runtime_snapshot:
            return []
        lines = ["", "ELI selected llama.cpp load parameters:"]
        if runtime_snapshot.get("model_name") or runtime_snapshot.get("model_path"):
            lines.append(f"- model: {runtime_snapshot.get('model_name') or Path(str(runtime_snapshot.get('model_path'))).name}")
        if runtime_snapshot.get("model_path"):
            lines.append(f"- model file: {runtime_snapshot.get('model_path')}")
        lines += [
            f"- context: {runtime_snapshot.get('n_ctx', 'unknown')}",
            f"- GPU-layer parameter: {runtime_snapshot.get('n_gpu_layers', 'unknown')}",
            f"- batch: {runtime_snapshot.get('n_batch', 'unknown')}",
            f"- CPU threads: {runtime_snapshot.get('n_threads', 'unknown')}",
        ]
        if runtime_snapshot.get("load_mode"):
            lines.append(f"- load mode: {runtime_snapshot.get('load_mode')}")
        try:
            _age = max(0.0, time.time() - float(runtime_snapshot.get("ts") or 0))
            if runtime_snapshot.get("ts"):
                lines.append(f"- snapshot age: {int(_age)} s" + (" (stale: the model may have been reloaded since)" if _age > 900 else ""))
        except Exception:
            log.debug("snapshot age unavailable", exc_info=True)
        _req, _eff = runtime_snapshot.get("requested"), runtime_snapshot.get("effective")
        if isinstance(_req, dict) and isinstance(_eff, dict):
            _fmt = lambda d: (f"ctx={d.get('n_ctx', '?')} gpu_layers={d.get('n_gpu_layers', '?')} "
                              f"batch={d.get('n_batch', '?')}")
            lines.append(f"- requested: {_fmt(_req)}; effective: {_fmt(_eff)}")
        return lines

    from eli.core.hardware_profile import get_live_gpu_telemetry
    telem = get_live_gpu_telemetry()

    if not telem["ok"]:
        lines = ["GPU status:", "- No GPU detected — running CPU-only."]
        lines.extend(_runtime_snapshot_lines())
        msg = "\n".join(lines)
        return {"ok": bool(runtime_snapshot), "content": msg, "response": msg}

    total = telem["total_mb"]
    free = telem["free_mb"]
    used = telem["used_mb"]
    util = telem["util_pct"]

    lines = ["GPU status:"]
    note = " (estimated — live probe unavailable)" if telem["estimated"] or telem["live_probe_failed"] else ""
    lines.append(f"- name: {telem['name']}{note}")
    if telem["driver"]:
        lines.append(f"- driver: {telem['driver']}")
    if total is not None and used is not None and free is not None:
        used_pct = (used / total * 100.0) if total else None
        _pct = f", {used_pct:.1f}% used" if used_pct is not None else ""
        lines.append(f"- VRAM: {used} MiB used / {total} MiB total ({free} MiB free{_pct})")
    elif total is not None:
        lines.append(f"- VRAM: ~{total} MiB total (best known figure, not necessarily a live reading)")
    else:
        lines.append("- VRAM: unavailable")
    # Several cards: use and temperature are each card's own. The one figure is the busiest (hottest)
    # card's, and a "1%" read from one of two cards was taken for the whole machine.
    cards = [d for d in (telem.get("devices") or []) if isinstance(d, dict)]

    def _each(key: str, unit: str) -> str:
        return ", ".join(f"GPU {d.get('index', i)} " + (f"{d[key]:.0f}{unit}" if d.get(key) is not None else "not reported")
                         for i, d in enumerate(cards))

    if len(cards) > 1 and any(d.get("util_pct") is not None for d in cards):
        lines.append(f"- GPU utilization: {_each('util_pct', '%')}")
    else:
        lines.append(f"- GPU utilization: {util}%" if util is not None else "- GPU utilization: unavailable")
    if len(cards) > 1 and any(d.get("temp_c") is not None for d in cards):
        lines.append(f"- GPU core temperature: {_each('temp_c', ' C')} (VRAM / memory-chip temperature is not "
                     f"reported by this reading)")
    else:
        lines.append(f"- GPU core temperature: {telem['temp_c']} C (VRAM / memory-chip temperature is not reported by this reading)"
                     if telem["temp_c"] is not None else "- temperature: unknown")
    if telem["power_w"] is not None:
        _limit = f" / {telem['power_limit_w']:.2f} W limit" if telem["power_limit_w"] is not None else ""
        lines.append(f"- power: {telem['power_w']:.2f} W{_limit}")
    else:
        lines.append("- power: unknown")

    lines.extend(_runtime_snapshot_lines())

    # Performance reading derived from this load, not boilerplate. The old fixed strings hedged
    # about a fallback that hadn't happened (requested and effective were identical). A runtime
    # report shouldn't speculate about its own state.
    _reading = []
    try:
        from eli.cognition.context_synthesiser import runtime_load_gap as _gap_fn
        _gap = _gap_fn(runtime_snapshot or None)
    except Exception:
        _gap = {"ok": False}

    if _gap.get("ok"):
        _reduced = _gap.get("reduced") or {}
        _raised = _gap.get("raised") or {}
        if _reduced:
            _detail = "; ".join(
                f"{k} {v['requested']} → {v['effective']}" for k, v in sorted(_reduced.items())
            )
            _reading.append(f"- Loaded BELOW request ({_detail}) — reduced to fit "
                            f"available VRAM, not a settings error.")
        elif _raised and _gap.get("moe_expert_offload"):
            # MoE raises n_gpu_layers above what was requested (llama.cpp puts everything on
            # GPU) — report the real requested->effective numbers, not "as requested".
            _detail = "; ".join(
                f"{k} {v['requested']} → {v['effective']}" for k, v in sorted(_raised.items())
            )
            _experts_gb = _gap.get("moe_experts_gb")
            _resident_gb = _gap.get("moe_resident_gb")
            _reading.append(
                f"- Loaded with mixture-of-experts offload ({_detail}): every layer's "
                f"attention/shared weights are on the GPU"
                + (f" (~{_resident_gb}GB VRAM)" if _resident_gb is not None else "")
                + ", the expert weights"
                + (f" (~{_experts_gb}GB)" if _experts_gb is not None else "")
                + " stay in system RAM and are processed by the CPU during generation."
            )
        else:
            _eff = _gap.get("effective") or {}
            _reading.append(
                "- Loaded exactly as requested (ctx="
                f"{_eff.get('n_ctx', 'unknown')}, gpu_layers={_eff.get('n_gpu_layers', 'unknown')}, "
                f"batch={_eff.get('n_batch', 'unknown')}) — no fallback occurred."
            )
        if not _gap.get("on_gpu"):
            _reading.append("- Running CPU-only: per-turn latency is significantly "
                            "higher than GPU mode.")

    if free is not None and total:
        # What reads free is spare except what llama.cpp still takes as it works: its compute buffers are
        # allocated with the model, and what it adds on the first prompts was measured on this machine
        # (eli/core/load_plan.py, headroom). The spare memory is turned into context this model's cache holds.
        try:
            from eli.core.hardware_profile import model_cost, vram_reserve_mb
            _lazy = float(vram_reserve_mb())
        except Exception:
            model_cost, _lazy = None, 0.0
        _spare = max(0.0, float(free) - _lazy)
        _reading.append(f"- {free:.0f} MiB of {total:.0f} MiB VRAM free"
                        + (f", {_lazy:.0f} MiB of it what llama.cpp was measured to add as it works" if _lazy else "")
                        + ".")
        _more = None
        try:
            _eff_r = (runtime_snapshot or {}).get("effective") or runtime_snapshot or {}
            _c = int(_eff_r.get("n_ctx") or 0)
            _cost = model_cost((runtime_snapshot or {}).get("model_path"),
                               kv_cache_type=str((runtime_snapshot or {}).get("cache_type_k") or "") or None) \
                if model_cost and _c > 0 else None
            if _cost is not None:
                _per_token = (_cost.kv_mb(_c + 1024) - _cost.kv_mb(_c)) / 1024.0
                _more = int(_spare / _per_token) if _per_token > 0 else None
        except Exception:
            _more = None
        _reading.append(f"- Spare: {_spare:.0f} MiB"
                        + (f", room for about {_more} more tokens of context for this model." if _more is not None
                           else "."))

    if _reading:
        lines.extend(["", "Performance reading:"] + _reading)
    msg = "\n".join(lines)
    return {"ok": True, "gpu": telem, "runtime_snapshot": runtime_snapshot, "content": msg, "response": msg}

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

def _import_audit_report() -> Dict[str, Any]:
    import importlib
    modules = [
        'eli.kernel.engine',
        'eli.cognition.gguf_inference',
        'eli.memory.memory',
        'eli.memory',
        'eli.execution.router_enhanced',
        'eli.execution.executor_enhanced',
        'eli.gui.gui_eli_original',
        'eli.core.runtime_settings',
        'eli.core.paths',
        'eli.planning.proactive_daemon',
    ]
    entries = []
    for mod in modules:
        status = 'PASS'; error = ''; fix = ''
        try:
            importlib.import_module(mod)
        except SystemExit:
            status = "SKIP"; error = "Module calls sys.exit() on import (e.g. missing Qt)"; fix = "Install PySide6 (LGPLv3 — `pip install PySide6`) or suppress sys.exit in GUI module."
        except Exception as e:
            status = 'FAIL'; error = f'{type(e).__name__}: {e}'
            if 'No module named' in error:
                fix = 'Check package path, missing module, or PYTHONPATH wiring.'
            elif 'SyntaxError' in error or 'unterminated string literal' in error:
                fix = 'Fix syntax errors in the target module before import.'
            else:
                fix = 'Inspect traceback and referenced dependency or symbol.'
        entries.append({'module': mod, 'status': status, 'error': error, 'suggested_fix': fix})
    return {'ok': True, 'entries': entries}

def _format_import_audit(report: Dict[str, Any]) -> str:
    lines = []
    for entry in report.get('entries', []):
        lines.append(f"{entry['module']} | {entry['status']} | {entry.get('error', '') or '-'} | {entry.get('suggested_fix', '') or '-'}")
    return '\n'.join(lines) if lines else 'No import audit results.'


def _diagnose_executor_wrappers() -> Dict[str, Any]:
    """Walk the executor wrapper stack and report:
       1. The canonical middleware table (named, ordered)
       2. The legacy ORIG_EXECUTE rebind chain (discovered via _ELI_*_ORIG_EXECUTE globals)
       3. For a small set of representative actions, which layer would handle them
    """
    g = globals()
    report: Dict[str, Any] = {
        "middleware_table": [],
        "rebind_chain": [],
        "action_handlers": {},
    }
    # 1. canonical middleware table — already structured as ((name, fn), ...)
    mw_table = g.get("_ELI_EXECUTOR_MIDDLEWARE_TABLE") or ()
    for entry in mw_table:
        try:
            name, fn = entry
            report["middleware_table"].append({
                "name": str(name),
                "callable": getattr(fn, "__name__", repr(fn)),
                "module": getattr(fn, "__module__", "?"),
            })
        except Exception:
            continue
    # 2. rebind chain — discover all _ELI_*_ORIG_EXECUTE globals
    for key, val in sorted(g.items()):
        if not isinstance(key, str):
            continue
        if not key.startswith("_ELI_") or "ORIG_EXECUTE" not in key:
            continue
        report["rebind_chain"].append({
            "sentinel": key,
            "target_callable": getattr(val, "__name__", repr(val) if val is not None else "<unbound>"),
            "is_callable": bool(callable(val)),
        })
    # 3. current top-level execute identity (which wrapper sits on top)
    top_execute = g.get("execute")
    report["active_top_execute"] = {
        "name": getattr(top_execute, "__name__", repr(top_execute)),
        "module": getattr(top_execute, "__module__", "?"),
    }
    report["active_top_execute_action"] = {
        "name": getattr(g.get("execute_action"), "__name__", repr(g.get("execute_action"))),
        "module": getattr(g.get("execute_action"), "__module__", "?"),
    }
    # 4. canonical base (what the middleware table eventually wraps)
    base = g.get("_ELI_EXECUTOR_CANONICAL_BASE")
    if base is not None:
        report["canonical_base"] = {
            "name": getattr(base, "__name__", repr(base)),
            "module": getattr(base, "__module__", "?"),
        }
    # 5. probe a representative set of actions — which middleware short-circuits which
    probes = [
        "CHAT", "GENERATE_SCRIPT", "FIX_FILE", "SELF_REPORT", "RUNTIME_AUDIT",
        "MEMORY_RECALL", "MEMORY_STATUS", "PERSONAL_MEMORY_SUMMARY",
        "PLAY_MEDIA", "PAUSE_MEDIA", "VOLUME", "OPEN_APP", "CLOSE_APP",
        "REASONING_MODE_STATUS", "GUI_RUNTIME_AUDIT",
    ]
    handlers: Dict[str, str] = {}
    for action in probes:
        # Heuristic: each middleware's filter is in its docstring or name. We don't
        # actually invoke; we just record that the middleware MIGHT handle it.
        handlers[action] = "via canonical middleware table → rebind chain → _execute_impl"
    report["action_handlers"] = handlers
    return report


def _format_executor_wrappers(report: Dict[str, Any]) -> str:
    lines = ["EXECUTOR WRAPPER DIAGNOSTIC", "=" * 60, ""]
    lines.append(f"Top-level execute:        {report.get('active_top_execute', {}).get('name')}")
    lines.append(f"Top-level execute_action: {report.get('active_top_execute_action', {}).get('name')}")
    base = report.get("canonical_base")
    if base:
        lines.append(f"Canonical base:           {base.get('name')}")
    lines.append("")
    mw = report.get("middleware_table") or []
    lines.append(f"Canonical middleware table ({len(mw)} entries, top-to-bottom):")
    for i, entry in enumerate(mw, 1):
        lines.append(f"  {i:>2}. {entry['name']:<32} → {entry['callable']}")
    lines.append("")
    rebinds = report.get("rebind_chain") or []
    lines.append(f"Legacy ORIG_EXECUTE sentinels ({len(rebinds)} found):")
    for entry in rebinds:
        status = "callable" if entry["is_callable"] else "unbound"
        lines.append(f"  - {entry['sentinel']:<48} [{status}] → {entry['target_callable']}")
    lines.append("")
    lines.append("Resolution order for any action:")
    lines.append("  1. Top-level execute() — outermost wrapper")
    lines.append("  2. Each middleware in the table (in order)")
    lines.append("  3. _ELI_EXECUTOR_CANONICAL_BASE — the legacy rebind chain")
    lines.append("  4. _execute_impl — the original 160+ action dispatcher")
    return "\n".join(lines)


def _resolve_runtime_paths_report() -> Dict[str, Any]:
    p = get_paths()
    project_root = Path(p.project_root).expanduser().resolve()
    entries = {
        'project_root': str(project_root),
        'artifacts': str(Path(p.artifacts_dir).expanduser().resolve()),
        'config': str(Path(p.config_dir).expanduser().resolve()),
        'notes': str(Path(getattr(p, 'notes_dir', '')).expanduser().resolve()) if getattr(p, 'notes_dir', '') else '',
        'models': str(Path(p.models_dir).expanduser().resolve()),
        'user_db': str(Path(p.user_db).expanduser().resolve()),
        'agent_db': str(Path(p.agent_db).expanduser().resolve()),
    }
    flags = {}
    for key, value in entries.items():
        resolved = Path(value).expanduser().resolve() if value else project_root
        try:
            resolved.relative_to(project_root)
            inside_project = True
        except Exception:
            inside_project = False
        machine_absolute = resolved.is_absolute()
        home_bound = str(resolved).startswith(str(Path.home().expanduser().resolve()))
        user_specific = bool(home_bound and not inside_project)
        non_redistributable = bool(user_specific)
        flags[key] = {
            'path': str(resolved),
            'inside_project': inside_project,
            'machine_absolute': machine_absolute,
            'user_specific': user_specific,
            'non_redistributable': non_redistributable,
        }
    critical_files = {
        key: str(path.expanduser().resolve())
        for key, path in _canonical_runtime_file_map().items()
    }
    import_audit = _import_audit_report()
    return {
        'ok': True,
        'entries': flags,
        'critical_files': critical_files,
        'import_audit': import_audit,
    }

def _format_runtime_paths(report: Dict[str, Any]) -> str:
    lines = []
    critical_files = report.get('critical_files') or {}
    if critical_files:
        lines.append("Critical runtime files:")
        for key, path in critical_files.items():
            lines.append(f"- {key}: {path}")

    import_audit = report.get('import_audit') or {}
    if import_audit.get('entries'):
        if lines:
            lines.append("")
        lines.append("Current import status:")
        for entry in import_audit.get('entries', []):
            error = entry.get('error') or '-'
            lines.append(f"- {entry.get('module')}: {entry.get('status')} ({error})")

    if lines:
        lines.append("")
    lines.append("Runtime directories:")
    for key, meta in report.get('entries', {}).items():
        lines.append(
            f"- {key}: {meta['path']} | "
            f"inside_project={meta.get('inside_project')} "
            f"user_specific={meta.get('user_specific')} "
            f"non_redistributable={meta.get('non_redistributable')}"
        )
    return "\n".join(lines)

def _grounded_file_audit_report(path: Path) -> Dict[str, Any]:
    """Grounded structural audit of an explicitly-referenced source file.

    Unlike the GUI wiring scan (which checks a fixed set of needles in one
    hardcoded file), this derives all of its evidence dynamically from the file
    the user actually pointed at: AST top-level structure for Python, plus
    merge-marker and user-specific-path scans. It reports what is genuinely in
    that file rather than confabulating about an unrelated one.
    """
    entry = {'path': str(path), 'status': 'PASS', 'issues': [], 'evidence': {}, 'snippets': {}}
    if not path.exists():
        entry['status'] = 'FAIL'
        entry['issues'].append({'type': 'missing_file', 'line': 0, 'message': 'file not found'})
        return {'ok': True, 'entry': entry}
    text = path.read_text(encoding='utf-8', errors='replace')
    lines = text.splitlines()
    entry['evidence']['lines_total'] = [len(lines)]
    if path.suffix == '.py':
        import ast
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            entry['status'] = 'FAIL'
            entry['issues'].append({'type': 'syntax', 'line': int(e.lineno or 0),
                                    'message': f'SyntaxError: {e.msg}'})
            tree = None
        if tree is not None:
            imports: List[int] = []
            classes: List[int] = []
            funcs: List[int] = []
            decorators: List[int] = []
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    imports.append(node.lineno)
                elif isinstance(node, ast.ClassDef):
                    classes.append(node.lineno)
                    entry['snippets'].setdefault('classes', []).append(
                        f"  line {node.lineno}: class {node.name}")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs.append(node.lineno)
                    entry['snippets'].setdefault('functions', []).append(
                        f"  line {node.lineno}: def {node.name}")
                    decorators.extend(node.lineno for _ in node.decorator_list)
            entry['evidence']['imports'] = imports
            entry['evidence']['classes'] = classes
            entry['evidence']['functions'] = funcs
            if decorators:
                entry['evidence']['decorated_defs'] = decorators
            # Cap the structural sample so the audit stays evidence, not a dump.
            for cat in ('classes', 'functions'):
                snips = entry['snippets'].get(cat) or []
                if len(snips) > 15:
                    entry['snippets'][cat] = snips[:15] + [f"  … +{len(snips) - 15} more"]
    entry['issues'].extend(_scan_merge_markers(lines))
    entry['issues'].extend(_scan_user_specific_paths(lines))
    if entry['issues'] and entry['status'] != 'FAIL':
        entry['status'] = 'FAIL'
    return {'ok': True, 'entry': entry}


def _gui_source_files(gui_path: Path) -> List[Path]:
    """The window module and, when it is part of a package, every module of that package."""
    pkg = gui_path.parent
    if (pkg / '__init__.py').is_file():
        return sorted(pkg.rglob('*.py'))
    return [gui_path]


def _gui_runtime_audit_report(question: Optional[str] = None) -> Dict[str, Any]:
    canon = _canonical_runtime_file_map()
    gui_path = canon['gui']
    # Dynamic target: if the user named a source file, audit that file (grounded, AST-derived)
    # instead of the fixed GUI file. A request about api/server.py used to come back describing the
    # GUI.
    if question:
        try:
            from eli.runtime.code_examiner import _extract_named_paths as _enp
            referenced = _enp(question)
        except Exception:
            referenced = []
        for cand in referenced:
            if cand.resolve() != gui_path.resolve():
                return _grounded_file_audit_report(cand)
    # No path in this turn — thread the file the conversation was actually about
    # (set when ELI last summarised/examined/fixed a file). This is what makes a
    # follow-up "prove you read it" audit the right file instead of the GUI.
    try:
        from eli.runtime.code_examiner import get_last_file as _glf
        last = str(_glf() or "").strip()
        if last:
            lp = Path(last)
            if lp.exists() and lp.resolve() != gui_path.resolve():
                return _grounded_file_audit_report(lp)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    entry = {'path': str(gui_path), 'status': 'PASS', 'issues': [], 'evidence': {}, 'snippets': {}}
    if not gui_path.exists():
        entry['status'] = 'FAIL'; entry['issues'].append({'type': 'missing_file', 'line': 0, 'message': 'file not found'})
        return {'ok': True, 'entry': entry}
    # The window is a package: every module of it, each line labelled with its file.
    lines, where = [], []
    for path in _gui_source_files(gui_path):
        for n, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            lines.append(line)
            where.append(f"{path.name}:{n}")
    patterns = {
        'router': 'router_enhanced',
        'executor': 'executor_enhanced',
        'cognition': 'cognitive_engine',
        'proactive_import': 'start_daemon',
        'send_worker': 'threading.Thread(target=generate_worker',
    }
    for key, needle in patterns.items():
        hits = [i for i, line in enumerate(lines) if needle in line]
        entry['evidence'][key] = [where[i] for i in hits]
        # Store actual code at the first 3 matching lines so the model reports
        # what is really there rather than fabricating plausible-looking code.
        entry['snippets'][key] = [f"  {where[i]}: {lines[i].strip()}" for i in hits[:3]]
        if not hits:
            entry['status'] = 'FAIL'
            entry['issues'].append({'type': 'missing_hook', 'line': 0, 'message': f'{key} hook not found: {needle}'})
    for issue in _scan_user_specific_paths(lines):
        n = int(issue.get('line') or 0)
        if 0 < n <= len(where):
            file_name, file_line = where[n - 1].rsplit(':', 1)
            issue = dict(issue, line=int(file_line), message=f"{file_name}: {issue.get('message', '')}")
        entry['issues'].append(issue)
    if entry['issues']:
        entry['status'] = 'FAIL'
    return {'ok': True, 'entry': entry}

def _format_gui_runtime_audit(report: Dict[str, Any]) -> str:
    entry = report.get('entry', {})
    out = [f"{entry.get('status', 'FAIL')} {entry.get('path', '')}"]
    evidence = entry.get('evidence') or {}
    snippets = entry.get('snippets') or {}
    for key, hits in evidence.items():
        out.append(f"  - {key}: lines {hits if hits else 'MISSING'}")
        for snip in snippets.get(key, []):
            out.append(f"    {snip}")
    for issue in entry.get('issues', []):
        line_no = int(issue.get('line', 0) or 0)
        prefix = f"  - line {line_no}" if line_no else '  - line ?'
        out.append(f"{prefix} [{issue.get('type')}] {issue.get('message')}")
    return '\n'.join(out)


def _split_text_on_lines(text: str, max_chars: int) -> List[str]:
    """Split text into <= max_chars windows on line boundaries. A single line
    longer than max_chars is hard-split so nothing is dropped."""
    out: List[str] = []
    buf: List[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        if len(line) > max_chars:
            if buf:
                out.append("".join(buf)); buf = []; size = 0
            for i in range(0, len(line), max_chars):
                out.append(line[i:i + max_chars])
            continue
        if size + len(line) > max_chars and buf:
            out.append("".join(buf)); buf = []; size = 0
        buf.append(line); size += len(line)
    if buf:
        out.append("".join(buf))
    return out


_SUMMARY_SYSTEM = ("You are a precise technical analyst. Follow the user's instruction "
                   "exactly — if they asked for a summary, summarize; if they asked you to "
                   "evaluate, critique, score, or answer something specific, do that instead "
                   "of defaulting to a generic summary. Ground every statement in the text "
                   "provided; never invent details that are not present.")


def _summarize_long_text(content: str, instruction: str = "", *, _depth: int = 0) -> Optional[str]:
    """Summarize text of ANY length WITHOUT truncation, via hierarchical
    map-reduce sized to the loaded model's real context window.

    Small inputs are summarised in one pass. Larger inputs are split on line
    boundaries into context-sized chunks, each chunk is summarised (MAP), then
    the partial summaries are synthesised into one (REDUCE) — recursing if the
    combined partials are themselves too large. The entire file is always read;
    nothing is silently dropped. Returns None only if no model is available.
    """
    from eli.cognition import gguf_inference as _gi
    if _gi.load_model() is None:
        return None
    # Usable context (tokens) → input char budget (~3.5 chars/token), leaving
    # headroom for the system prompt, instruction, and the generated summary.
    try:
        ctx_tokens = int(_gi.context_window() or 0)
    except Exception:
        ctx_tokens = 0
    if ctx_tokens <= 0:
        return None
    in_token_budget = max(512, int(ctx_tokens * 0.55))
    chunk_chars = max(2000, int(in_token_budget * 3.5))

    base_instruction = (instruction or "").strip() or "Summarize the following file content."

    if len(content) <= chunk_chars:
        msg = f"{base_instruction}\n\nFile content:\n\n{content}"
        return _gi.chat_completion(msg, system=_SUMMARY_SYSTEM, max_tokens=900, temperature=0.3)

    # Degenerate-input guard: stop recursing if reduction can't shrink the text.
    if _depth >= 5:
        msg = (f"{base_instruction}\n\nFile content (final reduction pass):\n\n"
               f"{content[:chunk_chars]}")
        return _gi.chat_completion(msg, system=_SUMMARY_SYSTEM, max_tokens=900, temperature=0.3)

    chunks = _split_text_on_lines(content, chunk_chars)
    n = len(chunks)
    partials: List[str] = []
    for i, ch in enumerate(chunks, 1):
        msg = (f"{base_instruction}\n\nThis is part {i} of {n} of a single file. "
               f"Summarize THIS part's key points, structure, and purpose:\n\n{ch}")
        part = _gi.chat_completion(msg, system=_SUMMARY_SYSTEM, max_tokens=500, temperature=0.3)
        partials.append(f"[Part {i}/{n}]\n{(part or '').strip()}")

    combined = "\n\n".join(partials)
    reduce_instruction = (
        "Synthesize these partial summaries of ONE file into a single coherent, "
        "complete summary covering its structure, key components, and purpose. "
        "Do not present them as separate parts.")
    return _summarize_long_text(combined, reduce_instruction, _depth=_depth + 1)


def _memory_health(mem: Any) -> Dict[str, Any]:
    try:
        return mem.integrity_report()
    except Exception:
        return {}


def _explain_memory_runtime_report() -> Dict[str, Any]:
    import sqlite3
    from eli.memory import (
        get_memory as _executor_get_memory,
        get_memory_status as _executor_get_memory_status,
        resolve_db_paths as _executor_resolve_db_paths,
    )

    paths = _executor_resolve_db_paths()
    mem = _executor_get_memory()
    status_candidates: List[Dict[str, Any]] = []
    for candidate in (
        getattr(mem, 'db_path', None),
        _eli_path_get(paths, "memory_db"),
        _eli_path_get(paths, "user_db"),
    ):
        if candidate in (None, ""):
            continue
        try:
            status_candidates.append(_executor_get_memory_status(candidate))
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    if status_candidates:
        status = max(
            status_candidates,
            key=lambda item: int(item.get("memory_entries", 0) or 0)
            + int(item.get("conversation_turns", 0) or 0),
        )
    else:
        status = _executor_get_memory_status(getattr(mem, 'db_path', None))

    name_guess = None
    try:
        from eli.kernel.state import get_user_name as _gun_exec
        name_guess = _gun_exec().strip() or None
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    try:
        recent = mem.get_recent_conversation(limit=6)
    except Exception:
        recent = []

    def _table_counts(path: Any) -> Dict[str, int]:
        # the one counter of a store's tables (eli/memory/memory_truth.inspect_sqlite); -1 one it
        # could not count
        if path in (None, ""):
            return {}
        from eli.memory.memory_truth import inspect_sqlite
        counts = inspect_sqlite(Path(path)).get("counts") or {}
        return {str(t): (-1 if n is None else int(n)) for t, n in counts.items() if not str(t).startswith("sqlite_")}

    def _identity_hits_from_sqlite(*db_paths: Any, limit: int = 5) -> List[str]:
        hits: List[str] = []
        wanted_tables = ("memories", "memory", "semantic")
        wanted_cols = ("text", "content", "fact", "value", "summary")
        for raw_path in db_paths:
            try:
                p = Path(raw_path)
                if not p.exists():
                    continue
                conn = sqlite3.connect(str(p))
                tables = {
                    row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                for table in wanted_tables:
                    if table not in tables:
                        continue
                    cols = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
                    text_col = next((c for c in wanted_cols if c in cols), None)
                    if not text_col:
                        continue
                    # only what the user said counts as evidence about them, never ELI's tallies or news
                    _mine = " AND COALESCE(origin, 'user_said') = 'user_said'" if "origin" in cols else ""
                    query = (
                        f'SELECT "{text_col}" FROM "{table}" '
                        f'WHERE (lower("{text_col}") LIKE "%name%" '
                        f'OR lower("{text_col}") LIKE "%identity%" '
                        f'OR lower("{text_col}") LIKE "%preference%"){_mine} '
                        f'ORDER BY rowid DESC LIMIT ?'
                    )
                    for (value,) in conn.execute(query, (limit,)).fetchall():
                        cleaned = " ".join(str(value or "").split())
                        if cleaned:
                            hits.append(cleaned[:160])
                            if len(hits) >= limit:
                                conn.close()
                                return hits
                conn.close()
            except Exception:
                continue
        return hits

    vector_status: Dict[str, Any] = {}
    try:
        from eli.memory import vector_store as _vs
        idx_path, meta_path = _vs._get_index_paths(create=False)
        vector_status = {
            "faiss_available": bool(getattr(_vs, "FAISS_AVAILABLE", False)),
            "index_path": str(idx_path),
            "meta_path": str(meta_path),
            "index_exists": Path(idx_path).exists(),
            "meta_exists": Path(meta_path).exists(),
            "embedding_model": str((get_paths().project_root / "models" / "embeddings" / "nomic-embed-text-v1.5.Q4_K_M.gguf").resolve()),
        }
        if vector_status["faiss_available"] and vector_status["index_exists"]:
            import faiss
            vector_status["ntotal"] = int(faiss.read_index(str(idx_path)).ntotal)
    except Exception as e:
        vector_status = {"ok": False, "error": repr(e)}

    schema = {
        "active_db": _table_counts(status.get("db_path")),
        "user_db": _table_counts(paths.user_db),
        "agent_db": _table_counts(paths.agent_db),
        "memory_db": _table_counts(_eli_path_get(paths, "memory_db")),
    }
    identity_hits = _identity_hits_from_sqlite(_eli_path_get(paths, "memory_db"), paths.user_db)

    # List every sqlite3 file actually in the db directory, not just the logical roles. The role
    # resolver aliases active_db/user_db/memory_db to user.sqlite3 and never sees coding_memory or
    # system_index, so the report said 2 databases when 4 exist. Count from the filesystem.
    db_files: List[Dict[str, Any]] = []
    try:
        _db_dir = Path(paths.user_db).parent
        for _f in sorted(_db_dir.glob("*.sqlite3")):
            try:
                _tc = _table_counts(_f)
                db_files.append({
                    "name": _f.name,
                    "path": str(_f),
                    "size_mb": round(_f.stat().st_size / (1024 * 1024), 3),
                    "tables": len(_tc),
                    "table_names": sorted(_tc.keys()),
                })
            except Exception:
                continue
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    # Live mechanism probes: re-derive every claim about ELI's internals each call instead of
    # hardcoding prose, since ELI can add or remove its own modules and tables. Probe the filesystem
    # for each mechanism module and the live schema for FTS5 mirrors and knowledge-graph counts.
    def _all_live_tables() -> set:
        names: set = set()
        for _f in db_files:
            for _n in (_f.get("table_names") or []):
                names.add(str(_n))
        for _tbls in (schema or {}).values():
            for _n in (_tbls or {}):
                names.add(str(_n))
        return names

    _live_tables = _all_live_tables()
    # FTS5 virtual tables are named "<x>_fts"; sqlite also creates shadow tables
    # (_fts_config/_data/_docsize/_idx) — exclude those, keep the real mirrors.
    fts_tables = sorted(
        t for t in _live_tables
        if t.endswith("_fts") and not re.search(r"_fts_(config|data|docsize|idx)$", t)
    )

    def _live_count(table: str) -> int:
        best = -1
        for src in (schema or {}).values():
            if src and table in src:
                best = max(best, int(src.get(table) or 0))
        return best

    kg = {
        "entities": _live_count("kg_entities"),
        "relations": _live_count("kg_relations"),
        "present": ("kg_entities" in _live_tables or "kg_relations" in _live_tables),
    }

    _proj_root = get_paths().project_root
    def _probe_module(relpath: str) -> Dict[str, Any]:
        try:
            return {"path": relpath, "present": (Path(_proj_root) / relpath).exists()}
        except Exception:
            return {"path": relpath, "present": False}

    mechanisms = {
        "hyde": _probe_module("eli/cognition/hyde.py"),
        "orchestrator": _probe_module("eli/cognition/orchestrator.py"),
        "agent_bus": _probe_module("eli/cognition/agent_bus.py"),
        "vector_store_faiss": _probe_module("eli/memory/vector_store.py"),
        "knowledge_graph": _probe_module("eli/memory/knowledge_graph.py"),
        "plan_graph_dag": _probe_module("eli/coding/plan_graph.py"),
    }

    return {
        'ok': True,
        'paths': {
            'active_db': str(status.get("db_path", getattr(mem, "db_path", "unknown"))),
            'user_db': str(paths.user_db),
            'agent_db': str(paths.agent_db),
            'memory_db': str(_eli_path_get(paths, "memory_db")),
        },
        'db_files': db_files,
        'status': status,
        'schema': schema,
        'vector_status': vector_status,
        'fts_tables': fts_tables,
        'kg': kg,
        'mechanisms': mechanisms,
        'name_guess': name_guess,
        'health': _memory_health(mem),
        'identity_hits': [
            str((h.get('text') or h.get('content') or '') if isinstance(h, dict) else h).strip()[:160]
            for h in identity_hits[:5]
        ],
        'recent_turn_count': min(6, int(status.get("conversation_turns", 0) or len(recent or []))),
    }

def _format_memory_runtime(report: Dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"Memory runtime inspection failed: {report.get('error', 'unknown error')}"

    paths = report.get("paths", {}) or {}
    status = report.get("status", {}) or {}
    name_guess = str(report.get("name_guess") or "").strip()
    identity_hits = report.get("identity_hits") or []
    recent_turn_count = int(report.get("recent_turn_count") or 0)

    def _clean_hit(value: Any) -> str:
        return " ".join(str(value or "").split())

    lines: List[str] = []

    if name_guess:
        lines.append(
            f"Yes — I do have grounded memory evidence for you. "
            f"The strongest current name signal in runtime memory is: {name_guess}."
        )
        lines.append("")
    else:
        lines.append(
            "I do have grounded runtime memory state, but I do not currently have a strong "
            "enough name signal to identify you by name with confidence."
        )
        lines.append("")

    lines.append("Memory runtime:")
    lines.append(f"- active_db: {paths.get('active_db', 'unknown')}")
    lines.append(f"- user_db: {paths.get('user_db', 'unknown')}")
    lines.append(f"- agent_db: {paths.get('agent_db', 'unknown')}")
    lines.append(f"- memory_db: {paths.get('memory_db', 'unknown')}")
    # Physical DB files on disk (the logical roles above can alias the same
    # file; this is the true count and is what "how many databases" should use).
    db_files = report.get("db_files") or []
    if db_files:
        lines.append(
            f"- physical_db_files: {len(db_files)} "
            f"(active/user/memory roles may alias the same file)"
        )
        for _f in db_files:
            lines.append(
                f"  - {_f.get('name')} "
                f"({_f.get('size_mb')} MB, {_f.get('tables')} tables)"
            )
    lines.append(f"- memory_entries: {int(status.get('memory_entries', 0) or 0)}")
    lines.append(f"- conversation_turns: {int(status.get('conversation_turns', 0) or 0)}")
    lines.append(f"- distinct_sessions: {int(status.get('distinct_sessions', 0) or 0)}")
    lines.append(f"- db_path: {status.get('db_path', 'unknown')}")
    lines.append(f"- recent_turn_count: {recent_turn_count}")

    schema = report.get("schema") or {}
    if schema:
        lines.append("")
        lines.append("SQLite tables observed live:")
        for db_name, tables in schema.items():
            if not tables:
                lines.append(f"- {db_name}: no tables found or DB missing")
                continue
            rendered = ", ".join(f"{name}({count})" for name, count in sorted(tables.items()))
            lines.append(f"- {db_name}: {rendered}")

    vector_status = report.get("vector_status") or {}
    _faiss = 'available' if vector_status.get('faiss_available') else 'not available'
    _nvec = vector_status.get('ntotal', 'unknown')
    db_files = report.get("db_files") or []
    fts_tables = report.get("fts_tables") or []
    kg = report.get("kg") or {}
    mechanisms = report.get("mechanisms") or {}

    def _present(key: str) -> str:
        m = mechanisms.get(key) or {}
        return f"detected at {m.get('path')}" if m.get("present") else f"NOT FOUND ({m.get('path')})"

    # Stores — derived live from the physical *.sqlite3 files on disk, with the
    # actual tables each one currently holds. No hardcoded per-DB description:
    # ELI can add/remove DBs and tables, so this is re-read every call.
    lines.append("")
    lines.append(f"Stores — {len(db_files)} physical SQLite file(s) on disk (live):")
    for _f in db_files:
        _names = _f.get("table_names") or []
        _shown = ", ".join(_names[:14]) + (" …" if len(_names) > 14 else "")
        lines.append(f"- {_f.get('name')} ({_f.get('size_mb')} MB, {_f.get('tables')} tables)")
        if _shown:
            lines.append(f"    tables: {_shown}")

    lines.append("")
    lines.append("STORAGE path (write — input → durable + indexed), live-annotated:")
    lines.append("1. A turn/fact enters via Memory.store_memory()/log_* (a persistence gate decides what to keep).")
    lines.append(f"2. Row written to SQLite and mirrored into FTS5 for keyword search "
                 f"— live FTS5 mirrors: {', '.join(fts_tables) if fts_tables else 'none detected'}.")
    lines.append(f"3. Text embedded with the local nomic embedder; vector added to FAISS "
                 f"— live: FAISS {_faiss}, {_nvec} vectors; embedder module {_present('vector_store_faiss')}.")
    lines.append(f"4. Entities/relations extracted into the knowledge graph "
                 f"— live: kg_entities={kg.get('entities', 'n/a')}, kg_relations={kg.get('relations', 'n/a')}; "
                 f"module {_present('knowledge_graph')}.")
    lines.append(f"   Live totals now: memories={int(status.get('memory_entries', 0) or 0)}, FAISS vectors={_nvec}.")

    lines.append("")
    lines.append("RECALL path (read — query → ranked context → answer), live-annotated:")
    lines.append(f"1. HyDE optionally expands a vague query before search — module {_present('hyde')}.")
    lines.append("2. Parallel retrieval: keyword (SQLite LIKE) + FTS5 full-text + FAISS vectors + knowledge-graph lookup.")
    lines.append(f"3. RAG hybrid merge + rerank by relevance/recency — orchestrator module {_present('orchestrator')}; "
                 f"parallel agent dispatch {_present('agent_bus')}.")
    lines.append("4. Context assembly → persona handoff → LLM synthesis (recency/session-biased; recall-narration filtered).")
    lines.append(f"- DAG: agents/retrieval run on a dependency DAG; the coding agent decomposes tasks via a subtask DAG "
                 f"— module {_present('plan_graph_dag')}.")

    _h = report.get("health") or {}
    if _h:
        lines.append("")
        lines.append("Memory health (measured): " + ", ".join(f"{k}={v}" for k, v in _h.items() if k != "ok")
                     + f" — {'consistent' if _h.get('ok') else 'needs attention'}.")
    lines.append("")
    lines.append("Index/runtime detail (live):")
    lines.append(f"- FTS5 mirror tables detected: {', '.join(fts_tables) if fts_tables else 'none'}.")
    lines.append(f"- FAISS: {_faiss}; index={vector_status.get('index_path', 'unknown')}; vectors={_nvec}.")
    lines.append(f"- Embedder: {vector_status.get('embedding_model', 'unknown')}")
    lines.append(f"- Knowledge graph: {kg.get('entities', 'n/a')} entities, {kg.get('relations', 'n/a')} relations "
                 f"({'present' if kg.get('present') else 'not present'}).")
    lines.append("- Short-term memory: in-process working memory + recent conversation_turns (no separate short-term DB).")
    lines.append("- Mechanism modules re-probed on the filesystem this call (no hardcoded claims): "
                 + "; ".join(
                       f"{k}={'present' if (mechanisms.get(k) or {}).get('present') else 'missing'}"
                       for k in ("hyde", "orchestrator", "agent_bus", "vector_store_faiss", "knowledge_graph", "plan_graph_dag")
                   ) + ".")

    if identity_hits:
        lines.append("- identity_evidence:")
        # Identity hits often repeat verbatim ("Capability inventory updated: ...", written by the
        # daemon every tick). De-dupe so the user sees distinct evidence.
        _seen: set = set()
        _printed = 0
        for hit in identity_hits:
            if _printed >= 3:
                break
            cleaned = _clean_hit(hit)
            if not cleaned:
                continue
            key = cleaned[:220].strip().lower()
            if key in _seen:
                continue
            _seen.add(key)
            lines.append(f"  - {cleaned[:220]}")
            _printed += 1

    lines.append("- Main functions/classes: eli.memory.memory.Memory, get_memory(), recall_memory(), store_memory(), get_recent_conversation(), eli.memory.vector_store.VectorStore.search().")

    return "\n".join(lines)

def _explain_all_reasoning_modes_report() -> Dict[str, Any]:
    """Read all mode data directly from reasoning_modes.py and return as structured evidence."""
    try:
        from eli.cognition.reasoning_modes import (
            _MODE_DESCRIPTIONS,
            _MODE_TASK_PIPELINE,
            _MODE_INSTRUCTION_STACK,
            _MODE_MIN_TOKENS,
            _MODE_MAX_TOKENS_CAP,
            _MODE_TEMPERATURE_CEIL,
            _DISPLAY,
            PRIVATE_REASONING_MODES,
        )
        modes = list(_DISPLAY.keys())
        mode_data = {}
        for key in modes:
            mode_data[key] = {
                "display": _DISPLAY.get(key, key),
                "private": key in PRIVATE_REASONING_MODES,
                "description": _MODE_DESCRIPTIONS.get(key, ""),
                "pipeline_stages": _MODE_TASK_PIPELINE.get(key, []),
                "instructions": _MODE_INSTRUCTION_STACK.get(key, []),
                "min_tokens": _MODE_MIN_TOKENS.get(key, 0),
                "max_tokens_cap": _MODE_MAX_TOKENS_CAP.get(key, 0),
                "temperature_ceil": _MODE_TEMPERATURE_CEIL.get(key, 0.7),
            }
        import importlib, inspect
        try:
            mod = importlib.import_module("eli.cognition.reasoning_modes")
            src_path = inspect.getfile(mod)
        except Exception:
            src_path = "eli/cognition/reasoning_modes.py"
        return {"ok": True, "source": src_path, "modes": modes, "mode_data": mode_data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _format_all_reasoning_modes(report: Dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"Reasoning modes report failed: {report.get('error')}"
    lines = [f"Source: {report.get('source', 'eli/cognition/reasoning_modes.py')}"]
    lines.append(f"Total modes: {len(report.get('modes', []))}")
    lines.append("")
    for key in report.get("modes", []):
        d = report["mode_data"][key]
        # Show the public mode name (quick/normal/advanced/research/expert), never the internal key
        # (chain_of_thought/self_consistency/...), which stays stable underneath but must not
        # surface.
        _public = str(d['display']).lower()
        lines.append(f"## {d['display']} (key={_public})")
        lines.append(f"  private: {d['private']}")
        lines.append(f"  temperature_ceil: {d['temperature_ceil']}")
        lines.append(f"  token_floor: {d['min_tokens']}  token_cap: {d['max_tokens_cap']}")
        lines.append(f"  description: {d['description']}")
        lines.append(f"  pipeline_stages:")
        for s in d["pipeline_stages"]:
            lines.append(f"    - {s}")
        lines.append(f"  instructions:")
        for instr in d["instructions"]:
            lines.append(f"    - {instr}")
        lines.append("")
    return "\n".join(lines)


def _explain_cognition_runtime_report(args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    files = _canonical_runtime_file_map()
    cog_path = files['cognitive_engine']
    mem_path = files['memory']
    router_path = files['router']
    executor_path = files['executor']
    if not cog_path.exists():
        return {'ok': False, 'error': 'cognitive_engine.py not found'}
    text = cog_path.read_text(encoding='utf-8', errors='replace')
    checks = {
        'process': 'def process(',
        'memory_retrieval': 'def _retrieve_relevant_memories(',
        'routing': 'route_intent(',
        'executor_evidence': '_gather_executor_evidence',
        'gguf': 'gguf_inference',
        'streaming': 'def _stream_chat(',
        'reasoning_loop': '_run_chat_reasoning_loop',
        'confidence': '_score_response_confidence',
    }
    out = {}
    for key, needle in checks.items():
        out[key] = [i for i, line in enumerate(text.splitlines(), 1) if needle in line]
    return {'ok': True, 'path': str(cog_path), 'memory_path': str(mem_path), 'router_path': str(router_path), 'executor_path': str(executor_path), 'checks': out}

def _format_cognition_runtime(report: Dict[str, Any], args: Optional[Dict[str, Any]] = None) -> str:
    if not report.get('ok'):
        return str(report.get('error') or 'Cognition runtime report failed')
    lines = []
    _args = args if isinstance(args, dict) else {}
    _focus = str(_args.get("diagnostic_focus") or "").strip().lower()
    if _focus in ("inference_ram", "inference_runtime"):
        try:
            from eli.runtime.inference_footprint import format_inference_footprint_report
            from eli.runtime.deterministic_grounding_gate import _inference_runtime_lines
            parts = []
            if _focus == "inference_runtime":
                try:
                    parts.append(_inference_runtime_lines())
                except Exception:
                    log.debug("[EXECUTOR] inference runtime block unavailable", exc_info=True)
            parts.append(
                format_inference_footprint_report(
                    include_latency_note=_focus != "inference_runtime",
                    question=str(_args.get("question") or ""),
                )
            )
            return "\n\n".join(p for p in parts if p)
        except Exception:
            log.debug("[EXECUTOR] inference footprint report unavailable", exc_info=True)
    # Live inference parameters go first. Asked for the current context window, this report gave
    # module paths, grep line numbers and table counts, then the synthesis said the evidence didn't
    # specify it, although n_ctx was in runtime_snapshot.json and the report had no field for it.
    try:
        from eli.runtime.deterministic_grounding_gate import _inference_runtime_lines
        lines.append(_inference_runtime_lines())
        lines.append("")
    except Exception:
        log.debug("[EXECUTOR] inference runtime block unavailable", exc_info=True)
    if _focus in ("inference_runtime", "gguf", "gguf_runtime"):
        try:
            from eli.runtime.gguf_runtime_report import format_gguf_diagnostics_report
            lines.append(format_gguf_diagnostics_report(
                question=str(_args.get("question") or "")))
            lines.append("")
        except Exception:
            log.debug("[EXECUTOR] gguf diagnostics block unavailable", exc_info=True)
    lines += [
        f"Cognition runtime: {report.get('path')}",
        f"Memory module: {report.get('memory_path')}",
        f"Router module: {report.get('router_path')}",
        f"Executor module: {report.get('executor_path')}",
    ]
    for key, hits in (report.get('checks') or {}).items():
        lines.append(f"- {key}: lines {hits if hits else 'missing'}")
    from eli.kernel.pipeline import get_pipeline_description
    lines.extend(get_pipeline_description())

    try:
        memory_text = _format_memory_runtime(_explain_memory_runtime_report())
        # Prefixes track the LIVE _format_memory_runtime output (#5/Option 4).
        # Keep in sync with that formatter — stale prefixes silently drop lines.
        keep_prefixes = (
            "Memory runtime:",
            "- active_db:",
            "- user_db:",
            "- agent_db:",
            "- memory_db:",
            "- physical_db_files:",
            "- memory_entries:",
            "- conversation_turns:",
            "- distinct_sessions:",
            "Stores —",
            "Index/runtime detail (live):",
            "- FTS5 mirror tables detected:",
            "- FAISS:",
            "- Knowledge graph:",
            "- Embedder:",
            "- Short-term memory:",
            "- Mechanism modules re-probed",
            "- Main functions/classes:",
        )
        # The prefix whitelist kept the "physical_db_files: 4" count line but dropped the per-file
        # lines under "Stores —", so the report asserted 4 files and listed only 2. Keep the whole
        # block so the count is backed by the actual files.
        memory_lines = []
        _in_stores = False
        for line in memory_text.splitlines():
            if line.startswith("Stores —"):
                _in_stores = True
                memory_lines.append(line)
                continue
            if _in_stores:
                if line.startswith("- ") or line.strip().startswith("tables:"):
                    memory_lines.append(line)
                    continue
                _in_stores = False  # a non-store line ends the block
            if any(line.startswith(prefix) for prefix in keep_prefixes):
                memory_lines.append(line)
        if memory_lines:
            lines.append("")
            lines.append("Memory and retrieval runtime:")
            lines.extend(memory_lines)
    except Exception as exc:
        lines.append("")
        lines.append(f"Memory/retrieval runtime summary unavailable: {exc}")

    return '\n'.join(lines)


def _runtime_status_report() -> Dict[str, Any]:
    report: Dict[str, Any] = {'ok': True}
    try:
        from eli.core.paths import get_paths
        p = get_paths()
        report['paths'] = {
            'project_root': str(Path(p.project_root).expanduser().resolve()),
            'artifacts_dir': str(Path(p.artifacts_dir).expanduser().resolve()),
            'models_dir': str(Path(getattr(p, 'models_dir', '')).expanduser().resolve()) if getattr(p, 'models_dir', '') else '',
            'user_db': str(Path(p.user_db).expanduser().resolve()),
            'agent_db': str(Path(p.agent_db).expanduser().resolve()),
            'memory_db': str(Path(_eli_path_get(p, "memory_db")).expanduser().resolve()),
        }
    except Exception as e:
        report['paths_error'] = str(e)

    try:
        from eli.core import runtime_settings as _rs
        settings = _rs.load_settings() or {}
    except Exception as e:
        settings = {}
        report['settings_error'] = str(e)
    report['settings'] = settings

    # Effective runtime, most to least authoritative: (1) runtime_snapshot.json on disk, written
    # after the load that succeeded; (2) gguf_inference live attributes if loaded in-process;
    # (3) empty dict, so effective values read "unknown" instead of being filled from settings.
    runtime: Dict[str, Any] = {}
    try:
        snap_path = Path(report.get('paths', {}).get('artifacts_dir', 'artifacts')) / "runtime_snapshot.json"
        report['runtime_snapshot_path'] = str(snap_path)
        if snap_path.exists():
            import json
            file_runtime = json.loads(snap_path.read_text(encoding='utf-8'))
            if isinstance(file_runtime, dict):
                runtime.update(file_runtime)
    except Exception as e:
        report['runtime_snapshot_error'] = str(e)

    # The four load params (n_ctx, n_gpu_layers, n_threads, n_batch) are trusted only from the disk
    # snapshot, written after a successful load with the clamped truth. In-process gguf_inference
    # may echo settings/defaults before anything loads and hide boot-time fallbacks. It can still
    # supply provider, model_path, model_name and the loaded flag.
    try:
        from eli.cognition import gguf_inference as _gg
        if hasattr(_gg, 'get_runtime_snapshot'):
            live = dict(_gg.get_runtime_snapshot() or {})
            for k, v in live.items():
                if k in ('n_ctx', 'n_gpu_layers', 'n_threads', 'n_batch'):
                    # Only fill in if disk snapshot didn't already have it.
                    if runtime.get(k) in (None, '', 0) and v not in (None, '', 0):
                        runtime[k] = v
                    continue
                if v not in (None, '',):
                    runtime[k] = v
    except Exception as e:
        report['gguf_runtime_error'] = str(e)

    report['runtime'] = runtime

    try:
        model_path = (
            runtime.get('model_path')
            or settings.get('model_path')
            or settings.get('custom_model_path')
            or settings.get('bundled_model_path')
            or ''
        )
        report['model_path'] = str(model_path)
    except Exception as e:
        report['model_path_error'] = str(e)

    return report


def _format_runtime_status(report: Dict[str, Any]) -> str:
    settings = report.get('settings') or {}
    runtime = report.get('runtime') or {}

    def _pick(runtime_key, *setting_keys, fallback='unknown'):
        value = runtime.get(runtime_key, None)
        if value not in (None, '', 0):
            return value
        for key in setting_keys:
            value = settings.get(key, None)
            if value not in (None, '', 0):
                return value
        return fallback

    provider = runtime.get('provider') or settings.get('provider') or 'unknown'
    model_path = str(
        runtime.get('model_path')
        or report.get('model_path')
        or settings.get('model_path')
        or settings.get('custom_model_path')
        or settings.get('bundled_model_path')
        or 'unknown'
    ).strip()
    model_name = runtime.get('model_name') or (Path(model_path).name if model_path not in ('', 'unknown') else 'unknown')
    loaded = bool(runtime.get('loaded', False))

    lines = [
        "Runtime status evidence:",
        f"- provider: {provider}",
        f"- model_name: {model_name}",
        f"- model_path: {model_path}",
        f"- context_size: {_pick('n_ctx', 'n_ctx', 'context_size')}",
        f"- gpu_layers: {_pick('n_gpu_layers', 'n_gpu_layers', 'gpu_layers')}",
        f"- batch_size: {_pick('n_batch', 'n_batch', 'batch_size', 'batch')}",
        f"- cpu_threads: {_pick('n_threads', 'n_threads', 'cpu_threads', 'threads')}",
        f"- gguf_loaded_in_this_process: {loaded}",
    ]
    try:
        from eli.runtime.truth_report import system_memory_line
        _sys = system_memory_line()
        if _sys:
            lines.append(f"- system: {_sys}")
    except Exception:
        log.debug("system memory line unavailable", exc_info=True)

    # Requested vs loaded, from the shared rule (truth_report.runtime_load_facts).
    try:
        from eli.runtime.truth_report import runtime_load_facts
        _facts = runtime_load_facts(runtime, settings)
        if _facts.get('differences'):
            for _d in _facts['differences']:
                lines.append(f"- loaded_below_request: {_d}")
        elif (runtime.get('requested') or runtime.get('effective')) and not _facts.get('above_request'):
            lines.append("- requested_vs_loaded: match")
        for _d in _facts.get('above_request') or ():
            lines.append(f"- loaded_above_request: {_d}")
        if _facts.get('tuner_recommendation'):
            lines.append(
                "- tuner_suggestion (stored fallback, NOT what loaded): "
                + ", ".join(f"{k}={v}" for k, v in _facts['tuner_recommendation'].items()))
    except Exception:
        log.debug("runtime load facts unavailable for RUNTIME_STATUS", exc_info=True)

    if settings.get('max_tokens') not in (None, ''):
        lines.append(f"- max_tokens: {settings.get('max_tokens')}")
        lines.append("- max_tokens_note: a ceiling derived from the loaded ctx, not a "
                     "per-call limit — each generation fits its own budget from the "
                     "prompt size and reasoning mode")
    if settings.get('temperature') not in (None, ''):
        lines.append(f"- temperature: {settings.get('temperature')}")
    if settings.get('use_mmap') not in (None, ''):
        lines.append(f"- use_mmap: {settings.get('use_mmap')}")
    if settings.get('use_mlock') not in (None, ''):
        lines.append(f"- use_mlock: {settings.get('use_mlock')}")
    if str(provider).lower() == 'ollama':
        if settings.get('ollama_host') not in (None, ''):
            lines.append(f"- ollama_host: {settings.get('ollama_host')}")
        if settings.get('ollama_model') not in (None, ''):
            lines.append(f"- ollama_model: {settings.get('ollama_model')}")

    return '\n'.join(lines)

def _get_db_schema_evidence() -> str:
    """Introspect the canonical SQLite databases and return real table names + row counts."""
    import sqlite3
    paths = get_paths()
    # memory_db is consolidated into user.sqlite3, so it is not listed separately —
    # doing so would show a phantom third database that is really the same file.
    db_files = {
        "user.sqlite3": Path(paths.user_db),
        "agent.sqlite3": Path(paths.agent_db),
    }
    lines = ["DATABASE SCHEMA (ground truth from live introspection):"]
    for name, path in db_files.items():
        if not path.exists():
            lines.append(f"  {name}: NOT FOUND")
            continue
        try:
            conn = sqlite3.connect(str(path))
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts_%' ORDER BY name"
            ).fetchall()]
            tbl_info = []
            for t in tables:
                _row = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()
                count = _row[0] if _row else 0
                if count > 0:
                    tbl_info.append(f"{t}({count})")
            conn.close()
            lines.append(f"  {name}: {', '.join(tbl_info) if tbl_info else 'all tables empty'}")
        except Exception as e:
            lines.append(f"  {name}: error reading — {e}")
    return "\n".join(lines)

def _memory_status_report() -> Dict[str, Any]:
    rep = _explain_memory_runtime_report()
    rep['ok'] = bool(rep.get('ok', True))
    return rep


def _format_memory_status(report: Dict[str, Any]) -> str:
    return _format_memory_runtime(report)


def _runtime_boot_status() -> Dict[str, Any]:
    """Live boot/wiring probe used by COGNITION_STATUS."""
    import importlib

    checks: Dict[str, bool] = {}
    errors: Dict[str, str] = {}

    module_names = {
        "memory_module": "eli.memory",
        "memory_core": "eli.memory.memory",
        "agent_bus": "eli.cognition.agent_bus",
        "orchestrator": "eli.cognition.orchestrator",
        "router": "eli.execution.router_enhanced",
        "executor": "eli.execution.executor_enhanced",
        "control_contracts": "eli.runtime.control_contracts",
        "response_surface": "eli.runtime.user_visible_response_surface",
    }

    loaded: Dict[str, Any] = {}
    for label, module_name in module_names.items():
        try:
            loaded[label] = importlib.import_module(module_name)
            checks[label] = True
        except Exception as exc:
            checks[label] = False
            errors[label] = f"{type(exc).__name__}: {exc}"

    try:
        router = loaded.get("router")
        checks["router.route"] = callable(getattr(router, "route", None))
    except Exception as exc:
        checks["router.route"] = False
        errors["router.route"] = f"{type(exc).__name__}: {exc}"

    try:
        executor = loaded.get("executor")
        checks["executor.execute"] = callable(getattr(executor, "execute", None))
    except Exception as exc:
        checks["executor.execute"] = False
        errors["executor.execute"] = f"{type(exc).__name__}: {exc}"

    try:
        memory = loaded.get("memory_module")
        checks["memory.get_memory"] = callable(getattr(memory, "get_memory", None))
    except Exception as exc:
        checks["memory.get_memory"] = False
        errors["memory.get_memory"] = f"{type(exc).__name__}: {exc}"

    try:
        paths = get_paths()
        path_checks = {
            "project_root": Path(paths.project_root),
            "user_db": Path(paths.user_db),
            "agent_db": Path(paths.agent_db),
            # where they really are: the settings file runtime_settings reads, the manifest this install reads
            "settings": __import__("eli.core.runtime_settings", fromlist=["_settings_file"])._settings_file(),
            "capability_manifest": __import__("eli.tools.registry.capability_updater",
                                              fromlist=["current_manifest_path"]).current_manifest_path(),
        }
        for label, path in path_checks.items():
            checks[f"path.{label}"] = path.exists()
    except Exception as exc:
        checks["paths"] = False
        errors["paths"] = f"{type(exc).__name__}: {exc}"

    return {
        "ok": all(checks.values()) if checks else False,
        "checks": checks,
        "errors": errors,
    }


def _cognition_status_report() -> Dict[str, Any]:
    rep = _explain_cognition_runtime_report()
    try:
        boot = _runtime_boot_status()
    except Exception as e:
        boot = {'ok': False, 'error': str(e)}
    rep['boot'] = boot
    return rep


def _format_cognition_status(report: Dict[str, Any]) -> str:
    lines = [_format_cognition_runtime(report)]
    boot = report.get('boot') or {}
    if boot:
        lines.append('')
        lines.append('Startup hooks:')
        for key, value in (boot.get('checks') or {}).items():
            state = 'present' if value else 'missing'
            lines.append(f'- {key}: {state}')
    return '\n'.join(lines)

# ----------------------------
# Config / state
# ----------------------------


# Work out the chat model at runtime; no model name is hardcoded. Ollama is only an attachable
# fallback (the real path is GGUF via llama-cpp-python). Order: ELI_CHAT_MODEL env, OLLAMA_MODEL env
# (legacy), settings["ollama_model"], then "" so the caller has to be explicit.
def _resolve_default_chat_model() -> str:
    envv = os.environ.get("ELI_CHAT_MODEL") or os.environ.get("OLLAMA_MODEL")
    if envv:
        return envv.strip()
    try:
        from eli.core import runtime_settings as _rs
        s = _rs.load_settings() or {}
        v = str(s.get("ollama_model") or "").strip()
        if v:
            return v
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return ""

DEFAULT_CHAT_MODEL = _resolve_default_chat_model()


def _maybe_background_codegen(action, args):
    """If a codegen task (CODE_SOLVE / GENERATE_SCRIPT) is estimated heavy, or the
    user asked to background it, run it on a background thread and return a job-id
    message immediately. Returns None to run inline. Re-dispatch carries
    `_no_background` so the worker doesn't background again."""
    try:
        args = args or {}
        if args.get("_no_background"):
            return None
        if os.environ.get("ELI_CODEGEN_BACKGROUND", "1").strip().lower() in ("0", "false", "no", "off"):
            return None
        desc = (args.get("description") or args.get("text") or args.get("prompt")
                or args.get("query") or "").strip()
        if not desc:
            return None
        from eli.coding.cost import should_background
        decision = should_background(desc, language=(args.get("language") or "python"))
        if not decision.get("background"):
            return None
        from eli.runtime.background_tasks import get_background_tasks
        bt = get_background_tasks()
        _bg_args = dict(args)
        _bg_args["_no_background"] = True
        jid = bt.submit(f"{action}: {desc[:60]}", lambda: execute(action, _bg_args))
        msg = (f"That's a heavier task ({decision.get('reason')}). I've started it in the "
               f"background as job #{jid} — say \"check job {jid}\" for the result, or "
               f"\"background jobs\" to list them.")
        return {"ok": True, "action": action, "background": True, "job_id": jid,
                "content": msg, "response": msg}
    except Exception as _bg_e:
        log.debug(f"[CODEGEN_BG] background decision failed: {_bg_e}")
        return None


def _record_tool_execution(action: str, args: Any, result: Any) -> None:
    """Log this action in the evidence ledger: what ran, whether it worked, and the
    argument names only (values can be private)."""
    if (os.environ.get("ELI_LEDGER_TOOL_EVENTS", "1") or "1").strip().lower() in ("0", "false", "no", "off"):
        return
    if not action or action.upper() == "CHAT":
        return
    from eli.runtime.evidence_ledger import execution_outcome, predict_success, record_event
    outcome = execution_outcome(result)
    predicted = predict_success(action)
    text = ""
    cause = ""
    verified = None
    if isinstance(result, dict):
        text = str(result.get("response") or result.get("content") or result.get("message") or "")
        verified = result.get("verified")
        if outcome in ("failed", "no_effect"):
            try:
                from eli.runtime.action_outcomes import cause_of
                cause = cause_of(result, action, args if isinstance(args, dict) else None)
            except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
                log.debug("cause not classified", exc_info=True)
    record_event(
        "tool_execution",
        source="executor",
        action=action,
        subject=",".join(sorted(str(k) for k in (args or {}))) if isinstance(args, dict) else "",
        content=text[:200],
        payload={"ok": outcome == "ok", **({"predicted": predicted} if predicted is not None else {}),
                 **({"cause": cause} if cause else {}), **({"verified": bool(verified)} if verified is not None else {})},
        outcome=outcome,
        severity="warning" if outcome == "failed" else "info",
    )
    # a prediction that missed by more than an even chance is a surprise: the memories about the
    # target matter more for it (eli/memory/salience.py)
    if predicted is not None and outcome in ("ok", "failed") and abs(float(predicted) - (1.0 if outcome == "ok" else 0.0)) > 0.5:
        try:
            from eli.memory.salience import note_surprise
            from eli.runtime.action_outcomes import subject_of
            _about = subject_of(action, args if isinstance(args, dict) else {})
            note_surprise("action", abs(float(predicted) - (1.0 if outcome == "ok" else 0.0)),
                          subjects=[_about] if _about else [], why=f"{action.replace('_', ' ').lower()}"
                          f"{(' for ' + _about) if _about else ''} was expected to {'fail' if outcome == 'ok' else 'work'} "
                          f"({float(predicted):.0%}) and {'worked' if outcome == 'ok' else 'failed'}")
        except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
            log.debug("action surprise not noted", exc_info=True)
    if outcome in ("ok", "failed"):
        try:
            from eli.runtime.lessons import observe_action
            observe_action(action, outcome == "ok")
        except Exception:
            log.debug("lesson observation skipped", exc_info=True)
        try:
            from eli.runtime.capability_state import observe_run
            observe_run(action, outcome, f"{result.get('error') or ''} {text}" if isinstance(result, dict) else text)
        except Exception:
            log.debug("capability state not updated", exc_info=True)


def _maybe_background_code_work(action, args):
    """Route FIX_FILE / EXAMINE_CODE / SELF_IMPROVE propose to the in-process background job
    queue when they would monopolize the shared model lock (CodeAgent, tier-3 review, sweeps).
    Returns None to run inline. The worker carries `_no_background` so it doesn't
    re-queue."""
    try:
        args = args or {}
        if args.get("_no_background"):
            return None
        a = str(action or "").upper()
        if a not in ("FIX_FILE", "EXAMINE_CODE", "SELF_IMPROVE"):
            return None
        if a == "SELF_IMPROVE" and str(args.get("mode") or "").strip().lower() != "propose":
            return None
        _env_key = {"FIX_FILE": "ELI_FIX_FILE_BACKGROUND", "EXAMINE_CODE": "ELI_EXAMINE_CODE_BACKGROUND",
                    "SELF_IMPROVE": "ELI_SELF_IMPROVE_BACKGROUND"}[a]
        if os.environ.get(_env_key, "1").strip().lower() in ("0", "false", "no", "off"):
            return None

        from eli.coding.cost import should_background, explicit_foreground

        if a == "FIX_FILE":
            path = str(args.get("path") or "").strip()
            desc = f"fix file {path or 'unknown'}"
            if extra := str(args.get("error") or args.get("stderr") or "").strip():
                desc += f" ({extra[:120]})"
            if explicit_foreground(desc):
                return None
            bg, reason = True, "verified file repair pipeline"
        elif a == "SELF_IMPROVE":
            # The coding agent over recent failures: tens of sequential model calls with the
            # model lock held and nothing shown until it returns (a chat turn sat on it for hours).
            if explicit_foreground(str(args.get("_raw_user_text") or "")):
                return None
            bg, reason = True, "coding-agent fix proposals for recent failures"
        else:
            request = (args.get("request") or args.get("query") or "").strip()
            from eli.runtime import code_examiner as _ce
            if _ce.is_recall_request(request) or _ce.is_fix_recall_request(request):
                return None
            named = _ce._extract_named_paths(request)
            if explicit_foreground(request):
                return None
            # With no file named, what it would examine (code_examiner.resolve_targets): with nothing
            # to examine it is asked about at once; the file just referred to is decided as a named one
            _sweep = not named
            if _sweep:
                named = _ce.resolve_targets(request)
                if not named:
                    return None
                _sweep = len(named) > 1
            if _sweep:
                bg, reason = True, "codebase sweep (no single file named)"
            elif len(named) > 1:
                bg, reason = True, f"{len(named)} files named"
            else:
                decision = should_background(request)
                try:
                    from eli.core.hardware_profile import runtime_cpu_only as _rco
                    _cpu = _rco()
                except Exception:
                    _cpu = False
                if decision.get("background") or _cpu:
                    bg = True
                    reason = (
                        decision.get("reason")
                        if decision.get("background")
                        else "tier-3 logic review on CPU-only runtime"
                    )
                else:
                    return None

        if not bg:
            return None

        from eli.runtime.background_tasks import get_background_tasks
        bt = get_background_tasks()
        _bg_args = dict(args)
        _bg_args["_no_background"] = True
        _bg_args.setdefault("_asked_at", time.time())      # what it finds is as old as the asking
        _label = (
            str(_bg_args.get("path") or "")[:60]
            if a == "FIX_FILE"
            else "fix proposals" if a == "SELF_IMPROVE"
            else (str(_bg_args.get("request") or _bg_args.get("query") or "")[:60])
        )
        jid = bt.submit(
            f"{a}: {_label}",
            lambda: execute(a, _bg_args),
            kind="code",
            meta={"action": a, "request": _label},
        )
        msg = (
            f"That's a heavier code task ({reason}). I've started it in the "
            f"background as job #{jid} — say \"check job {jid}\" for the result, or "
            f"\"background jobs\" to list them."
        )
        out = {
            "ok": True,
            "action": a,
            "background": True,
            "job_id": jid,
            "content": msg,
            "response": msg,
        }
        if a == "SELF_IMPROVE":
            out["evidence_source"] = "coding_agent"
        return out
    except Exception as _bg_e:
        log.debug(f"[CODE_BG] background decision failed: {_bg_e}")
        return None


def _wants_analysis(instruction) -> bool:
    """Asked to analyse, not summarise: all of each document gets read, not its opening."""
    return bool(re.search(r"\b(?:analy[sz]|evaluat|critiqu|assess|examin)", str(instruction or ""), re.I))


def _stub_marker(code: str) -> str:
    """What makes generated code a stub, or "". It says so in a comment, or it is the prompt coming
    back. The words alone are not enough: a repo analyser that counts TODOs, 100 s of real code, was
    thrown away for saying "TODO" in a string."""
    for echo in ("Generate only the requested source code", "This is a request for"):
        if echo.lower() in code.lower():
            return echo
    for n, line in enumerate(code.splitlines(), 1):
        note = re.search(r"(?:^|\s)(?:#|//|--|/\*)\s*(.*)$", line)
        if note and re.search(r"\b(?:TODO|add code here|placeholder)\b", note.group(1), re.I):
            return f"“{line.strip()[:60]}” on line {n}"
    return ""


def _asks_which(action: str, what: str) -> dict:
    """A file command with nothing named and nothing in hand. Never the current folder."""
    msg = f"Which {what}? Give me the path."
    return {"ok": False, "action": action, "error": "no path given", "content": msg, "response": msg,
            "awaiting": {"command": "analyse {answer}", "action": action, "needs": "path"}}


def _maybe_background_file_analysis(action, args):
    """Heavy folder/PDF analysis (many PDFs → extract + summarise) is slow and
    would block the UI. Run it on a background thread and return a job-id message
    immediately. Returns None to run inline. The worker carries `_no_background`."""
    try:
        args = args or {}
        if args.get("_no_background"):
            return None
        if os.environ.get("ELI_FILE_ANALYSIS_BACKGROUND", "1").strip().lower() in ("0", "false", "no", "off"):
            return None
        targets = [str(t).strip() for t in (args.get("folders") or []) if str(t).strip()]
        folder = str(args.get("folder") or args.get("path") or "").strip()
        targets = targets or ([folder] if folder else [])
        if not targets:
            return None
        exps = [os.path.expanduser(t) for t in targets]
        if len(exps) == 1 and not os.path.isdir(exps[0]):
            return None  # single file is fast enough inline
        # Counted as the analysis will read them (folder_analysis.files_to_read): a folder's PDFs, or
        # for a folder with none its own files. Only PDFs were counted, so a folder of code or notes
        # was "0 PDFs" and read inline, holding up the turn.
        try:
            from eli.runtime.folder_analysis import files_to_read
            _found, _of_files = files_to_read(exps, recursive=True, ordered=False)
            _n_files = len(_found)
        except Exception:
            log.debug("[FILE_BG] folder count failed", exc_info=True)
            _n_files, _of_files = 0, False
        _kind = "files" if _of_files else "PDFs"
        _threshold = int(os.environ.get("ELI_FILE_ANALYSIS_BG_THRESHOLD", "5") or 5)
        # Background when there are enough files to be slow, OR the user asked.
        _asked = bool(args.get("_force_background"))
        if _n_files < _threshold and not _asked and len(exps) == 1:
            return None
        from eli.runtime.background_tasks import get_background_tasks
        bt = get_background_tasks()
        _bg_args = dict(args)
        _bg_args["_no_background"] = True
        _bg_args["folders"] = targets
        from eli.kernel import request_context as _rc_bg
        _bg_args["_user_id"] = str(_rc_bg.user_id_var.get() or "")   # the job's thread has no request
        _label = (f"{len(targets)} folders" if len(targets) > 1
                  else os.path.basename(targets[0].rstrip("/")) or targets[0])
        _whole = _wants_analysis(args.get("instruction"))
        jid = bt.submit(f"Analyse {_n_files} {_kind}: {_label}",
                        lambda: execute("ANALYZE_PDF_FOLDER", _bg_args),
                        meta={"action": "ANALYZE_PDF_FOLDER", "targets": targets, "tell": True})
        _there = f"Those {len(targets)} folders have" if len(targets) > 1 else "That folder has"
        _each = ("each one is read (a document goes into the document index)" if _of_files
                 else "each one goes into the document index")
        msg = (f"{_there} {_n_files} {_kind}, so I've started on them in the background as job "
               f"#{jid}: {_each}, then one "
               f"{'analysis of the whole text' if _whole else 'summary covering all of them'} is "
               f"saved as a PDF. I'll tell you when it ends; ask how it's going any time, or say “check job {jid}”.")
        return {"ok": True, "action": "ANALYZE_PDF_FOLDER", "background": True, "job_id": jid,
                "content": msg, "response": msg}
    except Exception as _bg_e:
        log.debug(f"[FILE_BG] background decision failed: {_bg_e}")
        return None


# The actions the executor answers itself, here or in its pre-dispatch (no effector registers them),
# and after them every action an effector registers, read live from the action registry
# (eli/execution/effectors/registry.py): registering an action makes it a supported one, and nothing
# else lists it. This list held them all by hand, so a new action had to be written here as well, and a
# missing one could not be reached by the intent resolver.
from eli.execution.effectors.registry import SupportedActions as _SupportedActions
SUPPORTED_ACTIONS = _SupportedActions([
    'SET_COMMUNICATION_STYLE', 'SCHEDULE_TASK', 'CHAT', 'EXECUTE_GOAL', 'EXPLAIN_LAST_RESPONSE',
    'HELP', 'LIST_CAPABILITIES', 'NAME_SOURCE_AUDIT', 'OPEN_APP', 'OPEN_IDE',
    'PERSONAL_MEMORY_DEEP_EXPLAIN', 'PERSONAL_MEMORY_SUMMARY', 'GET_PROPOSALS',
    'REASONING_MODE_STATUS', 'SELF_REPAIR_PLAYBOOK', 'MULTI_COMMAND', 'SELF_UPDATE', 'SEQUENCE',
    'LIST_VOICES', 'DOWNLOAD_VOICE', 'SET_VOICE', 'CREATE_VOICE', 'DESIGN_VOICE', 'SET_TONE',
    'CLEAR_TONE',
])


STATE_PATH = Path(os.environ.get("ELI_STATE_FILE", str(get_paths().artifacts_dir / "state.json"))).expanduser().resolve()

# Short-term conversation continuity
MAX_HISTORY_MESSAGES = int(os.environ.get("ELI_MAX_HISTORY_MESSAGES", "32"))

# Long-term memory store (simple deterministic archive, no embeddings)
MEMORY_PATH = Path(os.environ.get("ELI_MEMORY_FILE", str(get_paths().artifacts_dir / "eli_memory_legacy.jsonl"))).expanduser().resolve()

# Lock behavior: if true, refuse CHAT when verification fails
LOCK_ENFORCED = os.environ.get("ELI_LOCK_ENFORCED", "1").strip() not in ("0", "false", "False", "no", "NO")


def _load_state() -> Dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return {}


def _save_state(st: Dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(st, indent=2), encoding="utf-8")
    except Exception:
        log.debug("suppressed exception", exc_info=True)


def _state_get_history(st: Dict[str, Any]) -> List[Dict[str, str]]:
    h = st.get("chat_history")
    if not isinstance(h, list):
        return []
    out: List[Dict[str, str]] = []
    for m in h:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            out.append({"role": role, "content": content})
    return out


def _state_append_history(st: Dict[str, Any], user_msg: str, assistant_msg: str) -> None:
    h = _state_get_history(st)
    h.append({"role": "user", "content": user_msg})
    h.append({"role": "assistant", "content": assistant_msg})
    if len(h) > MAX_HISTORY_MESSAGES:
        h = h[-MAX_HISTORY_MESSAGES:]
    st["chat_history"] = h
    st["updated_at"] = time.time()


# ----------------------------
# Persona lock + checksum verification
# ----------------------------

def _ollama_ps() -> Dict[str, Any]:
    try:
        return requests.get(f"{OLLAMA_HOST}/api/ps", timeout=10).json()
    except Exception:
        return {"models": []}


def _ollama_model_digest_from_ps(ps: Dict[str, Any], model_name: str) -> Optional[str]:
    for m in ps.get("models", []) or []:
        if (m.get("name") == model_name) or (m.get("model") == model_name):
            return m.get("digest") or None
    return None


def _verify_persona_lock(st: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Returns (ok, reason, details). If ok==False and LOCK_ENFORCED, CHAT should refuse to run.
    We verify:
      - model name matches expected (optional)
      - Modelfile checksum matches expected (optional)
      - Ollama digest matches expected (optional)
    """
    details: Dict[str, Any] = {}
    expected_model = (st.get("persona_lock") or {}).get("model") or None
    expected_modelfile = (st.get("persona_lock") or {}).get("modelfile_path") or None
    expected_modelfile_sha = (st.get("persona_lock") or {}).get("modelfile_sha256") or None
    expected_digest = (st.get("persona_lock") or {}).get("ollama_digest") or None
    current_model = (os.environ.get('ELI_CHAT_MODEL') or os.environ.get('OLLAMA_MODEL') or DEFAULT_CHAT_MODEL).strip()
    details["current_model"] = current_model
    details["expected_model"] = expected_model

    # 1) Model name check
    if expected_model and current_model != expected_model:
        return (False, "model_name_mismatch", {**details, "msg": f"Expected {expected_model}, got {current_model}."})

    # 2) Modelfile checksum check
    if expected_modelfile and expected_modelfile_sha:
        p = Path(os.path.expanduser(expected_modelfile))
        if not p.exists():
            return (False, "modelfile_missing", {**details, "msg": f"Locked Modelfile not found: {p}"})
        sha = _sha256_file(p)
        details["current_modelfile_sha256"] = sha
        if sha != expected_modelfile_sha:
            return (False, "modelfile_sha256_mismatch", {**details, "msg": "Modelfile checksum changed."})

    # 3) Ollama digest check (from /api/ps)
    # Resolve Ollama host once (no implicit globals)
    ollama_host = (
        os.environ.get('ELI_OLLAMA_HOST')
        or os.environ.get('OLLAMA_HOST')
        or 'http://localhost:11434'
    )
    ps = _ollama_ps()
    dig = _ollama_model_digest_from_ps(ps, current_model)
    details["current_ollama_digest"] = dig
    details["expected_ollama_digest"] = expected_digest
    if expected_digest and dig and dig != expected_digest:
        return (False, "ollama_digest_mismatch", {**details, "msg": "Loaded model digest differs."})
    if expected_digest and dig is None:
        # Model may not be loaded yet; attempt a warm-load then re-check /api/ps.
        warmed = False
        try:
            warmed = _ollama_warm_load(current_model, ollama_host)
        except Exception as e:
            details["warm_load_error"] = repr(e)

        if warmed:
            ps2 = _ollama_ps()
            dig2 = _ollama_model_digest_from_ps(ps2, current_model)
            details["current_ollama_digest"] = dig2
            if dig2 and dig2 != expected_digest:
                return (False, "ollama_digest_mismatch", {**details, "msg": "Loaded model digest differs after warm-load."})
            if dig2:
                return (True, "ok", details)

        return (True, "ok_unverified", {**details, "warn": "Model not yet loaded; digest cannot be verified until first use."})
    # FINAL SUCCESS PATH — if nothing failed above, the lock is valid
    return (True, "ok", details)


def persona_lock_set(model: Optional[str] = None, modelfile_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Sets the lock baseline. This is the "make it unbreakable" button.
    - model defaults to DEFAULT_CHAT_MODEL
    - modelfile_path can be None (then only model + digest are locked)
    """
    st = _load_state()
    model = (model or DEFAULT_CHAT_MODEL).strip()

    lock: Dict[str, Any] = {"model": model, "set_at": time.time()}

    if modelfile_path:
        p = Path(os.path.expanduser(modelfile_path))
        if not p.exists():
            msg = f"Modelfile not found: {p}"
            return {"ok": False, "action": "PERSONA_LOCK_SET", "error": msg, "content": msg, "response": msg}
        lock["modelfile_path"] = str(p)
        lock["modelfile_sha256"] = _sha256_file(p)
    #------------------------------
    # Capture current digest (best effort)
    #-------------------------------   
    ps = _ollama_ps()
    dig = _ollama_model_digest_from_ps(ps, model)
    if dig:
        lock["ollama_digest"] = dig

    st["persona_lock"] = lock
    _save_state(st)

    msg = f"Persona lock set for model={model}."
    return {"ok": True, "action": "PERSONA_LOCK_SET", "lock": lock, "content": msg, "response": msg}


def persona_lock_status() -> Dict[str, Any]:
    st = _load_state()
    ok, reason, details = _verify_persona_lock(st)
    lock = st.get("persona_lock") or {}
    msg = "Persona lock OK." if ok else f"Persona lock FAIL: {reason}"
    return {"ok": ok, "action": "PERSONA_LOCK_STATUS", "reason": reason, "details": details, "lock": lock, "content": msg, "response": msg}


def persona_lock_clear() -> Dict[str, Any]:
    st = _load_state()
    st.pop("persona_lock", None)
    _save_state(st)
    msg = "Persona lock cleared."
    return {"ok": True, "action": "PERSONA_LOCK_CLEAR", "content": msg, "response": msg}


# ----------------------------
# Persistent long-term memory (deterministic archive)
# ----------------------------

def memory_store_legacy(text: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        msg = "Empty memory text."
        return {"ok": False, "action": "MEMORY_STORE", "error": "empty_text", "content": msg, "response": msg}

    rec = {
        "ts": time.time(),
        "text": text,
        "tags": [t.strip() for t in (tags or []) if isinstance(t, str) and t.strip()],
    }
    try:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with MEMORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        msg = "Memory stored."
        return {"ok": True, "action": "MEMORY_STORE", "record": rec, "content": msg, "response": msg}
    except Exception as e:
        msg = "Failed to store memory."
        return {"ok": False, "action": "MEMORY_STORE", "error": repr(e), "content": msg, "response": msg}


def memory_recall_legacy(query: str, limit: int = 5) -> Dict[str, Any]:
    q = (query or "").strip().lower()
    if not q:
        msg = "Empty recall query."
        return {"ok": False, "action": "MEMORY_RECALL", "error": "empty_query", "content": msg, "response": msg}

    hits: List[Dict[str, Any]] = []
    try:
        if not MEMORY_PATH.exists():
            msg = "No memory archive yet."
            return {"ok": True, "action": "MEMORY_RECALL", "hits": [], "content": msg, "response": msg}

        with MEMORY_PATH.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                text = str(rec.get("text", "")).lower()
                tags = " ".join(rec.get("tags") or []).lower()
                if q in text or q in tags:
                    hits.append(rec)

        hits = hits[-max(1, int(limit)):]  # most recent matches
        # Human readable summary
        summary_lines = []
        for r in reversed(hits):
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("ts", 0)))
            t = str(r.get("text", "")).strip()
            t = t if len(t) <= 180 else t[:177] + "..."
            summary_lines.append(f"- [{ts}] {t}")
        content = "Memory recall:\n" + ("\n".join(summary_lines) if summary_lines else "(no matches)")
        return {"ok": True, "action": "MEMORY_RECALL", "hits": hits, "content": content, "response": content}
    except Exception as e:
        msg = "Memory recall failed."
        return {"ok": False, "action": "MEMORY_RECALL", "error": repr(e), "content": msg, "response": msg}


# ----------------------------
# Self-consistency tests + healthcheck
# ----------------------------

def self_test() -> Dict[str, Any]:
    """
    Runs a deterministic suite:
      - persona lock status
      - minimal chat handshake (GGUF when that is the active provider, else Ollama)
      - consistency probe (temperature=0 twice) and compare — Ollama only
    """
    import socket

    def _ollama_reachable() -> bool:
        try:
            s = socket.create_connection(("127.0.0.1", 11434), timeout=1)
            s.close()
            return True
        except OSError:
            return False

    def _resolve_backend() -> tuple[str, str]:
        """Return (backend, model_label) — 'gguf' or 'ollama'."""
        provider = ""
        model_label = ""
        try:
            from eli.core import config
            provider = str(config.get("provider") or "").strip().lower()
            if provider == "ollama":
                model_label = str(config.get("ollama_model") or "").strip()
        except Exception:
            log.debug("self_test config read failed", exc_info=True)
        try:
            from eli.core.paths import get_paths
            snap_path = Path(get_paths().artifacts_dir) / "runtime_snapshot.json"
            if snap_path.exists():
                snap = json.loads(snap_path.read_text(encoding="utf-8"))
                if isinstance(snap, dict):
                    if not provider:
                        provider = str(snap.get("provider") or "").strip().lower()
                    mp = str(snap.get("model_name") or snap.get("model_path") or "").strip()
                    if mp and not model_label:
                        model_label = Path(mp).name if "/" in mp or "\\" in mp else mp
        except Exception:
            log.debug("self_test snapshot read failed", exc_info=True)

        gguf_providers = {"bundled_gguf", "custom_gguf", "gguf"}
        if provider in gguf_providers:
            return "gguf", model_label or "gguf"
        if provider == "ollama":
            return "ollama", model_label or "ollama"

        # When both backends exist, prefer GGUF if it is loaded (typical AppImage path).
        try:
            from eli.cognition.gguf_inference import is_loaded, get_model_path
            if is_loaded():
                mp = get_model_path()
                label = mp.name if mp else (model_label or "gguf")
                return "gguf", label
        except Exception:
            log.debug("self_test gguf probe failed", exc_info=True)

        if _ollama_reachable():
            return "ollama", model_label or "ollama"
        return "gguf", model_label or "gguf"

    def _self_test_gguf(model_label: str) -> Dict[str, Any]:
        try:
            from eli.cognition.gguf_inference import chat_completion
            resp = chat_completion("Reply with exactly these 2 words: persona ok", max_tokens=16)
            gguf_ok = bool(resp and resp.strip())
        except Exception as exc:
            gguf_ok = False
            resp = str(exc)
        content = (resp or "").strip()
        tests = [
            {"name": "handshake", "ok": gguf_ok, "model": model_label or "gguf",
             "content": content, "response": content},
            {"name": "consistency_probe_1", "ok": gguf_ok, "model": model_label or "gguf",
             "content": "skipped (GGUF active)", "response": "skipped (GGUF active)"},
            {"name": "consistency_probe_2", "ok": gguf_ok, "model": model_label or "gguf",
             "content": "skipped (GGUF active)", "response": "skipped (GGUF active)"},
            {"name": "consistency_equal", "ok": gguf_ok,
             "content": "skipped (GGUF active)", "response": "skipped (GGUF active)"},
        ]
        return {"tests": tests, "handshake_ok": gguf_ok}

    st = _load_state()
    lock = st.get("persona_lock") or {}
    if lock.get("model") == "test":
        try:
            st.pop("persona_lock", None)
            _save_state(st)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    ok_lock, reason, details = _verify_persona_lock(st)

    backend, model_label = _resolve_backend()
    results: Dict[str, Any] = {
        "persona_lock": {"ok": ok_lock, "reason": reason, "details": details},
        "active_backend": backend,
        "active_model": model_label,
        "tests": []
    }
    try:
        from eli.integrations.media.capabilities import (
            detect_hardware_capabilities,
            detect_media_capabilities,
            platform_capability_report,
        )
        results["platform_capabilities"] = detect_media_capabilities()
        results["hardware_capabilities"] = detect_hardware_capabilities()
        results["platform_report"] = platform_capability_report(verbose=True)
    except Exception:
        log.debug("self_test platform capability probe failed", exc_info=True)
    try:
        from eli.perception.desktop_capabilities import runtime_tools_report
        results["desktop_control"] = runtime_tools_report()
    except Exception:
        log.debug("self_test desktop control probe failed", exc_info=True)

    if backend == "gguf":
        gguf_run = _self_test_gguf(model_label)
        results["tests"] = gguf_run["tests"]
        overall_ok = bool(ok_lock) and bool(gguf_run["handshake_ok"])
        detail = f"GGUF: {model_label}" if model_label else "GGUF"
        msg = f"SELF_TEST OK ({detail})" if overall_ok else f"SELF_TEST FAIL ({detail})"
        return {"ok": overall_ok, "action": "SELF_TEST", "results": results,
                "content": msg, "response": msg, "evidence_source": "self_test"}

    try:
        from eli.integrations.ollama.client import ollama_chosen as _ollama_chosen
        _use_ollama = _ollama_chosen()
    except Exception:
        _use_ollama = False
    if not _use_ollama or not _ollama_reachable():
        # Ollama is tested only when it is the backend the user chose, not whenever one is running
        gguf_run = _self_test_gguf(model_label)
        results["tests"] = gguf_run["tests"]
        results["active_backend"] = "gguf"
        overall_ok = bool(ok_lock) and bool(gguf_run["handshake_ok"])
        msg = "SELF_TEST OK (GGUF fallback)" if overall_ok else "SELF_TEST FAIL (GGUF fallback)"
        return {"ok": overall_ok, "action": "SELF_TEST", "results": results,
                "content": msg, "response": msg, "evidence_source": "self_test"}

    # ── Ollama path (explicit provider or no GGUF loaded) ──
    handshake_prompt = "Reply with exactly these 2 words: persona ok"
    h1 = _ollama_chat_raw(handshake_prompt, temperature=0.0, num_predict=16)
    results["tests"].append({"name": "handshake", **h1})

    probe = "Reply with exactly 4 words: consistency test passed."
    p1 = _ollama_chat_raw(probe, temperature=0.0, num_predict=16)
    p2 = _ollama_chat_raw(probe, temperature=0.0, num_predict=16)
    same = (p1.get("content") == p2.get("content")) and p1.get("ok") and p2.get("ok")
    results["tests"].append({"name": "consistency_probe_1", **p1})
    results["tests"].append({"name": "consistency_probe_2", **p2})
    results["tests"].append({"name": "consistency_equal", "ok": same, "content": str(same), "response": str(same)})

    overall_ok = bool(ok_lock) and bool(h1.get("ok")) and bool(p1.get("ok")) and bool(p2.get("ok"))
    detail = f"Ollama: {h1.get('model') or model_label or 'ollama'}"
    msg = f"SELF_TEST OK ({detail})" if overall_ok else f"SELF_TEST FAIL ({detail})"
    return {"ok": overall_ok, "action": "SELF_TEST", "results": results,
            "content": msg, "response": msg, "evidence_source": "self_test"}


def _ollama_chat_raw(user_text: str, temperature: float = 0.7, num_predict: int = 80) -> Dict[str, Any]:
    model = DEFAULT_CHAT_MODEL
    payload = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": user_text}],
        "options": {"temperature": float(temperature), "num_predict": int(num_predict)},
    }
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=(10, 3600))
        r.raise_for_status()
        j = r.json()
        content = _strip_ollama_artifacts(((j.get("message") or {}).get("content") or "").strip())
        return {"ok": True, "model": model, "content": content, "response": content}
    except Exception as e:
        msg = "raw chat failed"
        return {"ok": False, "model": model, "error": repr(e), "content": msg, "response": msg}


# ----------------------------
# Origin / identity (NO prompt hijack)
# ----------------------------

def _is_origin_question(msg: str) -> bool:
    low = (msg or "").lower()
    triggers = [
        "who created you", "who made you", "who built you",
        "who are you", "your creator", "your origin",
        "who developed you"
    ]
    return any(t in low for t in triggers)


def _state_user_name(st: Dict[str, Any] = None) -> str:
    """The name of whoever is asking, as their own profile keeps it (kernel/state.get_user_name, the
    one place a name is kept); "listed" and other since-rejected values read as unset. The executor's
    state file kept a copy of the owner's, which status read for every caller."""
    try:
        from eli.kernel.state import get_user_name
        return str(get_user_name("") or "").strip()
    except Exception:
        log.debug("the name was not read", exc_info=True)
        return ""


def _origin_facts(st: Dict[str, Any]) -> str:
    name = _state_user_name(st) or "the user"
    return (
        f"Factual note: {name} assembled/configured ELI; ELI runs locally via Ollama using base model weights. "
        f"Don't claim any company 'created ELI' as a shipped product."
    )


def _needs_origin_anchor(ans: str) -> bool:
    """
    If origin question answer drifts into corporate-creator claims,
    we append a short factual anchor (we do NOT rewrite the answer).
    """
    a = (ans or "").lower()
    bad = [
        "created by alibaba", "developed by alibaba",
        "created by qwen", "developed by qwen", "alibaba cloud created"
    ]
    return any(x in a for x in bad)


# ================== MEDIA CONTROL FUNCTIONS ==================
# The media layer (Spotify/mpv/YouTube/MPRIS transport, ducking, now-playing state) lives in
# effectors/media.py. One copy: two had drifted apart, each with its own _MEDIA_STATE.
from eli.execution.effectors.media import (  # noqa: F401
    _ensure_spotify_running,
    MEDIA_DUCK,
    MEDIA_PLAYER,
    _BROWSER_PLAYERS,
    _DUCKED_MEDIA,
    _DUCK_LOCK,
    _DUCK_TIMER,
    _MEDIA_STATE,
    _PLAYLIST_RE,
    _TARGET_ALIASES,
    _duck_media_pause,
    _duck_media_resume,
    _duck_schedule_resume,
    _get_active_player,
    _mpv_alive,
    _mpv_ipc,
    _mpv_load_confirmed,
    _mpv_numeric,
    _mpv_quit,
    _mpv_socket_path,
    _playerctl,
    _playerctl_p,
    _playerctl_run,
    _prune_mpv_logs,
    _resolve_media_target,
    _set_now_playing,
    _spotify_clear_track_repeat,
    _spotify_ctl,
    _spotify_is_liked_songs_request,
    _spotify_is_playing,
    _spotify_launch,
    _spotify_live_meta,
    _spotify_loop_status,
    _spotify_open_liked_songs,
    _spotify_open_uri,
    _spotify_play,
    _spotify_player_name,
    _spotify_playlist_name,
    _spotify_resolve_album_uri,
    _spotify_resolve_artist_uri,
    _spotify_resolve_playlist_uri,
    _spotify_running,
    _spotify_search,
    _spotify_try_open_and_play,
    _spotify_wait_playing,
    _spotify_wait_running,
    _targets_mpv,
    _yt_apply_browser_autoplay,
    _yt_autoplay_enabled,
    _yt_browser_play_url,
    _yt_is_client_bound_failure,
    _yt_mix_url,
    _yt_player_clients,
    _yt_resolve_watch_url,
    _yt_resolve_watch_url_ytdlp,
    next_media,
    now_playing,
    pause_media,
    play_media,
    play_specific,
    previous_media,
    repeat_media,
    shuffle_media,
    stop_media,
)


# ── Browser/streaming service → MPRIS player alias map ──────────────────────


def _open_in_browser(url: str) -> bool:
    """Open a URL in the default browser. Returns True on success."""
    try:
        from eli.utils.platform_compat import open_url as _open_url
        return bool(_open_url(url))
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return False




# Now-playing state and mpv (YouTube) control. "Play on YouTube" launches mpv with a visible window
# (yt-dlp); ELI_YOUTUBE_HEADLESS=1 gives audio-only. playerctl can't see mpv (no MPRIS), so
# pause/stop/resume talk to the IPC socket.


# ================== END MEDIA CONTROL ==================


def _volume_fallback(direction: str, delta: int = 10, level: int | None = None) -> Dict[str, Any]:
    from eli.execution.effectors.system_helpers import volume_fallback
    return volume_fallback(direction, delta=delta, level=level)


# ----------------------------
# CHAT with persistent conversation + lock enforcement
# ----------------------------

def _live_memory_audit() -> dict:
    """
    Real local-memory audit:
    - SQLite memory DB(s)
    - conversations JSONL
    - proactive artifacts
    - latest context/summary/action files
    Returns factual counts + health, never fabricated claims.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    root = Path(__file__).resolve().parents[2]  # project root (contains artifacts/, src/)
    candidates = {
        "root_artifacts_db": root / "artifacts" / "user.sqlite3",
        "src_artifacts_db": root / "src" / "eli" / "artifacts" / "user.sqlite3",
        "conversations_dir": root / "src" / "eli" / "artifacts" / "conversations",
        "proactive_dir": root / "artifacts" / "proactive",
        "src_latest_context": root / "artifacts" / "latest_context.txt",
        "src_latest_summary": root / "artifacts" / "latest_summary.txt",
        "src_latest_action": root / "artifacts" / "latest_action.txt",
    }

    report = {
        "ok": True,
        "ts_utc": now,
        "root": str(root),
        "stores": {},
        "errors": [],
    }

    # ---- SQLite scan ----
    def scan_sqlite(db_path: Path):
        out = {"path": str(db_path), "exists": db_path.exists(), "size_bytes": 0, "tables": {}, "recent_rows": []}
        if not db_path.exists():
            return out
        out["size_bytes"] = db_path.stat().st_size
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
            tables = [r[0] for r in cur.fetchall()]
            out["tables"]["__all__"] = len(tables)

            for t in tables:
                try:
                    cur.execute(f'SELECT COUNT(*) FROM "{t}"')
                    out["tables"][t] = int(cur.fetchone()[0] or 0)
                except Exception as e:
                    out["tables"][t] = f"count_error: {e}"

            # try pull likely memory/event rows
            for t in ("events", "memory", "memories", "conversation", "conversations"):
                if t in tables:
                    try:
                        cur.execute(f'SELECT * FROM "{t}" ORDER BY rowid DESC LIMIT 3')
                        rows = cur.fetchall()
                        out["recent_rows"].append({"table": t, "rows_found": len(rows)})
                    except Exception as e:
                        out["recent_rows"].append({"table": t, "error": str(e)})
            conn.close()
        except Exception as e:
            out["error"] = str(e)
        return out

    for key in ("root_artifacts_db", "src_artifacts_db"):
        report["stores"][key] = scan_sqlite(candidates[key])

    # ---- JSONL conversations ----
    conv_dir = candidates["conversations_dir"]
    conv_out = {"path": str(conv_dir), "exists": conv_dir.exists(), "files": 0, "lines_total": 0, "latest_file": None}
    try:
        if conv_dir.exists():
            files = sorted(conv_dir.glob("*.jsonl"))
            conv_out["files"] = len(files)
            if files:
                conv_out["latest_file"] = str(files[-1])
            total = 0
            for f in files:
                try:
                    with f.open("r", encoding="utf-8", errors="ignore") as fh:
                        for _ in fh:
                            total += 1
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            conv_out["lines_total"] = total
    except Exception as e:
        conv_out["error"] = str(e)
    report["stores"]["conversations"] = conv_out

    # ---- proactive files ----
    pro_dir = candidates["proactive_dir"]
    pro_out = {"path": str(pro_dir), "exists": pro_dir.exists(), "files": {}}
    for name in ("latest_context.txt", "latest_summary.txt", "latest_action.txt", "latest_decision.txt", "latest.txt"):
        fp = pro_dir / name
        entry = {"exists": fp.exists(), "size_bytes": (fp.stat().st_size if fp.exists() else 0)}
        if fp.exists():
            try:
                txt = fp.read_text(encoding="utf-8", errors="ignore").strip()
                entry["preview"] = txt[:200]
            except Exception as e:
                entry["read_error"] = str(e)
        pro_out["files"][name] = entry
    report["stores"]["proactive"] = pro_out

    # ---- top-level latest files ----
    for key in ("src_latest_context", "src_latest_summary", "src_latest_action"):
        fp = candidates[key]
        entry = {"path": str(fp), "exists": fp.exists(), "size_bytes": (fp.stat().st_size if fp.exists() else 0)}
        if fp.exists():
            try:
                entry["preview"] = fp.read_text(encoding="utf-8", errors="ignore").strip()[:200]
            except Exception as e:
                entry["read_error"] = str(e)
        report["stores"][key] = entry

    return report


def _format_memory_audit_for_chat(rep: dict) -> str:
    try:
        s = rep.get("stores", {})
        db1 = s.get("root_artifacts_db", {})
        db2 = s.get("src_artifacts_db", {})
        conv = s.get("conversations", {})
        pro = s.get("proactive", {})

        def table_count(d):
            t = d.get("tables", {})
            return ", ".join([f"{k}:{v}" for k, v in t.items() if k != "__all__"][:6]) or "none"

        lines = []
        lines.append("Memory status data:")
        lines.append(f"- Root DB exists: {db1.get('exists')} | size: {db1.get('size_bytes',0)} bytes | tables: {db1.get('tables',{}).get('__all__',0)}")
        lines.append(f"  table counts: {table_count(db1)}")
        lines.append(f"- Src DB exists: {db2.get('exists')} | size: {db2.get('size_bytes',0)} bytes | tables: {db2.get('tables',{}).get('__all__',0)}")
        lines.append(f"  table counts: {table_count(db2)}")
        lines.append(f"- Conversation logs: files={conv.get('files',0)} lines_total={conv.get('lines_total',0)} latest={conv.get('latest_file')}")
        latest_ctx = pro.get("files", {}).get("latest_context.txt", {})
        latest_sum = pro.get("files", {}).get("latest_summary.txt", {})
        latest_act = pro.get("files", {}).get("latest_action.txt", {})
        lines.append(f"- Proactive context exists={latest_ctx.get('exists')} size={latest_ctx.get('size_bytes',0)}")
        lines.append(f"- Proactive summary exists={latest_sum.get('exists')} size={latest_sum.get('size_bytes',0)}")
        lines.append(f"- Proactive action exists={latest_act.get('exists')} size={latest_act.get('size_bytes',0)}")
        lines.append("")
        lines.append("If you want retrieval proof for a specific topic, ask: \"memory audit for <topic>\" and I will query the DB rows directly.")
        return "\n".join(lines)
    except Exception as e:
        return f"Live memory audit failed to format: {e}"

def chat(message: str, *, model: Optional[str] = None, skip_router: bool = False) -> Dict[str, Any]:
    """
    Send a chat message to the LLM.
    If skip_router is True, the message is sent directly without attempting to route as a command.
    """
    msg = (message or "").strip()
    low_msg = msg.lower()

    _convlog_append('user', msg, {'fn':'chat','model': str(model or DEFAULT_CHAT_MODEL)})
    if not msg:
        return {"ok": False, "action": "CHAT", "error": "empty_message", "content": "", "response": ""}

    # Live audit path for memory-capability questions (real DB/files scan, never template)
    if (
        "how your memory" in low_msg or "how does your memory" in low_msg or
        "persistent memory" in low_msg or "across sessions" in low_msg or
        "continuous memory" in low_msg or "memory audit" in low_msg
    ):
        # Return raw data; the cognitive engine will synthesize a natural answer
        import json
        from eli.memory import get_memory, get_memory_status
        mem = get_memory()
        rep = get_memory_status(mem.db_path)
        rep["query"] = message
        content = json.dumps(rep, indent=2)
        _convlog_append('assistant', "[memory data]", {'fn':'chat','path':'live_memory_audit'})
        return {"ok": True, "action": "CHAT", "content": content, "response": content, "memory_audit": rep}

    # Explicit "yesterday" recall path (no fabrication)
    if ("what did we discuss yesterday" in low_msg or ("what did" in low_msg and "yesterday" in low_msg and "discuss" in low_msg)):
        try:
            rec = memory_recall("", limit=8)
            rows = rec.get("results") if isinstance(rec, dict) else []
            if rows:
                lines = []
                for r in rows[:8]:
                    t = str((r or {}).get("text", "")).strip()
                    if t:
                        lines.append("- " + " ".join(t.split())[:240])
                ans = "I can only report what is in persistent memory. Recent stored notes:\n" + ("\n".join(lines) if lines else "(none)")
            else:
                ans = "I can’t verify yesterday from persistent memory right now (no matching stored notes found). I won’t guess."
        except Exception:
            ans = "I can’t verify yesterday from persistent memory right now. I won’t guess."
        return {"ok": True, "action": "CHAT", "model": model or DEFAULT_CHAT_MODEL, "content": ans, "response": ans}

    # Route non-chat intents (commands) only if skip_router is False
    if not skip_router:
        try:
            from eli.execution.router_enhanced import route
            intent = route(msg)
            if intent.get("action") and intent["action"] != "CHAT":
                return execute(intent["action"], intent.get("args", {}))
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    st = _load_state()

    # Enforce lock BEFORE chatting (prevents silent personality drift)
    ok_lock, reason, details = _verify_persona_lock(st)
    if LOCK_ENFORCED and not ok_lock and (st.get("persona_lock") is not None):
        content = (
            f"ELI LOCKDOWN: persona verification failed ({reason}).\n"
            f"{details.get('msg','')}\n\n"
            "Run PERSONA_LOCK_SET with the intended model/modelfile, or fix environment drift."
        )
        return {"ok": False, "action": "CHAT", "error": "persona_lock_failed", "reason": reason, "details": details, "content": content, "response": content}

    model = (model or DEFAULT_CHAT_MODEL).strip()

    # ── GGUF first (works fully offline) ──────────────────────────────────────
    try:
        from eli.cognition.gguf_inference import load_model, chat_completion as gguf_chat
        if load_model() is not None:
            from eli.core.config import get_persona
            system = get_persona()
            content = gguf_chat(msg, system=system)
            content = _strip_ollama_artifacts((content or "").strip())
            if content:
                _convlog_append('assistant', content, {'fn': 'chat', 'model': 'gguf'})
                return {"ok": True, "action": "CHAT", "model": "gguf", "content": content, "response": content}
    except Exception:
        log.debug("suppressed exception", exc_info=True)  # GGUF unavailable → fall through to Ollama

    # ── Ollama: only when the user chose it as the backend, never as a fallback ──
    try:
        from eli.integrations.ollama.client import ollama_chosen
        if not ollama_chosen():
            err = "The model is not loaded yet. ELI uses its GGUF model; load it in Settings > Model."
            return {"ok": False, "action": "CHAT", "error": "model_not_loaded", "content": err, "response": err}
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        body = {"model": model, "messages": [{"role": "user", "content": msg}], "stream": False}
        res = requests.post(f"{_OLLAMA_HOST}/api/chat", json=body, timeout=CHAT_TIMEOUT_S)
        res.raise_for_status()
        j = res.json() if res.content else {}
        content = _strip_ollama_artifacts(((j.get("message") or {}).get("content") or "").strip())
        if not content:
            content = "(empty model response)"
        _convlog_append('assistant', content, {'fn':'chat','model': model})
        return {"ok": True, "action": "CHAT", "model": model, "content": content, "response": content}
    except Exception as e:
        err = f"chat_failed: {e}"
        _convlog_append('assistant', err, {'fn':'chat','model': model, 'error': True})
        return {"ok": False, "action": "CHAT", "error": str(e), "content": err, "response": err}


def chat_stream(message: str, *, model: Optional[str] = None):
    """
    Streaming chat generator that yields token chunks (str).
    - Routes non-chat intents through execute()
    - Enforces persona lock
    - Falls back to non-stream chat() behavior if needed
    """
    msg = (message or "").strip()

    low_msg = msg.lower()
    if (
        "how your memory" in low_msg or "how does your memory" in low_msg or
        "persistent memory" in low_msg or "across sessions" in low_msg or
        "continuous memory" in low_msg or "memory audit" in low_msg
    ):
        rep = _live_memory_audit()
        yield _format_memory_audit_for_chat(rep)
        return
    if not msg:
        return

    # Route commands first
    try:
        from eli.execution.router_enhanced import route
        intent = route(msg)
        if intent.get("action") and intent["action"] != "CHAT":
            r = execute(intent["action"], intent.get("args", {}))
            out = str((r or {}).get("response") or (r or {}).get("content") or "")
            if out:
                yield out
            return
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    st = _load_state()
    ok_lock, reason, details = _verify_persona_lock(st)
    if LOCK_ENFORCED and not ok_lock and (st.get("persona_lock") is not None):
        yield (
            f"ELI LOCKDOWN: persona verification failed ({reason}).\n"
            f"{details.get('msg','')}\n\n"
            "Run PERSONA_LOCK_SET with the intended model/modelfile, or fix environment drift."
        )
        return

    try:
        from eli.integrations.ollama.client import ollama_chosen
        if not ollama_chosen():
            # Ollama only when the user chose it as the backend: never a fallback for the GGUF model
            yield "The model is not loaded yet. ELI uses its GGUF model; load it in Settings > Model."
            return
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    model = (model or DEFAULT_CHAT_MODEL).strip()
    full = []
    try:
        body = {"model": model, "messages": [{"role": "user", "content": msg}], "stream": True}
        with requests.post(f"{_OLLAMA_HOST}/api/chat", json=body, stream=True, timeout=CHAT_TIMEOUT_S) as res:
            res.raise_for_status()
            for line in res.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                delta = _strip_ollama_artifacts(((j.get("message") or {}).get("content") or ""))
                if delta:
                    full.append(delta)
                    yield delta
                if j.get("done"):
                    break

        content = "".join(full).strip()
        if content:
            _convlog_append('assistant', content, {'fn':'chat_stream','model': model})
        else:
            fallback = chat(msg, model=model)
            text = str(fallback.get("response") or fallback.get("content") or "")
            if text:
                yield text
    except Exception:
        fallback = chat(msg, model=model)
        text = str(fallback.get("response") or fallback.get("content") or "")
        if text:
            yield text

def open_audio_settings() -> Dict[str, Any]:
    for cmd in (["pavucontrol"], ["gnome-control-center", "sound"]):
        r = _run(cmd, timeout=5)
        if r.get("ok"):
            msg = "Audio settings opened."
            return {"ok": True, "action": "OPEN_AUDIO_SETTINGS", "cmd": cmd, "content": msg, "response": msg}
    msg = "No audio settings app found (pavucontrol / gnome-control-center)."
    return {"ok": False, "action": "OPEN_AUDIO_SETTINGS", "error": msg, "content": msg, "response": msg}


def open_file_system(path: str = "~") -> Dict[str, Any]:
    from eli.execution.effectors.system_helpers import open_file_system as _ofs
    return _ofs(path)


def open_browser(url: str = "https://duckduckgo.com", urls: list = None) -> Dict[str, Any]:
    from eli.execution.effectors.system_helpers import open_browser as _ob
    return _ob(url, urls=urls)


def set_communication_style_action(style: str) -> Dict[str, Any]:
    """Persist an explicit tone/communication preference ('how to speak to me'). Empty or a
    reset phrase clears it. Feeds the persona brief via user_profile.json."""
    s = str(style or "").strip()
    if s.lower() in {"reset", "clear", "default", "normal", "none", "your default", "back to normal"}:
        s = ""
    if not s and str(style or "").strip():
        try:
            from eli.kernel.state import set_communication_style as _set
            _set("")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        msg = "Tone reset — back to my default voice."
        return {"ok": True, "action": "SET_COMMUNICATION_STYLE", "communication_style": "", "content": msg, "response": msg}
    if not s:
        msg = "Tell me how you'd like me to speak — e.g. 'blunt, with dry humour, concise'."
        return {"ok": False, "action": "SET_COMMUNICATION_STYLE", "error": "empty", "content": msg, "response": msg}
    try:
        from eli.kernel.state import set_communication_style as _set
        _set(s)
    except Exception as e:
        msg = f"Couldn't save that tone preference: {e}"
        return {"ok": False, "action": "SET_COMMUNICATION_STYLE", "error": repr(e), "content": msg, "response": msg}
    msg = f"Got it — I'll speak to you {s} from now on. Say 'reset your tone' to clear it."
    return {"ok": True, "action": "SET_COMMUNICATION_STYLE", "communication_style": s, "content": msg, "response": msg}


def set_user_name(name: str, declared_by: str = "") -> Dict[str, Any]:
    """Store the user's name. `declared_by` is how it was said (the router's route meta): a name read
    from one word ("I'm Sam", `i_am_word`) is no declaration, so it never replaces a different
    stored name or clears the name rows by itself: ELI asks, and a yes says "my name is <name>"."""
    n = (name or "").strip()
    if not n:
        msg = "Empty name."
        return {"ok": False, "action": "SET_USER_NAME", "error": "empty_name", "content": msg, "response": msg}

    # --- Validation gate: whether this is a name is the shared validator's to say ---
    # (eli/runtime/identity_validation.normalize_identity_candidate, the one reader of name shape
    # that every other path that stores a name uses). It is not a second word list here: that
    # refused "Will", "May" and "Can", and an ASCII-only pattern refused every name with a letter
    # outside A-Z ("Zoë", "Łukasz", any non-Latin script).
    from eli.runtime.identity_validation import normalize_identity_candidate as _nic
    _is_phrase = not _nic(n)
    if _is_phrase:
        log.debug(f"[EXECUTOR] set_user_name: rejected phrase-like name {n!r}")
        msg = "That doesn't look like a name — please say just the name."
        return {"ok": False, "action": "SET_USER_NAME", "error": "invalid_name", "content": msg, "response": msg}
    if str(declared_by or "") == "i_am_word":
        try:
            from eli.kernel.state import get_user_name as _stored_name
            stored = str(_stored_name("") or "").strip()
        except Exception:
            log.debug("stored name unreadable", exc_info=True)
            stored = ""
        if stored and stored.lower() != n.lower():
            try:
                from eli.runtime.pending_proposal import set_pending_proposal
                set_pending_proposal(f"my name is {n}", summary=f"call the user {n} instead of {stored}")
            except Exception:
                log.debug("name confirmation not armed", exc_info=True)
            # the facts; the reply asking is worded by the model (explain_in_words)
            msg = (f"Stored name: {stored}. Read from this message: {n} (one word after \"I'm\", not "
                   f"\"my name is\"). Not changed. A yes replaces {stored} with {n}.")
            return {"ok": True, "action": "SET_USER_NAME", "changed": False, "user_name": stored,
                    "asked_to_replace_with": n, "explain_in_words": True, "content": msg, "response": msg}
    # Written to the caller's own user_profile.json, the one place a name is kept (what GET_STATUS
    # reads too): the executor's state file kept a copy of the owner's, read for every caller.
    try:
        from eli.kernel.state import set_user_name as _state_set_user_name
        _state_set_user_name(n)
    except Exception as _sue:
        log.debug(f"[EXECUTOR] set_user_name profile write failed: {_sue}")
    # Self-heal stale identity patterns: when the name is (re)set, purge identity.name /
    # preferred_name / nickname rows in user_patterns that don't match the new name, so a wrong
    # earlier value (a mis-extracted "speak") can't surface forever.
    try:
        _purge_conflicting_identity_patterns(n)
    except Exception as _pe:
        log.debug(f"[EXECUTOR] identity-pattern purge failed (non-fatal): {_pe}")
    msg = f"Got it. I'll call you {n}."
    return {"ok": True, "action": "SET_USER_NAME", "user_name": n, "content": msg, "response": msg}


def _purge_conflicting_identity_patterns(new_name: str) -> int:
    """Delete this user's identity.* user_patterns rows whose value conflicts with new_name: only
    name/preferred/nickname patterns that contain the confirmed name are kept. Whose rows they are
    is profile_extractor.pattern_owner's (the owner's are ""): the purge ran over every user's, so an
    API member naming themself deleted the owner's name rows. Returns count.
    """
    import sqlite3 as _sq
    nm = (new_name or "").strip().lower()
    if not nm:
        return 0
    from eli.runtime.profile_extractor import pattern_owner
    who = pattern_owner()
    try:
        from eli.memory.memory import get_memory as _gm
        _db = str(_gm().db_path)
    except Exception:
        from eli.core.paths import get_paths as _gp
        _db = str(_gp().artifacts_dir / "db" / "user.sqlite3")
    conn = _sq.connect(_db)
    names = ("SELECT rowid, COALESCE(pattern_data,'') FROM user_patterns WHERE lower(COALESCE(pattern_type,'')) "
             "IN ('identity.preferred_name','identity.name','identity.nickname')")
    try:
        try:
            rows = conn.execute(names + " AND COALESCE(user_id,'') = ?", (who,)).fetchall()
        except _sq.OperationalError:
            # written before rows carried a user: every row is the owner's
            rows = [] if who else conn.execute(names).fetchall()
        stale = [rid for (rid, data) in rows if nm not in str(data).lower()]
        for rid in stale:
            conn.execute("DELETE FROM user_patterns WHERE rowid=?", (rid,))
        conn.commit()
        if stale:
            log.debug(f"[EXECUTOR] purged {len(stale)} stale identity pattern(s) conflicting with {new_name!r}")
        return len(stale)
    finally:
        conn.close()


def get_status() -> Dict[str, Any]:
    st = _load_state()
    ps = _ollama_ps()
    msg = "Status OK."
    return {
        "ok": True,
        "action": "GET_STATUS",
        "user_name": _state_user_name(st),
        "chat_model_default": DEFAULT_CHAT_MODEL,
        "persona_lock": st.get("persona_lock") or None,
        "ollama_ps": ps,
        "content": msg,
        "response": msg,
    }


def clear_chat_history() -> Dict[str, Any]:
    st = _load_state()
    st["chat_history"] = []
    st["updated_at"] = time.time()
    _save_state(st)
    msg = "Chat history cleared."
    return {"ok": True, "action": "CLEAR_CHAT_HISTORY", "content": msg, "response": msg}


# ================== MISSING FUNCTIONS FROM Eli_PC_Control2_Working.py ==================

# --- SMART IMPORT HANDLING ---
def _try_import_faster_whisper():
    """Try multiple strategies to import faster_whisper."""
    strategies = [
        # Try direct import first
        lambda: __import__('faster_whisper'),
        
        # Try adding venv path
        lambda: (sys.path.insert(0, os.path.join(os.path.dirname(__file__), '.venv', 'lib', 
                                                 f'python{sys.version_info.major}.{sys.version_info.minor}', 
                                                 'site-packages')) or __import__('faster_whisper')),
        
        # Platform-correct site-packages via the site module (Linux/macOS/Windows),
        # not a hardcoded ~/.local/lib path.
        lambda: ([sys.path.insert(0, _p) for _p in
                  (list(__import__('site').getsitepackages()) +
                   [__import__('site').getusersitepackages()])]
                 and __import__('faster_whisper')),
    ]
    
    for strategy in strategies:
        try:
            return strategy()
        except ImportError:
            continue
    
    return None


# --- ENV HELPERS ---
def _env_int(name: str, default: int) -> int:
    try:
        v = (os.environ.get(name) or "").strip()
        return int(v) if v else int(default)
    except Exception:
        return int(default)


# --- MEDIA DUCKING ---


# --- TTS FUNCTIONS ---
_TTS_ENGINE = None
_MIC_IGNORE_UNTIL = 0.0  # ignore mic until this monotonic time
_BUSY_UNTIL = 0.0  # ignore mic while executing actions
_STOP_REQUESTED = False

def _init_tts():
    global _TTS_ENGINE
    if _TTS_ENGINE is not None:
        return
    try:
        import pyttsx3
    except Exception as e:
        if DEBUG:
            log.debug(f"[TTS] pyttsx3 not available: {e}")
        _TTS_ENGINE = None
        return

    eng = pyttsx3.init()
    voice_name = os.environ.get("ELI_TTS_VOICE", "").strip().lower()
    rate       = os.environ.get("ELI_TTS_RATE", "").strip()

    # optional: choose a different voice by partial name
    if voice_name:
        try:
            for v in eng.getProperty("voices"):
                if voice_name in v.name.lower():
                    eng.setProperty("voice", v.id)
                    break
        except Exception as e:
            if DEBUG:
                log.debug(f"[TTS] voice selection failed: {e}")

    if rate:
        try:
            eng.setProperty("rate", int(rate))
        except Exception as e:
            if DEBUG:
                log.debug(f"[TTS] rate set failed: {e}")

    _TTS_ENGINE = eng

def _tts_list_voices() -> dict:
    _init_tts()
    eng = _TTS_ENGINE
    voices = []
    try:
        for v in (eng.getProperty("voices") or []):
            voices.append({
                "id": getattr(v, "id", "") or "",
                "name": getattr(v, "name", "") or "",
            })
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return {"current": os.environ.get("ELI_TTS_VOICE", ""), "voices": voices, "count": len(voices)}

def _tts_set_voice(target: str) -> dict:
    _init_tts()
    eng = _TTS_ENGINE
    target_l = (target or "").strip().lower()
    if not target_l:
        raise ValueError("empty voice target")
    chosen_id = None
    chosen_name = None
    try:
        for v in (eng.getProperty("voices") or []):
            vid = (getattr(v, "id", "") or "").lower()
            vname = (getattr(v, "name", "") or "").lower()
            if target_l in vid or target_l in vname:
                chosen_id = getattr(v, "id", None)
                chosen_name = getattr(v, "name", None) or target
                break
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    if not chosen_id:
        raise ValueError(f"Voice not found: {target}")
    try:
        eng.setProperty("voice", chosen_id)
    except Exception:
        # still persist preference; engine may apply on next init
        log.debug("suppressed exception", exc_info=True)
    os.environ["ELI_TTS_VOICE"] = str(chosen_name)
    try:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        TTS_PREF_FILE = MEMORY_PATH.parent / "tts_voice.json"
        TTS_PREF_FILE.write_text(json.dumps({"voice": os.environ["ELI_TTS_VOICE"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return {"voice": os.environ["ELI_TTS_VOICE"], "id": chosen_id}

def _speak_legacy(text: str):
    """Compatibility wrapper: delegate to canonical TTS router."""
    try:
        from eli.perception.tts_router import maybe_speak
        maybe_speak(text, enabled=True)
    except Exception as e:
        log.debug(f"[ELI-TTS] exception: {repr(e)}")
    return


# --- TTS PATCH ---
import tempfile
from eli.utils.platform_compat import open_url, open_file, notify, play_alarm_sound

# ---- Ollama host canonical config ----
OLLAMA_HOST = (os.environ.get("ELI_OLLAMA_HOST") or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
_OLLAMA_HOST = OLLAMA_HOST  # backward-compatible alias
CHAT_TIMEOUT_S = (10, 3600)  # connect/read timeout for Ollama chat


def _eli_set_pulse_sink():
    sink = (os.environ.get("ELI_OUTPUT_DEVICE") or "").strip()
    if sink:
        os.environ["PULSE_SINK"] = sink

def _eli_tts_echo(_text: str) -> None:
    # Always show what ELI *tries* to say, even if audio routing is cursed.
    try:
        if os.environ.get("ELI_TTS_ECHO", "1") != "0":
            log.debug(f"[ELI-SAY] {_text}")
        if os.environ.get("ELI_TTS_LOG", "1") != "0":
            from pathlib import Path
            import json
            from datetime import datetime
            try:
                from eli.core.paths import logs_dir
                logp = Path(logs_dir()) / "tts.jsonl"
            except Exception:
                logp = Path(__file__).resolve().parent / "data" / "logs" / "tts.jsonl"
            logp.parent.mkdir(parents=True, exist_ok=True)
            rec = {"ts": datetime.now().isoformat(timespec="seconds"), "text": _text}
            with open(logp, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        log.debug("suppressed exception", exc_info=True)

def _speak(text: str, *args, **kwargs):
    """Speak arbitrarily long text by chunking + sanitizing to avoid TTS silent failures."""
    import re as _re

    if text is None:
        return
    text = str(text)

    # Strip model junk tokens
    text = text.replace("<|end|>", "").replace("<|", "").replace("|>", "")

    # Remove/replace fenced code blocks (TTS engines often choke on them)
    text = _re.sub(r"```.*?```", " [code omitted] ", text, flags=_re.DOTALL)

    # Normalize whitespace
    text = _re.sub(r"[ \t]+", " ", text)
    text = _re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return

    # Chunk by sentence-ish boundaries, capped by char length
    MAX_CHARS = 350
    parts = _re.split(r"(?<=[.!?])\s+", text)
    buf = ""
    chunks = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(buf) + len(part) + 1 <= MAX_CHARS:
            buf = (buf + " " + part).strip()
        else:
            if buf:
                chunks.append(buf)
            # hard-split very long parts
            while len(part) > MAX_CHARS:
                chunks.append(part[:MAX_CHARS])
                part = part[MAX_CHARS:]
            buf = part
    if buf:
        chunks.append(buf)

    for c in chunks:
        _speak_raw(c, *args, **kwargs)

def _speak_raw(text: str) -> None:
    """Compatibility wrapper: delegate to canonical TTS router."""
    try:
        from eli.perception.tts_router import speak
        speak(text)
    except Exception:
        return


# --- PIPER TTS HOOK ---
def _eli_speak_piper(text: str):
    """Compatibility wrapper: canonical Piper selection lives in eli.perception.tts_router."""
    try:
        from eli.perception.tts_router import speak
        speak(text)
    except Exception:
        return


# Hook into speak function if piper is configured
try:
    _ELI_SPEAK_ORIG = _speak
    def _speak(text, *args, **kwargs):
        if os.environ.get("ELI_TTS_ENGINE", "").strip().lower() == "piper":
            return _eli_speak_piper(str(text))
        return _ELI_SPEAK_ORIG(text, *args, **kwargs)
except Exception:
    # If ELI changes its internals, fail gracefully instead of crashing startup
    log.debug("suppressed exception", exc_info=True)


# --- UTILITY FUNCTIONS ---
def _slugify(name: str) -> str:
    name = (name or "doc").strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return name or "doc"

def _open_file(path: str) -> None:
    try:
        open_file(path)
    except Exception:
        log.debug("suppressed exception", exc_info=True)

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


# App opening. Canonical alias table lives in eli/execution/app_aliases.py; this copy and the
# router's each lacked names the other had (thunderbird, mozilla, browser vs vs code, calculator,
# files), so resolution depended on which layer handled the phrase.
from eli.execution.app_aliases import APP_ALIASES as _APP_ALIASES

def _normalize_app(spoken: str) -> str:
    """Normalize application names using aliases."""
    s = (spoken or "").strip().lower()
    s = " ".join(s.split())
    return _APP_ALIASES.get(s, s)

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

def _open_app_with_timeout(argv, timeout=30):
    from eli.execution.effectors.system_helpers import open_app_with_timeout
    return open_app_with_timeout(argv, timeout=timeout)

def _open_app_builtin(app_name: str) -> dict:
    app = _normalize_app(app_name)
    if not app:
        return {"ok": False, "error": "empty app_name"}

    allowed = _allowed_apps_set()
    if allowed is not None and app not in allowed:
        return {"ok": False, "error": f"app not allowed: {app} (set ELI_ALLOWED_APPS)"}

    # Allow per-app override command
    env_key = "ELI_APP_CMD_" + re.sub(r"[^A-Z0-9]+", "_", app.upper())
    override = (os.environ.get(env_key) or "").strip()

    if override:
        argv = shlex.split(override)
    else:
        # sensible defaults
        if app == "thunderbird":
            argv = ["thunderbird"]
        elif app == "firefox":
            argv = ["firefox"]
        elif app == "spotify":
            SPOTIFY_CMD = os.environ.get("ELI_SPOTIFY_CMD", "").strip() or "spotify"
            argv = shlex.split(SPOTIFY_CMD) if SPOTIFY_CMD else ["spotify"]
        else:
            # last resort: try running by name
            argv = [app]

    try:
        return _open_app_with_timeout(argv)
    except Exception as e:
        return {"ok": False, "error": str(e), "app": app, "cmd": argv}


# --- BUILTIN DOCUMENT FUNCTIONS ---
def _builtin_create_doc(args: dict) -> dict:
    try:
        doc_type  = (args.get("doc_type") or "txt").strip().lower()
        title     = (args.get("title") or "document").strip()
        content   = args.get("content") or ""
        filename  = (args.get("filename") or "").strip()
        convert_to = (args.get("convert_to") or "omit").strip().lower()
        open_after = bool(args.get("open_after", False))

        ext = doc_type if doc_type in {"md","txt","tex"} else "txt"

        outdir = Path(os.environ.get("ELI_DOC_DIR", str(Path(__file__).resolve().parent.parent / "eli_docs"))).expanduser()
        outdir.mkdir(parents=True, exist_ok=True)

        if filename:
            fn = filename if filename.endswith(f".{ext}") else filename + f".{ext}"
        else:
            fn = _slugify(title) + f".{ext}"

        outpath = outdir / fn
        outpath.write_text(content, encoding="utf-8")

        if open_after:
            _open_file(str(outpath))

        return {"ok": True, "path": str(outpath)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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


def _calendar_event_builtin(args: dict) -> dict:
    """Open a prefilled Google Calendar event in the browser (handles 'tomorrow', weekdays, 2pm, 16.40pm, etc.)."""
    try:
        import re, urllib.parse, subprocess, os
        from datetime import datetime, date, timedelta

        title = (args.get("title") or "ELI Event").strip()
        date_s = (args.get("date") or args.get("day") or "").strip()
        time_s = (args.get("time") or args.get("at") or "").strip()
        start_s = (args.get("start") or "").strip()
        location = (args.get("location") or "").strip()
        notes = (args.get("notes") or args.get("details") or "").strip()

        dur = args.get("duration_min", None)
        if dur is None:
            dur = args.get("duration", None)
        try:
            duration_min = int(dur) if dur is not None else 30
        except Exception:
            duration_min = 30

        def parse_time(ts: str) -> str:
            if not ts:
                return "09:00"
            t = ts.strip().lower().replace(" ", "")
            t = t.replace(".", ":")
            m = re.match(r'^(\d{1,2})(?::(\d{2}))?(am|pm)?$', t)
            if not m:
                return "09:00"
            hh = int(m.group(1))
            mm = int(m.group(2) or "0")
            ap = m.group(3)
            # If already 13-23, ignore am/pm even if user says nonsense like 16:40pm
            if ap and hh <= 12:
                if ap == "pm" and hh < 12: hh += 12
                if ap == "am" and hh == 12: hh = 0
            hh = max(0, min(23, hh))
            mm = max(0, min(59, mm))
            return f"{hh:02d}:{mm:02d}"

        def parse_date(ds: str) -> str:
            today = date.today()
            if not ds:
                return today.isoformat()
            d = ds.strip().lower()
            wmap = {"monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}
            if d == "today":
                return today.isoformat()
            if d == "tomorrow":
                return (today + timedelta(days=1)).isoformat()
            if d in wmap:
                delta = (wmap[d] - today.weekday()) % 7
                delta = 7 if delta == 0 else delta
                return (today + timedelta(days=delta)).isoformat()
            # ISO date?
            if re.match(r'^\d{4}-\d{2}-\d{2}$', ds.strip()):
                return ds.strip()
            return today.isoformat()

        dt = None
        if start_s:
            # try iso-ish start string
            try:
                dt = datetime.fromisoformat(start_s.replace("Z",""))
            except Exception:
                # salvage "tomorrowT14:00" style
                if "T" in start_s:
                    left, right = start_s.split("T", 1)
                    date_s2 = parse_date(left)
                    time_s2 = parse_time(right)
                    dt = datetime.fromisoformat(f"{date_s2}T{time_s2}")

        if dt is None:
            d_iso = parse_date(date_s)
            t_hm = parse_time(time_s)
            dt = datetime.fromisoformat(f"{d_iso}T{t_hm}")

        end_dt = dt + timedelta(minutes=duration_min)

        def fmt(d: datetime) -> str:
            return d.strftime("%Y%m%dT%H%M%S")

        params = {
            "action": "TEMPLATE",
            "text": title,
            "dates": f"{fmt(dt)}/{fmt(end_dt)}",
        }
        if location:
            params["location"] = location
        if notes:
            params["details"] = notes

        # timezone offset (local)
        import time as _time
        tz_offset = _time.localtime().tm_gmtoff
        tz_sign = "+" if tz_offset >= 0 else "-"
        tz_hours = abs(tz_offset) // 3600
        tz_minutes = (abs(tz_offset) % 3600) // 60
        params["ctz"] = f"UTC{tz_sign}{tz_hours:02d}:{tz_minutes:02d}"

        base_url = "https://calendar.google.com/calendar/render"
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        open_url(url)

        return {"ok": True, "title": title, "start": dt.isoformat(), "end": end_dt.isoformat(),
                "duration_min": duration_min, "url": url}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _recall_memory_builtin(args: dict) -> dict:
    """Read recent utterances from ./data/memory/utterances.jsonl (if present)."""
    try:
        import json
        from pathlib import Path

        limit = int(args.get("limit", 20))
        root = Path(__file__).resolve().parent.parent
        memory_log = root / "data" / "memory" / "utterances.jsonl"

        if not memory_log.exists():
            return {"ok": True, "entries": [], "message": "No memory log found"}

        entries = []
        with open(memory_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        recent = entries[-limit:] if entries else []
        return {"ok": True, "entries": recent, "count": len(recent), "total": len(entries)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _type_text_builtin(text: str) -> dict:
    text = (text or "")
    if not text:
        return {"ok": False, "error": "empty text"}

    # Linux: prefer the native typing tools (respecting Wayland vs X11) when installed.
    if sys.platform.startswith("linux"):
        sess = (os.environ.get("XDG_SESSION_TYPE") or "").lower().strip()
        if sess == "wayland" and shutil.which("wtype"):
            return _run_argv(["wtype", "--", text], timeout=30)
        if shutil.which("xdotool"):
            return _run_argv(["xdotool", "type", "--clearmodifiers", "--delay", "8", text], timeout=30)
        # else fall through to the cross-platform backend below

    # Cross-platform backend (Windows, macOS, or Linux without wtype/xdotool): pyautogui.
    try:
        import pyautogui
        pyautogui.typewrite(text, interval=0.008)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"no typing backend available ({e})"}


# --- NOTIFICATION FUNCTION ---
def _notify(title: str, msg: str):
    try:
        if os.environ.get("ELI_NOTIFY", "1") == "1":
            notify(title, msg)
        if os.environ.get("ELI_VOICE_DEBUG", "0") == "1":
            log.debug(f"[NOTIFY] {title}: {msg}")
    except Exception:
        if os.environ.get("ELI_VOICE_DEBUG", "0") == "1":
            log.debug(f"[NOTIFY] {title}: {msg}")


# ----------------------------
# Artifact saver
# ----------------------------

def _artifacts_dir() -> "Path":
    """Return the canonical artifacts root. Honours ELI_ARTIFACTS_DIR (so tests can
    redirect to a tmp dir and never pollute the real artifacts/) — defaults to
    artifacts/ under the project root."""
    from pathlib import Path
    import os as _o
    _env = _o.environ.get("ELI_ARTIFACTS_DIR")
    if _env:
        _p = Path(_env).expanduser()
        return _p if _p.is_absolute() else (Path(__file__).resolve().parents[2] / _p)
    return Path(__file__).resolve().parents[2] / "artifacts"


# Saving an artifact lives in effectors._base (one copy for the executor and every effector).
from eli.execution.effectors._base import _save_artifact  # noqa: E402,F401


def _eli_self_description_block(limit: int = 2600) -> str:
    """Return a bounded, factual self-description of ELI for grounding documents
    that are ABOUT ELI itself ("propose upgrades for yourself"). Sourced from the
    repo's own blueprints (real, code-traceable) so a self-referential document is
    grounded in ELI's ACTUAL architecture instead of a hallucinated generic plan.
    Best-effort: returns "" if no source is available (caller degrades).

    Preference order: local blueprint markdown (maintainer checkouts, richest
    detail) -> README.md (tracked in git, so every fresh clone has it — the
    blueprints/ markdown does NOT ship publicly, only the PDFs do)."""
    try:
        from pathlib import Path as _P
        root = _P(__file__).resolve().parents[2]
        for rel in (
            "blueprints/what_eli_is.md",
            "blueprints/capability_catalogue.md",
            "README.md",
        ):
            fp = root / rel
            if fp.is_file():
                txt = fp.read_text(encoding="utf-8", errors="ignore").strip()
                if txt:
                    return txt[:limit].rstrip() + ("\n…" if len(txt) > limit else "")
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return ""


# Generative actions that must be grounded in gathered evidence before synthesis
# (the evidence-routing hook at the top of _execute_impl). Status/grounded-audit
# actions already gather via the bus's specialist agent sets, so they are not here.
_GENERATIVE_EVIDENCE_ACTIONS = {
    "GENERATE_DOCUMENT", "CREATE_DOCUMENT", "DOC_GENERATE",
    "GENERATE_SCRIPT", "CREATE_SCRIPT", "WRITE_SCRIPT", "GENERATE_PROJECT",
}
# DATA_FABRICATOR is intentionally NOT here: it delegates to CREATE_DOCUMENT, which
# gathers evidence itself — listing it too would double-gather.


# ----------------------------
# Dispatcher
# ----------------------------

def _eli_marketplace_install(query: str):
    """Install a community listing through the verified path, or None if not one.

    Returns None when the query does not match anything in a configured community
    registry, so the caller falls through to the bundled-plugin path. Chat can
    trigger a verified install but NEVER a permissioned one: anything that would
    open the consent dialog is reported back as needing the desktop screen, because
    a chat message cannot carry informed consent for granting capabilities.
    """
    try:
        from eli.plugins.marketplace import needs_review, preview_install, search
    except Exception:
        return None
    try:
        matches = search(query)
    except Exception:
        return None
    exact = [m for m in matches if str(m.get("id", "")).lower() == query.lower()]
    candidates = exact or matches
    if not candidates:
        return None
    if len(candidates) > 1 and not exact:
        listing = None
    else:
        listing = candidates[0]
    if listing is None:
        names = ", ".join(str(m.get("id")) for m in candidates[:5])
        return {"ok": True,
                "message": f"Several community listings match '{query}': {names}. "
                           f"Name one exactly and I'll check it."}

    if str(listing.get("kind")) == "mcp":
        from eli.plugins.mcp import network_caveat
        return {"ok": True,
                "message": (f"'{listing.get('name')}' is an MCP server, which runs as a "
                            f"separate program.\n\n{network_caveat()}\n\n"
                            f"Add it from Settings > Marketplace > MCP servers.")}

    preview = preview_install(listing)
    reasons = needs_review(preview)
    if reasons:
        scan = (preview.get("scan") or {})
        detail = "; ".join(reasons)
        verdict = scan.get("verdict")
        head = (f"I checked '{listing.get('name')}' and will not install it from chat: "
                if verdict == "malicious" else
                f"I checked '{listing.get('name')}'. It needs your decision: ")
        return {"ok": verdict != "malicious",
                "message": head + detail +
                           "\n\nOpen Settings > Marketplace to see the full report and "
                           "decide."}

    from eli.plugins.marketplace import install
    result = install(listing, confirm=lambda _p: True)
    if not result.get("ok"):
        return {"ok": False,
                "message": "Install failed: " + "; ".join(result.get("problems") or [])}
    return {"ok": True,
            "message": (f"Installed '{listing.get('name')}' — it matched the checksum in "
                        f"its listing, scanned clean, and asks for no permissions. "
                        f"It is switched OFF; say 'enable plugin "
                        f"{listing.get('id')}' when you want it.")}


def _eli_mcp_client():
    """MCP runtime client — ported from v3, which had the calling half while v2 had
    only the lifecycle half. Same config file and same `mcpServers` shape, so the
    two halves compose: eli.plugins.mcp installs and verifies a server,
    eli.integrations.mcp.client connects to it and calls its tools."""
    from eli.integrations.mcp import client
    return client


def _h_mcp_status(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        st = _eli_mcp_client().status()
    except Exception as e:
        msg = f"Couldn't read the MCP configuration: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    if not st["configured"]:
        msg = ("No MCP servers are configured yet, so I have no external tools beyond my own. "
               f"Add them to {st['config_path']} and I'll pick them up.")
        return {"ok": True, "action": a, "content": msg, "response": msg, **st}

    lines = [f"{st['configured']} MCP server(s) configured:"]
    for s in st["servers"]:
        if s["connected"]:
            tools = ", ".join(s["tools"]) or "no tools"
            lines.append(f"  {s['name']} — connected ({tools})")
        elif s["error"]:
            lines.append(f"  {s['name']} — not working: {s['error']}")
        else:
            lines.append(f"  {s['name']} — configured, not started yet")
    body = "\n".join(lines)
    return {"ok": True, "action": a, "content": body, "response": body, **st}


def _h_mcp_tools(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        tools = _eli_mcp_client().list_tools(refresh=bool(args.get("refresh")))
    except Exception as e:
        msg = f"Couldn't list MCP tools: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    if not tools:
        msg = ("I can't see any MCP tools right now — either nothing is configured, "
               "or the configured servers didn't start. Ask me for MCP status for the reason.")
        return {"ok": True, "action": a, "tools": [], "content": msg, "response": msg}

    lines = [f"{len(tools)} MCP tool(s) available:"]
    for t in tools:
        desc = (t["description"] or "").strip().splitlines()[0][:110] if t["description"] else ""
        lines.append(f"  {t['qualified']}" + (f" — {desc}" if desc else ""))
    body = "\n".join(lines)
    return {"ok": True, "action": a, "tools": tools, "content": body, "response": body}


def _h_mcp_call(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    tool = str(args.get("tool") or args.get("name") or args.get("qualified") or "").strip()
    if not tool:
        msg = "Tell me which MCP tool to call — ask for MCP tools to see what's available."
        return {"ok": False, "action": a, "error": "missing tool", "content": msg, "response": msg}

    arguments = args.get("arguments") or args.get("args") or {}
    if not isinstance(arguments, dict):
        arguments = {"input": arguments}

    try:
        res = _eli_mcp_client().call_tool(tool, arguments)
    except Exception as e:
        msg = f"MCP call failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    if not res.get("ok"):
        reason = str(res.get("error") or "the tool call failed")
        hint = res.get("hint")
        msg = f"{tool} didn't run: {reason}" + (f" ({hint})" if hint else "")
        return {"ok": False, "action": a, "error": reason, "content": msg, "response": msg}

    body = str(res.get("text") or "").strip() or f"{tool} ran and returned nothing to show."
    return {"ok": True, "action": a, "tool": tool, "content": body, "response": body}


def _execute_impl(action: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    args = args or {}
    # Normalize action for dispatch (accept open_app, OPEN_APP, open-app, etc.)
    a = (str(action) if action is not None else "").strip()
    a = re.sub(r"[\s\-]+", "_", a)
    a = a.upper()

    # Action synonyms: the model sometimes emits a name that means an existing handler (NEWS_SEARCH,
    # daily/weekly report). Normalise to the real action so it doesn't fall through to "Unsupported
    # executor action".
    _ACTION_ALIASES = {
        "NEWS_SEARCH": "NEWS_FETCH",
        "FETCH_NEWS": "NEWS_FETCH",
        "GET_NEWS": "NEWS_FETCH",
        "WEBSITE_SEARCH": "WEB_SEARCH",
        "WEBSITES_SEARCH": "WEB_SEARCH",
        "SEARCH_WEB": "WEB_SEARCH",
        "DAILY_REPORT": "MORNING_REPORT",
        "WEEKLY_REPORT": "MORNING_REPORT",
        # The LLM resolver often emits these for "list <dir>"; none are registered, so
        # without aliasing they fall to CHAT and the model CONFABULATES the listing.
        "LIST_FILE": "LIST_DIR",
        "LIST_FILES": "LIST_DIR",
        "LIST_DIRECTORY": "LIST_DIR",
        "LIST_CONTENTS": "LIST_DIR",
        "LS": "LIST_DIR",
        "CREATE_DOC": "CREATE_DOCUMENT",
        "WRITE_DOCUMENT": "CREATE_DOCUMENT",
        # Other names for a script: one generator writes them all (effectors/coding.py).
        "CREATE_SCRIPT": "GENERATE_SCRIPT",
        "WRITE_SCRIPT": "GENERATE_SCRIPT",
        "GENERATE_CODE": "GENERATE_SCRIPT",
        "WRITE_CODE": "GENERATE_SCRIPT",
    }
    a = _ACTION_ALIASES.get(a, a)

    # Gather evidence before generating a document/script/project (code, web, memory, runtime) so
    # the generator works from real findings. Idempotent: skipped when _evidence is already there, so
    # the GENERATE_* -> CREATE_* recursion only gathers once. See eli/runtime/evidence_planner.py.
    if a in _GENERATIVE_EVIDENCE_ACTIONS and args.get("_evidence") is None:
        try:
            from eli.runtime.evidence_planner import plan_and_gather as _pag
            _ev_q = str(args.get("topic") or args.get("description") or args.get("query")
                        or args.get("text") or args.get("prompt")
                        or args.get("_raw_user_text") or "").strip()
            _ev_mode = "quick"
            try:
                import eli.kernel.engine as _eng_mod
                _eng = getattr(_eng_mod, "_engine", None)  # read singleton; never construct
                if _eng is not None:
                    _ev_mode = str(getattr(_eng, "_reasoning_mode", "quick") or "quick")
                else:
                    from eli.core.runtime_settings import load_settings as _ls
                    _ev_mode = str((_ls() or {}).get("reasoning_mode", "quick") or "quick")
            except Exception:
                _ev_mode = "quick"
            args["_evidence"], args["_evidence_sources"] = _pag(
                a, _ev_q, _ev_mode,
                session_id=str(args.get("session_id") or ""),
                user_id=str(args.get("user_id") or ""))
        except Exception as _ev_err:
            log.debug(f"[EVIDENCE] injection failed for {a}: {_ev_err}")
            args.setdefault("_evidence", "")
            args.setdefault("_evidence_sources", [])

    # Effector registry first: an action with a module under eli/execution/effectors is handled there,
    # never by an inline branch below (tests/test_effector_dispatch_order.py holds this). Only the
    # generative-evidence pre-step above runs before it, because it enriches args for effectors too.
    try:
        from eli.execution.effectors import load_all as _load_effectors
        from eli.execution.effectors.registry import get as _effector_get
        _load_effectors()
        _effector = _effector_get(a)
    except Exception as _eff_err:
        log.debug("[EXECUTOR] effector lookup failed for %s", a, exc_info=True)
        _effector = None
    if _effector is not None:
        try:
            _eff_out = _effector(a, args)
            # Where the user asked to be shown what it made ("open it in the Labs IDE") travels with
            # the result, unless the effector said otherwise.
            if isinstance(_eff_out, dict):
                for _shown in ("destination", "open_in_labs"):
                    if _shown in args and _shown not in _eff_out:
                        _eff_out[_shown] = args[_shown]
            return _eff_out
        except Exception as _eff_err:
            log.debug("[EXECUTOR] effector dispatch failed for %s", a, exc_info=True)
            return {"ok": False, "action": a, "error": str(_eff_err), "content": str(_eff_err), "response": str(_eff_err)}

    if a == "CHAT":
        msg = args.get("message") or args.get("prompt") or args.get("text") or ""
        model = args.get("model") or None
        return chat(str(msg), model=model)

    # ---- DISPATCH PATCH: CORE + PROACTIVE ACTIONS ----
    # NOTE: Capabilities can be "registered" without being "implemented".
    # This block ensures the common actions actually route to real code.

    if a == "TIMESTAMP_DIAG":
        import datetime as _dt_diag
        from eli.core.worldclock import describe as _wc_describe
        lines = [
            "TIMESTAMP DIAGNOSTIC",
            "",
            "Wall clock (authoritative):",
            _wc_describe("", default_date=True, default_time=True),
        ]
        try:
            from eli.memory.memory import get_memory as _gm_diag
            turns = _gm_diag().get_recent_conversation(limit=8) or []
            if turns:
                lines.append("")
                lines.append("Recent conversation turns (stored timestamps):")
                for t in turns[-6:]:
                    ts = float((t or {}).get("timestamp") or 0)
                    when = (
                        _dt_diag.datetime.fromtimestamp(ts).astimezone().strftime(
                            "%A %d %b %Y %H:%M:%S %Z"
                        )
                        if ts > 0
                        else "no timestamp stored"
                    )
                    role = str((t or {}).get("role") or "?")
                    content = str((t or {}).get("content") or "").replace("\n", " ")[:120]
                    lines.append(f"  [{when}] {role}: {content}")
        except Exception as _td_err:
            lines.append("")
            lines.append(f"(conversation log unavailable: {_td_err})")
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "content": msg, "response": msg}
    # ---- HELP / LIST_CAPABILITIES ----
    if a in ("HELP", "LIST_CAPABILITIES"):
        # What the code has, and what this machine has shown of it: a bare count read as that many
        # working capabilities, when most had never been run here.
        caps: Dict[str, Any] = {}
        try:
            from eli.runtime.capability_sync import CapabilitySync
            caps = CapabilitySync().discover()
        except Exception:
            log.debug("capability discovery failed", exc_info=True)
        status: Dict[str, Any] = {}
        try:
            from eli.tools.registry.capability_updater import capability_status
            status = capability_status(sorted(caps) or None)
        except Exception:
            log.debug("capability status unavailable", exc_info=True)
        here = status.get("actions") or {}
        capabilities = sorted(caps) or sorted(here) or sorted(set(SUPPORTED_ACTIONS))

        def _why_not(st: Dict[str, Any]) -> str:
            if st.get("dependency_ready") is False:
                return "something it needs is not installed"
            if st.get("permission_state") == "denied":
                return "its plugin is not allowed"
            return "its device was not found"

        public_actions, lines = [], []
        for name in capabilities:
            meta, st = caps.get(name) or {}, here.get(name) or {}
            state = st.get("status", "untested")
            public_actions.append({"action": name, "sources": [meta.get("source", "")],
                                   "plugins": [meta["plugin"]] if meta.get("plugin") else [], "status": state})
            notes = {"verified": ["has worked here"], "unavailable": ["cannot run here: " + _why_not(st)]}.get(state, [])
            if a == "HELP" and meta.get("plugin"):
                notes.append(f"plugin {meta['plugin']}")
            lines.append(f"- {name}" + (f" ({'; '.join(notes)})" if notes else ""))
        _worked = int(status.get("verified", 0))
        head = (f"{len(capabilities)} actions in my code; {_worked} {'has' if _worked == 1 else 'have'} worked on this machine"
                + (f", {status['unavailable']} cannot run here" if status.get("unavailable") else "")
                + ", the rest have not been tried here yet.")
        msg = head + "\n" + "\n".join(lines)
        return {"ok": True, "action": a, "count": len(capabilities), "capabilities": capabilities,
                "capability_report": public_actions, "content": msg, "response": msg}

    # ---- OPEN_IDE REDIRECT GUARD ----
    if a == "OPEN_IDE":
        target = str((args or {}).get("name") or (args or {}).get("app") or (args or {}).get("target") or "").strip().lower()

        generic = {
            "", "ide", "the ide", "editor", "the editor",
            "built in ide", "built-in ide", "gui ide", "eli ide",
            "internal ide", "ide tab", "the ide tab"
        }

        alias_map = {
            "vscode": "code",
            "visual studio code": "code",
            "virtual studio code": "code",
            "vs code": "code",
            "code": "code",
            "gedit": "gedit",
            "codium": "codium",
            "kate": "kate",
            "sublime": "subl",
            "sublime text": "subl",
        }

        if target not in generic:
            mapped = alias_map.get(target, target)
            return _execute_impl("OPEN_APP", {"name": mapped})

    # ---- OPEN_APP ----
    if a == "OPEN_APP":
        try:
            # Accept the synonyms other producers use for the same slot: the LLM resolver names its
            # own arg keys, and an OPEN_APP with {"app_name": ...} was rejected as "Missing app
            # name".
            name = ""
            for _k in ("name", "app", "app_name", "application", "target", "program"):
                _v = (args or {}).get(_k)
                if _v is not None and str(_v).strip():
                    name = str(_v).strip()
                    break
            candidates = list((args or {}).get("candidates") or [])

            if not name and candidates:
                name = str(candidates[0]).strip()

            if not name:
                return {
                    "ok": False,
                    "action": a,
                    "error": "Missing app name",
                    "content": "Missing app name",
                    "response": "Missing app name",
                }

            _APP_FUZZY = {
                "calender": "gnome-calendar",
                "calander": "gnome-calendar",
                "calendar": "gnome-calendar",
                "calandar": "gnome-calendar",
                "settings": "gnome-control-center",
                "setting": "gnome-control-center",
                "files": "nautilus",
                "file manager": "nautilus",
                "text editor": "gedit",
                "editor": "gedit",
                "gedit": "gedit",
                "calculator": "gnome-calculator",
                "calc": "gnome-calculator",
                "terminal": "x-terminal-emulator", "term": "x-terminal-emulator",
                "monitor": "gnome-system-monitor",
                "system monitor": "gnome-system-monitor",
                "music": "rhythmbox",
                "photos": "eog",
                "image viewer": "eog",
                "screenshot": "gnome-screenshot",
                "screen recorder": "obs",
                "disk usage": "baobab",
                "disks": "gnome-disks",
                "chrome": "chromium",
                "google chrome": "chromium",
                "chromium": "chromium",
                "camera": "snapshot",
                "vscode": "code",
                "visual studio code": "code",
                "virtual studio code": "code",
                "vs code": "code",
                "code": "code",
                "firefox": "firefox",
            }

            _KNOWN_GUI_BINARIES = {
                "spotify", "discord", "thunderbird", "brave", "firefox",
                "chromium", "libreoffice", "gnome-calendar", "gnome-control-center",
                "nautilus", "gedit", "gnome-calculator", "gnome-terminal",
                "gnome-system-monitor", "rhythmbox", "eog", "gnome-screenshot",
                "obs", "baobab", "gnome-disks", "snapshot", "code", "codium",
                "kate", "subl", "idle",
            }

            def _desktop_entry_exists(app_label: str) -> bool:
                token = str(app_label or "").strip().lower()
                if not token:
                    return False
                from eli.utils.platform_compat import LINUX
                if not LINUX:
                    from eli.system.portable_app_control import resolve_app
                    return resolve_app(app_label).source != "unknown"

                from eli.system.portable_app_control import _linux_desktop_dirs
                for root in _linux_desktop_dirs():
                    try:
                        if root.exists():
                            for _ in root.glob(f"*{token}*.desktop"):
                                return True
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                return False

            # Web apps: open in browser rather than trying to install a package
            _WEB_APPS = {
                "gmail": "https://mail.google.com",
                "google mail": "https://mail.google.com",
                "netflix": "https://www.netflix.com",
                "youtube": "https://www.youtube.com",
                "google calendar": "https://calendar.google.com",
                "google drive": "https://drive.google.com",
                "google docs": "https://docs.google.com",
                "google sheets": "https://sheets.google.com",
                "whatsapp": "https://web.whatsapp.com",
                "spotify web": "https://open.spotify.com",
                "twitter": "https://www.twitter.com",
                "x": "https://www.x.com",
                "instagram": "https://www.instagram.com",
                "facebook": "https://www.facebook.com",
                "reddit": "https://www.reddit.com",
            }
            _web_url = _WEB_APPS.get(name.lower())
            if _web_url:
                # Cross-platform default-browser open (was raw xdg-open → Linux-only).
                from eli.utils.platform_compat import open_url as _open_url
                _open_url(_web_url)
                msg = f"Opening {name} in browser."
                return {"ok": True, "action": a, "content": msg, "response": msg}

            app = _APP_FUZZY.get(name.lower(), name)

            if re.search(r'[:\.,]', name) and len(name.split()) > 3:
                msg = "I can't perform multi-step commands like that. Please break it down."
                return {
                    "ok": False,
                    "action": a,
                    "error": "launch_failed",
                    "content": msg,
                    "response": msg,
                }

            launch_list = [str(x).strip() for x in candidates if str(x).strip()] or [app]
            if app not in launch_list:
                launch_list.insert(0, app)

            tries = []
            last_err = "launch_failed"

            for item in launch_list:
                cmd = [item]
                tries.append(cmd)
                probe = _open_app_with_timeout(cmd)

                gui_evidence = (
                    _desktop_entry_exists(item)
                    or _desktop_entry_exists(name)
                    or _desktop_entry_exists(app)
                    or item in _KNOWN_GUI_BINARIES
                )

                if (probe.get("ok") or probe.get("spawned")) and gui_evidence:
                    msg = f"Opened app: {app}"
                    return {
                        "ok": True,
                        "action": a,
                        "content": msg,
                        "response": msg,
                        "tries": tries,
                        "probe": probe,
                    }

                last_err = probe.get("stderr") or probe.get("error") or f"not_gui_or_not_launchable:{item}"

            try:
                from eli.runtime import grounded_remediation as _eli_gr
                _diag = _eli_gr.diagnose_app(app)
                if not _diag.get("ok", False):
                    return _eli_gr.as_executor_result(_eli_gr.offer_for_result(_diag), ok=False)
            except Exception as _gr_e:
                last_err = f"{last_err}; remediation_error={_gr_e}"

            msg = f"Could not open {app}."
            return {
                "ok": False,
                "action": a,
                "error": last_err,
                "content": msg,
                "response": msg,
                "tries": tries,
            }

        except Exception as e:
            return {
                "ok": False,
                "action": a,
                "error": str(e),
                "content": str(e),
                "response": str(e),
            }

    if a == "GET_PROPOSALS":
        # Surface ELI's own agenda: the proposal-only goals goal autogenesis forms from real signals
        # (recurring errors, frequent behaviours, code health) plus pending capability proposals.
        # This is what "do you have any proposals for me?" answers, live and not canned.
        try:
            lines: list[str] = []
            # A goal ELI would work on next asks first (eli/planning/goal_tick): those waiting for the
            # user's yes come first, each once, with what working on it would do.
            waiting: list = []
            try:
                from eli.planning.goal_tick import pending_goal_proposals
                waiting = list(pending_goal_proposals() or [])
            except Exception:
                log.debug("waiting goal proposals unread", exc_info=True)
            for w in waiting:
                _obj = str(w.get("objective") or "").strip()
                lines.append(f"• [waiting for your yes] {w.get('title') or '(goal)'}" + (f" — {_obj}" if _obj else "")
                             + (f" ({w['action_class']})" if w.get("action_class") else ""))
            _asked = {str(w.get("goal_id") or "") for w in waiting} - {""}
            try:
                from eli.planning.goal_store import load_goals
                goals = [
                    g for g in (load_goals() or [])
                    if getattr(g, "enabled", True)
                    and str(getattr(g, "goal_id", "")) not in _asked
                    and str(getattr(g, "status", "")).lower() in ("active", "proposed", "queued")
                    and str(getattr(g, "autonomy_mode", "")).lower() == "proposal_only"
                ]
                goals.sort(key=lambda g: float(getattr(g, "priority", 0.5) or 0.5), reverse=True)
                for g in goals:          # every one: the first eight were listed and the rest left unsaid
                    _obj = str(getattr(g, "objective", "") or "").strip()
                    lines.append(f"• {getattr(g, 'title', '(goal)')}" + (f" — {_obj}" if _obj else ""))
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            try:
                from eli.memory import get_memory as _gm
                for p in (_gm().get_pending_proposals(limit=5) or []):
                    if not isinstance(p, dict):
                        continue
                    _cap = str(p.get("capability") or p.get("name") or "").strip()
                    _rsn = str(p.get("reasoning") or p.get("description") or "").strip()
                    if _cap:
                        lines.append(f"• [capability] {_cap}" + (f" — {_rsn}" if _rsn else ""))
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            if lines:
                msg = "Proposals I've formed from what I've observed:\n" + "\n".join(lines)
            else:
                msg = ("No active proposals right now — I form them from your recurring "
                       "behaviour, recurring errors, and my own code health as signals build up.")
            # The goals waiting for the user's yes are kept open, so a yes to the list (or "the second
            # one") confirms them through the one consent reader (pending_proposal.goals_agreed, read
            # by the engine), which runs goal_tick.confirm_goal_proposal. Listed, they were not: a yes
            # was read as a new message.
            if waiting:
                try:
                    from eli.runtime.pending_proposal import offer_goal_proposals
                    offer_goal_proposals(waiting)
                except Exception:
                    log.debug("waiting goal proposals not kept open", exc_info=True)
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "proposal_count": len(lines), "waiting_goal_proposals": waiting,
                    "evidence_source": "proposals_runtime"}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    if a == "SCHEDULE_TASK":
        # Schedule a heavy/overnight task (code/research/self-upgrade/reflection)
        # to run at a parsed time and surface the result. See runtime.scheduled_tasks.
        req = str((args or {}).get("request") or (args or {}).get("text")
                  or (args or {}).get("message") or "").strip()
        when = str((args or {}).get("when") or req)
        kind = (args or {}).get("kind")
        if not req:
            msg = "What should I work on, and when? (e.g. 'research X overnight')."
            return {"ok": False, "action": a, "content": msg, "response": msg}
        # Recurring if the user said "every/each/daily/nightly" (so 'every morning'
        # repeats; a bare 'tomorrow' is one-shot).
        import re as _re_sched
        recurring = bool(_re_sched.search(r"\b(every|each|daily|nightly|recurring|each day)\b",
                                          f"{when} {req}", _re_sched.I))
        try:
            from eli.runtime.scheduled_tasks import schedule_request
            r = schedule_request(req, when_spec=when, kind=kind, recurring=recurring)
        except Exception as e:
            msg = f"Couldn't schedule that: {e}"
            return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}
        if not r.get("ok"):
            msg = f"Couldn't schedule that: {r.get('error')}"
            return {"ok": False, "action": a, "content": msg, "response": msg}
        msg = (f"Scheduled (job #{r['job_id']}, {r['kind']}{', recurring' if recurring else ''}): "
               f"I'll run this at {r['when_human']}"
               f"{' and each time after' if recurring else ''} and surface the result "
               f"in the Tasks/Proactive panel.")
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "job_id": r["job_id"], "kind": r["kind"], "when_ts": r["when_ts"]}


    # ---- MULTI_COMMAND — run several chained commands in order, combine results ----
    # 'close steam and set an alarm for 7am' → route + execute each segment. Sequential
    # (actions have side effects/order). Each segment is a single command, so no re-entry.
    if a == "MULTI_COMMAND":
        cmds = list((args or {}).get("commands") or [])
        if not cmds:
            # Incomplete input, not a system fault — ask, and mark fault=False so the
            # failure tracker doesn't log it as a recurring error (it isn't one).
            msg = "I didn't catch any commands in that — what would you like me to run?"
            return {"ok": True, "action": a, "fault": False, "needs_input": True,
                    "content": msg, "response": msg}
        from eli.execution.router_enhanced import route as _mc_route
        from eli.execution.command_sequence_log import start_or_resume, mark_step, finish
        from eli.execution.command_dependency_graph import infer_dependencies
        raw = str((args or {}).get("raw") or " | ".join(cmds))
        seq = start_or_resume(raw, cmds)
        seq_id, done_ok = seq["sequence_id"], seq["done_ok"]

        n = len(cmds)
        # Steps ELI listed and the user agreed to stand on their own; no model call to order them.
        deps = {} if (args or {}).get("results_only") else infer_dependencies(cmds)
        try:
            from eli.core.dag import build_dag
            g = build_dag({str(i): [str(d) for d in deps.get(i, [])] for i in range(n)})
            layers = [[int(nid) for nid in layer] for layer in g.topological_layers()]
        except Exception:
            layers = [list(range(n))]   # no real dependencies, or the graph didn't validate

        parts = [None] * n
        ok_flags = [i in done_ok for i in range(n)]
        all_ok = True
        for layer in layers:
            for i in sorted(layer):
                c = cmds[i]
                if i in done_ok:
                    parts[i] = done_ok[i] + "  (already done)"
                    continue
                blockers = [d for d in deps.get(i, []) if not ok_flags[d]]
                if blockers:
                    reason = ", ".join(str(d) for d in blockers)
                    parts[i] = f"• {c.strip()} →\nskipped — depends on step {reason} which failed"
                    all_ok = False
                    mark_step(seq_id, i, False, parts[i])
                    continue
                try:
                    sub = _mc_route(c)
                    sa = (sub.get("action") or "CHAT")
                    sargs = dict(sub.get("args") or {})
                    # Asked later why it played a song, ELI saw only PLAY_MEDIA and made up a reason.
                    from eli.kernel import request_context as _rc_step
                    _step_facts = _rc_step.turn_facts_var.get()
                    if _step_facts is not None:
                        _step_facts.setdefault("steps", []).append(
                            {"words": c, "action": sa, "via": str((sub.get("meta") or {}).get("matched_by") or "")})
                    sargs.setdefault("_raw_user_text", c)
                    if sa == "MULTI_COMMAND":          # guard against re-entry
                        sa, sargs = "CHAT", {"message": c}
                    r = execute(sa, sargs)
                    txt = (r.get("response") or r.get("content") or "") if isinstance(r, dict) else str(r)
                    # Done is what the step really did: a play that played nothing or a search
                    # that found nothing is not there for a step that depends on it.
                    from eli.runtime.evidence_ledger import execution_outcome as _step_outcome
                    ok = not (isinstance(r, dict) and (r.get("ok") is False or _step_outcome(r) in ("failed", "no_effect")))
                    if not ok:
                        all_ok = False
                except Exception as e:
                    txt, ok, all_ok = f"failed: {e}", False, False
                ok_flags[i] = ok
                # Steps ELI offered and the user agreed to: the outcome is what matters, not
                # ELI's own sentence read back.
                parts[i] = ((txt or "Done.").strip() if (args or {}).get("results_only")
                            else f"• {c.strip()} →\n{(txt or '(done)').strip()}")
                mark_step(seq_id, i, ok, parts[i])
        finish(seq_id, all_ok)
        msg = ("\n" if (args or {}).get("results_only") else "\n\n").join(parts)
        return {"ok": all_ok, "action": a, "content": msg, "response": msg, "of_steps": True,
                "meta": {"commands": cmds, "resumed": seq["resumed"], "dependencies": deps}}

    if a == "SET_COMMUNICATION_STYLE":
        return set_communication_style_action(str(args.get("style", "")))


    # ---- SELF_REPAIR_PLAYBOOK ----
    if a == "SELF_REPAIR_PLAYBOOK":
        try:
            from eli.runtime.repair_playbook import build_repair_playbook_report
            q = str((args or {}).get("question") or (args or {}).get("query") or "")
            report = build_repair_playbook_report(q, live=True)
            return {
                "ok": True,
                "action": a,
                "content": report,
                "response": report,
                "evidence_source": "repair_playbook",
            }
        except Exception as e:
            msg = f"Repair playbook unavailable: {e}"
            return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    # ---- GET_WEATHER ----

    _LOC_PATS = [
        re.compile(r"weather (?:in|at|for) ([A-Za-z][A-Za-z ,]+?)(?:\?|$|[,;])", re.I),
        re.compile(r"(?:in|at|for) ([A-Za-z][A-Za-z ,]+?) weather", re.I),
        re.compile(r"(?:temperature|forecast) (?:in|at) ([A-Za-z][A-Za-z ,]+?)(?:\?|$)", re.I),
    ]


    # ---- end GAZE ENGINE ----

    # ---- OPEN_IDE / OPEN_IN_IDE / SHOW_DIFF ----
    if a == "OPEN_IDE":
        target = str(
            (args or {}).get("name")
            or (args or {}).get("app")
            or (args or {}).get("target")
            or (args or {}).get("path")
            or (args or {}).get("text")
            or (args or {}).get("query")
            or ""
        ).strip().lower()

        generic = {
            "", "ide", "the ide", "editor", "the editor",
            "built in ide", "built-in ide", "gui ide", "eli ide",
            "internal ide", "ide tab", "the ide tab"
        }

        alias_map = {
            "vscode": "code",
            "visual studio code": "code",
            "virtual studio code": "code",
            "vs code": "code",
            "code": "code",
            "gedit": "gedit",
            "text editor": "gedit",
            "codium": "codium",
            "kate": "kate",
            "sublime": "subl",
            "sublime text": "subl",
            "idle": "idle"
        }

        if target not in generic:
            mapped = alias_map.get(target, target)
            return _execute_impl("OPEN_APP", {"name": mapped})

        for editor in ["code", "codium", "subl", "gedit", "kate"]:
            try:
                subprocess.Popen(
                    [editor],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                msg = f"Opened IDE: {editor}"
                return {"ok": True, "action": a, "content": msg, "response": msg}
            except FileNotFoundError:
                continue

        return {
            "ok": False,
            "action": a,
            "error": "No IDE found",
            "content": "No IDE found",
            "response": "No IDE found"
        }


    if a == "SEQUENCE":
        steps = args.get("steps") or []
        if not isinstance(steps, list):
            msg = "SEQUENCE steps must be a list."
            return {"ok": False, "action": a, "error": msg, "content": msg, "response": msg, "steps": []}

        results = []
        overall_ok = True

        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                msg = f"step {i} is not a dict"
                results.append({"ok": False, "action": "SEQUENCE_STEP", "error": msg, "content": msg, "response": msg})
                overall_ok = False
                continue

            sa = str(step.get("action") or "").strip()
            sargs = step.get("args") or {}

            try:
                r = _execute_impl(sa, sargs)
            except Exception as e:
                r = {"ok": False, "action": sa, "error": str(e), "content": str(e), "response": str(e)}

            if not isinstance(r, dict):
                r = {"ok": False, "action": sa, "error": "non-dict step result", "content": "non-dict step result", "response": "non-dict step result"}

            if r.get("ok") is not True:
                overall_ok = False
            results.append(r)

        # Report what each step DID, not just how many ran. "Executed 2 step(s)."
        # tells the user nothing about whether both folders actually opened —
        # the complaint that started this was a second target silently dropped.
        _step_msgs = []
        for r in results:
            _t = str(r.get("response") or r.get("content") or r.get("error") or "").strip()
            if _t and _t not in _step_msgs:
                _step_msgs.append(_t)
        if _step_msgs:
            msg = "\n".join(_step_msgs)
        else:
            msg = f"Executed {len(results)} step(s)." if overall_ok else f"Executed {len(results)} step(s) with failures."
        return {
            "ok": overall_ok,
            "action": a,
            "steps": results,
            "content": msg,
            "response": msg,
        }


    if a == 'EXECUTE_GOAL':
        goal = (args or {}).get('goal', '')
        try:
            from eli.runtime.pipeline_models import RouteDecision as _RD
            from eli.execution.execution_planner import build_execution_plan as _build_plan
            _plan = _build_plan(_RD(user_input=str(goal or ''), action='EXECUTE_GOAL'))
            _steps = [f"{i + 1}. {s.name} ({s.kind})" for i, s in enumerate(_plan.steps)]
            _content = (
                "Execution plan for goal:\n" + "\n".join(_steps)
                if _steps else "No plan steps produced for goal."
            )
            return {'ok': True, 'action': a, 'content': _content, 'response': _content, 'plan': _plan.to_dict()}
        except Exception as _exc:
            return {'ok': False, 'action': a, 'error': str(_exc), 'content': str(_exc), 'response': str(_exc)}

    # ---- LIST_VOICES ----
    # Enumerate installed voices + a summary of what's downloadable, so the voice
    # library (previously reachable only via Settings ▸ Voice) is answerable by voice.
    if a == "LIST_VOICES":
        try:
            from eli.perception.tts_router import list_voices, get_active_voice
            installed = [v for v in (list_voices() or []) if not str(v).startswith("sys:")]
            piper = [v for v in installed if not v.startswith(("char:", "clone:"))]
            char = [v.replace("char:", "") for v in installed if v.startswith("char:")]
            lines = [f"Active voice: {get_active_voice()}.",
                     f"Installed ({len(piper)}): " + (", ".join(piper[:24]) or "none")]
            if char:
                lines.append("Character voices: " + ", ".join(char))
            try:
                from eli.runtime.voice_assets import list_available_voices
                cat = list_available_voices()
                dl = [r for r in cat if not r["present"]]
                langs = sorted({r["language_name"] for r in cat if r.get("language_name")})
                en = [r for r in dl if str(r.get("language", "")).startswith("en")]
                if dl:
                    lines.append(
                        f"Downloadable: {len(dl)} more"
                        + (f" across {len(langs)} languages" if langs else "")
                        + (f" ({len(en)} English accents)" if en else "")
                        + ". Say e.g. \"download a British voice\", or open Settings \u25b8 Runtime \u25b8 \"VOICE / TTS\".")
            except Exception:
                log.debug("[LIST_VOICES] catalog summary failed", exc_info=True)
            msg = "\n".join(lines)
            return {"ok": True, "action": a, "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- DOWNLOAD_VOICE ----
    # Resolve a spoken request ("download a Scottish voice", "get en_GB-alan") to a
    # concrete voice, fetch it (md5-verified, netguard-gated) and make it active.
    if a == "DOWNLOAD_VOICE":
        try:
            q = (args.get("query") or args.get("voice") or args.get("text")
                 or args.get("name") or args.get("message") or "").strip()
            from eli.runtime.voice_assets import (
                resolve_voice_query, download_voice, list_available_voices)
            from eli.perception.tts_router import set_active_voice
            r = resolve_voice_query(q)
            vid = r.get("voice") or ""
            if vid.startswith("char:"):
                # Character presets are effect chains — nothing to fetch; just switch.
                set_active_voice(vid)
                try:
                    from eli.perception import voice_fx
                    spec = voice_fx.get_preset(vid) or {}
                    label = spec.get("label") or vid.replace("char:", "")
                    desc = (spec.get("desc") or "").strip()
                    msg = f"Switched to style preset «{label}» ({vid})."
                    if desc:
                        msg += f" {desc}"
                except Exception:
                    msg = f"Switched to style preset {vid}."
                return {"ok": True, "action": a, "voice": vid, "content": msg, "response": msg}
            if not vid:
                try:
                    sample = ", ".join(x["id"] for x in list_available_voices(language="en")[:6])
                except Exception:
                    sample = "en_US-amy-medium"
                msg = (f"I couldn't tell which voice you meant from \"{q}\". Try an accent or a "
                       f"name — e.g. \"download a British voice\". Options include: {sample}.")
                return {"ok": False, "action": a, "content": msg, "response": msg}
            res = download_voice(vid, mirror=True)
            if res.get("ok"):
                try:
                    set_active_voice(vid)
                except Exception:
                    log.debug("[DOWNLOAD_VOICE] set-active after fetch failed", exc_info=True)
                where = "already installed" if res.get("already_present") else "downloaded"
                note = (f" (picked {vid} for that accent)"
                        if r.get("kind") == "accent" and len(r.get("matches", [])) > 1 else "")
                msg = f"Voice {vid} {where} and set as the active voice{note}."
                return {"ok": True, "action": a, "voice": vid, "content": msg, "response": msg}
            msg = f"Couldn't download {vid}: {res.get('error') or 'download failed'}."
            return {"ok": False, "action": a, "voice": vid, "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- SET_VOICE ----
    # Switch the active TTS voice to an installed one (distinct from persona tone,
    # which is SET_COMMUNICATION_STYLE). Offers to download when it isn't installed.
    if a == "SET_VOICE":
        try:
            q = (args.get("query") or args.get("voice") or args.get("text")
                 or args.get("name") or args.get("message") or "").strip()
            from eli.runtime.voice_assets import resolve_voice_query
            from eli.perception.tts_router import list_voices, set_active_voice
            installed = set(list_voices() or [])
            r = resolve_voice_query(q)
            vid = r.get("voice") or ""
            # char: (effect chains) and natural: (neural, graceful Piper fallback)
            # are always selectable even when not in the installed .onnx list.
            if vid and vid not in installed and not vid.startswith(("char:", "natural:")):
                alt = next((m for m in r.get("matches", []) if m in installed), "")
                if alt:
                    vid = alt
                else:
                    msg = (f"I don't have {vid} installed yet — say \"download a {q}\" "
                           f"and I'll fetch it and switch to it.")
                    return {"ok": False, "action": a, "voice": vid, "content": msg, "response": msg}
            if not vid:
                msg = (f"I couldn't match \"{q}\" to a voice. Say \"list voices\" to see what's "
                       f"installed, or \"download a British voice\" to add one.")
                return {"ok": False, "action": a, "content": msg, "response": msg}
            set_active_voice(vid)
            label = vid.replace("char:", "").upper() if vid.startswith("char:") else vid
            msg = f"Voice set to {label}."
            return {"ok": True, "action": a, "voice": vid, "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # CREATE_VOICE clones a voice from a dropped-in recording (ffmpeg pulls the audio) and saves it
    # as clone:<name>. DESIGN_VOICE builds a named voice from a base plus pitch/speed effects
    # (char:<name>), which nothing could reach before.
    if a == "DESIGN_VOICE":
        try:
            from eli.perception import voice_fx, tts_router
            name = str(args.get("name") or args.get("voice") or "").strip()
            if not name:
                msg = ("Give the voice a name — e.g. \"design a voice called Nova, deep and "
                       "slow\". You can set base (any installed voice), pitch (semitones, "
                       "-6..+6), speed (0.5..1.5) and a description.")
                return {"ok": False, "action": a, "content": msg, "response": msg}
            installed = list(tts_router.list_voices())
            base = str(args.get("base") or "").strip()
            if base and base not in installed:
                msg = (f"'{base}' isn't installed. Available: " + ", ".join(installed[:12]) +
                       ("…" if len(installed) > 12 else ""))
                return {"ok": False, "action": a, "content": msg, "response": msg}
            spec = {"base": base or (installed[0] if installed else "en_US-amy-medium"),
                    "desc": str(args.get("desc") or args.get("description") or
                                f"Custom voice: {name}")}
            for k, lo, hi in (("pitch", -6.0, 6.0), ("speed", 0.5, 1.5)):
                if args.get(k) is not None:
                    try:
                        spec[k] = max(lo, min(hi, float(args.get(k))))
                    except (TypeError, ValueError):
                        log.debug("suppressed exception", exc_info=True)
            if args.get("filters"):
                spec["filters"] = str(args.get("filters"))
            res = voice_fx.save_preset(name, spec)
            if not res.get("ok"):
                msg = f"Could not save that voice: {res.get('error')}"
                return {"ok": False, "action": a, "content": msg, "response": msg}
            msg = (f"Made the voice '{res['name']}' ({spec['desc']}) on {spec['base']}"
                   + (f", pitch {spec['pitch']:+g}" if "pitch" in spec else "")
                   + (f", speed {spec['speed']:g}x" if "speed" in spec else "")
                   + f". Use it any time with: use the {res['name']} voice.")
            return {"ok": True, "action": a, "content": msg, "response": msg, "result": res}
        except Exception as e:
            msg = f"DESIGN_VOICE failed: {e}"
            return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    if a == "CREATE_VOICE":
        try:
            name = (args.get("name") or args.get("voice") or "").strip()
            src = (args.get("file") or args.get("path") or args.get("audio")
                   or args.get("source") or "").strip()
            if not src:
                # The UI path here must match the real GUI: the create-voice drop-zone is on the RUNTIME
                # settings page under the "VOICE / TTS" card (_build_settings_runtime_page). The old text named
                # a Settings > Voice page that doesn't exist. tests/test_create_voice_ui_path.py pins this.
                msg = ("To make a voice, give me an audio file of it — a .wav, .mp3 or .mp4 "
                       "(a short, clean ~6–20s clip works best). You can also drop the clip "
                       "straight into the app: Settings ▸ Runtime ▸ \"VOICE / TTS\", where it "
                       "says \"Drop an audio/video clip here, or click to browse…\".")
                return {"ok": False, "action": a, "content": msg, "response": msg}
            import os as _os
            src = _os.path.expanduser(src)
            if not _os.path.isfile(src):
                msg = f"I couldn't find that audio file: {src}"
                return {"ok": False, "action": a, "content": msg, "response": msg}
            if not name:
                name = _os.path.splitext(_os.path.basename(src))[0]
            from eli.perception import tts_xtts
            res = tts_xtts.add_clone(name, src)
            if not res.get("ok"):
                msg = f"Couldn't create that voice: {res.get('error')}"
                return {"ok": False, "action": a, "content": msg, "response": msg}
            vid = res.get("id") or ("clone:" + name)
            try:
                from eli.perception.tts_router import set_active_voice
                set_active_voice(vid)
            except Exception:
                log.debug("[CREATE_VOICE] set-active failed", exc_info=True)
            extra = ("" if res.get("synth_ready") else
                     " It'll sound like the sample once the neural voice extra is installed "
                     "(`pip install -e \".[natural]\"` from the ELI project root); until then "
                     "I'll use a normal voice.")
            msg = f"Created the voice '{name}' from your recording and set it active.{extra}"
            return {"ok": True, "action": a, "voice": vid, "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # SET_TONE: pin the emotion/register ELI expresses (words, voice, face). Distinct from SET_VOICE
    # (which voice) and SET_COMMUNICATION_STYLE (free-text persona); it picks a named tone from the
    # emotion palette ("be comedic", "talk street").
    if a == "SET_TONE":
        try:
            q = (args.get("tone") or args.get("style") or args.get("query")
                 or args.get("text") or args.get("message") or "").strip()
            from eli.cognition import tone_adaptor, emotion_palette
            res = tone_adaptor.set_tone(q)
            if res.get("ok"):
                msg = f"Tone set to {res['tone']} — {res.get('desc','')}. Say \"go back to normal\" to clear it."
                return {"ok": True, "action": a, "tone": res["tone"], "content": msg, "response": msg}
            sample = ", ".join(emotion_palette.list_tones()[:12])
            msg = (f"I couldn't match \"{q}\" to a tone. Try one like: {sample}… "
                   f"(or \"be comedic\", \"talk street\", \"go professional\").")
            return {"ok": False, "action": a, "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- CLEAR_TONE ----
    # Drop the tone override → back to autonomous, emotion-adaptive expression.
    if a == "CLEAR_TONE":
        try:
            from eli.cognition import tone_adaptor
            tone_adaptor.clear_tone()
            msg = "Back to my normal, adaptive tone — I'll read the room again."
            return {"ok": True, "action": a, "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # Last stop before "unsupported": an installed plugin may own this action. Checked late so a
    # plugin can never shadow a built-in (a listing declaring SHUTDOWN or SEND_EMAIL would take over
    # that verb for the whole assistant).
    try:
        from eli.plugins.manager import get_manager
        _plugin_result = get_manager().dispatch(a, args)
        if _plugin_result is not None:
            return _plugin_result
    except Exception:
        log.debug("[EXECUTOR] plugin dispatch failed", exc_info=True)

    return {"ok": False, "action": a, "error": f"Unsupported executor action: {a}", "content": f"Unsupported executor action: {a}", "response": f"Unsupported executor action: {a}"}


# ---------------------------------------------------------------------------
# Action dispatch hooks (added by phaseBW2 — replace 5 chained wrappers)
# ---------------------------------------------------------------------------

# Answered by the pre-dispatch below (grounded_remediation), not by an effector: what is declared of
# them is declared here (eli/execution/effectors/registry.py).
from eli.execution.effectors.registry import declare as _declare_action
_declare_action("CHECK_TARGET_STATUS", direct=True)
_declare_action("PREPARE_REMEDIATION", direct=True, offered=False)


def _action_pre_dispatch(
    action: str,
    args: "Optional[Dict[str, Any]]" = None,
    **kwargs,
) -> "Optional[Dict[str, Any]]":
    """
    Centralised pre-dispatch action overrides.

    Returns a result dict if the action is fully handled, or None to
    fall through to the main execute() dispatch.

    Replaces the chain of monkey-patch wrappers retired in phaseBW2:
      * GROUNDED EXECUTOR WRAPPER          (OPEN_APP/OPEN_IDE pre-flight,
                                            CHECK_TARGET_STATUS et al)
      * CONTROL_CONTRACT_EXECUTOR_WRAPPER  (SELF_UPDATE, EXPLAIN_LAST_RESPONSE)
    READ_FILE, SCREENSHOT, REFRESH_USER_INFO, PERSONA_REFRESH and USER_INFO_REPORT are their
    effectors' (files_notes, screen, memory_ops, persona_lock), answered on the recorded path: they
    were answered here, ahead of the registry, unnormalised, unverified and unrecorded, by copies of
    those effectors. The report of what ELI keeps on the user is the router's USER_INFO_REPORT
    (route_contracts.identity_question), not a MEMORY_RECALL turned into it here.
    """
    a = str(action or "").upper().strip()
    args = args or {}

    # Control contract evidence ------------------------------------------
    if a in {"SELF_UPDATE", "EXPLAIN_LAST_RESPONSE"}:
        try:
            from eli.runtime.control_contracts import build_control_evidence
            return build_control_evidence(None, a, args, str(args.get("query") or ""))
        except Exception as _e:
            _msg = f"Control evidence failed for {a}: {_e!r}"
            return {"ok": False, "action": a, "error": repr(_e),
                    "content": _msg, "response": _msg}

    # OPEN_APP / OPEN_IDE pre-flight via grounded_remediation ----------
    if a in {"OPEN_APP", "OPEN_IDE"}:
        try:
            from eli.runtime import grounded_remediation as _gr
        except Exception:
            return None
        _subject = (args.get("app") or args.get("name") or args.get("target")
                    or args.get("message") or args.get("query") or "")
        _app = _gr.extract_app_name(str(_subject))
        if _app:
            _diag = _gr.diagnose_app(_app)
            if not _diag.get("ok", False):
                return _gr.as_executor_result(_gr.offer_for_result(_diag), ok=False)
            if a == "OPEN_IDE":
                # Rewrite OPEN_IDE -> OPEN_APP and call _execute_impl
                # directly so we don't re-enter pre-dispatch.
                _open_args = dict(args)
                _open_args["name"] = _app
                _merged = dict(_open_args)
                _merged.update(kwargs or {})
                _result = _execute_impl(action="OPEN_APP", args=_merged)
                _result = _normalize_result(_result)
                _offer = _gr.capture_executor_failure("OPEN_APP", _open_args, _result)
                if _offer:
                    return _gr.as_executor_result(_offer, ok=False)
                return _result

    # CHECK_TARGET_STATUS and the pending repair -----------------------
    # (EXPLAIN_LAST_FAILURE is its effector's: effectors/introspection.py)
    if a in {"CHECK_TARGET_STATUS",
             "PREPARE_REMEDIATION", "CONFIRM_PENDING_REMEDIATION",
             "CANCEL_PENDING_REMEDIATION"}:
        try:
            from eli.runtime import grounded_remediation as _gr
        except Exception:
            return None
        _msg = args.get("message") or args.get("query") or a
        _handled = _gr.try_handle_query(str(_msg))
        if _handled:
            return _gr.as_executor_result(
                _handled, ok=not _handled.startswith("Could not"))

    return None


# Per-process failure tally for the "attempt N" anti-retry hint, keyed by action+args+error and
# reset each restart so a new session doesn't inherit a stale "attempt 4". The DB still logs
# failures for learning; this only governs the user-facing repeat hint.
_SESSION_FAILURE_COUNTS: "Dict[str, int]" = {}


_INDEXED_AFTER = frozenset((
    "READ_FILE", "SUMMARIZE_FILE", "ANALYZE_PDF", "CREATE_DOCUMENT", "DOC_GENERATE", "GENERATE_DOCUMENT",
    "CONVERT_DOCUMENT", "WRITE_NOTE", "NEW_NOTE",
))
_DOCUMENT_PATH_KEYS = ("path", "file", "output", "output_path", "saved_to", "pdf_path")


# Remediation's own confirm/cancel actions are terminal honest outcomes, never failures to learn from.
_REMEDIATION_ACTIONS = frozenset(("CONFIRM_PENDING_REMEDIATION", "CANCEL_PENDING_REMEDIATION"))


def _outcome_facts(action: str, result: "Dict[str, Any]", args: "Optional[Dict[str, Any]]" = None) -> "Dict[str, Any]":
    """The cause of a failed run and whether a run was checked, for the record it is learned from."""
    if not isinstance(result, dict):
        return {}
    out: Dict[str, Any] = {}
    if result.get("verified") is not None:
        out["verified"] = bool(result.get("verified"))
    if not result.get("ok", True) or result.get("effect") is False or result.get("played") is False:
        try:
            from eli.runtime.action_outcomes import cause_of
            out["cause"] = cause_of(result, action, args)
        except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
            log.debug("cause not classified", exc_info=True)
    return out


def _checked_result(action: str, args: "Optional[Dict[str, Any]]", result: "Dict[str, Any]",
                    started: float) -> "Dict[str, Any]":
    """A result that says it worked, checked against the world where that can be seen
    (runtime/action_outcomes.verify): a run that writes a file that is not there did not work, and
    says so; a search that brought nothing back had no effect. Both are recorded as such, so no reply
    and no reliability figure rests on a success that was only claimed. What cannot be seen is left
    as reported, marked unchecked."""
    if not isinstance(result, dict) or not result.get("ok", False):
        return result
    try:
        from eli.runtime.action_outcomes import verify
        verified, how = verify(action, args, result, started=started)
    except Exception:
        log.debug("result not checked", exc_info=True)
        return result
    if verified is None:
        return result
    result["verified"] = bool(verified)
    result["verified_how"] = how
    if verified:
        return result
    if "came back" in how:
        result["effect"] = False
    else:
        result["claimed"] = str(result.get("response") or result.get("content") or "")[:300]
        result["ok"] = False
        result["error"] = f"did not take effect: {how}"
        result["content"] = result["response"] = f"⚡ That did not take effect: {how}."
    return result


def _finish_run(action: str, args: "Optional[Dict[str, Any]]", result: Any, started: float) -> Any:
    """What every run goes through once it has come back, whichever layer answered it: checked
    against the world where that can be seen, recorded in the evidence ledger, and post-dispatched
    (its executor_action row, the turn's record of what ran, and what is learned of a failure)."""
    result = _normalize_result(result) if not isinstance(result, dict) else result
    result = _checked_result(action, args, result, started)
    try:
        _record_tool_execution(action, args, result)
    except Exception:
        log.debug("tool execution not recorded", exc_info=True)
    return _action_post_dispatch(action, args, result)


def _action_post_dispatch(
    action: str,
    args: "Optional[Dict[str, Any]]",
    result: "Dict[str, Any]",
) -> "Dict[str, Any]":
    """
    Apply post-dispatch failure capture: if the result indicates failure
    grounded_remediation may produce a remediation offer to surface to
    the user. Returns the capture result if any, else the original result.

    Replaces the post-execute capture from the GROUNDED EXECUTOR WRAPPER,
    retired in phaseBW2.
    """
    try:
        from eli.runtime.evidence_ledger import execution_outcome, record_event as _eli_record_event
        # Whose turn ran it, so a period log on a shared install shows each user their own.
        try:
            from eli.kernel import request_context as _rc_who
            _who = str(_rc_who.user_id_var.get() or "")
            _sess = str(_rc_who.session_id_var.get() or "")
        except Exception:
            _who = _sess = ""

        _eli_record_event(
            "executor_action",
            source="executor.post_dispatch",
            action=str(action or "").upper(),
            subject=str(
                (args or {}).get("path")
                or (args or {}).get("target")
                or (args or {}).get("name")
                or (args or {}).get("topic")
                or ""
            ),
            content=str((result or {}).get("content") or (result or {}).get("response") or (result or {}).get("error") or ""),
            payload={"args": args or {}, "result": result or {},
                     **(_outcome_facts(action, result, args))},
            severity="error" if execution_outcome(result) == "failed" else "info",
            outcome=execution_outcome(result),
            reusable=True,
            user_id=_who,
            session_id=_sess,
        )
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    # A document ELI has just read or written is kept whole in the document index; the reply
    # itself only ever saw its first 8000 characters.
    if str(action or "").upper() in _INDEXED_AFTER and bool((result or {}).get("ok", True)):
        try:
            from eli.kernel import request_context as _rc_doc
            from eli.memory.document_index import note_document
            _uid = str(_rc_doc.user_id_var.get() or "")
            _seen = {str((src or {}).get(key) or "") for src in (result, args) for key in _DOCUMENT_PATH_KEYS}
            for _doc in sorted(_seen - {""}):
                note_document(_doc, user_id=_uid, source=str(action).upper())
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    # An action that asked the user something ("When is it?", "Which one?") keeps the request
    # open, so the reply completes it instead of starting a new conversation. A handler says so
    # by returning `awaiting` ({"command", "action"}) and, for a choice, `choices`.
    if isinstance(result, dict) and (result.get("awaiting") or result.get("choices")):
        try:
            from eli.runtime.pending_proposal import set_pending_proposal
            _asked = re.findall(r"[^.!?\n]+\?", str(result.get("response") or result.get("content") or ""))
            set_pending_proposal("", items=list(result.get("choices") or []), awaiting=result.get("awaiting"),
                                 question=_asked[0].strip() if _asked else "")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    # The turn's audit row names what really ran: the agent bus can execute an action
    # and the turn still end as CHAT (live: PERSONA_LOCK_CLEAR ran, the row said CHAT).
    # A result that presented several things (headlines, search results, notes, files) also
    # says so with `listed` and `follow_up`; the reply that shows it keeps the listing open, so
    # "the second one" or "tell me more about X" goes further into it (runtime.pending_proposal).
    try:
        from eli.kernel import request_context as _rc_exec
        _turn = _rc_exec.turn_facts_var.get()
        if _turn is not None:
            _turn.setdefault("executed_actions", []).append(str(action or "").upper())
            # What each run said, so a claim about a state it left ("shuffle is off now") is held to
            # the result's own words (self_claims._state_on_record). `ok` is the ledger's reading of it.
            from eli.runtime.evidence_ledger import execution_outcome as _outcome_of
            _res = result if isinstance(result, dict) else {}
            _turn.setdefault("action_results", []).append({
                "action": str(action or "").upper(), "ok": _outcome_of(result) == "ok",
                "said": str(_res.get("response") or _res.get("content") or _res.get("error") or "")})
            # What the run resolved its target to (the folder found for "my project folder", the app
            # it opened): the engine keeps it with the last command, so "list it" means that place.
            if _outcome_of(result) == "ok":
                from eli.execution.route_contracts import TARGET_ARGS as _TARGETS
                _resolved = {k: _res[k] for k in _TARGETS if isinstance(_res.get(k), str) and _res[k].strip()}
                if _resolved:
                    _turn["acted_on"] = {"action": str(action or "").upper(), "args": _resolved}
            if isinstance(result, dict) and result.get("listed") and result.get("follow_up"):
                _turn["listed"] = {"action": str(action or "").upper(),
                                   "items": list(result["listed"]),
                                   "follow_up": dict(result["follow_up"])}
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    # ELI's world feels its tools (eli/world/world_event_bus.py, queued, never in the reply's way): a tool
    # that worked is work done, which eases the trouble a failure raised. Talking is not a tool. A
    # failure reaches the world below, where it is logged with what went wrong.
    if isinstance(result, dict) and result.get("ok") is True and str(action or "").upper() != "CHAT":
        from eli.world.shown import what_came_of
        from eli.world.world_event_bus import fire_tool_result_event
        fire_tool_result_event(str(action), True, said=what_came_of(str(action), dict(args or {}), result))

    repeat_hint = ""
    _remediation_actions = _REMEDIATION_ACTIONS
    # Non-bugs that poisoned the SI failure feed: expected responses (no query typed, plugin not
    # configured, action outside this build). They must not enter the failure-cluster pipeline the
    # SelfImprovementEngine acts on.
    _SI_NOISE_ERRORS = frozenset((
        "empty_query",
        "empty",
        "not_configured",
        "unsupported",
        "unsupported_action",
        "no_query",
        # Variants actually produced by executor actions (mixed-case / with spaces)
        "no query",
        "no query provided",
        # Security filter working as intended — not a bug
        "security_blocked",
    ))
    try:
        # Grounded-remediation exit-20 ("No grounded install candidate found") is
        # an honest terminal outcome, not a retryable failure. Don't log it as a
        # failure and don't append the "attempt N" hint.
        if isinstance(result, dict) and not bool(result.get("ok", True)):
            import json as _json

            # Remediation actions are terminal honest outcomes — skip failure logging.
            if str(action or "").upper() in _remediation_actions:
                return result

            raw_error = result.get("error")
            err_text = str(
                raw_error
                or result.get("message")
                or result.get("response")
                or result.get("content")
                or "executor failure"
            ).strip()

            # Quick reject for the canonical "this isn't a real bug" surfaces.
            err_token = str(raw_error or "").strip().lower()
            if err_token in _SI_NOISE_ERRORS:
                # Don't log to failures table; the post-dispatch path still
                # returns the result to the caller, just doesn't poison SI.
                return result

            from eli.world.world_event_bus import fire_tool_result_event
            fire_tool_result_event(str(action), False, detail=err_text)
            signature_input = f"{str(action or '').upper()} {_json.dumps(args or {}, sort_keys=True, default=str)}"
            # Failures live in one store, the agent/self-improvement DB (agent.sqlite3) next to
            # improvements and code_patches. Dual-writing to the user DB split the log; the
            # Self-Improve panel reads only the agent store.
            # The one record of an action failure, keyed by the component: the action and the names of
            # its arguments, never their values (a note's text or a search is the person's own words).
            # What it was run with stays in the context, where self-repair rebuilds the run from it.
            _record_key = (f"{str(action or '').upper()} ("
                           + ", ".join(sorted(str(k) for k in (args or {}) if not str(k).startswith("_"))) + ")")
            try:
                from eli.memory import get_agent_memory as _get_agent_memory
                _get_agent_memory().log_failure(
                    _record_key,
                    error=err_text,
                    confidence=0.0,
                    context={"action": action, "args": args or {}, "result": result},
                    source="executor_post_dispatch",
                )
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            # Count repeats in THIS running session only. The hint means "you
            # keep retrying the same thing right now" — not all-time DB history,
            # which falsely claimed "attempt 4" on a fresh session's first try.
            _sess_key = f"{signature_input}\x00{err_text}"
            occurrence_count = _SESSION_FAILURE_COUNTS.get(_sess_key, 0) + 1
            _SESSION_FAILURE_COUNTS[_sess_key] = occurrence_count
            if occurrence_count >= 2:
                subject = ""
                if isinstance(args, dict):
                    subject = str(
                        args.get("path")
                        or args.get("target")
                        or args.get("name")
                        or args.get("app")
                        or args.get("query")
                        or ""
                    ).strip()
                target = f" `{subject}`" if subject else ""
                repeat_hint = (
                    f"This is attempt {occurrence_count} for the same {str(action or '').upper()} failure{target}. "
                    "Do you want me to inspect the target, logs, or generated artifact instead of retrying the same command?"
                )
    except Exception:
        repeat_hint = ""

    # What ELI has learned of such failures (runtime/action_outcomes): the cause, how this has fared
    # here, and what worked instead. Only an action that does the same deed for this same thing by
    # other means, proven on it, writing and destroying nothing and likelier than another go, is tried
    # now, built from this request's own arguments; the reply keeps what the failed run itself said and
    # adds what was tried and what came of it. Otherwise the reply says what has worked. Either way the
    # failure is a thought, in the Debug Basement, with its reasons. A run made of steps (MULTI_COMMAND)
    # failed in its steps, and each step's own note already says so: it adds none of its own.
    advice_words = ""
    if (isinstance(result, dict) and not bool(result.get("ok", True)) and str(action or "").upper() not in _remediation_actions
            and not result.get("of_steps")):
        try:
            from eli.runtime import action_outcomes as _ao
            from eli.runtime.evidence_ledger import execution_outcome as _outcome
            _ao.forget()
            _advice = _ao.advice(str(action or "").upper(), args or {}, result)
            _attempt = _advice.get("attempt")
            _instead = execute(_attempt["action"], _attempt["args"]) if _attempt else None
            _worked = isinstance(_instead, dict) and _outcome(_instead) == "ok"
            advice_words = _ao.said(_advice, tried=_instead)
            try:
                from eli.cognition.thoughts import record as _thought
                from eli.kernel import request_context as _rc_t
                _rid = str(_rc_t.request_id_var.get() or "")
                _thought("action", f"{_ao.plain(action)}{(' for ' + _advice['subject']) if _advice.get('subject') else ''} failed",
                         why=[_advice["cause_words"], f"failed {_advice['failed_lately']} of its last {_advice['runs_lately']} runs here"
                              if _advice["runs_lately"] > 1 else "its first run here lately"],
                         chance=float(_advice["chance"]["p"]),
                         decision="tried" if _attempt and _worked else ("declined" if _attempt else ("advised" if _advice["alternatives"] else "told")),
                         reason=(f"{_ao.plain(_attempt['action'])} had worked instead for this target" if _attempt else
                                 (f"{_ao.plain(_advice['alternatives'][0]['action'])} has worked instead" if _advice["alternatives"]
                                  else "nothing has worked instead yet")),
                         subject=str(_advice.get("subject") or ""), request_id=_rid,
                         ref=f"action:{_rid}:{str(action or '').upper()}", user_id=str(_rc_t.user_id_var.get() or ""))
                if _attempt:
                    # what was tried is settled by what came of it; advice given is settled by the next turn
                    from eli.cognition.thoughts import settle as _settle_thought
                    _settle_thought(f"action:{_rid}:{str(action or '').upper()}", "done" if _worked else "wrong",
                                    how=f"{_ao.plain(_attempt['action'])} {'worked' if _worked else 'failed too'}")
            except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
                log.debug("action thought not kept", exc_info=True)
            if isinstance(_instead, dict):
                _own = str(result.get("response") or result.get("content") or result.get("error") or "").strip()
                _theirs = str(_instead.get("response") or _instead.get("content") or _instead.get("error") or "").strip()
                if _worked:
                    _instead = dict(_instead)
                    _instead["instead_of"] = {"action": str(action or "").upper(), "error": str(result.get("error") or ""),
                                              "cause": _advice["cause"], "response": _own}
                    _instead["response"] = _instead["content"] = "\n\n".join(t for t in (_own, advice_words, _theirs) if t)
                    return _instead
                result["alternative"] = {"action": _attempt["action"], "ok": False, "response": _theirs}
                advice_words = "\n\n".join(t for t in (advice_words, _theirs) if t)
        except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
            log.debug("learned alternatives skipped", exc_info=True)

    # a result that asks the user something (a clarifying question, a request for input) is the whole
    # reply: what has worked lately does not belong under a question
    if isinstance(result, dict) and (result.get("asks_user") or result.get("needs_input") or result.get("awaiting")):
        advice_words = ""
        repeat_hint = ""

    def _with_notes(text: str) -> str:
        return "\n\n".join(t for t in (str(text or "").strip(), advice_words, repeat_hint) if t).strip()

    try:
        from eli.runtime import grounded_remediation as _gr
    except Exception:
        if (repeat_hint or advice_words) and isinstance(result, dict):
            combined = _with_notes(result.get("content") or result.get("response") or result.get("error") or "")
            result["content"] = combined
            result["response"] = combined
        return result
    try:
        _offer = _gr.capture_executor_failure(action, args or {}, result)
        if _offer:
            return _gr.as_executor_result(_with_notes(_offer), ok=False)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    if (repeat_hint or advice_words) and isinstance(result, dict):
        combined = _with_notes(result.get("content") or result.get("response") or result.get("error") or "")
        result["content"] = combined
        result["response"] = combined
    return result


def _eli_open_url_action(raw_url=None, *, query=None):
    """
    Safe URL opener for OPEN_URL.

    Rules:
    - Accepts http/https URLs.
    - Accepts bare domains like github.com and normalizes to https://github.com.
    - Blocks unsupported schemes such as file:, javascript:, data:, ftp:, etc.
    - Uses xdg-open without shell=True.
    """
    from urllib.parse import urlparse, quote_plus
    import shutil
    import subprocess
    import webbrowser

    value = str(raw_url or "").strip()

    if not value and query:
        value = "https://duckduckgo.com/?q=" + quote_plus(str(query).strip())

    if not value:
        msg = "No URL was provided."
        return {
            "ok": False,
            "action": "OPEN_URL",
            "error": msg,
            "content": f"⚡ Could not open URL. {msg}",
            "response": f"⚡ Could not open URL. {msg}",
        }

    # Allow common bare domains: github.com, openai.com/docs, etc.
    if "://" not in value:
        if " " in value or "." not in value:
            msg = f"Not a valid URL or bare domain: {value!r}"
            return {
                "ok": False,
                "action": "OPEN_URL",
                "url": value,
                "error": msg,
                "content": f"⚡ Could not open URL. {msg}",
                "response": f"⚡ Could not open URL. {msg}",
            }
        value = "https://" + value

    parsed = urlparse(value)

    if parsed.scheme not in {"http", "https"}:
        msg = f"Blocked unsupported URL scheme: {parsed.scheme!r}"
        return {
            "ok": False,
            "action": "OPEN_URL",
            "url": value,
            "error": msg,
            "content": f"⚡ Could not open URL. {msg}",
            "response": f"⚡ Could not open URL. {msg}",
        }

    if not parsed.netloc:
        msg = f"URL is missing a domain: {value!r}"
        return {
            "ok": False,
            "action": "OPEN_URL",
            "url": value,
            "error": msg,
            "content": f"⚡ Could not open URL. {msg}",
            "response": f"⚡ Could not open URL. {msg}",
        }

    try:
        opener = shutil.which("xdg-open")
        if opener:
            subprocess.Popen(
                [opener, value],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        else:
            webbrowser.open(value, new=2)

        msg = f"Opened URL: {value}"
        return {
            "ok": True,
            "action": "OPEN_URL",
            "url": value,
            "content": msg,
            "response": msg,
        }

    except Exception as exc:
        msg = f"Failed to open URL: {exc!r}"
        return {
            "ok": False,
            "action": "OPEN_URL",
            "url": value,
            "error": msg,
            "content": f"⚡ Could not open URL. {msg}",
            "response": f"⚡ Could not open URL. {msg}",
        }


def execute(action: str, args: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:

    # Unwrap dict form: execute({"action": "X", "args": {...}})
    if isinstance(action, dict):
        args = action.get("args", args) or args
        action = action.get("action", "")
    action = action if isinstance(action, str) else ""
    a = action.upper().strip()

    # Pre-dispatch action overrides (formerly applied via monkey-patch wrappers). Every run, whichever
    # layer answers it, is checked, recorded and post-dispatched once, by the outermost execute
    # (_eli_recording_execute at the end of this module).
    _override = _action_pre_dispatch(action, args, **kwargs)
    if _override is not None:
        return _override
    # An action is its effector's alone (eli/execution/effectors, reached through _execute_impl's
    # registry lookup): eighteen status and audit actions were answered here, above it, fifteen from
    # copies that had drifted from their effectors and three with no effector at all
    # (tests/test_effector_dispatch_order.py).
    merged: Dict[str, Any] = {}
    if isinstance(args, dict):
        merged.update(args)
    merged.update(kwargs or {})
    res = _execute_impl(action=str(a), args=merged)
    __eli_ret = _normalize_result(res)
    # Normalize LIST_CAPABILITIES top-level content/response (pipeline expects table here)
    try:
        if isinstance(__eli_ret, dict) and __eli_ret.get('action') == 'LIST_CAPABILITIES':
            caps = __eli_ret.get('capabilities')
            if isinstance(caps, dict):
                table = (caps.get('content') or '')
                if table:
                    __eli_ret['content'] = table
                    __eli_ret['response'] = table
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return __eli_ret


# Alias for compatibility with tests
try:
    execute_action = execute
except Exception:
    log.debug("suppressed exception", exc_info=True)


_PACKAGE_MIRROR_TOPLEVEL = {
    "api", "brain", "controllers", "core", "gui", "integrations", "plugins", "tools", "utils"
}

def _resolve_existing_user_or_artifact_path(path: str) -> Path:
    """Resolve a user path, then recover common generated artifact locations."""
    raw = str(path or "").strip()
    resolved = Path(resolve_user_repo_path(raw))
    if resolved.exists():
        return resolved

    repo_root = Path(__file__).resolve().parents[2]
    expanded = Path(raw).expanduser()
    basename = expanded.name
    candidates = []

    if raw and not expanded.is_absolute():
        candidates.append((repo_root / raw).resolve(strict=False))
    if basename:
        for subdir in ("scripts", "documents"):
            artifact_dir = repo_root / "artifacts" / subdir
            candidates.append((artifact_dir / basename).resolve(strict=False))
            if not Path(basename).suffix:
                try:
                    candidates.extend(sorted(artifact_dir.glob(f"{basename}.*")))
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate.resolve(strict=False)
        except Exception:
            continue
    return resolved

def _resolve_legacy_eli_fs_path(path: str) -> Path:
    raw = str(path or "").strip()
    if not raw:
        return Path(".").resolve()

    expanded = Path(raw).expanduser()
    repo_root = Path(__file__).resolve().parents[2]  # project root
    legacy_root = Path.home() / "eli"

    if expanded == legacy_root:
        return (repo_root / "eli").resolve()

    try:
        rel = expanded.relative_to(legacy_root)
    except Exception:
        return expanded.resolve()

    if not rel.parts:
        return (repo_root / "eli").resolve()

    top = rel.parts[0]
    if top in _PACKAGE_MIRROR_TOPLEVEL:
        return (repo_root / "eli" / rel).resolve()

    return (repo_root / rel).resolve()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action")
    ap.add_argument("--args", default="{}")
    ns = ap.parse_args()
    print(json.dumps(execute(ns.action, json.loads(ns.args)), indent=2))



# ----------------------------
# Proactive daemon control (real PID management)
# ----------------------------
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
    # In-process daemon: the GUI runs it as a thread (no separate PID), so the PID check above
    # falsely reports STOPPED. Also honour the in-process signals: the singleton's running flag and
    # the named daemon thread.
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


class Executor:
    """Thin class wrapper around the functional execute() API."""

    def __init__(self):
        pass

    def execute(self, action: str, args: dict = None):
        return execute(action, args or {})

    def __call__(self, action: str, args: dict = None):
        return execute(action, args or {})


# What REFRESH_USER_INFO, PERSONA_REFRESH and USER_INFO_REPORT do; their effectors (memory_ops,
# persona_lock) answer the actions and call these.
def _eli_refresh_user_info(force=False, reason="manual"):
    from eli.cognition.user_info_builder import refresh_user_info
    out = refresh_user_info(force=bool(force), reason=str(reason or "manual"))
    return {
        "ok": True,
        "action": "REFRESH_USER_INFO",
        "content": out.get("summary") or "User info refreshed.",
        "artifacts": out,
    }

def _eli_persona_refresh(reason="manual"):
    """Purge the stale persona auto-overlay (dedup + prune noisy/stale entries)
    and rebuild the active user-info profile. Overlay hygiene only — no
    conversation or memory rows are deleted. Backs the conversational offer
    "purge the stale context and update the active profile" so that a user's
    "yes" actually performs it instead of ELI just talking about it."""
    hygiene = {"ok": True, "changed": False}
    try:
        from eli.cognition.persona_hygiene import clean_auto_persona
        hygiene = clean_auto_persona() or hygiene
    except Exception as e:
        hygiene = {"ok": False, "error": str(e), "changed": False}

    profile = {"ok": True, "summary": ""}
    try:
        from eli.cognition.user_info_builder import refresh_user_info
        profile = refresh_user_info(force=True, reason=str(reason or "manual")) or profile
    except Exception as e:
        profile = {"ok": False, "error": str(e), "summary": ""}

    purged = bool(hygiene.get("changed"))
    sections = hygiene.get("sections")
    refreshed = bool(profile.get("refreshed", True))
    parts = []
    if purged:
        parts.append(f"purged the stale persona overlay"
                     + (f" ({sections} sections)" if sections else ""))
    else:
        parts.append("persona overlay was already clean")
    parts.append("rebuilt the active profile" if refreshed
                 else "profile was already fresh")
    content = "Done — " + " and ".join(parts) + "."
    return {
        "ok": bool(hygiene.get("ok", True) and profile.get("ok", True)),
        "action": "PERSONA_REFRESH",
        "content": content,
        "artifacts": {"hygiene": hygiene, "profile": profile},
    }

def _eli_user_info_report(force=False, reason="query"):
    from eli.cognition.user_info_builder import read_user_info, refresh_user_info
    if force:
        refresh_user_info(force=True, reason=str(reason or "query"))
    out = read_user_info(auto_refresh=True, reason=str(reason or "query"))
    return {
        "ok": True,
        "action": "USER_INFO_REPORT",
        "content": out.get("text", ""),
        "meta": out.get("meta", {}),
        "path": out.get("path", ""),
    }


# Local-only YouTube playback. No browser search. No external API.

# Canonical media runtime interceptor.
# Keep routing in router_enhanced/portable_intent_contract.py.
# Keep execution-side media behaviour in eli.execution.media_runtime.
try:
    from eli.execution.media_runtime import install_media_executor as _install_media_executor
    execute_action = _install_media_executor(execute_action)
    execute = execute_action  # late wrappers delegate to `execute`, not `execute_action`
except Exception as _media_runtime_error:
    log.debug(f"[MEDIA_RUNTIME] failed to install media executor wrapper: {_media_runtime_error}")


# gui_schema_contract_guard
try:
    _ELI_SCHEMA_CONTRACT_ORIGINAL_EXECUTE
except NameError:
    _ELI_SCHEMA_CONTRACT_ORIGINAL_EXECUTE = execute

    def _eli_schema_project_root():
        env_root = os.environ.get("ELI_PROJECT_ROOT")
        if env_root:
            return Path(env_root).expanduser().resolve()
        return Path(__file__).resolve().parents[2]

    def _eli_schema_artifacts_dir():
        root = _eli_schema_project_root()
        env_artifacts = os.environ.get("ELI_ARTIFACTS_DIR")
        if env_artifacts:
            candidate = Path(env_artifacts).expanduser()
            return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        return (root / "artifacts").resolve()

    def _eli_schema_slug(value, default="document"):
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
        return slug or default

    def _eli_schema_create_document(args):
        args = args if isinstance(args, dict) else {}
        topic = args.get("topic") or args.get("title") or args.get("name") or "document"
        title = args.get("title") or args.get("name") or topic
        fmt = str(args.get("format") or args.get("ext") or "md").lower().lstrip(".")
        if fmt not in {"md", "txt", "json", "csv", "tex", "py"}:
            fmt = "md"

        content = (
            args.get("content")
            or args.get("body")
            or args.get("text")
            or args.get("markdown")
        )
        if content is None:
            return _eli_schema_failure(
                "CREATE_DOCUMENT",
                "Document write refused: no substantive body was supplied. "
                "Use GENERATE_DOCUMENT/CREATE_DOCUMENT with a topic for model drafting, "
                "or WRITE_DOCUMENT with explicit content.",
            )

        out_dir = _eli_schema_artifacts_dir() / "documents"
        out_dir.mkdir(parents=True, exist_ok=True)

        filename = _eli_schema_slug(title, "document")
        if not filename.lower().endswith(f".{fmt}"):
            filename = f"{filename}.{fmt}"

        path = out_dir / filename
        if path.exists() and not bool(args.get("overwrite", False)):
            stem = path.stem
            suffix = path.suffix
            idx = 2
            while True:
                candidate = out_dir / f"{stem}_{idx}{suffix}"
                if not candidate.exists():
                    path = candidate
                    break
                idx += 1

        path.write_text(str(content), encoding="utf-8")
        msg = f"Document saved: {path}"
        return {
            "ok": True,
            "action": "CREATE_DOCUMENT",
            "doc_path": str(path),
            "path": str(path),
            "content": msg,
            "response": msg,
        }

    def _eli_schema_failure(action_name, message):
        return {
            "ok": False,
            "action": action_name,
            "error": str(message),
            "content": str(message),
            "response": str(message),
            "evidence": [str(message)],
        }

    def _eli_schema_normalise_failure(action_name, result):
        if not isinstance(result, dict):
            return result
        if bool(result.get("ok", True)):
            return result
        if result.get("error"):
            return result
        for key in ("content", "response", "message"):
            value = result.get(key)
            if value:
                result["error"] = str(value)
                return result
        evidence = result.get("evidence")
        if isinstance(evidence, (list, tuple)) and evidence:
            result["error"] = str(evidence[0])
            return result
        result["error"] = f"{action_name} failed"
        return result

    def execute(action, args=None, *pargs, **kwargs):
        action_name = str(action or "").strip().upper().replace("-", "_")
        call_args = args if isinstance(args, dict) else {}

        if action_name in {"CREATE_DOCUMENT", "CREATE_DOC", "WRITE_DOCUMENT"}:
            has_explicit_body = any(
                isinstance(call_args.get(key), str) and call_args.get(key).strip()
                for key in ("content", "body", "text", "markdown")
            )
            if has_explicit_body:
                return _eli_schema_create_document(call_args)
            result = _ELI_SCHEMA_CONTRACT_ORIGINAL_EXECUTE(action, args, *pargs, **kwargs)
            return _eli_schema_normalise_failure(action_name, result)

        result = _ELI_SCHEMA_CONTRACT_ORIGINAL_EXECUTE(action, args, *pargs, **kwargs)
        return _eli_schema_normalise_failure(action_name, result)

# voice_runtime_executor_contract_guard
# Stable execution support for direct voice rules. Uses commands discovered
# through PATH and platform APIs; no absolute user-machine paths.

try:
    _ELI_VOICE_CONTRACT_ORIG_EXECUTE
except NameError:
    _ELI_VOICE_CONTRACT_ORIG_EXECUTE = globals().get("execute")
    _ELI_VOICE_CONTRACT_ORIG_EXECUTE_ACTION = globals().get("execute_action")

    def _eli_voice_contract_action_name(action):
        return str(action or "").strip().upper().replace("-", "_")

    def _eli_voice_contract_arg_text(args, *keys):
        if not isinstance(args, dict):
            return ""
        for key in keys:
            val = args.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        return ""

    def _eli_voice_contract_open_settings():
        import os
        import platform
        import shutil
        import subprocess

        system = platform.system().lower()

        if system == "linux":
            candidates = (
                "gnome-control-center",
                "systemsettings",
                "xfce4-settings-manager",
                "mate-control-center",
                "cinnamon-settings",
            )
            for cmd in candidates:
                resolved = shutil.which(cmd)
                if resolved:
                    subprocess.Popen(
                        [resolved],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return {
                        "ok": True,
                        "action": "OPEN_SYSTEM_SETTINGS",
                        "content": f"Opened system settings: {cmd}",
                        "response": f"Opened system settings: {cmd}",
                    }

        if system == "darwin":
            subprocess.Popen(
                ["open", "-b", "com.apple.systempreferences"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {
                "ok": True,
                "action": "OPEN_SYSTEM_SETTINGS",
                "content": "Opened system settings.",
                "response": "Opened system settings.",
            }

        if system == "windows":
            try:
                os.startfile("ms-settings:")
                return {
                    "ok": True,
                    "action": "OPEN_SYSTEM_SETTINGS",
                    "content": "Opened system settings.",
                    "response": "Opened system settings.",
                }
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        return {
            "ok": False,
            "action": "OPEN_SYSTEM_SETTINGS",
            "error": "No supported system settings launcher was found on PATH.",
            "content": "No supported system settings launcher was found on PATH.",
            "response": "No supported system settings launcher was found on PATH.",
        }

    def _eli_voice_contract_set_volume(percent):
        from eli.utils import platform_compat as _pc

        level = max(0, min(100, int(percent)))
        if _pc.set_volume(level):
            return {
                "ok": True,
                "action": "VOLUME",
                "content": f"Volume set to {level}%",
                "response": f"Volume set to {level}%",
            }

        if _pc.LINUX:
            import shutil
            import subprocess

            pactl = shutil.which("pactl")
            if pactl:
                subprocess.run(
                    [pactl, "set-sink-mute", "@DEFAULT_SINK@", "0"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                rc = subprocess.run(
                    [pactl, "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                if rc == 0:
                    return {
                        "ok": True,
                        "action": "VOLUME",
                        "content": f"Volume set to {level}%",
                        "response": f"Volume set to {level}%",
                    }

            amixer = shutil.which("amixer")
            if amixer:
                rc = subprocess.run(
                    [amixer, "-D", "pulse", "sset", "Master", f"{level}%"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                if rc == 0:
                    return {
                        "ok": True,
                        "action": "VOLUME",
                        "content": f"Volume set to {level}%",
                        "response": f"Volume set to {level}%",
                    }

        return None

    def _eli_voice_contract_direct_result(action, args):
        action_name = _eli_voice_contract_action_name(action)
        args = args if isinstance(args, dict) else {}

        if action_name in {"NOOP", "SAY", "DIRECT_RESPONSE", "ANSWER"}:
            msg = (
                args.get("message")
                or args.get("response")
                or args.get("content")
                or ""
            )
            return {
                "ok": True,
                "action": action_name,
                "content": str(msg),
                "response": str(msg),
            }

        app_name = _eli_voice_contract_arg_text(args, "name", "app", "target", "application").lower()
        if action_name == "OPEN_SYSTEM_SETTINGS" or (
            action_name in {"OPEN_APP", "OPEN_APPLICATION", "LAUNCH_APP"}
            and app_name in {"settings", "system settings", "gnome settings"}
        ):
            return _eli_voice_contract_open_settings()

        if action_name == "VOLUME":
            level = args.get("level", args.get("percent", args.get("value")))
            if level is not None:
                try:
                    result = _eli_voice_contract_set_volume(int(level))
                    if result is not None:
                        return result
                except Exception as exc:
                    return {
                        "ok": False,
                        "action": "VOLUME",
                        "error": f"Volume set failed: {exc}",
                        "content": f"Volume set failed: {exc}",
                        "response": f"Volume set failed: {exc}",
                    }

        return None

    if callable(_ELI_VOICE_CONTRACT_ORIG_EXECUTE):
        def execute(action, args=None, *pargs, **kwargs):
            direct = _eli_voice_contract_direct_result(action, args)
            if direct is not None:
                return direct
            return _ELI_VOICE_CONTRACT_ORIG_EXECUTE(action, args, *pargs, **kwargs)

    if callable(_ELI_VOICE_CONTRACT_ORIG_EXECUTE_ACTION):
        def execute_action(action, args=None, *pargs, **kwargs):
            direct = _eli_voice_contract_direct_result(action, args)
            if direct is not None:
                return direct
            return _ELI_VOICE_CONTRACT_ORIG_EXECUTE_ACTION(action, args, *pargs, **kwargs)

# portable_runtime_contract_v3_executor_hook
try:
    _ELI_PORTABLE_V3_ORIG_EXECUTE
except NameError:
    _ELI_PORTABLE_V3_ORIG_EXECUTE = globals().get("execute")
    _ELI_PORTABLE_V3_ORIG_EXECUTE_ACTION = globals().get("execute_action")

    def _eli_v3_action_name(action):
        return str(action or "").strip().upper().replace("-", "_")

    def _eli_v3_args(args):
        return args if isinstance(args, dict) else {}

    def _eli_v3_direct_system_action(action, args=None):
        action_name = _eli_v3_action_name(action)
        data = _eli_v3_args(args)

        if action_name in {"OPEN_APP", "LAUNCH_APP", "OPEN_APPLICATION"}:
            from eli.system.portable_app_control import open_app
            _r = open_app(data.get("name") or data.get("target") or data.get("app") or "")
            # Only short-circuit on success; failures fall through to _execute_impl
            # so grounded_remediation can offer the install/download dialogue.
            if isinstance(_r, dict) and _r.get("ok"):
                return _r
            return None

        if action_name in {"CLOSE_APP", "QUIT_APP", "EXIT_APP", "CLOSE_APPLICATION"}:
            # A site is a browser tab (the router says so with `tab` and `site`): closed as a tab, never
            # by closing the browser's window or ending its process, which took every other tab too.
            if data.get("tab") and (data.get("site") or data.get("name")):
                from eli.system.portable_app_control import close_tab
                return close_tab(str(data.get("site") or data.get("name")))
            from eli.system.portable_app_control import close_app
            _r = close_app(
                data.get("name") or data.get("target") or data.get("app") or "",
                force=bool(data.get("force", False)),
            )
            # `handled`: close_app reached the app and has the answer (still open after being asked,
            # or running where only a signal would end it). Falling through, the effector signalled it.
            if isinstance(_r, dict) and (_r.get("ok") or _r.get("handled")):
                return _r
            return None

        if action_name in {
            "MINIMIZE_APP", "MINIMISE_APP", "HIDE_APP",
            "MINIMIZE_WINDOW", "MINIMISE_WINDOW",
        }:
            from eli.system.portable_app_control import minimize_app
            _r = minimize_app(data.get("name") or data.get("target") or data.get("app") or "")
            if isinstance(_r, dict) and _r.get("ok"):
                return _r
            return None

        # Maximise was missing from this table while minimise was there, so they drifted. Only a named
        # target is handled here; a bare "maximise" means the active window and belongs to the executor
        # branch. Existing capability name only, aliases here would add capabilities and stale every
        # documented count.
        if action_name == "MAXIMISE_WINDOW":
            from eli.system.portable_app_control import BARE_WINDOW_TARGETS as _BWT
            _mx_target = (data.get("name") or data.get("target") or data.get("app")
                          or data.get("window_name") or data.get("window") or "")
            # A deictic target is not a named window; it must reach the
            # executor's active-window branch, not maximize_app.
            if str(_mx_target).strip() and str(_mx_target).strip().lower() not in _BWT:
                from eli.system.portable_app_control import maximize_app
                _r = maximize_app(str(_mx_target).strip())
                if isinstance(_r, dict) and _r.get("ok"):
                    return _r
            return None

        return None

    if callable(_ELI_PORTABLE_V3_ORIG_EXECUTE):
        def execute(action, args=None, *pargs, **kwargs):
            direct = _eli_v3_direct_system_action(action, args)
            if direct is not None:
                return direct

            return _ELI_PORTABLE_V3_ORIG_EXECUTE(action, args, *pargs, **kwargs)

    if callable(_ELI_PORTABLE_V3_ORIG_EXECUTE_ACTION):
        def execute_action(action, args=None, *pargs, **kwargs):
            direct = _eli_v3_direct_system_action(action, args)
            if direct is not None:
                return direct

            return _ELI_PORTABLE_V3_ORIG_EXECUTE_ACTION(action, args, *pargs, **kwargs)


# ELI_REASONING_MODE_EXECUTOR_FIX_20260505
_ELI_REASONING_MODE_ORIG_EXECUTE = globals().get("execute")
_ELI_REASONING_MODE_ORIG_EXECUTE_ACTION = globals().get("execute_action")

def _eli_reasoning_mode_execute(action, args=None, *pargs, **kwargs):
    action_name = str(action or "").upper()
    if action_name == "REASONING_MODE_STATUS":
        try:
            from eli.runtime.reasoning_status import current_reasoning_mode_text
            msg = current_reasoning_mode_text()
        except Exception:
            msg = "Current reasoning mode: Quick"
        return {"ok": True, "action": "REASONING_MODE_STATUS", "content": msg, "response": msg}

    orig = _ELI_REASONING_MODE_ORIG_EXECUTE
    if callable(orig):
        return orig(action, args or {}, *pargs, **kwargs)

    orig_action = _ELI_REASONING_MODE_ORIG_EXECUTE_ACTION
    if callable(orig_action):
        return orig_action(action, args or {}, *pargs, **kwargs)

    return {"ok": False, "action": action_name, "content": "Executor unavailable.", "response": "Executor unavailable."}

try:
    execute = _eli_reasoning_mode_execute
    execute_action = _eli_reasoning_mode_execute
except Exception:
    log.debug("suppressed exception", exc_info=True)


# ELI_TILE_WINDOWS_PORTABLE_FIX — chain to prior wrapper after TILE_WINDOWS override.
_ELI_SECOND_ORIG_EXECUTE = globals().get("execute")
_ELI_SECOND_ORIG_EXECUTE_ACTION = globals().get("execute_action")


def _eli_second_execute(action, args=None, *pargs, **kwargs):
    action_name = str(action or "").upper()
    args = args or {}

    if action_name == "REASONING_MODE_STATUS":
        try:
            from eli.runtime.reasoning_status import current_reasoning_mode_text
            msg = current_reasoning_mode_text()
        except Exception as _rm_err:
            msg = "Couldn't read the current reasoning mode."
            return {"ok": False, "action": "REASONING_MODE_STATUS", "error": str(_rm_err), "content": msg,
                    "response": msg}
        return {"ok": True, "action": "REASONING_MODE_STATUS", "content": msg, "response": msg}

    if action_name == "TILE_WINDOWS":
        from eli.system.portable_app_control import tile_windows
        out = tile_windows()
        out.setdefault("action", action_name)
        return out

    orig = _ELI_SECOND_ORIG_EXECUTE
    if callable(orig) and orig is not _eli_second_execute:
        return orig(action, args, *pargs, **kwargs)
    orig_action = _ELI_SECOND_ORIG_EXECUTE_ACTION
    if callable(orig_action) and orig_action is not _eli_second_execute:
        return orig_action(action, args, *pargs, **kwargs)

    msg = f"No executor available for {action_name}"
    return {"ok": False, "action": action_name, "content": msg, "response": msg}

try:
    execute = _eli_second_execute
    execute_action = _eli_second_execute
except Exception:
    log.debug("suppressed exception", exc_info=True)


# ELI_PERSONAL_MEMORY_MODE_AWARE_EXECUTOR_FIX_20260505
# Must remain late in file so this execute wrapper wins over previous wrappers.
_ELI_PM_ORIG_EXECUTE = globals().get("execute")
_ELI_PM_ORIG_EXECUTE_ACTION = globals().get("execute_action")

def _eli_pm_execute(action, args=None, *pargs, **kwargs):
    action_name = str(action or "").upper()
    args = args or {}

    if action_name in {"PERSONAL_MEMORY_DEEP_EXPLAIN", "PERSONAL_MEMORY_SUMMARY"}:
        try:
            from eli.runtime.personal_memory_deep_response import build_personal_memory_deep_response
            try:
                from eli.runtime.reasoning_status import current_reasoning_mode_label
                mode_label = current_reasoning_mode_label()
            except Exception:
                mode_label = ""
            msg = build_personal_memory_deep_response(str(args.get("question") or ""), mode_label=mode_label)
        except Exception as e:
            msg = f"Personal memory deep response failed: {type(e).__name__}: {e}"
        return {
            "ok": True,
            "action": action_name,
            "content": msg,
            "response": msg,
            "evidence_source": "personal_memory_sqlite",
            "response_contract": "quick_direct_nonquick_persona_synthesis",
            "generation_invoked": False,
        }

    if action_name == "ROUTING_FAULT_EXPLAIN":
        try:
            from eli.runtime.personal_memory_deep_response import build_routing_fault_explanation
            msg = build_routing_fault_explanation(str(args.get("question") or ""))
        except Exception as e:
            msg = f"Routing fault explanation failed: {type(e).__name__}: {e}"
        return {
            "ok": True,
            "action": "ROUTING_FAULT_EXPLAIN",
            "content": msg,
            "response": msg,
            "evidence_source": "routing_trace",
            "response_contract": "quick_direct_nonquick_persona_synthesis",
            "generation_invoked": False,
        }

    if action_name == "NAME_SOURCE_AUDIT":
        try:
            from eli.runtime.user_visible_response_surface import format_name_source_audit
            msg = format_name_source_audit(str(args.get("question") or ""))
        except Exception as e:
            msg = f"Name-source audit failed: {type(e).__name__}: {e}"
        return {
            "ok": True,
            "action": "NAME_SOURCE_AUDIT",
            "content": msg,
            "response": msg,
            "evidence_source": "local_identity_sources",
            "response_contract": "quick_direct_nonquick_persona_synthesis",
            "generation_invoked": False,
        }

    if callable(_ELI_PM_ORIG_EXECUTE):
        return _ELI_PM_ORIG_EXECUTE(action, args, *pargs, **kwargs)
    if callable(_ELI_PM_ORIG_EXECUTE_ACTION):
        return _ELI_PM_ORIG_EXECUTE_ACTION(action, args, *pargs, **kwargs)

    msg = f"No executor available for {action_name}"
    return {"ok": False, "action": action_name, "content": msg, "response": msg}

try:
    execute = _eli_pm_execute
    execute_action = _eli_pm_execute
except Exception:
    log.debug("suppressed exception", exc_info=True)


# --- ELI generated-script safety installer ---
try:
    from eli.runtime.generated_script_guard import install as _eli_install_generated_script_guard
    _eli_install_generated_script_guard(globals())
    log.debug("[EXECUTOR] generated-script safety wrapper installed")
except Exception as _eli_gen_guard_err:
    log.debug(f"[EXECUTOR] generated-script guard install failed: {_eli_gen_guard_err}")
# --- end ELI generated-script safety installer ---

# Identity-only executor contract (ELI_IDENTITY_ONLY_EXECUTOR_CONTRACT): keeps USER_IDENTITY_SUMMARY
# from leaking preferences, working style or project memory into name/identity questions. No
# hardcoded user names.
try:
    def _eli_identity_only_name():
        try:
            from eli.kernel.state import get_user_name
            name = str(get_user_name("") or "").strip()
            if name:
                return name
            # Only the active user's own confirmed name answers "who am I". Answering never stores
            # one: the learned patterns are one table for every user of the install (no user_id),
            # and a summary line such as "the cat is called X" is filed there as a name.
            return ""
        except Exception:
            return ""

    def _eli_identity_only_active_user_id():
        try:
            from eli.kernel.state import get_active_user_id
            return str(get_active_user_id() or "").strip()
        except Exception:
            return ""

    def _eli_identity_only_response(args):
        args = args or {}
        scope = str(args.get("identity_scope") or "identity_only").strip().lower()
        name = _eli_identity_only_name()
        uid = _eli_identity_only_active_user_id()

        lines = []

        if scope == "name_only":
            if name:
                lines.append(f"Confirmed active-user name: {name}")
            else:
                lines.append("No confirmed name is stored for the active user.")
            if uid:
                lines.append(f"Active user ID: {uid}")
            return "\n".join(lines)

        if scope == "memory_presence_only":
            lines.append("Yes, I have memory records associated with the active user profile.")
            if name:
                lines.append(f"Confirmed active-user name: {name}")
            else:
                lines.append("No confirmed name is stored for the active user.")
            if uid:
                lines.append(f"Active user ID: {uid}")
            lines.append("I am not dumping preferences here because this is an identity/presence question, not a profile-summary request.")
            return "\n".join(lines)

        # identity_only
        if name:
            lines.append(f"Confirmed active-user identity: {name}")
        else:
            lines.append("No confirmed name/identity label is stored for the active user.")
        if uid:
            lines.append(f"Active user ID: {uid}")
        lines.append("Profile preferences are intentionally excluded from this identity-only answer.")
        return "\n".join(lines)

    # identity-only wrapper removed — mw_identity_only in the canonical
    # middleware table now calls _eli_identity_only_response() directly.
    pass
except Exception as _eli_identity_only_err:
    log.debug(f"[EXECUTOR] identity-only helpers failed: {_eli_identity_only_err}")
# =============================================================================

# Makes PERSONAL_MEMORY_SUMMARY mode-aware: inventory_only is just the category counts,
# preferences_detail the preference/working-style content, full_profile the whole active-user
# snapshot. Also strips identity/name chatter out of EXPLAIN_MEMORY_RUNTIME.
try:
    def _eli_profile_scope_active_user_id():
        try:
            from eli.kernel.state import get_active_user_id
            return str(get_active_user_id() or "").strip()
        except Exception:
            return ""

    def _eli_profile_scope_user_info_text(reason="profile_scope_contract"):
        try:
            from eli.cognition.user_info_builder import refresh_user_info
            res = refresh_user_info(force=True, reason=reason)
            return str(res.get("text") or "")
        except Exception as exc:
            return f""

    def _eli_profile_scope_sections(text):
        import re as _re
        sections = {}
        current = None
        for raw in str(text or "").splitlines():
            line = raw.strip()
            m = _re.fullmatch(r"\[([^\]]+)\]", line)
            if m:
                current = m.group(1).strip()
                sections.setdefault(current, [])
                continue
            if current and line.startswith("- "):
                item = line[2:].strip()
                if item and item.lower() != "none confirmed.":
                    sections.setdefault(current, []).append(item)
        return sections

    def _eli_profile_scope_inventory_answer(question):
        uid = _eli_profile_scope_active_user_id()
        text = _eli_profile_scope_user_info_text("profile_inventory_only")
        sections = _eli_profile_scope_sections(text)

        interesting = [
            "Identity",
            "Communication Preferences",
            "Working Style",
            "Active Projects",
            "Technical Environment",
            "Constraints / Avoidances",
            "Recent Significant Changes",
            "Uncertain / Needs Confirmation",
        ]

        lines = [
            "Active-user profile memory inventory:",
            f"- active_user_id: {uid or 'unknown'}",
        ]

        identity_count = len(sections.get("Identity", []))
        lines.append(f"- confirmed_identity_items: {identity_count}")

        stored = []
        for name in interesting:
            n = len(sections.get(name, []))
            if n:
                stored.append(f"{name}({n})")

        lines.append("- stored_profile_categories: " + (", ".join(stored) if stored else "none"))
        lines.append("- detail_policy: preference/project details are withheld unless explicitly requested.")
        lines.append("- ask_for_details: use 'show my preferences' or 'dump my full profile memory'.")

        return "\n".join(lines)

    def _eli_profile_scope_preferences_answer(question):
        uid = _eli_profile_scope_active_user_id()
        text = _eli_profile_scope_user_info_text("profile_preferences_detail")
        sections = _eli_profile_scope_sections(text)

        prefs = []
        prefs.extend(sections.get("Communication Preferences", []))
        prefs.extend(sections.get("Working Style", []))
        prefs.extend(sections.get("Constraints / Avoidances", []))

        lines = [
            "Stored preference / working-style facts for active user:",
            f"- active_user_id: {uid or 'unknown'}",
        ]

        if not prefs:
            lines.append("- None confirmed.")
        else:
            for item in prefs[:40]:
                lines.append(f"- {item}")

        return "\n".join(lines)

    def _eli_profile_scope_full_answer(question):
        uid = _eli_profile_scope_active_user_id()
        text = _eli_profile_scope_user_info_text("profile_full_detail")
        sections = _eli_profile_scope_sections(text)

        ordered = [
            "Identity",
            "Communication Preferences",
            "Working Style",
            "Active Projects",
            "Technical Environment",
            "Constraints / Avoidances",
            "Recent Significant Changes",
            "Uncertain / Needs Confirmation",
        ]

        lines = [
            "Full active-user profile memory snapshot:",
            f"- active_user_id: {uid or 'unknown'}",
            "",
        ]

        for name in ordered:
            lines.append(f"[{name}]")
            vals = sections.get(name, [])
            if not vals:
                lines.append("- None confirmed.")
            else:
                for item in vals[:60]:
                    lines.append(f"- {item}")
            lines.append("")

        return "\n".join(lines).rstrip()

    def _eli_profile_scope_infer(question, explicit_scope=""):
        import re as _re
        low = _re.sub(r"\s+", " ", str(question or "").strip().lower())

        if explicit_scope:
            return explicit_scope

        if _re.search(r"\b(show|list|summari[sz]e|tell me|what are|display|read)\b.{0,80}\b(my|stored|profile)?\s*preferences\b", low):
            return "preferences_detail"

        if _re.search(r"\b(dump|show|print|read|display)\b.{0,80}\b(full|complete|entire|all)\b.{0,80}\b(profile|personal memory|memory profile)\b", low):
            return "full_profile"

        if low in {
            "what do you know about me",
            "what do you remember about me",
            "what do you remember of me",
            "do you know me",
        }:
            return "inventory_only"

        return ""

    def _eli_profile_scope_execute_answer(action_name, args):
        args = args if isinstance(args, dict) else {}
        question = str(args.get("question") or args.get("query") or args.get("text") or "")
        scope = _eli_profile_scope_infer(question, str(args.get("profile_scope") or ""))

        if scope == "inventory_only":
            text = _eli_profile_scope_inventory_answer(question)
        elif scope == "preferences_detail":
            text = _eli_profile_scope_preferences_answer(question)
        elif scope == "full_profile":
            text = _eli_profile_scope_full_answer(question)
        else:
            return None

        return {
            "ok": True,
            "action": str(action_name or "").upper(),
            "content": text,
            "response": text,
            "evidence_source": "active_user_profile_scope",
                "report": {
                    "profile_scope": str(((locals().get("real_args") or locals().get("args") or {}) or {}).get("profile_scope") or "unknown"),
                    "active_user_scoped": True,
                },
            "profile_scope": scope,
        }

    def _eli_profile_scope_distinct_sessions():
        try:
            import sqlite3
            from pathlib import Path
            from eli.core.paths import get_paths

            uid = _eli_profile_scope_active_user_id()
            db = Path(get_paths().artifacts_dir) / "db" / "user.sqlite3"
            if not db.exists():
                return None

            con = sqlite3.connect(str(db))
            try:
                cols = {r[1] for r in con.execute("PRAGMA table_info(conversation_turns)").fetchall()}
                if "session_id" not in cols:
                    return None

                where = "WHERE COALESCE(session_id,'') <> ''"
                params = []
                if uid and "user_id" in cols:
                    where += " AND COALESCE(user_id,'') = ?"
                    params.append(uid)

                row = con.execute(
                    f"SELECT COUNT(DISTINCT session_id) FROM conversation_turns {where}",
                    params,
                ).fetchone()
                return int(row[0] or 0)
            finally:
                con.close()
        except Exception:
            return None

    def _eli_profile_scope_clean_memory_runtime_text(text):
        import re as _re
        lines = []
        for raw in str(text or "").splitlines():
            low = raw.lower()
            if "strong enough name signal" in low:
                continue
            if "identify you by name" in low:
                continue
            if "confirmed name" in low and "memory runtime" not in low:
                continue
            lines.append(raw)

        cleaned = "\n".join(lines).lstrip()
        n = _eli_profile_scope_distinct_sessions()
        if n is not None:
            cleaned = _re.sub(r"- distinct_sessions:\s*\d+", f"- distinct_sessions: {n}", cleaned)

        return cleaned

    # profile-scope wrapper removed — mw_profile_scope in the canonical middleware
    # table now calls _eli_profile_scope_execute_answer() and
    # _eli_profile_scope_clean_memory_runtime_text() directly.
    pass
except Exception as _eli_profile_scope_exec_err:
    log.debug(f"[EXECUTOR] profile memory scope contract failed: {_eli_profile_scope_exec_err}")
# =============================================================================

# Memory runtime report sanitizer: EXPLAIN_MEMORY_RUNTIME is architecture/runtime evidence only, so
# no identity guesses, profile or preference facts, or noisy "Session context" identity hits in its
# metadata.
try:
    def _eli_memory_runtime_count_distinct_sessions(report):
        try:
            import sqlite3
            from pathlib import Path

            paths = report.get("paths") if isinstance(report, dict) else {}
            status = report.get("status") if isinstance(report, dict) else {}

            db_path = (
                (status or {}).get("db_path")
                or (paths or {}).get("memory_db")
                or (paths or {}).get("user_db")
                or (paths or {}).get("active_db")
            )
            if not db_path:
                return None

            db = Path(str(db_path)).expanduser()
            if not db.exists():
                return None

            con = sqlite3.connect(str(db))
            try:
                cur = con.cursor()
                tables = {
                    str(r[0])
                    for r in cur.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }

                if "conversation_turns" in tables:
                    row = cur.execute(
                        """
                        SELECT COUNT(DISTINCT session_id)
                        FROM conversation_turns
                        WHERE COALESCE(session_id,'') <> ''
                        """
                    ).fetchone()
                    return int(row[0] or 0)

                if "conversations" in tables:
                    row = cur.execute(
                        """
                        SELECT COUNT(DISTINCT session_id)
                        FROM conversations
                        WHERE COALESCE(session_id,'') <> ''
                        """
                    ).fetchone()
                    return int(row[0] or 0)

                return None
            finally:
                con.close()
        except Exception:
            return None


    def _eli_sanitize_memory_runtime_output(out):
        if not isinstance(out, dict):
            return out

        report = out.get("report")
        if not isinstance(report, dict):
            return out

        # Remove identity/profile inference from memory-runtime metadata.
        for key in (
            "name_guess",
            "identity_hits",
            "identity",
            "stored_name",
            "fact_candidates",
            "profile_scope",
            "personal_memory_summary",
        ):
            report.pop(key, None)

        status = report.get("status")
        if isinstance(status, dict):
            fixed_sessions = _eli_memory_runtime_count_distinct_sessions(report)
            if fixed_sessions is not None:
                status["distinct_sessions"] = fixed_sessions
                report["status"] = status

                text = str(out.get("content") or out.get("response") or "")
                text = re.sub(
                    r"(?m)^- distinct_sessions:\s*\d+\s*$",
                    f"- distinct_sessions: {fixed_sessions}",
                    text,
                )
                out["content"] = text
                out["response"] = text

        # Defensive visible-output scrub.
        text = str(out.get("content") or out.get("response") or "")
        bad_fragments = (
            "strong enough name signal",
            "identity_hits",
            "name_guess",
            "Session context:",
        )
        if any(x.lower() in text.lower() for x in bad_fragments):
            clean_lines = []
            for line in text.splitlines():
                low = line.lower()
                if "strong enough name signal" in low:
                    continue
                if "identity_hits" in low:
                    continue
                if "name_guess" in low:
                    continue
                if "session context:" in low:
                    continue
                clean_lines.append(line)
            text = "\n".join(clean_lines).strip()
            out["content"] = text
            out["response"] = text

        out["report"] = report
        out["evidence_source"] = "memory_runtime_sanitized"
        return out


    # memory-runtime sanitizer wrapper removed — mw_memory_runtime_sanitizer in
    # the canonical middleware table now calls _eli_sanitize_memory_runtime_output().
    pass

except Exception as _eli_memory_runtime_sanitizer_err:
    log.debug(f"[EXECUTOR] memory-runtime report sanitizer failed: {_eli_memory_runtime_sanitizer_err}")
# =============================================================================

# Memory count compact evidence provider: structured evidence only; non-quick final wording belongs
# to cognition.
try:
    def _eli_memory_count_is_requested(args):
        import re
        if not isinstance(args, dict):
            return False
        if str(args.get("memory_scope") or "").strip().lower() == "count_only":
            return True
        q = str(args.get("question") or args.get("query") or "").lower()
        return bool(re.search(r"\b(how many|number of|count)\b.{0,80}\b(memories|memory entries|stored memories|memory rows)\b", q))

    def _eli_memory_count_path():
        from pathlib import Path
        try:
            from eli.core.paths import user_db_path
            return Path(user_db_path())
        except Exception:
            try:
                from eli.core.paths import get_paths
                return Path(get_paths().artifacts_dir) / "db" / "user.sqlite3"
            except Exception:
                return Path.cwd() / "artifacts" / "db" / "user.sqlite3"

    def _eli_table_exists(conn, table):
        try:
            return bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
                (table,),
            ).fetchone())
        except Exception:
            return False

    def _eli_count(conn, table):
        try:
            if not _eli_table_exists(conn, table):
                return 0
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
        except Exception:
            return 0

    def _eli_distinct_sessions(conn):
        try:
            if _eli_table_exists(conn, "conversation_turns"):
                cols = [str(r[1]) for r in conn.execute("PRAGMA table_info(conversation_turns)").fetchall()]
                if "session_id" in cols:
                    n = int(conn.execute(
                        "SELECT COUNT(DISTINCT session_id) FROM conversation_turns WHERE COALESCE(session_id,'') <> ''"
                    ).fetchone()[0] or 0)
                    if n:
                        return n
            if _eli_table_exists(conn, "conversations"):
                return _eli_count(conn, "conversations")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return 0

    def _eli_faiss_count():
        try:
            import faiss
            from pathlib import Path
            try:
                from eli.core.paths import data_dir
                idx = Path(data_dir()) / "vectors" / "index.faiss"
            except Exception:
                root = Path(__file__).resolve().parents[2]
                idx = root / "artifacts" / "vectors" / "index.faiss"
            if not idx.exists():
                return 0
            return int(getattr(faiss.read_index(str(idx)), "ntotal", 0) or 0)
        except Exception:
            return 0

    def _eli_memory_count_evidence():
        import sqlite3

        db = _eli_memory_count_path()
        counts = {
            "long_term_memory_rows": 0,
            "memory_fts_rows": 0,
            "faiss_vector_entries": _eli_faiss_count(),
            "conversation_turns": 0,
            "conversation_records": 0,
            "distinct_sessions": 0,
            "learning_replay_rows": 0,
            "observations": 0,
            "user_patterns": 0,
            "recall_log_rows": 0,
        }

        if db.exists():
            con = sqlite3.connect(str(db))
            try:
                counts["long_term_memory_rows"] = _eli_count(con, "memories")
                counts["memory_fts_rows"] = _eli_count(con, "memories_fts")
                counts["conversation_turns"] = _eli_count(con, "conversation_turns")
                counts["conversation_records"] = _eli_count(con, "conversations")
                counts["distinct_sessions"] = _eli_distinct_sessions(con)
                counts["learning_replay_rows"] = _eli_count(con, "learning_replay")
                counts["observations"] = _eli_count(con, "observations")
                counts["user_patterns"] = _eli_count(con, "user_patterns")
                counts["recall_log_rows"] = _eli_count(con, "recall_log")
            finally:
                con.close()

        evidence = (
            "MEMORY_COUNT_EVIDENCE\n"
            f"db_path: {db}\n"
            f"long_term_memory_rows: {counts['long_term_memory_rows']}\n"
            f"memory_fts_rows: {counts['memory_fts_rows']}\n"
            f"faiss_vector_entries: {counts['faiss_vector_entries']}\n"
            f"conversation_turns: {counts['conversation_turns']}\n"
            f"conversation_records: {counts['conversation_records']}\n"
            f"distinct_sessions: {counts['distinct_sessions']}\n"
            f"learning_replay_rows: {counts['learning_replay_rows']}\n"
            f"observations: {counts['observations']}\n"
            f"user_patterns: {counts['user_patterns']}\n"
            f"recall_log_rows: {counts['recall_log_rows']}\n"
            "answer_contract: The direct answer to 'how many memories' is long_term_memory_rows. "
            "Mention related retrieval/conversation counts only as separate supporting counts. "
            "Do not invent any number not present in this evidence."
        )

        return {
            "ok": True,
            "action": "MEMORY_STATUS",
            "content": evidence,
            "response": evidence,
            "evidence_source": "memory_count_compact_sqlite_evidence",
            "report": {
                "ok": True,
                "memory_scope": "count_only",
                "db_path": str(db),
                "counts": counts,
                "requires_grounded_synthesis": True,
                "requires_output_validation": True,
                "quick_direct_allowed": True,
            },
        }

    # memory-count wrapper removed — mw_memory_count in the canonical middleware
    # table now intercepts and calls _eli_memory_count_evidence() directly.
    pass
except Exception as _err:
    log.debug(f"[EXECUTOR] memory-count compact evidence provider failed: {_err}")

# Recent memory processing evidence provider: MEMORY_STATUS with memory_scope=recent_processing
# returns compact SQLite-backed evidence, and no invented "I was processing equations" unless the DB
# says so.
try:
    import json as _eli_recent_mem_json
    import re as _eli_recent_mem_re
    import sqlite3 as _eli_recent_mem_sqlite
    from pathlib import Path as _eli_recent_mem_Path

    def _eli_recent_mem_paths() -> dict:
        out = {}
        try:
            from eli.core.paths import get_paths
            paths = get_paths()
            root = _eli_recent_mem_Path(getattr(paths, "root", "") or _eli_recent_mem_Path.cwd())
            artifacts = _eli_recent_mem_Path(getattr(paths, "artifacts_dir", "") or (root / "artifacts"))
        except Exception:
            root = _eli_recent_mem_Path.cwd()
            artifacts = root / "artifacts"

        out["root"] = root
        out["user_db"] = artifacts / "db" / "user.sqlite3"
        out["agent_db"] = artifacts / "db" / "agent.sqlite3"
        out["faiss_index"] = artifacts / "vectors" / "index.faiss"
        out["faiss_meta"] = artifacts / "vectors" / "meta.pkl"
        return out

    def _eli_recent_mem_columns(conn, table: str) -> set[str]:
        try:
            return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            return set()

    def _eli_recent_mem_table_count(conn, table: str) -> int:
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
        except Exception:
            return 0

    def _eli_recent_mem_vector_count(paths: dict) -> int:
        try:
            import pickle
            meta = paths.get("faiss_meta")
            if meta and _eli_recent_mem_Path(meta).exists():
                with open(meta, "rb") as f:
                    data = pickle.load(f)
                if isinstance(data, dict):
                    for k in ("rows", "items", "metadata", "ids"):
                        v = data.get(k)
                        if isinstance(v, (list, tuple, dict)):
                            return len(v)
                if isinstance(data, (list, tuple, dict)):
                    return len(data)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        try:
            import faiss
            index = paths.get("faiss_index")
            if index and _eli_recent_mem_Path(index).exists():
                return int(faiss.read_index(str(index)).ntotal)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        return 0

    def _eli_recent_mem_clean_text(value: object, limit: int = 220) -> str:
        txt = str(value or "").replace("\x00", " ")
        txt = _eli_recent_mem_re.sub(r"\s+", " ", txt).strip()
        if not txt:
            return ""

        # Avoid dumping JSON blobs raw.
        if txt.startswith("{") and txt.endswith("}"):
            try:
                obj = _eli_recent_mem_json.loads(txt)
                if isinstance(obj, dict):
                    for key in ("summary", "content", "text", "event", "action", "response", "message"):
                        if obj.get(key):
                            txt = str(obj.get(key))
                            break
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        txt = _eli_recent_mem_re.sub(r"\s+", " ", txt).strip()
        if len(txt) > limit:
            txt = txt[: limit - 1].rstrip() + "…"
        return txt

    def _eli_recent_mem_is_noise(txt: str) -> bool:
        low = str(txt or "").lower().strip()
        if not low:
            return True

        noisy_prefixes = (
            "session context:",
            "reflection (24h):",
            "runtime status:",
            "personal memory summary",
            "capability inventory updated:",
            "generated script:",
            "script generated:",
            "proactive daemon started",
            "path not found:",
        )
        if any(low.startswith(x) for x in noisy_prefixes):
            return True

        noisy_contains = (
            "req-000001",
            "sqlite tables observed live",
            "memory_entries:",
            "identity_evidence:",
            "unsupported executor action",
            "name 'q' is not defined",
        )
        if any(x in low for x in noisy_contains):
            return True

        return False

    def _eli_recent_mem_recent_rows(conn, table: str, *, limit: int = 6) -> list[dict]:
        cols = _eli_recent_mem_columns(conn, table)
        if not cols:
            return []

        text_candidates = [
            "content", "summary", "text", "memory", "observation", "event",
            "details", "message", "value", "response", "input", "output",
        ]
        time_candidates = [
            "timestamp", "ts", "created_at", "updated_at", "last_seen_at", "ended_at", "started_at",
        ]

        text_cols = [c for c in text_candidates if c in cols]
        if not text_cols:
            return []

        time_col = next((c for c in time_candidates if c in cols), "")
        select_cols = []
        if "id" in cols:
            select_cols.append("id")
        if time_col:
            select_cols.append(time_col)
        select_cols.extend(text_cols[:4])

        order = f"{time_col} DESC" if time_col else "rowid DESC"

        try:
            rows = conn.execute(
                f"SELECT {', '.join(select_cols)} FROM {table} ORDER BY {order} LIMIT ?",
                (max(limit * 4, 20),),
            ).fetchall()
        except Exception:
            return []

        out = []
        for row in rows:
            d = dict(row)
            parts = []
            for c in text_cols[:4]:
                val = _eli_recent_mem_clean_text(d.get(c), limit=220)
                if val:
                    parts.append(val)

            text = " | ".join(dict.fromkeys(parts))
            text = _eli_recent_mem_clean_text(text, limit=260)
            if _eli_recent_mem_is_noise(text):
                continue

            item = {
                "table": table,
                "text": text,
            }
            if time_col and d.get(time_col):
                item["time"] = str(d.get(time_col))
            if "id" in d and d.get("id") is not None:
                item["id"] = d.get("id")
            out.append(item)
            if len(out) >= limit:
                break

        return out

    def _eli_recent_memory_processing_report(question: str = "") -> dict:
        paths = _eli_recent_mem_paths()
        user_db = paths["user_db"]

        counts = {
            "long_term_memories": 0,
            "memory_fts_rows": 0,
            "faiss_vectors": _eli_recent_mem_vector_count(paths),
            "conversation_turns": 0,
            "conversation_records": 0,
            "learning_replay_rows": 0,
            "observations": 0,
            "runtime_events": 0,
            "user_patterns": 0,
        }

        recent = {
            "memories": [],
            "observations": [],
            "learning_replay": [],
            "runtime_events": [],
        }

        if not user_db.exists():
            return {
                "ok": False,
                "question": question,
                "db_path": str(user_db),
                "counts": counts,
                "recent": recent,
                "problems": [f"user DB missing: {user_db}"],
            }

        try:
            conn = _eli_recent_mem_sqlite.connect(str(user_db))
            conn.row_factory = _eli_recent_mem_sqlite.Row
            try:
                counts["long_term_memories"] = _eli_recent_mem_table_count(conn, "memories")
                counts["memory_fts_rows"] = _eli_recent_mem_table_count(conn, "memories_fts")
                counts["conversation_turns"] = _eli_recent_mem_table_count(conn, "conversation_turns")
                counts["conversation_records"] = _eli_recent_mem_table_count(conn, "conversations")
                counts["learning_replay_rows"] = _eli_recent_mem_table_count(conn, "learning_replay")
                counts["observations"] = _eli_recent_mem_table_count(conn, "observations")
                counts["runtime_events"] = _eli_recent_mem_table_count(conn, "runtime_events")
                counts["user_patterns"] = _eli_recent_mem_table_count(conn, "user_patterns")

                recent["memories"] = _eli_recent_mem_recent_rows(conn, "memories", limit=5)
                recent["observations"] = _eli_recent_mem_recent_rows(conn, "observations", limit=5)
                recent["learning_replay"] = _eli_recent_mem_recent_rows(conn, "learning_replay", limit=5)
                recent["runtime_events"] = _eli_recent_mem_recent_rows(conn, "runtime_events", limit=5)
            finally:
                conn.close()
        except Exception as e:
            return {
                "ok": False,
                "question": question,
                "db_path": str(user_db),
                "counts": counts,
                "recent": recent,
                "problems": [repr(e)],
            }

        return {
            "ok": True,
            "question": question,
            "db_path": str(user_db),
            "counts": counts,
            "recent": recent,
            "policy": {
                "must_not_claim": [
                    "Do not claim recent mathematical-equation processing unless present in recent DB rows.",
                    "Do not invent emotional or experiential memory activity.",
                    "Use only the counts and rows in this report.",
                ],
            },
        }

    def _eli_recent_memory_processing_content(report: dict) -> str:
        counts = report.get("counts") or {}
        recent = report.get("recent") or {}

        lines = [
            "Recent memory-processing evidence:",
            f"- long_term_memories: {counts.get('long_term_memories', 0)}",
            f"- memory_fts_rows: {counts.get('memory_fts_rows', 0)}",
            f"- faiss_vectors: {counts.get('faiss_vectors', 0)}",
            f"- conversation_turns: {counts.get('conversation_turns', 0)}",
            f"- conversation_records: {counts.get('conversation_records', 0)}",
            f"- learning_replay_rows: {counts.get('learning_replay_rows', 0)}",
            f"- observations: {counts.get('observations', 0)}",
            "",
        ]

        emitted = False
        for label, key in [
            ("Recent durable memories", "memories"),
            ("Recent observations", "observations"),
            ("Recent learning replay", "learning_replay"),
            ("Recent runtime events", "runtime_events"),
        ]:
            rows = list(recent.get(key) or [])
            if not rows:
                continue
            emitted = True
            lines.append(f"{label}:")
            for r in rows[:5]:
                prefix = f"[{r.get('time')}] " if r.get("time") else ""
                lines.append(f"- {prefix}{r.get('text')}")
            lines.append("")

        if not emitted:
            lines.append("No clean recent memory rows were found after filtering runtime noise.")
            lines.append("")

        lines.append("Grounding rule: if a detail is not listed above, ELI must not claim it was recently processing it.")
        return "\n".join(lines).rstrip()

    # recent-memory-processing wrapper removed — mw_recent_memory_processing in
    # the canonical middleware table now calls _eli_recent_memory_processing_report()
    # and _eli_recent_memory_processing_content() directly.
    pass

except Exception as _eli_recent_memory_exec_err:
    log.debug(f"[EXECUTOR] recent-memory-processing provider install failed: {_eli_recent_memory_exec_err}")

# Second pass on the recent-memory-processing cleanup. The first provider was too loose and
# surfaced runtime echoes, the current prompt, model-not-ready messages and old hallucinated
# answers. This wrapper scrubs the report before user-visible synthesis.
try:
    def _eli_recent_mem_v2_bad_runtime_text(text: object, question: object = "") -> bool:
        low = str(text or "").strip().lower()
        qlow = str(question or "").strip().lower()

        if not low:
            return True

        # Current prompt echoes are not memory-processing evidence.
        if qlow and low == qlow:
            return True
        if qlow and qlow[:80] and qlow[:80] in low:
            return True

        bad = [
            "complex mathematical equations",
            "area under a curve",
            "linear equations",
            "digital hoarder",
            "filled to the brim",
            "favorite memories",
            "i don't have personal experiences or memories",
            "i do not have personal experiences or memories",
            "model not ready",
            "failed to create llama_context",
            "failed to load model from file",
            "recent memory-processing evidence:",
            "grounding rule:",
            "a diagnostic evidence packet was produced",
            "capability inventory updated:",
            "session context:",
            "runtime truth report",
            "sqlite tables observed live",
        ]

        return any(x in low for x in bad)

    def _eli_recent_mem_v2_clean_report(report: dict) -> dict:
        report = dict(report or {})
        recent = dict(report.get("recent") or {})
        question = report.get("question") or ""

        cleaned = {}
        for key, rows in recent.items():
            good_rows = []
            for row in list(rows or []):
                if not isinstance(row, dict):
                    continue
                text = str(row.get("text") or "")
                if _eli_recent_mem_v2_bad_runtime_text(text, question):
                    continue
                good_rows.append(row)
            cleaned[key] = good_rows[:5]

        # Runtime events are operational traces. Keep them in the structured
        # report only if clean, but do not expose them as primary "memory".
        cleaned["runtime_events"] = cleaned.get("runtime_events", [])[:2]
        report["recent"] = cleaned

        policy = dict(report.get("policy") or {})
        policy["surface_rule"] = (
            "Recent memory-processing answers must summarize durable memories, "
            "observations, learning replay, and clean operational facts only. "
            "Do not treat prior assistant text, prompt echoes, or runtime errors as memory activity."
        )
        report["policy"] = policy
        return report

    def _eli_recent_mem_v2_content(report: dict) -> str:
        counts = report.get("counts") or {}
        recent = report.get("recent") or {}

        lines = [
            "Grounded recent memory-processing answer:",
            f"- long-term memory rows: {counts.get('long_term_memories', 0)}",
            f"- FTS memory rows: {counts.get('memory_fts_rows', 0)}",
            f"- FAISS vector entries: {counts.get('faiss_vectors', 0)}",
            f"- conversation turns: {counts.get('conversation_turns', 0)}",
            f"- learning replay rows: {counts.get('learning_replay_rows', 0)}",
            f"- observations: {counts.get('observations', 0)}",
            "",
        ]

        durable = list(recent.get("memories") or [])
        observations = list(recent.get("observations") or [])
        replay = list(recent.get("learning_replay") or [])

        def _fmt_ev_ts(v):
            # Render the stored epoch as a human-readable local time (the raw float
            # '1781004767.88' is meaningless to a user — they asked for hours/minutes).
            try:
                import datetime as _dt
                f = float(v)
                if f > 1e11:   # accidentally stored in ms
                    f /= 1000.0
                return _dt.datetime.fromtimestamp(f).strftime("%Y-%m-%d %H:%M")
            except Exception:
                return str(v)

        if durable:
            lines.append("Clean recent durable memory evidence:")
            for row in durable[:3]:
                stamp = f"[{_fmt_ev_ts(row.get('time'))}] " if row.get("time") else ""
                lines.append(f"- {stamp}{row.get('text')}")
            lines.append("")

        if observations:
            lines.append("Clean recent observation evidence:")
            for row in observations[:3]:
                stamp = f"[{_fmt_ev_ts(row.get('time'))}] " if row.get("time") else ""
                lines.append(f"- {stamp}{row.get('text')}")
            lines.append("")

        if replay:
            lines.append("Clean recent learning-replay evidence:")
            for row in replay[:3]:
                stamp = f"[{_fmt_ev_ts(row.get('time'))}] " if row.get("time") else ""
                lines.append(f"- {stamp}{row.get('text')}")
            lines.append("")

        if not durable and not observations and not replay:
            lines.append(
                "I found memory-system activity counts, but no clean recent durable-memory details "
                "that should be described as specific remembered topics."
            )
            lines.append("")

        lines.append(
            "I should not claim I was processing topics that are not present in this evidence."
        )
        return "\n".join(lines).rstrip()

    # recent-memory-processing v2 cleanup wrapper removed — mw_recent_memory_processing
    # in the canonical middleware table now calls _eli_recent_mem_v2_clean_report() and
    # _eli_recent_mem_v2_content() directly.
    pass

except Exception as _eli_recent_mem_v2_exec_err:
    log.debug(f"[EXECUTOR] recent-memory-processing cleanup v2 install failed: {_eli_recent_mem_v2_exec_err}")

# Self-report recent updates evidence provider: deterministic evidence for "Tell me about yourself.
# What updates/checks have been performed lately?".
try:
    import json as _eli_self_json
    import subprocess as _eli_self_subprocess
    from pathlib import Path as _eli_self_Path


    def _eli_self_project_root():
        """Canonical, environment-honouring root — never the module's own location.

        `Path(__file__).parents[2]` resolves INSIDE the read-only AppImage mount in
        a shipped build, so this report read a runtime_snapshot.json that does not
        exist there and ran `git -C` against a directory that is not a repository.
        Observed live at 2.1.81: every runtime field printed None ("model_name:
        None, loaded: None") in the same session whose log shows the model loaded
        and the snapshot written to ~/.local/share/ELI_v3/artifacts/. This is the
        same class of bug as the v2.1.6 sweep, missed by it because the root is
        resolved in a local function rather than a module-level constant.
        """
        try:
            from eli.core.paths import project_root as _canonical_project_root
            root = _eli_self_Path(_canonical_project_root())
            if str(root):
                return root
        except Exception:
            log.debug("self-report: canonical project_root unavailable", exc_info=True)
        try:
            return _eli_self_Path(__file__).resolve().parents[2]
        except Exception:
            return _eli_self_Path.cwd()

    def _eli_self_run_git(root, git_args, timeout=4):
        try:
            proc = _eli_self_subprocess.run(
                ["git", "-C", str(root), *git_args],
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode != 0:
                return {
                    "ok": False,
                    "stdout": proc.stdout.strip(),
                    "stderr": proc.stderr.strip(),
                    "returncode": proc.returncode,
                }
            return {
                "ok": True,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
                "returncode": proc.returncode,
            }
        except Exception as e:
            return {"ok": False, "stdout": "", "stderr": repr(e), "returncode": -1}

    def _eli_self_read_json(path):
        from eli.utils.jsonio import read_json_dict
        return read_json_dict(_eli_self_Path(path))

    def _eli_self_split_lines(text, limit=12):
        lines = [x.rstrip() for x in str(text or "").splitlines() if x.strip()]
        return lines[:limit]

    def _eli_self_installed_version():
        from importlib.metadata import PackageNotFoundError, version as _pkg_version
        try:
            return str(_pkg_version("eli"))
        except PackageNotFoundError:   # a bundle has no package record: normal, and printed a traceback every time
            log.debug("no package record")
        try:
            import tomllib as _toml
            pp = _eli_self_Path(__file__).resolve().parents[2] / "pyproject.toml"
            if pp.is_file():
                return str(_toml.loads(pp.read_text(encoding="utf-8")).get("project", {}).get("version") or "")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return ""

    def _eli_self_packaged_update_summary(report, *, question=""):
        """Short conversational answer when git history is unavailable (AppImage/portable)."""
        ver = _eli_self_installed_version() or "unknown"
        caps = (report.get("capabilities") or {}).get("manifest_total")
        rt = report.get("runtime") or {}
        model = rt.get("model_name") or rt.get("model_path") or "unknown model"
        q = str(question or "").lower()
        if any(w in q for w in ("notice", "feel", "sense", "changes")):
            lead = (
                f"Yeah — I'm on v{ver} in this packaged build. I can't read git commits from "
                f"{report.get('project_root')} (packaged install — no trustworthy git repo). "
                f"This build is ELI v{ver}."
            )
        else:
            lead = (
                f"I'm running ELI v{ver} as a packaged install — no git repo at "
                f"{report.get('project_root')}, so commit history isn't visible from here."
            )
        tail = f"Loaded: {model}"
        if caps:
            tail += f", {caps} capabilities."
        else:
            tail += "."
        return f"{lead} {tail}"

    def _eli_self_build_recent_updates_report(question=""):
        root = _eli_self_project_root()

        # Packaged AppImage/portable runs anchor project_root at ~/.local/share/ELI_v3, which often
        # holds an old extracted source tree with a stale .git (a v2.3.89 AppImage reported v2.3.62
        # commits). Never trust git there unless it's a genuine dev checkout of the running version.
        _installed_ver = _eli_self_installed_version()
        _trust_git = True
        try:
            from eli.core.paths import is_frozen as _eli_is_frozen
            if _eli_is_frozen():
                _trust_git = False
        except Exception:
            log.debug("self-report: is_frozen check failed", exc_info=True)
        if _trust_git and _installed_ver:
            try:
                import tomllib as _toml_git
                _pp = root / "pyproject.toml"
                if _pp.is_file():
                    _root_ver = str(
                        _toml_git.loads(_pp.read_text(encoding="utf-8"))
                        .get("project", {})
                        .get("version") or ""
                    )
                    if _root_ver and _root_ver != _installed_ver:
                        _trust_git = False
            except Exception:
                log.debug("self-report: pyproject version cross-check failed", exc_info=True)

        if _trust_git:
            head = _eli_self_run_git(root, ["log", "--oneline", "--decorate", "-8"])
            status = _eli_self_run_git(root, ["status", "--short"])
            branch = _eli_self_run_git(root, ["branch", "--show-current"])
            tags = _eli_self_run_git(
                root,
                [
                    "tag",
                    "--list",
                    "--sort=-creatordate",
                    "identity_scope_clean_*",
                    "runtime_identity_media_learning_*",
                    "wip_runtime_gui_memory_*",
                    "wip_first_run_policy_tracking_*",
                    "memory_count_grounded_*",
                    "adaptive_cold_gguf_loader_*",
                    "effective_runtime_*",
                    "recent_memory_processing_*",
                ],
            )
        else:
            head = {"ok": False, "stdout": "", "stderr": "packaged_install_no_git", "returncode": -1}
            status = {"ok": False, "stdout": "", "stderr": "", "returncode": -1}
            branch = {"ok": False, "stdout": "", "stderr": "", "returncode": -1}
            tags = {"ok": False, "stdout": "", "stderr": "", "returncode": -1}

        # Read the snapshot where the loader WRITES it — get_paths().artifacts_dir,
        # which honours the artifacts override. `root / "artifacts"` guessed at the
        # location and guessed wrong in any redirected or frozen run.
        runtime_snapshot = {}
        try:
            runtime_snapshot = _eli_self_read_json(
                get_paths().artifacts_dir / "runtime_snapshot.json")
        except Exception:
            runtime_snapshot = {}
        if not runtime_snapshot:
            runtime_snapshot = _eli_self_read_json(root / "artifacts" / "runtime_snapshot.json")

        # The one this install regenerated, else the one the build shipped (capability_updater).
        try:
            from eli.tools.registry.capability_updater import current_manifest_path as _cur_manifest
            manifest = _eli_self_read_json(_cur_manifest())
        except Exception:
            manifest = {}
        if not manifest:
            try:
                manifest = _eli_self_read_json(
                    _eli_self_Path(__file__).resolve().parents[2] / "capability_manifest.json")
            except Exception:
                manifest = {}

        runtime = {
            "model_path": runtime_snapshot.get("model_path"),
            "model_name": runtime_snapshot.get("model_name"),
            "loaded": runtime_snapshot.get("loaded"),
            "effective": runtime_snapshot.get("effective") or {
                "n_ctx": runtime_snapshot.get("n_ctx"),
                "n_gpu_layers": runtime_snapshot.get("n_gpu_layers"),
                "n_threads": runtime_snapshot.get("n_threads"),
                "n_batch": runtime_snapshot.get("n_batch"),
            },
            "requested": runtime_snapshot.get("requested") or {
                "n_ctx": runtime_snapshot.get("requested_n_ctx") or runtime_snapshot.get("n_ctx"),
                "n_gpu_layers": runtime_snapshot.get("requested_n_gpu_layers") or runtime_snapshot.get("n_gpu_layers"),
                "n_threads": runtime_snapshot.get("requested_n_threads") or runtime_snapshot.get("n_threads"),
                "n_batch": runtime_snapshot.get("requested_n_batch") or runtime_snapshot.get("n_batch"),
            },
            "runtime_contract": runtime_snapshot.get("runtime_contract"),
            "runtime_source": runtime_snapshot.get("runtime_source"),
        }

        report = {
            "ok": True,
            "question": str(question or ""),
            "project_root": str(root),
            "branch": (branch.get("stdout") or "").strip(),
            "git": {
                "recent_commits": _eli_self_split_lines(head.get("stdout"), 8),
                "status_short": _eli_self_split_lines(status.get("stdout"), 20),
                "tags": _eli_self_split_lines(tags.get("stdout"), 20),
                "head_ok": bool(head.get("ok")),
                "status_ok": bool(status.get("ok")),
                # A shipped build has no repository at all. Without this the empty
                # output of a FAILED `git status --short` was rendered as "clean",
                # which is a positive claim about a working tree that isn't there.
                "available": bool(head.get("ok") or status.get("ok")),
            },
            "capabilities": {
                "manifest_total": manifest.get("total"),
                "generated_at": manifest.get("generated_at"),
            },
            "runtime": runtime,
            "policy": {
                "must_not_claim": [
                    "Do not claim user_profile.json unless it appears in this report.",
                    "Do not claim Spotify/app activity unless it appears in this report.",
                    "Do not claim '3 rewired' unless it appears in this report.",
                    "Do not claim vague routine updates/checks without naming concrete Git/runtime evidence.",
                    "Do not invent emotional state such as 'usual nutty self'.",
                ],
                "answer_contract": (
                    "Answer self-report/update questions using only Git commit/tag/status evidence, "
                    "capability manifest count, and runtime snapshot fields in this report."
                ),
            },
        }

        lines = []
        lines.append("Grounded ELI self-report / recent update evidence:")
        lines.append(f"- identity: ELI / Enhanced Learning Interface")
        if report["branch"]:
            lines.append(f"- current_branch: {report['branch']}")
        if report["capabilities"]["manifest_total"] is not None:
            lines.append(f"- capability_manifest_total: {report['capabilities']['manifest_total']}")
        if report["capabilities"]["generated_at"]:
            lines.append(f"- capability_manifest_generated_at: {report['capabilities']['generated_at']}")

        lines.append("")
        lines.append("Recent Git updates:")
        commits = report["git"]["recent_commits"]
        _git_available = report["git"].get("available", True)
        if commits:
            for c in commits:
                lines.append(f"- {c}")
        elif not _git_available:
            lines.append(f"- No git repository at {report['project_root']} — this is a packaged "
                         "build, so commit history is not readable from here.")
        else:
            lines.append("- No recent Git commit evidence was available.")

        lines.append("")
        lines.append("Relevant clean tags:")
        tag_lines = report["git"]["tags"]
        if tag_lines:
            for t in tag_lines[:10]:
                lines.append(f"- {t}")
        else:
            lines.append("- No relevant clean tags were found.")

        lines.append("")
        lines.append("Runtime snapshot:")
        rt = report["runtime"]
        eff = rt.get("effective") or {}
        req = rt.get("requested") or {}
        lines.append(f"- model_name: {rt.get('model_name')}")
        lines.append(f"- model_path: {rt.get('model_path')}")
        lines.append(f"- loaded: {rt.get('loaded')}")
        lines.append(
            "- effective: "
            f"ctx={eff.get('n_ctx')} "
            f"gpu_layers={eff.get('n_gpu_layers')} "
            f"threads={eff.get('n_threads')} "
            f"batch={eff.get('n_batch')}"
        )
        lines.append(
            "- requested: "
            f"ctx={req.get('n_ctx')} "
            f"gpu_layers={req.get('n_gpu_layers')} "
            f"threads={req.get('n_threads')} "
            f"batch={req.get('n_batch')}"
        )

        lines.append("")
        lines.append("Working tree status:")
        dirty = report["git"]["status_short"]
        if dirty:
            lines.append(f"- dirty_entries: {len(dirty)} shown below")
            for d in dirty[:10]:
                lines.append(f"  - {d}")
        elif not _git_available:
            lines.append("- not available (no git repository in this build)")
        else:
            lines.append("- clean according to git status --short")

        audit_content = "\n".join(lines)
        if not _git_available:
            content = _eli_self_packaged_update_summary(report, question=question)
        else:
            content = audit_content

        # The grounding rule is an instruction to the model and must never be visible. It was appended
        # to `content`, which is returned as `response`, so the user read it as ELI's reply. It lives
        # in the report payload with the rest of the policy block.
        report["policy"]["grounding_rule"] = (
            "If an update/check is not listed in this report, ELI must not claim it happened."
        )
        report["audit_content"] = audit_content

        return {
            "ok": True,
            "action": "SELF_REPORT",
            "evidence_source": "self_report_recent_updates_git_runtime",
            "report": report,
            "content": content,
            "response": content,
        }

    # SELF_REPORT recent-updates wrapper removed — mw_self_report_recent_updates
    # in the canonical middleware table intercepts this and calls
    # _eli_self_build_recent_updates_report() directly.
    pass
except Exception as _eli_self_report_recent_exec_error:
    log.debug(f"[EXECUTOR][WARN] self-report recent-updates provider failed: {_eli_self_report_recent_exec_error}")

# GUI runtime audit visible-result contract (ELI_GUI_RUNTIME_AUDIT_VISIBLE_RESULT_CONTRACT):
# normalises GUI_RUNTIME_AUDIT executor output so the GUI gets visible content. Doesn't change
# routing, cognition, GGUF or the audit evidence source.
try:
    _ELI_GUI_RUNTIME_AUDIT_VISIBLE_PREV = _gui_runtime_audit_report

    def _eli_gui_runtime_audit_entry_to_text(entry):
        entry = entry if isinstance(entry, dict) else {}
        path = entry.get("path") or "<unknown>"
        status = entry.get("status") or ("PASS" if entry.get("ok", True) else "FAIL")
        issues = entry.get("issues") or []
        evidence = entry.get("evidence") or {}

        lines = [
            f"{status} — GUI wiring scan (SHALLOW line-hit check): {path}",
            "",
            "SCOPE (read this first): this only confirms that wiring references appear "
            "on the lines below. It does NOT verify behaviour, correctness, or full-file "
            "semantics. A 'PASS' here is NOT a clean bill of health — do not claim 'no issues'.",
            "",
            "Visible contract:",
            "- GUI_RUNTIME_AUDIT returned structured evidence from executor.",
            "- This response is generated from the executor entry, not a hallucinated chat answer.",
            "",
            "Core wiring evidence:",
        ]

        for key in ("router", "executor", "cognition", "proactive_import", "send_worker"):
            val = evidence.get(key, [])
            lines.append(f"- {key}: lines {val}")

        extra_keys = [k for k in evidence.keys() if k not in {"router", "executor", "cognition", "proactive_import", "send_worker"}]
        if extra_keys:
            lines.append("")
            lines.append("Additional evidence keys:")
            for key in extra_keys[:30]:
                val = evidence.get(key)
                if isinstance(val, list):
                    lines.append(f"- {key}: lines {val[:40]}{' ...' if len(val) > 40 else ''}")
                else:
                    lines.append(f"- {key}: {val}")

        lines.append("")
        lines.append("Issues:")
        if issues:
            for issue in issues:
                lines.append(f"- {issue}")
        else:
            lines.append("- This shallow line-hit scan flagged nothing — but it CANNOT detect "
                         "logic, runtime, or correctness bugs, so this is not evidence the file "
                         "is bug-free.")

        lines.append("")
        lines.append("Important limitation:")
        lines.append("- The current baseline GUI audit reports line-hit wiring evidence. It does not yet perform a deep semantic full-file audit.")

        return "\n".join(lines).strip()

    def _gui_runtime_audit_report(*args, **kwargs):  # type: ignore[no-redef]
        result = _ELI_GUI_RUNTIME_AUDIT_VISIBLE_PREV(*args, **kwargs)

        if not isinstance(result, dict):
            return result

        # Already valid: leave untouched.
        if str(result.get("content") or result.get("response") or "").strip():
            return result

        entry = result.get("entry")
        if not isinstance(entry, dict):
            return result

        content = _eli_gui_runtime_audit_entry_to_text(entry)

        fixed = dict(result)
        fixed["ok"] = bool(result.get("ok", True))
        fixed["action"] = "GUI_RUNTIME_AUDIT"
        fixed["content"] = content
        fixed["response"] = content

        meta = dict(fixed.get("meta") or {})
        meta.update({
            "response_mode": "executor_visible_audit_contract",
            "raw_executor_return": False,
            "suppress_gui_response": False,
            "visible_contract_repaired": True,
        })
        fixed["meta"] = meta

        return fixed

    log.debug("[EXECUTOR] GUI_RUNTIME_AUDIT visible result contract installed")

except Exception as _eli_gui_audit_visible_contract_err:
    log.debug(f"[EXECUTOR] GUI_RUNTIME_AUDIT visible result contract failed: {_eli_gui_audit_visible_contract_err}")
# =============================================================================

# Final execute_action alias sync (ELI_EXECUTOR_FINAL_EXECUTE_ACTION_ALIAS_SYNC_V1): execute_action
# must expose the same final contract as execute. Several historical wrappers reassigned execute
# without rebinding execute_action, leaving it pinned to an older wrapper.
try:
    _ELI_EXECUTOR_FINAL_ALIAS_SYNC_PREV_EXECUTE_ACTION = globals().get("execute_action")
    if callable(globals().get("execute")):
        execute_action = execute
        try:
            execute_action._eli_final_alias_sync_v1 = True
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        log.debug("[EXECUTOR] final execute_action alias synced to execute")
except Exception as _eli_executor_final_alias_sync_err:
    log.debug(f"[EXECUTOR] final execute_action alias sync failed: {_eli_executor_final_alias_sync_err}")
# =============================================================================

# RUNTIME_STATUS evidence metadata normalisation is done inline by the mw_runtime_status_metadata
# middleware in the canonical table below; the old inert wrapper here was removed.


# --- Phase 11: multi-PDF helpers (consumed by mw_multipdf middleware) ------
# Wrapper machinery removed — only the helpers below are still referenced.
try:
    if not globals().get("_ELI_PHASE11_MULTIPDF_HELPERS_INSTALLED"):
        _ELI_PHASE11_MULTIPDF_HELPERS_INSTALLED = True

        def _eli_phase11_clean_paths(paths):
            if isinstance(paths, str):
                raw = [p.strip() for p in paths.split(",")]
            elif isinstance(paths, (list, tuple, set)):
                raw = list(paths)
            else:
                raw = []

            out = []
            seen = set()
            for p in raw:
                s = str(p or "").strip()
                if not s:
                    continue
                if s not in seen:
                    seen.add(s)
                    out.append(s)
            return out

        def _eli_phase11_format_multipdf_result(results, paths):
            lines = [
                f"Multi-PDF analysis completed for {len(paths)} file(s).",
                "",
            ]

            ok_count = 0
            fail_count = 0

            for idx, (path, res) in enumerate(zip(paths, results), 1):
                ok = bool(isinstance(res, dict) and res.get("ok"))
                if ok:
                    ok_count += 1
                else:
                    fail_count += 1

                status = "OK" if ok else "FAILED"
                lines.append(f"## {idx}. {Path(path).name} — {status}")
                lines.append(f"Source: `{path}`")

                if isinstance(res, dict):
                    saved_to = res.get("saved_to")
                    pages = res.get("pages")
                    chars = res.get("chars")
                    err = res.get("error")
                    response = res.get("response") or res.get("content") or ""

                    if pages is not None or chars is not None:
                        lines.append(f"Pages: {pages} | Characters: {chars}")
                    if saved_to:
                        lines.append(f"Saved to: `{saved_to}`")
                    if err:
                        lines.append(f"Error: {err}")
                    if response:
                        lines.append("")
                        lines.append(str(response))
                else:
                    lines.append(f"Unexpected executor result type: {type(res).__name__}")

                lines.append("")

            lines.insert(1, f"Successful: {ok_count} | Failed: {fail_count}")
            return "\n".join(lines).strip()

except Exception as _eli_phase11_multipdf_executor_err:
    log.debug(f"[EXECUTOR] Phase 11 multi-PDF helpers failed: {_eli_phase11_multipdf_executor_err}")

# Canonical middleware table (ELI_EXECUTOR_CANONICAL_MIDDLEWARE_TABLE_V1): consolidates the stacked
# execute wrappers into one explicit chain. The legacy wrapper blocks above are kept as retired
# compatibility helpers.
try:
    if not globals().get("_ELI_EXECUTOR_CANONICAL_MIDDLEWARE_TABLE_V1"):
        _ELI_EXECUTOR_CANONICAL_MIDDLEWARE_TABLE_V1 = True

        # Capture the current outermost execute as the legacy base. After Tier 2
        # cleanup, this is the PM wrapper (the new top of the legacy chain).
        _ELI_EXECUTOR_CANONICAL_BASE = globals().get("execute")

        def _eli_exec_ctx_from_call(action, args, pargs, kwargs):
            real_action = action
            real_args = args

            if isinstance(action, dict):
                real_action = action.get("action")
                real_args = action.get("args", args) or action.get("params", args) or {}

            if not isinstance(real_args, dict):
                real_args = {}

            return {
                "action": str(real_action or "").upper().strip(),
                "args": dict(real_args or {}),
                "pargs": tuple(pargs or ()),
                "kwargs": dict(kwargs or {}),
            }

        def _eli_exec_mw_multipdf(ctx, nxt):
            if ctx["action"] != "ANALYZE_PDF":
                return nxt(ctx)

            clean_paths = globals().get("_eli_phase11_clean_paths")
            format_result = globals().get("_eli_phase11_format_multipdf_result")
            if not callable(clean_paths) or not callable(format_result):
                return nxt(ctx)

            paths = clean_paths((ctx.get("args") or {}).get("paths"))
            if len(paths) <= 1:
                return nxt(ctx)

            results = []
            for p in paths:
                one_ctx = {
                    "action": "ANALYZE_PDF",
                    "args": dict(ctx["args"]),
                    "pargs": tuple(ctx.get("pargs") or ()),
                    "kwargs": dict(ctx.get("kwargs") or {}),
                }
                one_ctx["args"]["path"] = p
                one_ctx["args"].pop("paths", None)
                results.append(nxt(one_ctx))

            all_ok = all(isinstance(r, dict) and r.get("ok") for r in results)
            content = format_result(results, paths)
            return {
                "ok": bool(all_ok),
                "action": "ANALYZE_PDF",
                "paths": paths,
                "results": results,
                "content": content,
                "response": content,
                "response_mode": "canonical_middleware_multipdf",
            }

        def _eli_exec_mw_runtime_status_metadata(ctx, nxt):
            out = nxt(ctx)
            if ctx["action"] != "RUNTIME_STATUS":
                return out

            if not isinstance(out, dict):
                txt = str(out or "").strip()
                out = {
                    "ok": bool(txt),
                    "action": "RUNTIME_STATUS",
                    "content": txt,
                    "response": txt,
                }
            else:
                out = dict(out)

            txt = str(out.get("content") or out.get("response") or "").strip()
            out["ok"] = bool(out.get("ok", bool(txt)))
            out["action"] = "RUNTIME_STATUS"
            out["grounded"] = True
            out["evidence_used"] = True

            if not out.get("source"):
                out["source"] = "runtime_status_executor_evidence_metadata_v1"
            if not out.get("evidence_source"):
                out["evidence_source"] = "runtime_status_live_runtime_telemetry"

            report = dict(out.get("report") or {})
            report.setdefault("repair_reason", "runtime_status_executor_evidence_metadata_v1")
            report.setdefault("metadata_normalised", True)
            report.setdefault("evidence_contract", "live_runtime_status_telemetry")
            report.setdefault("runtime_status_content_preserved", True)
            out["report"] = report

            if txt:
                out.setdefault("content", txt)
                out.setdefault("response", txt)

            return out

        def _eli_exec_mw_self_report_recent_updates(ctx, nxt):
            if ctx["action"] != "SELF_REPORT":
                return nxt(ctx)

            report_builder = globals().get("_eli_self_build_recent_updates_report")
            if not callable(report_builder):
                return nxt(ctx)

            real_args = dict(ctx.get("args") or {})
            scope = str(real_args.get("self_report_scope") or "").strip().lower()
            q = str(real_args.get("question") or "")
            low = q.lower()
            updateish = (
                scope == "recent_updates"
                or "what updates" in low
                or "updates and checks" in low
                or "checks have been" in low
                or "checks were" in low
                or "recent checks" in low
                or "routine updates" in low
                or "recent updates" in low
                or "what have you been doing" in low
                or "what have you been processing" in low
                or "what have you been working on" in low
            )
            if not updateish:
                return nxt(ctx)

            try:
                return report_builder(q)
            except Exception as e:
                msg = f"Self-report recent-updates evidence failed: {e!r}"
                return {
                    "ok": False,
                    "action": "SELF_REPORT",
                    "evidence_source": "self_report_recent_updates_error",
                    "error": repr(e),
                    "content": msg,
                    "response": msg,
                }

        def _eli_exec_mw_recent_memory_processing(ctx, nxt):
            if ctx["action"] != "MEMORY_STATUS":
                return nxt(ctx)

            real_args = dict(ctx.get("args") or {})
            if str(real_args.get("memory_scope") or "") != "recent_processing":
                return nxt(ctx)

            build_report = globals().get("_eli_recent_memory_processing_report")
            clean_report = globals().get("_eli_recent_mem_v2_clean_report")
            content_v2 = globals().get("_eli_recent_mem_v2_content")
            content_v1 = globals().get("_eli_recent_memory_processing_content")

            if not callable(build_report):
                return nxt(ctx)

            report = build_report(str(real_args.get("question") or ""))
            if callable(clean_report):
                report = clean_report(report or {})

            if callable(content_v2):
                content = content_v2(report or {})
                source = "recent_memory_processing_sqlite_clean_v2"
            elif callable(content_v1):
                content = content_v1(report or {})
                source = "recent_memory_processing_sqlite"
            else:
                content = str(report or "")
                source = "recent_memory_processing_sqlite"

            return {
                "ok": bool((report or {}).get("ok")),
                "action": "MEMORY_STATUS",
                "evidence_source": source,
                "report": report,
                "content": content,
                "response": content,
            }

        def _eli_exec_mw_memory_health(ctx, nxt):
            if ctx["action"] != "MEMORY_STATUS" or str((ctx.get("args") or {}).get("memory_scope") or "") != "health":
                return nxt(ctx)
            from eli.memory.memory_truth import memory_truth_report, format_memory_truth
            content = format_memory_truth(memory_truth_report())
            return {"ok": True, "action": "MEMORY_STATUS", "evidence_source": "memory_truth",
                    "content": content, "response": content}

        def _eli_exec_mw_memory_count(ctx, nxt):
            if ctx["action"] != "MEMORY_STATUS":
                return nxt(ctx)

            is_requested = globals().get("_eli_memory_count_is_requested")
            build_evidence = globals().get("_eli_memory_count_evidence")
            if callable(is_requested) and callable(build_evidence):
                try:
                    if is_requested(ctx.get("args") or {}):
                        return build_evidence()
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            return nxt(ctx)

        def _eli_exec_mw_memory_runtime_sanitizer(ctx, nxt):
            out = nxt(ctx)
            if ctx["action"] != "EXPLAIN_MEMORY_RUNTIME":
                return out
            sanitizer = globals().get("_eli_sanitize_memory_runtime_output")
            if callable(sanitizer):
                try:
                    return sanitizer(out)
                except Exception:
                    return out
            return out

        def _eli_exec_mw_profile_scope(ctx, nxt):
            action_name = ctx["action"]
            real_args = dict(ctx.get("args") or {})

            scope_answer = globals().get("_eli_profile_scope_execute_answer")
            if action_name in {"PERSONAL_MEMORY_SUMMARY", "PERSONAL_MEMORY_DEEP_EXPLAIN"} and callable(scope_answer):
                try:
                    scoped = scope_answer(action_name, real_args)
                    if scoped is not None:
                        return scoped
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

            out = nxt(ctx)

            if action_name == "EXPLAIN_MEMORY_RUNTIME" and isinstance(out, dict):
                cleaner = globals().get("_eli_profile_scope_clean_memory_runtime_text")
                if callable(cleaner):
                    try:
                        txt = str(out.get("content") or out.get("response") or "")
                        cleaned = cleaner(txt)
                        out = dict(out)
                        out["content"] = cleaned
                        out["response"] = cleaned
                        out["evidence_source"] = out.get("evidence_source") or "memory_runtime_no_identity_preamble"
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)

            return out

        def _eli_exec_mw_identity_only(ctx, nxt):
            if ctx["action"] != "USER_IDENTITY_SUMMARY":
                return nxt(ctx)

            builder = globals().get("_eli_identity_only_response")
            if not callable(builder):
                return nxt(ctx)

            text = str(builder(dict(ctx.get("args") or {})) or "").strip()
            if not text:
                return nxt(ctx)
            return {
                "ok": True,
                "action": "USER_IDENTITY_SUMMARY",
                "content": text,
                "response": text,
                "evidence_source": "active_user_identity_scope",
                "generation_invoked": False,
            }

        _ELI_EXECUTOR_MIDDLEWARE_TABLE = (
            ("multipdf", _eli_exec_mw_multipdf),
            ("runtime_status_metadata", _eli_exec_mw_runtime_status_metadata),
            ("self_report_recent_updates", _eli_exec_mw_self_report_recent_updates),
            ("recent_memory_processing", _eli_exec_mw_recent_memory_processing),
            ("memory_health", _eli_exec_mw_memory_health),
            ("memory_count", _eli_exec_mw_memory_count),
            ("memory_runtime_sanitizer", _eli_exec_mw_memory_runtime_sanitizer),
            ("profile_scope", _eli_exec_mw_profile_scope),
            ("identity_only", _eli_exec_mw_identity_only),
        )

        def _eli_exec_core(ctx):
            base = globals().get("_ELI_EXECUTOR_CANONICAL_BASE")
            if not callable(base):
                msg = f"No executor base available for {ctx.get('action')}"
                return {"ok": False, "action": str(ctx.get("action") or ""), "content": msg, "response": msg}
            return base(
                str(ctx.get("action") or ""),
                dict(ctx.get("args") or {}),
                *(ctx.get("pargs") or ()),
                **(ctx.get("kwargs") or {}),
            )

        def _eli_exec_apply_middleware(ctx, index=0):
            if index >= len(_ELI_EXECUTOR_MIDDLEWARE_TABLE):
                if ctx.get("_pipeline_trace"):
                    _perf = __import__("time").perf_counter
                    _core_t0 = _perf()
                    out = _eli_exec_core(ctx)
                    _dt = (_perf() - _core_t0) * 1000.0
                    log.debug(
                        f"[PIPELINE][EXECUTOR] core action={ctx.get('action')} "
                        f"ok={bool(isinstance(out, dict) and out.get('ok'))} dt_ms={_dt:.2f}",
                    )
                    return out
                return _eli_exec_core(ctx)

            _name, mw = _ELI_EXECUTOR_MIDDLEWARE_TABLE[index]
            if ctx.get("_pipeline_trace"):
                _perf = __import__("time").perf_counter
                _mw_t0 = _perf()
                log.debug(
                    f"[PIPELINE][EXECUTOR] enter mw={_name} action={ctx.get('action')}",
                )
                out = mw(ctx, lambda next_ctx=None: _eli_exec_apply_middleware(ctx if next_ctx is None else next_ctx, index + 1))
                _dt = (_perf() - _mw_t0) * 1000.0
                _ok = bool(isinstance(out, dict) and out.get("ok"))
                log.debug(
                    f"[PIPELINE][EXECUTOR] exit  mw={_name} action={ctx.get('action')} "
                    f"ok={_ok} out_type={type(out).__name__} dt_ms={_dt:.2f}",
                )
                return out
            return mw(ctx, lambda next_ctx=None: _eli_exec_apply_middleware(ctx if next_ctx is None else next_ctx, index + 1))

        def execute(action, args=None, *pargs, **kwargs):  # type: ignore[no-redef]
            ctx = _eli_exec_ctx_from_call(action, args, pargs, kwargs)
            _trace = str(__import__("os").environ.get("ELI_PIPELINE_TRACE", "")).strip().lower() in {"1", "true", "yes", "on"}
            if _trace:
                ctx["_pipeline_trace"] = True
                _preview = str(ctx.get("action") or "")
                log.debug(f"[PIPELINE][EXECUTOR] begin action={_preview}")
            out = _eli_exec_apply_middleware(ctx, 0)
            if isinstance(out, dict):
                if _trace:
                    log.debug(
                        f"[PIPELINE][EXECUTOR] final action={out.get('action')} ok={out.get('ok')}",
                    )
                return out
            txt = str(out or "")
            return {
                "ok": bool(txt.strip()),
                "action": str(ctx.get("action") or ""),
                "content": txt,
                "response": txt,
            }

        execute_action = execute
        try:
            Executor.execute = lambda self, action, args=None, *a, **kw: execute(action, args, *a, **kw)  # type: ignore[name-defined]
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        log.debug("[EXECUTOR] canonical middleware table installed")
except Exception as _eli_executor_canonical_middleware_err:
    log.debug(f"[EXECUTOR] canonical middleware table install failed: {_eli_executor_canonical_middleware_err}")


try:
    from eli.execution.effectors import load_all as _load_effectors_at_import
    _load_effectors_at_import()
except Exception:
    log.debug("[EXECUTOR] effector modules failed to load", exc_info=True)

# --- AUTO-REGISTER CAPABILITIES (ON IMPORT) ---
try:
    from eli.tools.registry.capability_registry import register as _cap_register
    for _a in SUPPORTED_ACTIONS:
        _cap_register(_a, {"description": f"Executor action: {_a}"})
except Exception:
    log.debug("suppressed exception", exc_info=True)


# The one place a run is finished (_finish_run): the outermost execute. The layers below it (the
# middleware table, the generated-script guard, the personal-memory, second-layer, reasoning-mode,
# portable, voice, schema and media wrappers, and the base execute with its inline status answers)
# each return their result here, so a run answered by any of them is checked, recorded and
# post-dispatched once. A layer that hands the same run back to execute() is part of that run.
_ELI_RUNS_UNDER_WAY: "contextvars.ContextVar[frozenset]" = contextvars.ContextVar(
    "eli_executor_runs_under_way", default=frozenset())


def _eli_run_key(action: Any, args: Any, kwargs: Dict[str, Any]) -> "Tuple[str, Dict[str, Any], str]":
    if isinstance(action, dict):
        args = action.get("args", args) or args
        action = action.get("action", "")
    name = (action if isinstance(action, str) else "").upper().strip()
    merged: Dict[str, Any] = dict(args) if isinstance(args, dict) else {}
    merged.update(kwargs or {})
    try:
        sig = json.dumps(merged, sort_keys=True, default=str)
    except (TypeError, ValueError):
        sig = repr(sorted(merged.items(), key=lambda kv: str(kv[0])))
    return name, merged, f"{name}\x00{sig}"


def _lessons_for_the_run(action: str, args: Dict[str, Any]) -> str:
    """What was learned about runs like this one (eli/runtime/lessons.brief), asked before it runs:
    brief() is what marks the lessons as shown for this run, and observe_action checks only lessons
    that were shown, so with nothing asking it no lesson was ever checked. The lines go on the
    turn's record (and the result), where the reply about the run reads them. "" when none."""
    if not action or action == "CHAT":
        return ""
    try:
        from eli.runtime.lessons import brief
        told = brief(action, args=args)
    except Exception:
        log.debug("lessons not read before the run", exc_info=True)
        return ""
    if told:
        try:
            from eli.kernel import request_context as _rc_lessons
            _turn = _rc_lessons.turn_facts_var.get()
            if _turn is not None:
                _turn.setdefault("lessons_shown", {})[action] = told
        except Exception:
            log.debug("lessons not noted on the turn", exc_info=True)
    return told


def _eli_install_recording(inner):
    if getattr(inner, "_eli_records_runs", False):
        return inner

    def execute(action, args=None, *pargs, **kwargs):  # type: ignore[no-redef]
        name, merged, key = _eli_run_key(action, args, kwargs)
        under_way = _ELI_RUNS_UNDER_WAY.get()
        if key in under_way:
            return inner(action, args, *pargs, **kwargs)
        # lever proven_first (eli/runtime/lessons.py): an action shown to work less often than the rest,
        # with an alternative proven for this very thing, runs the alternative first; when that does not
        # work, the action asked for runs as it would have
        try:
            from eli.runtime.action_outcomes import first_choice
            first = first_choice(name, merged)
        except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
            log.debug("proven-first choice unavailable", exc_info=True)
            first = None
        if first:
            done = execute(first["action"], first["args"])
            from eli.runtime.evidence_ledger import execution_outcome as _outcome_of
            if isinstance(done, dict) and _outcome_of(done) == "ok":
                return dict(done, instead_of=name, proven_first=True)
        token = _ELI_RUNS_UNDER_WAY.set(under_way | {key})
        told = _lessons_for_the_run(name, merged)
        started = time.time()
        try:
            out = inner(action, args, *pargs, **kwargs)
        finally:
            _ELI_RUNS_UNDER_WAY.reset(token)
        if told and isinstance(out, dict):
            out.setdefault("lessons_applied", told)
        return _finish_run(name, merged, out, started)

    execute._eli_records_runs = True  # type: ignore[attr-defined]
    execute.__wrapped__ = inner  # type: ignore[attr-defined]
    return execute


execute = _eli_install_recording(execute)
execute_action = execute
_eli_recording_execute = execute
