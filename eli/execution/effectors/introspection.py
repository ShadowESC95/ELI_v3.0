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
