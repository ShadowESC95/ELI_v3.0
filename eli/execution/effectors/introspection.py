"""Introspection effector — runtime/status/audit report dispatch.

Thin dispatch only: the report/format helpers stay in the executor /
system_intel (they are shared, ~300-helper machinery the decomposition keeps).
Handlers reach them via a lazy import of the executor namespace, which
re-exports system_intel's helpers alongside its own.
"""
from __future__ import annotations

import json

from eli.utils.log import get_logger

log = get_logger(__name__)


def _ex():
    from eli.execution import executor_enhanced as _e
    return _e


def _h_image_status(a, args):
    try:
        from eli.runtime.evidence_ledger import status_evidence
        query = ""
        if isinstance(args, dict):
            query = str(args.get("query") or args.get("message") or args.get("text") or "")
        rep = status_evidence(query or "image status")
        txt = "Image/status evidence packet:\n" + json.dumps(rep, indent=2, ensure_ascii=False, default=str)
        return {'ok': True, 'action': a, 'report': rep, 'content': txt, 'response': txt}
    except Exception as _e:
        msg = f"Image/status evidence failed: {_e!r}"
        return {'ok': False, 'action': a, 'error': repr(_e), 'content': msg, 'response': msg}


def _h_runtime_audit(a, args):
    e = _ex(); rep = e._runtime_audit_report(); txt = e._format_runtime_audit(rep)
    return {'ok': True, 'action': a, 'report': rep, 'content': txt, 'response': txt}


def _h_import_audit(a, args):
    e = _ex(); rep = e._import_audit_report(); txt = e._format_import_audit(rep)
    return {'ok': True, 'action': a, 'report': rep, 'content': txt, 'response': txt}


def _h_diagnose_wrappers(a, args):
    e = _ex(); rep = e._diagnose_executor_wrappers(); txt = e._format_executor_wrappers(rep)
    return {'ok': True, 'action': a, 'report': rep, 'content': txt, 'response': txt}


def _h_resolve_runtime_paths(a, args):
    e = _ex(); rep = e._resolve_runtime_paths_report(); txt = e._format_runtime_paths(rep)
    return {'ok': True, 'action': a, 'report': rep, 'content': txt, 'response': txt}


def _h_explain_all_reasoning_modes(a, args):
    e = _ex(); rep = e._explain_all_reasoning_modes_report(); txt = e._format_all_reasoning_modes(rep)
    return {'ok': bool(rep.get('ok', True)), 'action': a, 'report': rep, 'content': txt, 'response': txt}


def _h_explain_memory_runtime(a, args):
    e = _ex(); rep = e._explain_memory_runtime_report(); txt = e._format_memory_runtime(rep)
    return {'ok': True, 'action': a, 'report': rep, 'content': txt, 'response': txt}


def _h_explain_cognition_runtime(a, args):
    e = _ex(); rep = e._explain_cognition_runtime_report(); txt = e._format_cognition_runtime(rep)
    return {'ok': bool(rep.get('ok', True)), 'action': a, 'report': rep, 'content': txt, 'response': txt}


def _h_runtime_status(a, args):
    e = _ex(); rep = e._runtime_status_report(); txt = e._format_runtime_status(rep)
    return {'ok': bool(rep.get('ok', True)), 'action': a, 'report': rep, 'content': txt, 'response': txt}


def _h_gpu_status(a, args):
    e = _ex(); rep = e._gpu_status_report()
    return {'ok': bool(rep.get('ok', False)), 'action': a, 'report': rep,
            'content': rep.get('content', ''), 'response': rep.get('response', rep.get('content', ''))}


def _h_memory_status(a, args):
    e = _ex(); rep = e._memory_status_report(); txt = e._format_memory_status(rep)
    try:
        txt += '\n\n' + e._get_db_schema_evidence()
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return {'ok': bool(rep.get('ok', True)), 'action': a, 'report': rep, 'content': txt, 'response': txt}


def _h_cognition_status(a, args):
    e = _ex(); rep = e._cognition_status_report(); txt = e._format_cognition_status(rep)
    return {'ok': bool(rep.get('ok', True)), 'action': a, 'report': rep, 'content': txt, 'response': txt}


def _h_gui_runtime_audit(a, args):
    _q = ""
    if isinstance(args, dict):
        _q = str(args.get("question") or args.get("message") or args.get("text") or "")
    e = _ex(); rep = e._gui_runtime_audit_report(_q); txt = e._format_gui_runtime_audit(rep)
    return {'ok': True, 'action': a, 'report': rep, 'content': txt, 'response': txt}


def _h_orchestration_status(a, args):
    try:
        from eli.cognition.agent_bus import orchestration_snapshot
        snap = orchestration_snapshot()
        lines = [
            f"Agent orchestration ({snap.get('engine')}): "
            f"{snap.get('count')} agents, critical path {snap.get('critical_path')} layer(s).",
            "Execution layers (each runs in parallel):",
        ]
        for i, layer in enumerate(snap.get("execution_layers") or []):
            lines.append(f"  layer {i}: {', '.join(layer)}")
        if snap.get("dependencies"):
            lines.append("Dependencies: " + "; ".join(
                f"{k}←{','.join(v)}" for k, v in snap["dependencies"].items()))
        last = snap.get("last_run")
        if last:
            lines.append(f"Last run: ok={last.get('ok')} "
                         f"{last.get('seconds')}s, failed={last.get('failed')}, "
                         f"skipped={last.get('skipped')}")
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "evidence_source": "agent_orchestrator", "result": snap}
    except Exception as e:
        msg = f"ORCHESTRATION_STATUS failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


def _h_eli_identity_audit(a, args):
    try:
        from eli.runtime.eli_identity_audit import (
            build_eli_identity_audit,
            format_eli_identity_audit,
        )

        report = build_eli_identity_audit(str(args.get("question") or args.get("query") or ""))
        text = format_eli_identity_audit(report)
        return {
            "ok": bool(report.get("ok", True)),
            "action": a,
            "report": report,
            "content": text,
            "response": text,
            "evidence_source": "eli_identity_audit_local_verified_matrix_v1",
        }
    except Exception as e:
        msg = f"ELI identity audit failed: {type(e).__name__}: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


def _h_codebase_graph(a, args):
    """ELI answers questions about its OWN architecture from the live import graph of
    its source (codebase_graph.py) — grounded in what the code actually imports, never
    hand-drawn. The user's phrasing resolves component names against the real graph."""
    try:
        from eli.runtime import codebase_graph as _cg
        question = str((args or {}).get("question") or (args or {}).get("text") or "").strip()
        msg = _cg.explain(question)
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "evidence_source": "codebase_graph",
                "result": {"graph": _cg.build_graph(), "question": question}}
    except Exception as e:
        msg = f"CODEBASE_GRAPH failed: {type(e).__name__}: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


def _h_autopilot_debug(a, args):
    """Turn a pasted traceback / pytest failure into a grounded plan — root cause,
    affected files, suspect commits (git), rollback plan, patch plan (relevance-filtered
    code_examiner scan), validation commands. Assembles existing parts; invents nothing."""
    try:
        from eli.runtime import autopilot_debugger as _dbg
        _a = args or {}
        error_text = str(_a.get("error_text") or _a.get("text")
                         or _a.get("traceback") or _a.get("query") or "")
        targets = _a.get("targets") or _a.get("files") or []
        if isinstance(targets, str):
            targets = [t.strip() for t in targets.split(",") if t.strip()]
        apply = bool(_a.get("apply") or _a.get("fix"))
        verify = bool(_a.get("verify") or _a.get("reproduce"))
        if apply or verify:
            # Closed loop: reproduce (run validation), and if apply — attempt a validated,
            # auto-reverting fix and re-verify. Governed; never commits.
            trace = _dbg.resolve(error_text=error_text, targets=list(targets), apply=apply)
            msg = _dbg.format_resolution(trace)
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "evidence_source": "autopilot_debugger", "result": trace}
        verdict = _dbg.diagnose(error_text=error_text, targets=list(targets),
                                run_tests=bool(_a.get("run_tests")))
        msg = _dbg.format_report(verdict)
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "evidence_source": "autopilot_debugger", "result": verdict}
    except Exception as e:
        msg = f"AUTOPILOT_DEBUG failed: {type(e).__name__}: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "IMAGE_STATUS": _h_image_status,
    "RUNTIME_AUDIT": _h_runtime_audit,
    "IMPORT_AUDIT": _h_import_audit,
    "DIAGNOSE_WRAPPERS": _h_diagnose_wrappers,
    "RESOLVE_RUNTIME_PATHS": _h_resolve_runtime_paths,
    "EXPLAIN_ALL_REASONING_MODES": _h_explain_all_reasoning_modes,
    "EXPLAIN_MEMORY_RUNTIME": _h_explain_memory_runtime,
    "EXPLAIN_COGNITION_RUNTIME": _h_explain_cognition_runtime,
    "RUNTIME_STATUS": _h_runtime_status,
    "GPU_STATUS": _h_gpu_status,
    "MEMORY_STATUS": _h_memory_status,
    "COGNITION_STATUS": _h_cognition_status,
}.items():
    _register(_action, _fn)

_register("GUI_RUNTIME_AUDIT", _h_gui_runtime_audit)

_register("ORCHESTRATION_STATUS", _h_orchestration_status)
_register("ELI_IDENTITY_AUDIT", _h_eli_identity_audit)
_register("CODEBASE_GRAPH", _h_codebase_graph)
_register("AUTOPILOT_DEBUG", _h_autopilot_debug)
