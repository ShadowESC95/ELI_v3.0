"""System-intel effector — runtime / cognition / memory / gpu status + report formatters.

Grounded-introspection reports (self-report from real evidence). Lifted from
executor_enhanced.py (v3 modularisation). Code-audit + reasoning-mode-middleware reports
stay behind for their own slices.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from eli.core.paths import get_paths
from eli.utils.log import get_logger
log = get_logger(__name__)


def _canonical_runtime_file_map() -> Dict[str, Path]:
    root = get_paths().project_root.resolve()
    # Files live under eli/ subdir
    _eli = root / 'eli'
    root = _eli if _eli.exists() else root
    # Canonical runtime file paths under the live `eli/` layout.
    # phaseBW5 fix: replaced legacy `brain/cognition/...` and
    # `tools/automation/...` candidates that no longer correspond
    # to anything on disk and were producing hallucinated "FAIL"
    # entries in RUNTIME_AUDIT output.
    rels = {
        'cognitive_engine': ['kernel/engine.py'],
        'gguf_inference':   ['cognition/gguf_inference.py'],
        'memory':           ['memory/memory.py'],
        'memory_init':      ['memory/__init__.py'],
        'router':           ['execution/router_enhanced.py'],
        'executor':         ['execution/executor_enhanced.py'],
        'gui':              ['gui/gui_eli_original.py'],
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

    # Recent LIVE failures — surface what the failure log recorded so the audit
    # reflects live problems, not just static source state. Reads the same
    # canonical store the Self-Improve panel uses; resolved entries are filtered
    # out by get_recent_failures, so the audit and the panel agree.
    def _failures():
        from eli.runtime.self_improvement import get_self_improvement
        rows = get_self_improvement().memory.get_recent_failures(limit=5)
        if not rows:
            return True, "no live failures logged"
        summary = "; ".join(
            f"{(r.get('error') or r.get('user_input') or '')[:70]} (×{r.get('occurrence_count', 1)})"
            for r in rows)
        return False, f"{len(rows)} recent live failure(s): {summary}"
    _probe("recent_failures", _failures)

    return probes


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


def _gpu_status_report() -> Dict[str, Any]:
    query = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,power.draw,power.limit,driver_version",
        "--format=csv,noheader,nounits",
    ]
    runtime_snapshot: Dict[str, Any] = {}
    try:
        snap_path = get_paths().artifacts_dir / "runtime_snapshot.json"
        if snap_path.exists():
            runtime_snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    except Exception:
        runtime_snapshot = {}

    try:
        proc = subprocess.run(
            query,
            timeout=6,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        msg = f"GPU status unavailable: nvidia-smi could not run ({exc})."
        return {"ok": False, "error": str(exc), "content": msg, "response": msg}

    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "nvidia-smi returned no output").strip()
        msg = f"GPU status unavailable: {msg}"
        return {"ok": False, "returncode": proc.returncode, "content": msg, "response": msg}

    import csv
    rows = list(csv.reader((proc.stdout or "").splitlines()))
    if not rows:
        msg = "GPU status unavailable: nvidia-smi returned no GPU rows."
        return {"ok": False, "content": msg, "response": msg}

    fields = [
        "name", "memory_total_mib", "memory_used_mib", "memory_free_mib",
        "utilization_percent", "temperature_c", "power_draw_w", "power_limit_w",
        "driver_version",
    ]
    parsed = []
    for row in rows:
        values = [cell.strip() for cell in row]
        item = {field: (values[i] if i < len(values) else "") for i, field in enumerate(fields)}
        parsed.append(item)

    first = parsed[0]
    def _num(value: Any) -> Optional[float]:
        try:
            cleaned = re.sub(r"[^0-9.]+", "", str(value or ""))
            return float(cleaned) if cleaned else None
        except Exception:
            return None

    total = _num(first.get("memory_total_mib"))
    used = _num(first.get("memory_used_mib"))
    free = _num(first.get("memory_free_mib"))
    util = _num(first.get("utilization_percent"))
    used_pct = (used / total * 100.0) if total and used is not None else None

    lines = [
        "GPU status:",
        f"- name: {first.get('name') or 'unknown'}",
        f"- driver: {first.get('driver_version') or 'unknown'}",
    ]
    if total is not None and used is not None and free is not None:
        lines.append(
            f"- VRAM: {used:.0f} MiB used / {total:.0f} MiB total "
            f"({free:.0f} MiB free, {used_pct:.1f}% used)"
        )
    else:
        lines.append("- VRAM: unavailable")
    lines.extend([
        f"- GPU utilization: {util:.0f}%" if util is not None else "- GPU utilization: unavailable",
        f"- temperature: {first.get('temperature_c') or 'unknown'} C",
        f"- power: {first.get('power_draw_w') or 'unknown'} W / {first.get('power_limit_w') or 'unknown'} W limit",
    ])

    if runtime_snapshot:
        lines.extend([
            "",
            "ELI selected llama.cpp load parameters:",
            f"- context: {runtime_snapshot.get('n_ctx', 'unknown')}",
            f"- GPU-layer parameter: {runtime_snapshot.get('n_gpu_layers', 'unknown')}",
            f"- batch: {runtime_snapshot.get('n_batch', 'unknown')}",
            f"- CPU threads: {runtime_snapshot.get('n_threads', 'unknown')}",
        ])

    # Performance reading, derived from THIS load — not boilerplate. The previous
    # three lines were fixed strings printed regardless of what was measured, and
    # the middle one hedged about a fallback that had provably not happened while
    # the proof (requested vs effective) sat in the same payload.
    _reading = []
    try:
        from eli.cognition.context_synthesiser import runtime_load_gap as _gap_fn
        _gap = _gap_fn(runtime_snapshot or None)
    except Exception:
        _gap = {"ok": False}

    if _gap.get("ok"):
        _reduced = _gap.get("reduced") or {}
        if _reduced:
            _detail = "; ".join(
                f"{k} {v['requested']} → {v['effective']}" for k, v in sorted(_reduced.items())
            )
            _reading.append(f"- Loaded BELOW request ({_detail}) — reduced to fit "
                            f"available VRAM, not a settings error.")
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
        _free_pct = (float(free) / float(total)) * 100.0
        # "Free" from nvidia-smi is NOT spare. llama.cpp allocates its compute/graph
        # buffer LAZILY, at the first decode — so the reserve the loader deliberately
        # kept back still reads as free until a generation touches it. Reporting it as
        # headroom told the operator to raise settings into memory that is already
        # committed, which is precisely the over-commit that aborted the process
        # mid-generation at 2.2.7 and 2.2.9 (ggml-cuda.cu:98: CUDA error).
        #
        # Live at 2.3.6: 1493 MiB read as free while the compute buffer (736 MB at
        # ctx=12288 batch=128), CUDA overhead (350 MB) and the kept reserve (700 MB)
        # already accounted for more than that. Subtract what generation will take
        # and report what is genuinely spare.
        _committed = 0.0
        try:
            from eli.core.hardware_profile import (_compute_graph_reserve_mb,
                                                   _CUDA_OVERHEAD_MB, vram_reserve_mb)
            _eff_r = (runtime_snapshot or {}).get("effective") or runtime_snapshot or {}
            _c = int(_eff_r.get("n_ctx") or 0)
            _b = int(_eff_r.get("n_batch") or 0)
            if _c > 0 and _b > 0:
                _committed = (float(_compute_graph_reserve_mb(_c, _b))
                              + float(_CUDA_OVERHEAD_MB) + float(vram_reserve_mb()))
        except Exception:
            _committed = 0.0
        _spare = float(free) - _committed
        if _committed > 0:
            _reading.append(
                f"- {free:.0f} MiB of {total:.0f} MiB VRAM reads free ({_free_pct:.1f}%), but "
                f"{_committed:.0f} MiB is already committed to generation (compute buffer, "
                f"CUDA overhead and the kept reserve) — llama.cpp allocates it lazily at the "
                f"first decode.")
            if _spare >= 512:
                _reading.append(
                    f"- Genuinely spare: {_spare:.0f} MiB — room to raise context or batch.")
            else:
                _reading.append(
                    "- Genuinely spare: none to speak of. Raising context, batch or GPU "
                    "layers from here is what aborts mid-generation.")
        elif _free_pct < 10:
            _reading.append(
                f"- {free:.0f} MiB of {total:.0f} MiB VRAM free ({_free_pct:.1f}%): no headroom "
                "to raise context or batch without lowering GPU layers.")
        else:
            _reading.append(
                f"- {free:.0f} MiB of {total:.0f} MiB VRAM free ({_free_pct:.1f}%): headroom "
                "available for a larger context or batch.")

    if _reading:
        lines.extend(["", "Performance reading:"] + _reading)
    msg = "\n".join(lines)
    return {"ok": True, "gpus": parsed, "runtime_snapshot": runtime_snapshot, "content": msg, "response": msg}


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
        # Phase 9 fix (2026-05-11): identity hits often repeat verbatim
        # ("Capability inventory updated: ...") because the proactive daemon
        # writes the same line at every tick. De-dupe so the user sees
        # distinct evidence rather than the same line N times.
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
        # Show the PUBLIC mode name (quick/normal/advanced/research/expert), never the
        # internal key (chain_of_thought/self_consistency/...). The internal keys were kept
        # stable under the hood, but they must not surface to the user — the public scheme
        # is quick/normal/advanced/research/expert.
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


def _explain_cognition_runtime_report() -> Dict[str, Any]:
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

    if settings.get('max_tokens') not in (None, ''):
        lines.append(f"- max_tokens: {settings.get('max_tokens')}")
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
            "settings": Path(paths.project_root) / "config" / "settings.json",
            "capability_manifest": Path(paths.project_root) / "capability_manifest.json",
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


def _memory_capability_truth() -> str:
    """Truth-only memory capability statement. Never fabricate recall."""
    try:
        has_adapter = False
        has_sqlite = False
        try:
            from eli.memory.memory_adapter import memory_recall as _mr  # noqa: F401
            has_adapter = True
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            import sqlite3 as _sql  # noqa: F401
            has_sqlite = True
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        if has_adapter or has_sqlite:
            return (
                "I use two memory layers: session context and persistent local memory. "
                "Persistent memory is stored locally (SQLite/artifacts via memory adapters) "
                "and can be recalled across restarts if saved and retrievable by query."
            )
        return (
            "I currently have session context in this runtime; persistent memory backends are "
            "not available right now. I won’t claim cross-session recall unless retrieval succeeds."
        )
    except Exception:
        return (
            "I won’t guess about memory state. Session context is available; persistent recall "
            "depends on configured local memory backends and successful retrieval."
        )


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

__all__ = ['_canonical_runtime_file_map', '_runtime_health_probes', '_format_runtime_audit', '_gpu_status_report', '_diagnose_executor_wrappers', '_format_runtime_paths', '_format_gui_runtime_audit', '_format_memory_runtime', '_explain_all_reasoning_modes_report', '_format_all_reasoning_modes', '_explain_cognition_runtime_report', '_format_runtime_status', '_format_memory_status', '_runtime_boot_status', '_memory_capability_truth', '_live_memory_audit', '_format_memory_audit_for_chat']
