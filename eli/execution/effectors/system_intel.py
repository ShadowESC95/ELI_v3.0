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


def _canonical_runtime_file_map(*a, **k):
    # The executor's copy is the maintained one; this one had fallen behind it.
    from eli.execution.executor_enhanced import _canonical_runtime_file_map as _f
    return _f(*a, **k)


def _runtime_health_probes(*a, **k):
    # The executor's copy is the maintained one; this one had fallen behind it.
    from eli.execution.executor_enhanced import _runtime_health_probes as _f
    return _f(*a, **k)


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


def _gpu_status_report(*a, **k):
    # The executor's copy is the maintained one; this one had fallen behind it.
    from eli.execution.executor_enhanced import _gpu_status_report as _f
    return _f(*a, **k)


def _mib(value) -> str:
    return f"{float(value):.0f} MiB" if value is not None else "unknown"


def gpu_use_facts(report: Dict[str, Any]) -> Dict[str, Any]:
    """What the GPU reading means, as facts: each card on its own (a "1%" was the busiest of two cards,
    read while ELI was idle), whether ELI was generating when it was read, what the cards did during
    ELI's last generation where that was measured, and how the model is split between the cards and
    the CPU. {"lines": [...], "devices": [...], "generating": bool, "offload": {...}}; the words a
    person reads are the model's (or this list, shown as it is, for a bare "gpu status")."""
    telem = dict((report or {}).get("gpu") or {})
    snap = dict((report or {}).get("runtime_snapshot") or {})
    lines: List[str] = []
    devices = [dict(d) for d in (telem.get("devices") or [])]
    if devices:
        lines.append(f"Each GPU, read now ({len(devices)} card{'s' if len(devices) != 1 else ''}):")
        for i, d in enumerate(devices):
            bits = [f"use {d['util_pct']:.0f}%" if d.get("util_pct") is not None else "use not reported"]
            if d.get("total_mb") is not None:
                bits.append(f"VRAM {_mib(d.get('used_mb'))} of {_mib(d.get('total_mb'))}")
            if d.get("temp_c") is not None:
                bits.append(f"{d['temp_c']:.0f} C")
            if d.get("power_w") is not None:
                bits.append(f"{float(d['power_w']):.0f} W")
            lines.append(f"- GPU {d.get('index', i)} {d.get('name') or ''}: " + ", ".join(bits))
        if len(devices) > 1 and telem.get("util_pct") is not None:
            lines.append(f"- the single utilisation figure above ({telem['util_pct']}%) is the busiest card's")

    generating = False
    try:
        from eli.cognition import inference_broker as _broker
        generating = bool(_broker.foreground_in_progress())
        took = float(getattr(_broker, "_last_foreground_duration", 0.0) or 0.0)
    except Exception:
        log.debug("generation state unread", exc_info=True)
        took = 0.0
    lines.append("When read: ELI was " + ("generating a reply." if generating else
                 "not generating anything, so this is the cards at rest, not the cards at work."))
    during = None
    try:
        from eli.core import hardware_profile as _hp
        _record = getattr(_hp, "gpu_use_during_last_generation", None)
        during = _record() if callable(_record) else None
    except Exception:
        log.debug("last generation's GPU use unread", exc_info=True)
    if during and during.get("devices"):
        lines.append(f"During ELI's last generation ({float(during.get('seconds') or took):.0f} s, measured):")
        for d in during["devices"]:
            lines.append(f"- GPU {d.get('index')}: average use {float(d.get('util_avg') or 0):.0f}%, "
                         f"peak {float(d.get('util_max') or 0):.0f}% over {int(d.get('samples') or 0)} readings")
    else:
        lines.append("During ELI's last generation: no reading of the cards was taken"
                     + (f" (it ran {took:.0f} s)." if took else "."))

    offload: Dict[str, Any] = {}
    eff = dict(snap.get("effective") or {})
    on_gpu = eff.get("n_gpu_layers", snap.get("n_gpu_layers"))
    try:
        from eli.core.gguf_sizes import weights
        w = weights(snap.get("model_path")) if snap.get("model_path") else None
    except Exception:
        log.debug("model layers unread", exc_info=True)
        w = None
    if w is not None and on_gpu is not None:
        n = int(on_gpu)
        layers_gpu = min(n, w.blocks)
        experts_in_ram = bool(snap.get("moe_expert_offload"))
        gpu_bytes = w.gpu_bytes(n, experts_in_ram=experts_in_ram)
        all_bytes = sum(w.block_bytes) + sum(w.expert_bytes) + w.output_bytes
        offload = {"layers_on_gpu": layers_gpu, "layers": w.blocks, "layers_on_cpu": w.blocks - layers_gpu,
                   "weights_on_gpu_mb": gpu_bytes / 2 ** 20, "weights_mb": all_bytes / 2 ** 20}
        lines.append(f"The model: {layers_gpu} of its {w.blocks} layers on the GPUs, {w.blocks - layers_gpu} on "
                     f"the CPU; {gpu_bytes / 2 ** 30:.1f} GB of its {all_bytes / 2 ** 30:.1f} GB of weights on the "
                     f"GPUs" + (" (its expert weights stay in system RAM)" if experts_in_ram else "") + ".")
    split = snap.get("tensor_split")
    if isinstance(split, (list, tuple)) and len(split) > 1 and any(float(x or 0) for x in split):
        total = sum(float(x or 0) for x in split)
        share = ", ".join(f"GPU {i} {float(x) / total:.0%}" for i, x in enumerate(split))
        offload["tensor_split"] = list(split)
        lines.append(f"Split between the cards (tensor_split {list(split)}): {share} of the GPU layers.")
    return {"lines": lines, "devices": devices, "generating": generating, "during": during, "offload": offload}


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


def _format_memory_runtime(*a, **k):
    # The executor's copy is the maintained one; this one had fallen behind it.
    from eli.execution.executor_enhanced import _format_memory_runtime as _f
    return _f(*a, **k)


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


def _explain_cognition_runtime_report(*a, **k):
    # The executor's copy is the maintained one; this one had fallen behind it.
    from eli.execution.executor_enhanced import _explain_cognition_runtime_report as _f
    return _f(*a, **k)


def _format_runtime_status(*a, **k):
    # The executor's copy is the maintained one; this one had fallen behind it.
    from eli.execution.executor_enhanced import _format_runtime_status as _f
    return _f(*a, **k)


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

    root = Path(__file__).resolve().parents[3]  # project root (contains artifacts/, src/)
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
