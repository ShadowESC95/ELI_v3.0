from __future__ import annotations
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

def _eli_path_get(obj, key, default=None):
    """
    Compatibility helper for ELI path containers.
    Accepts both dict-style path maps and object/namespace-style path maps.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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

# ── Security gate (lazy singleton) ───────────────────────────────────────────

# ── apps_windows family + _base primitives moved to eli.execution.effectors ──
from eli.execution.effectors.apps_windows import (  # noqa: F401
    _eli_open_url_action,
    _eli_tile_windows,
    _open_app_with_timeout,
    open_browser,
    open_file_system,
)


# --- Memory bridge: route through canonical Memory class (not memory_db) ---
# memory_db has a different schema (model/dim/vec columns) than Memory (kind/source/confidence/weight).
# Using Memory directly avoids the "no such column: model" crash.


# ── memory_ops family moved to eli.execution.effectors.memory_ops ──
from eli.execution.effectors.memory_ops import (  # noqa: F401
    _convlog_append,
    _get_canonical_memory,
    memory_recall,
    memory_store,
)


def add_memory(text, tags=""):
    """Store via canonical Memory.store_memory()."""
    try:
        mem = _get_canonical_memory()
        tag_list = [t.strip() for t in str(tags).split(",") if t.strip()] if tags else []
        return mem.store_memory(text, tags=tag_list)
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- Compatibility shim: some older code expects search_memory_compat() ---


def _browser_user_dir() -> Path:
    try:
        base = Path(get_paths().config_dir)
    except Exception:
        base = Path.home() / ".config" / "eli"
    return (base / "browser").expanduser().resolve()

# === CONVERSATION LOGGING (append-only) ===


# === END CONVERSATION LOGGING ===

# === MEMORY ===



def _sqlite_init(conn):
    # Canonical Memory manages schema; keep as no-op for compatibility.
    return None







# ── inference_backends (ollama + default-model) moved to effectors ──
from eli.execution.effectors.inference_backends import (  # noqa: F401
    DEFAULT_CHAT_MODEL,
    OLLAMA_HOST,
    _ollama_chat_raw,
    _ollama_model_digest_from_ps,
    _ollama_ps,
    _ollama_warm_load,
    _strip_ollama_artifacts,
)



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




def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()



# ── Media family + effector primitives moved to eli.execution.effectors ──
# (v3 modularisation; imported back so the dispatch + external callers are unchanged).
from eli.execution.effectors._base import (  # noqa: F401
    _full_control,
    _open_in_browser,
    _run_argv,
)
from eli.execution.effectors.media import (  # noqa: F401
    _get_active_player,
    _mpv_alive,
    _mpv_ipc,
    _mpv_quit,
    _playerctl,
    _resolve_media_target,
    _spotify_is_playing,
    _targets_mpv,
    _yt_mix_url,
    play_media,
    play_specific,
)







# ----------------------------
# Grounded runtime audit helpers
# ----------------------------

# ── system_intel report family moved to eli.execution.effectors.system_intel ──
from eli.execution.effectors.system_intel import (  # noqa: F401
    _canonical_runtime_file_map,
    _diagnose_executor_wrappers,
    _explain_all_reasoning_modes_report,
    _explain_cognition_runtime_report,
    _format_all_reasoning_modes,
    _format_gui_runtime_audit,
    _format_memory_audit_for_chat,
    _format_memory_runtime,
    _format_memory_status,
    _format_runtime_audit,
    _format_runtime_paths,
    _format_runtime_status,
    _gpu_status_report,
    _live_memory_audit,
    _runtime_boot_status,
    _runtime_health_probes,
)


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



def _runtime_audit_report() -> Dict[str, Any]:
    files = _canonical_runtime_file_map()
    entries = []
    for key in ['cognitive_engine', 'gguf_inference', 'memory', 'memory_init', 'router', 'executor', 'gui', 'runtime_settings', 'paths', 'proactive_daemon']:
        entries.append(_audit_python_file(files[key]))
    probes = _runtime_health_probes()
    return {'ok': True, 'entries': entries, 'health_probes': probes}




# ── self_maintenance family moved to eli.execution.effectors.self_maintenance ──
from eli.execution.effectors.self_maintenance import (  # noqa: F401
    _self_improvement_log_report,
    proactive_start,
    proactive_status,
    proactive_stop,
)
import eli.execution.effectors.gaze  # noqa: F401
import eli.execution.effectors.input_control  # noqa: F401
import eli.execution.effectors.window_control  # noqa: F401
import eli.execution.effectors.content_analysis  # noqa: F401
import eli.execution.effectors.document_analysis  # noqa: F401
import eli.execution.effectors.document_creation  # noqa: F401
import eli.execution.effectors.introspection  # noqa: F401
import eli.execution.effectors.status_reports  # noqa: F401
import eli.execution.effectors.voice_input  # noqa: F401
import eli.execution.effectors.coding  # noqa: F401
import eli.execution.effectors.plugins  # noqa: F401
import eli.execution.effectors.persona_lock  # noqa: F401
import eli.execution.effectors.pomodoro  # noqa: F401
import eli.execution.effectors.wake_word  # noqa: F401
import eli.execution.effectors.self_improve  # noqa: F401
import eli.execution.effectors.system_open  # noqa: F401
import eli.execution.effectors.web_info  # noqa: F401
import eli.execution.effectors.timers  # noqa: F401
import eli.execution.effectors.jobs  # noqa: F401
import eli.execution.effectors.screen  # noqa: F401
import eli.execution.effectors.file_ops  # noqa: F401


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


def _gui_runtime_audit_report(question: Optional[str] = None) -> Dict[str, Any]:
    canon = _canonical_runtime_file_map()
    gui_path = canon['gui']
    # Dynamic target: if the user explicitly referenced a source file, audit
    # THAT file (grounded, AST-derived) instead of the hardcoded GUI file. The
    # old behaviour ignored the referenced path entirely and always reported on
    # the GUI — so a request about api/server.py came back describing the GUI.
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
    text = gui_path.read_text(encoding='utf-8', errors='replace')
    lines = text.splitlines()
    patterns = {
        'router': 'router_enhanced',
        'executor': 'executor_enhanced',
        'cognition': 'cognitive_engine',
        'proactive_import': 'start_daemon',
        'send_worker': 'threading.Thread(target=generate_worker',
    }
    for key, needle in patterns.items():
        hits = [i for i, line in enumerate(lines, 1) if needle in line]
        entry['evidence'][key] = hits
        # Store actual code at the first 3 matching lines so the model reports
        # what is really there rather than fabricating plausible-looking code.
        entry['snippets'][key] = [
            f"  line {lineno}: {lines[lineno-1].strip()}"
            for lineno in hits[:3]
        ]
        if not hits:
            entry['status'] = 'FAIL'
            entry['issues'].append({'type': 'missing_hook', 'line': 0, 'message': f'{key} hook not found: {needle}'})
    entry['issues'].extend(_scan_user_specific_paths(lines))
    if entry['issues']:
        entry['status'] = 'FAIL'
    return {'ok': True, 'entry': entry}



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


_SUMMARY_SYSTEM = ("You are a precise technical analyst. Summarize key points, "
                   "structure, and purpose. Ground every statement in the text "
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
        ctx_tokens = int(_gi.current_context_limit() or 0)
    except Exception:
        ctx_tokens = 0
    if ctx_tokens <= 0:
        ctx_tokens = 4096
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
        out: Dict[str, int] = {}
        try:
            p = Path(path)
            if not p.exists():
                return out
            conn = sqlite3.connect(str(p))
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            for (table_name,) in rows:
                try:
                    count_row = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
                    out[str(table_name)] = int((count_row or [0])[0] or 0)
                except Exception:
                    out[str(table_name)] = -1
            conn.close()
        except Exception:
            return out
        return out

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
                    query = (
                        f'SELECT "{text_col}" FROM "{table}" '
                        f'WHERE lower("{text_col}") LIKE "%name%" '
                        f'OR lower("{text_col}") LIKE "%identity%" '
                        f'OR lower("{text_col}") LIKE "%preference%" '
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
        idx_path, meta_path = _vs._get_index_paths()
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

    # Enumerate ALL physical sqlite3 files actually on disk in the db directory —
    # not just the logical roles. The role resolver aliases active_db/user_db/
    # memory_db to the SAME user.sqlite3 and never sees siblings like
    # coding_memory.sqlite3 or system_index.sqlite3, so the report claimed "2
    # databases" when there are 4 on disk (user-reported, 2026-06-06: "you have at least
    # 4 sqlite databases"). Ground the count in the filesystem.
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

    # ── Live mechanism probes (#5/Option 4) ───────────────────────────────
    # Re-derive every claim about ELI's own internals each call rather than
    # hardcoding prose: ELI can add/remove its own modules and tables, so a
    # static description would lie. Probe the filesystem for each mechanism
    # module and the live schema for FTS5 mirrors + knowledge-graph counts.
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
        'identity_hits': [
            str((h.get('text') or h.get('content') or '') if isinstance(h, dict) else h).strip()[:160]
            for h in identity_hits[:5]
        ],
        'recent_turn_count': min(6, int(status.get("conversation_turns", 0) or len(recent or []))),
    }







def _format_cognition_runtime(report: Dict[str, Any]) -> str:
    if not report.get('ok'):
        return str(report.get('error') or 'Cognition runtime report failed')
    lines = [
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
        # The prefix whitelist kept the count line ("physical_db_files: 4" / "Stores — 4
        # physical …") but DROPPED the per-file enumeration under "Stores —" (those lines
        # start with "- <name>.sqlite3" / "    tables: …", matching no prefix), so the report
        # asserted 4 files yet listed only the 2-distinct-path logical roles. Keep the whole
        # "Stores —" block so the claimed COUNT is substantiated by the ACTUAL files on disk.
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

    # Effective runtime resolution order (most → least authoritative):
    #   1. runtime_snapshot.json on disk — written by the loader after the
    #      llama.cpp load attempt that actually succeeded. This is the truth
    #      even if the in-process gguf object is stale or absent.
    #   2. gguf_inference live attributes — when llama is loaded in-process.
    #   3. Empty dict (effective values become "unknown" in the formatter,
    #      not silently substituted with settings).
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

    # The four core load params (n_ctx, n_gpu_layers, n_threads, n_batch) are
    # ONLY trusted from the disk snapshot — that file is written by the loader
    # AFTER a successful llama.cpp load, so it reflects the clamped truth.
    # In-process gguf_inference may echo settings/defaults if the model isn't
    # actually loaded yet; allowing it to overlay would mask boot-time fallbacks.
    # We do let it contribute non-load metadata: provider, model_path, model_name,
    # loaded flag.
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


# Work out the chat model at runtime — I never hardcode a model name anywhere.
# Ollama is just a fallback the user can attach; the real path is GGUF via
# llama-cpp-python. When someone picks an Ollama model in the GUI it lands in
# runtime_settings as "ollama_model" and I read it back here.
# Order I check: ELI_CHAT_MODEL env -> OLLAMA_MODEL env (legacy) ->
# settings["ollama_model"] -> "" (empty, so the caller has to be explicit instead
# of me guessing).



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
        folder = str(args.get("folder") or args.get("path") or "").strip()
        if not folder:
            return None
        from pathlib import Path as _PB
        exp = os.path.expanduser(folder)
        if not os.path.isdir(exp):
            return None  # single file is fast enough inline
        try:
            _n_pdfs = sum(1 for _ in _PB(exp).rglob("*.pdf"))
        except Exception:
            _n_pdfs = 0
        _threshold = int(os.environ.get("ELI_FILE_ANALYSIS_BG_THRESHOLD", "5") or 5)
        # Background when there are enough PDFs to be slow, OR the user asked.
        _asked = bool(args.get("_force_background"))
        if _n_pdfs < _threshold and not _asked:
            return None
        from eli.runtime.background_tasks import get_background_tasks
        bt = get_background_tasks()
        _bg_args = dict(args)
        _bg_args["_no_background"] = True
        _bg_args["folder"] = folder
        _label = os.path.basename(folder.rstrip("/")) or folder
        jid = bt.submit(f"Analyse {_n_pdfs} PDFs: {_label}",
                        lambda: execute("ANALYZE_PDF_FOLDER", _bg_args))
        msg = (f"That folder has {_n_pdfs} PDFs — reading and summarising them properly takes time, "
               f"so I've started it in the background as job #{jid}. Say “check job {jid}” for the "
               f"summary when it's ready, or “background jobs” to see all running tasks.")
        return {"ok": True, "action": "ANALYZE_PDF_FOLDER", "background": True, "job_id": jid,
                "content": msg, "response": msg}
    except Exception as _bg_e:
        log.debug(f"[FILE_BG] background decision failed: {_bg_e}")
        return None


# Enumerated actions for capability_registry bootstrap
SUPPORTED_ACTIONS = [
    'SET_USER_NAME',
    'SKIP_YOUTUBE_AD',
    'ANALYZE_PDF_FOLDER',
    'IMAGE_STATUS',
    'LISTEN_FOR_COMMAND',
    'PLUGIN_STATUS',
    'STT_DIAGNOSTICS',
    'VOICE_DIAGNOSTICS',
    'ADD_EVENT',
    'BACKGROUND_JOBS',
    'SCHEDULE_TASK',
    'AMBIENT_VISION',
    'ANALYZE_CSV',
    'ANALYZE_IMAGE',
    'ANALYZE_PDF',
    'AWARENESS_STATUS',
    'CANCEL_CODE_FIX',
    'CHAT',
    'CHECK_CHRONAL_ALIGNMENT',
    'CLEAR_CHAT_HISTORY',
    'CLOSE_APP',
    'CONFIRM_CODE_FIX',
    'CONFIRM_HABIT',
    'DECLINE_HABIT',
    'CHECK_JOB',
    'CODE_CHANGES',
    'CODE_SOLVE',
    'COGNITION_STATUS',
    'CONVERT_DOCUMENT',
    'CPU_USAGE',
    'CREATE_DOCUMENT',
    'CREATE_FILE',
    'CREATE_FOLDER',
    'DATA_FABRICATOR',
    'DATE',
    'DIAGNOSE_WRAPPERS',
    'DICTATE',
    'ELI_IDENTITY_AUDIT',
    'EXAMINE_CODE',
    'EXECUTE_GOAL',
    'EXPLAIN_ALL_REASONING_MODES',
    'EXPLAIN_COGNITION_RUNTIME',
    'EXPLAIN_LAST_RESPONSE',
    'EXPLAIN_MEMORY_RUNTIME',
    'FILE_AUDIT',
    'FIX_FILE',
    'FRONTIER_STATUS',
    'FOCUS_APP',
    'GENERATE_DOCUMENT',
    'GENERATE_PROJECT',
    'GENERATE_SCRIPT',
    'GET_CLIPBOARD',
    'GET_DATE',
    'GET_STATUS',
    'GET_TIME',
    'GET_WEATHER',
    'GAZE_CALIBRATE',
    'GAZE_CLICK',
    'GAZE_DISABLE',
    'GAZE_ENABLE',
    'GAZE_STATUS',
    'GPU_STATUS',
    'GUI_RUNTIME_AUDIT',
    'HABIT_STATUS',
    'HARDWARE_PROFILE',
    'HELP',
    'IMPORT_AUDIT',
    'KEYBOARD',
    'LIST_CAPABILITIES',
    'LIST_DIR',
    'LIST_EVENTS',
    'LIST_NOTES',
    'MAXIMISE_WINDOW',
    'MEDIA_CONTROL',
    'MEMORY_RECALL',
    'MESSAGE_TIME_QUERY',
    'MEMORY_STATS',
    'MEMORY_STATUS',
    'MEMORY_STORE',
    'MINIMISE_ALL',
    'MINIMISE_APP',
    'MINIMISE_WINDOW',
    'MINIMIZE_APP',
    'MINIMIZE_WINDOW',
    'HIDE_APP',
    'MORNING_REPORT',
    'MOUSE_CONTROL',
    'NAME_SOURCE_AUDIT',
    'NEWS_FETCH',
    'NEW_NOTE',
    'NEXT_MEDIA',
    'NEXT_WINDOW',
    'OCR_IMAGE',
    'OPEN_APP',
    'OPEN_AUDIO_SETTINGS',
    'OPEN_BROWSER',
    'OPEN_COMMUNICATION_HUB',
    'OPEN_FILE_SYSTEM',
    'OPEN_IDE',
    'OPEN_IN_IDE',
    'OPEN_MEDIA_HUB',
    'OPEN_NETWORK_BROWSER',
    'OPEN_POWER_SETTINGS',
    'OPEN_SYSTEM_SETTINGS',
    'OPEN_URL',
    'PAUSE_MEDIA',
    'PERSONAL_MEMORY_DEEP_EXPLAIN',
    'PERSONAL_MEMORY_SUMMARY',
    'PERSONA_LOCK_CLEAR',
    'PERSONA_LOCK_SET',
    'PERSONA_LOCK_STATUS',
    'PLAY_MEDIA',
    'PLUGIN_DISABLE',
    'PLUGIN_ENABLE',
    'PLUGIN_INSTALL',
    'PLUGIN_LIST',
    'PLUGIN_SEARCH',
    'PLUGIN_UNINSTALL',
    'POMODORO_START',
    'POMODORO_STATUS',
    'POMODORO_STOP',
    'PREVIOUS_MEDIA',
    'GET_PROPOSALS',
    'PREVIOUS_WINDOW',
    'PROACTIVE_START',
    'PROACTIVE_STATUS',
    'PROACTIVE_STOP',
    'RAM_USAGE',
    'READ_FILE',
    'REFRESH_USER_INFO',
    'PERSONA_REFRESH',
    'REPEAT_MEDIA',
    'RESOLVE_RUNTIME_PATHS',
    'RESTORE_WINDOWS',
    'RUNTIME_AUDIT',
    'RUNTIME_STATUS',
    'REASONING_MODE_STATUS',
    'RUN_CMD',
    'SCREENSHOT',
    'SCREEN_LOCATE',
    'SCREEN_READ_ANALYZE',
    'SEARCH_NOTES',
    'SELF_ANALYZE',
    'SELF_IMPROVE',
    'SELF_IMPROVEMENT_LOG',
    'SELF_PATCH',
    'SELF_REPORT',
    'SELF_TEST',
    'RUN_TESTS',
    'GENERATE_TESTS',
    'LORA_STATUS',
    'LORA_TRAIN',
    'ORCHESTRATION_STATUS',
    'TEST_REVIEW',
    'MULTI_COMMAND',
    'SELF_UPDATE',
    'SELF_UPGRADE',
    'SEQUENCE',
    'SET_ALARM',
    'SET_CLIPBOARD',
    'SET_TIMER',
    'SHELL_EXEC',
    'SHOW_DIFF',
    'SHUFFLE_MEDIA',
    'SMART_HOME',
    'SPEAK',
    'STOP_MEDIA',
    'NOW_PLAYING',
    'SUMMARIZE_FILE',
    'SWITCH_WORKSPACE',
    'SYSTEM_STATS',
    'TILE_WINDOWS',
    'TIME',
    'TRANSCRIBE',
    'WAKE_TRAIN',
    'WAKE_ENROLL',
    'WAKE_SET',
    'TRAIN_VOICE',
    'USER_IDENTITY_SUMMARY',
    'VOLUME',
    'WEB_SEARCH',
    'WRITE_NOTE',
]


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






# ----------------------------
# Persona lock + checksum verification
# ----------------------------





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





# ----------------------------
# Self-consistency tests + healthcheck
# ----------------------------

def self_test() -> Dict[str, Any]:
    """
    Runs a deterministic suite:
      - persona lock status
      - minimal chat handshake (Ollama or GGUF fallback)
      - consistency probe (temperature=0 twice) and compare
    """
    import socket

    def _ollama_reachable() -> bool:
        try:
            s = socket.create_connection(("127.0.0.1", 11434), timeout=1)
            s.close()
            return True
        except OSError:
            return False

    st = _load_state()
    lock = st.get("persona_lock") or {}
    if lock.get("model") == "test":
        try:
            st.pop("persona_lock", None)
            _save_state(st)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    ok_lock, reason, details = _verify_persona_lock(st)

    results: Dict[str, Any] = {
        "persona_lock": {"ok": ok_lock, "reason": reason, "details": details},
        "tests": []
    }

    # ── GGUF-only fallback when Ollama is not running ──
    if not _ollama_reachable():
        try:
            from eli.cognition.gguf_inference import chat_completion
            resp = chat_completion("Reply with exactly these 2 words: persona ok", max_tokens=16)
            gguf_ok = bool(resp and resp.strip())
        except Exception as exc:
            gguf_ok = False
            resp = str(exc)
        results["tests"].append({
            "name": "handshake", "ok": gguf_ok, "model": "gguf",
            "content": (resp or "").strip(), "response": (resp or "").strip(),
        })
        # Consistency probes skipped — GGUF has no temp=0 determinism guarantee
        results["tests"].append({"name": "consistency_probe_1", "ok": gguf_ok, "model": "gguf",
                                  "content": "skipped (no Ollama)", "response": "skipped (no Ollama)"})
        results["tests"].append({"name": "consistency_probe_2", "ok": gguf_ok, "model": "gguf",
                                  "content": "skipped (no Ollama)", "response": "skipped (no Ollama)"})
        results["tests"].append({"name": "consistency_equal", "ok": gguf_ok,
                                  "content": "skipped (no Ollama)", "response": "skipped (no Ollama)"})
        overall_ok = bool(ok_lock) and gguf_ok
        msg = "SELF_TEST OK (GGUF)" if overall_ok else "SELF_TEST FAIL (GGUF)"
        return {"ok": overall_ok, "action": "SELF_TEST", "results": results, "content": msg, "response": msg}

    # ── Standard Ollama path ──
    # 1) Handshake: "Reply with exactly: persona_ok"
    handshake_prompt = "Reply with exactly these 2 words: persona ok"
    h1 = _ollama_chat_raw(handshake_prompt, temperature=0.0, num_predict=16)
    results["tests"].append({"name": "handshake", **h1})

    # 2) Consistency probe: same prompt twice with temp=0
    probe = "Reply with exactly 4 words: consistency test passed."
    p1 = _ollama_chat_raw(probe, temperature=0.0, num_predict=16)
    p2 = _ollama_chat_raw(probe, temperature=0.0, num_predict=16)
    same = (p1.get("content") == p2.get("content")) and p1.get("ok") and p2.get("ok")
    results["tests"].append({"name": "consistency_probe_1", **p1})
    results["tests"].append({"name": "consistency_probe_2", **p2})
    results["tests"].append({"name": "consistency_equal", "ok": same, "content": str(same), "response": str(same)})

    # overall ok
    overall_ok = bool(ok_lock) and bool(h1.get("ok")) and bool(p1.get("ok")) and bool(p2.get("ok"))
    msg = "SELF_TEST OK" if overall_ok else "SELF_TEST FAIL"
    return {"ok": overall_ok, "action": "SELF_TEST", "results": results, "content": msg, "response": msg}




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


def _origin_facts(st: Dict[str, Any]) -> str:
    name = (st.get("user_name") or "").strip() or "the user"
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




# ── Browser/streaming service → MPRIS player alias map ──────────────────────






# ── media transport API moved to eli.execution.effectors.media ──
from eli.execution.effectors.media import (  # noqa: F401
    next_media,
    now_playing,
    pause_media,
    previous_media,
    repeat_media,
    shuffle_media,
    stop_media,
)































# ── Now-playing state + headless-mpv (YouTube) control ───────────────────────
# "Play on YouTube" launches a HEADLESS mpv (audio only, no window) with a JSON IPC
# control socket. playerctl can't see that mpv (no MPRIS), so pause/stop/resume must
# talk to the socket directly. We also remember the last source + title so ELI can
# answer "what's playing?" and aim controls at the right player.















# ================== END MEDIA CONTROL ==================

from eli.execution.effectors._base import _run_ok  # noqa: F401


def _volume_fallback(direction: str, delta: int = 10, level: int | None = None) -> Dict[str, Any]:
    direction = (direction or '').strip().lower()
    try:
        if direction == 'set' and level is not None:
            if shutil.which('wpctl') and _run_ok(['wpctl', 'set-volume', '@DEFAULT_AUDIO_SINK@', f'{int(level)}%']):
                msg = f'Volume set to {int(level)}%'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
            if shutil.which('pactl') and _run_ok(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{int(level)}%']):
                msg = f'Volume set to {int(level)}%'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
        if direction in ('up', 'raise'):
            if shutil.which('wpctl') and _run_ok(['wpctl', 'set-volume', '@DEFAULT_AUDIO_SINK@', f'{int(delta)}%+']):
                msg = 'Volume up'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
            if shutil.which('pactl') and _run_ok(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'+{int(delta)}%']):
                msg = 'Volume up'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
            if shutil.which('amixer') and _run_ok(['amixer', '-D', 'pulse', 'sset', 'Master', f'{int(delta)}%+']):
                msg = 'Volume up'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
        if direction in ('down', 'lower'):
            if shutil.which('wpctl') and _run_ok(['wpctl', 'set-volume', '@DEFAULT_AUDIO_SINK@', f'{int(delta)}%-']):
                msg = 'Volume down'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
            if shutil.which('pactl') and _run_ok(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'-{int(delta)}%']):
                msg = 'Volume down'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
            if shutil.which('amixer') and _run_ok(['amixer', '-D', 'pulse', 'sset', 'Master', f'{int(delta)}%-']):
                msg = 'Volume down'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
        if direction == 'mute':
            if shutil.which('wpctl') and _run_ok(['wpctl', 'set-mute', '@DEFAULT_AUDIO_SINK@', '1']):
                msg = 'Muted'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
            if shutil.which('pactl') and _run_ok(['pactl', 'set-sink-mute', '@DEFAULT_SINK@', '1']):
                msg = 'Muted'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
        if direction == 'unmute':
            if shutil.which('wpctl') and _run_ok(['wpctl', 'set-mute', '@DEFAULT_AUDIO_SINK@', '0']):
                msg = 'Unmuted'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
            if shutil.which('pactl') and _run_ok(['pactl', 'set-sink-mute', '@DEFAULT_SINK@', '0']):
                msg = 'Unmuted'
                return {'ok': True, 'action': 'VOLUME', 'content': msg, 'response': msg}
    except Exception as e:
        return {'ok': False, 'action': 'VOLUME', 'error': str(e), 'content': str(e), 'response': str(e)}
    msg = 'Volume control failed'
    return {'ok': False, 'action': 'VOLUME', 'content': msg, 'response': msg}


# ----------------------------
# CHAT with persistent conversation + lock enforcement
# ----------------------------






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

    # ── Ollama fallback (requires network / local Ollama server) ──────────────
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







def set_user_name(name: str) -> Dict[str, Any]:
    import re as _re_sun
    n = (name or "").strip()
    if not n:
        msg = "Empty name."
        return {"ok": False, "action": "SET_USER_NAME", "error": "empty_name", "content": msg, "response": msg}

    # --- Validation gate: reject phrase-like or sentence-fragment names ---
    _NAME_SENTENCE_SIGNALS = frozenset({
        "my", "by", "your", "the", "a", "an", "is", "was", "are", "were",
        "be", "been", "am", "not", "no", "yes", "ok", "okay", "it", "its",
        "this", "that", "which", "what", "who", "how", "why", "when",
        "will", "would", "could", "should", "can", "may", "might", "do",
        "does", "did", "have", "has", "had", "i", "me", "we", "you",
        "they", "he", "she", "and", "or", "but", "so", "if", "then",
        "to", "for", "of", "in", "on", "at", "with", "about", "just",
        "name", "call", "called", "calling", "named",
    })
    _tokens = n.split()
    # Reject if: too long (> 25 chars), too many tokens (> 3 words for a name),
    # or any token matches a sentence-signal word
    _is_phrase = (
        len(n) > 25
        or len(_tokens) > 3
        or any(t.lower() in _NAME_SENTENCE_SIGNALS for t in _tokens)
        or bool(_re_sun.search(r"[^A-Za-z0-9'\-\. ]", n))  # exotic punctuation
    )
    if _is_phrase:
        log.debug(f"[EXECUTOR] set_user_name: rejected phrase-like name {n!r}")
        msg = "That doesn't look like a name — please say just the name."
        return {"ok": False, "action": "SET_USER_NAME", "error": "invalid_name", "content": msg, "response": msg}
    # Write to runtime state (legacy path)
    st = _load_state()
    st["user_name"] = n
    st["updated_at"] = time.time()
    _save_state(st)
    # Write to user_profile.json (authoritative path read by get_user_name())
    try:
        from eli.kernel.state import set_user_name as _state_set_user_name
        _state_set_user_name(n)
    except Exception as _sue:
        log.debug(f"[EXECUTOR] set_user_name profile write failed: {_sue}")
    # Self-heal stale identity patterns: when the name is (re)set, purge any
    # identity.name / identity.preferred_name / identity.nickname rows in
    # user_patterns that do NOT match the new name. This is what stops a wrong
    # earlier value (e.g. a mis-extracted "speak") from surfacing forever, for
    # ANY user — without it, set_user_name updated the profile but left the bad
    # pattern behind.
    try:
        _purge_conflicting_identity_patterns(n)
    except Exception as _pe:
        log.debug(f"[EXECUTOR] identity-pattern purge failed (non-fatal): {_pe}")
    msg = f"Got it. I'll call you {n}."
    return {"ok": True, "action": "SET_USER_NAME", "user_name": n, "content": msg, "response": msg}


def _purge_conflicting_identity_patterns(new_name: str) -> int:
    """Delete identity.* user_patterns rows whose value conflicts with new_name.

    Generic, user-agnostic: keeps only identity name/preferred/nickname patterns
    that actually contain the confirmed name; removes the rest. Returns count.
    """
    import sqlite3 as _sq
    nm = (new_name or "").strip().lower()
    if not nm:
        return 0
    try:
        from eli.memory.memory import get_memory as _gm
        _db = str(_gm().db_path)
    except Exception:
        from eli.core.paths import get_paths as _gp
        _db = str(_gp().artifacts_dir / "db" / "user.sqlite3")
    conn = _sq.connect(_db)
    try:
        rows = conn.execute(
            "SELECT rowid, COALESCE(pattern_data,'') FROM user_patterns "
            "WHERE lower(COALESCE(pattern_type,'')) IN "
            "('identity.preferred_name','identity.name','identity.nickname')"
        ).fetchall()
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
        "user_name": st.get("user_name", ""),
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

# ── tts_speech family moved to eli.execution.effectors.tts_speech ──
from eli.execution.effectors.tts_speech import (  # noqa: F401
    _eli_speak_piper,
    _speak,
)

_MIC_IGNORE_UNTIL = 0.0  # ignore mic until this monotonic time
_BUSY_UNTIL = 0.0  # ignore mic while executing actions
_STOP_REQUESTED = False






# --- TTS PATCH ---
import tempfile
from eli.utils.platform_compat import open_url, open_file, notify, play_sound, WINDOWS

# ---- Ollama host canonical config ----
_OLLAMA_HOST = OLLAMA_HOST  # backward-compatible alias
CHAT_TIMEOUT_S = (10, 3600)  # connect/read timeout for Ollama chat


def _eli_set_pulse_sink():
    sink = (os.environ.get("ELI_OUTPUT_DEVICE") or "").strip()
    if sink:
        os.environ["PULSE_SINK"] = sink





# --- PIPER TTS HOOK ---


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


# ── files_notes family moved to eli.execution.effectors.files_notes ──
from eli.execution.effectors.files_notes import (  # noqa: F401
    _eli_safe_read_file,
    _eli_safe_screenshot,
    _eli_safe_write_note,
    _open_file,
)









# --- APP OPENING FUNCTIONS ---






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

from eli.execution.effectors._base import _artifacts_dir, _save_artifact  # noqa: F401


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

from eli.execution.effectors.registry import get as _registry_get
import eli.execution.effectors.datetime_calendar  # noqa: F401  (register handlers)

def _execute_impl(action: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    args = args or {}
    # Normalize action for dispatch (accept open_app, OPEN_APP, open-app, etc.)
    a = (str(action) if action is not None else "").strip()
    a = re.sub(r"[\s\-]+", "_", a)
    a = a.upper()

    # Action synonyms: the model sometimes emits an action name that means an
    # existing handler but isn't its canonical key (NEWS_SEARCH, daily/weekly
    # report, etc.). Normalise to the real action so these don't fall through to
    # "Unsupported executor action: …".
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
    }
    a = _ACTION_ALIASES.get(a, a)

    # ── Evidence-routing for generative tasks (the DAG/plan principle) ─────────
    # Before ELI generates a document/script/project, INTUIT and gather the right
    # evidence (real code analysis, web, memory, runtime — the same agents the bus
    # uses) and attach it so the generator synthesises from real findings, not
    # generic priors. One uniform mechanism for every generative action and every
    # reasoning mode. Idempotent (gated on _evidence absent) so the GENERATE_*→
    # CREATE_* recursion and bus/direct double-dispatch each gather only once; the
    # gatherer's own WEB_SEARCH/RUNTIME_STATUS sub-calls aren't in this set, so
    # there is no recursion. See eli/runtime/evidence_planner.py.
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

    # v3 dispatch registry: effectors own their actions; consult before the if-chain.
    _rh = _registry_get(a)
    if _rh is not None:
        return _rh(a, args)

    if a == "CHAT":
        msg = args.get("message") or args.get("prompt") or args.get("text") or ""
        model = args.get("model") or None
        return chat(str(msg), model=model)

    # Persistent memory
    
    # --- media control ---

    # ---- DISPATCH PATCH: CORE + PROACTIVE ACTIONS ----
    # NOTE: Capabilities can be "registered" without being "implemented".
    # This block ensures the common actions actually route to real code.

    # ---- TIME ----

    # ---- HELP / LIST_CAPABILITIES ----
    if a in ("HELP", "LIST_CAPABILITIES"):
        # Try live introspection first (awareness module)
        public_actions = []
        capabilities = []
        fallback_msg = None

        try:
            from eli.runtime.capability_sync import CapabilitySync
            sync = CapabilitySync()
            caps = sync.discover()
            capabilities = sorted(caps.keys())
            public_actions = [
                {"action": k, "sources": [v.get("source", "")], "aliases": [],
                 "plugins": [v["plugin"]] if v.get("plugin") else []}
                for k, v in caps.items()
            ]
        except Exception as e:
            fallback_msg = f"Live introspection failed: {e}"

        # Fallback: generated report file
        if not capabilities:
            repo_root = Path(__file__).resolve().parents[2]
            report_path = repo_root / "capability_public_report.generated.json"
            if report_path.exists():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    public_actions = report.get("public_actions", []) or []
                    capabilities = [
                        item.get("action")
                        for item in public_actions
                        if isinstance(item, dict) and item.get("action")
                    ]
                except Exception as e:
                    fallback_msg = f"Generated report unreadable: {e}"
            else:
                fallback_msg = "Generated capability report not found."

        # Fallback: SUPPORTED_ACTIONS
        if not capabilities:
            capabilities = sorted(set(SUPPORTED_ACTIONS))
            if not fallback_msg:
                fallback_msg = "Using SUPPORTED_ACTIONS fallback."

        if a == "HELP":
            lines = ["ELI Capabilities:"]
            if public_actions:
                for item in public_actions:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("action") or "").strip()
                    if not name:
                        continue
                    aliases = item.get("aliases") or []
                    plugins = item.get("plugins") or []
                    sources = item.get("sources") or []
                    extras = []
                    if aliases:
                        extras.append("aliases=" + ", ".join(str(x) for x in aliases))
                    if plugins:
                        extras.append("plugins=" + ", ".join(str(x) for x in plugins))
                    if sources:
                        extras.append("sources=" + ", ".join(str(x) for x in sources))
                    if extras:
                        lines.append(f"  {name} ({'; '.join(extras)})")
                    else:
                        lines.append(f"  {name}")
                if fallback_msg:
                    lines.append("")
                    lines.append(f"[note] {fallback_msg}")
            else:
                for cap in capabilities:
                    lines.append(f"  {cap}")
                if fallback_msg:
                    lines.append("")
                    lines.append(f"[note] {fallback_msg}")

            txt = "\n".join(lines)
            return {
                "ok": True,
                "action": a,
                "count": len(capabilities),
                "capabilities": capabilities,
                "capability_report": public_actions,
                "content": txt,
                "response": txt,
            }

        lines = [f"Loaded {len(capabilities)} public capabilities:"]
        for cap in capabilities:
            lines.append(f"- {cap}")
        msg = "\n".join(lines)
        if fallback_msg:
            msg += f"\n\n[note] {fallback_msg}"
        return {
            "ok": True,
            "action": a,
            "count": len(capabilities),
            "capabilities": capabilities,
            "capability_report": public_actions,
            "content": msg,
            "response": msg,
        }

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

    # ---- OPEN_FILE_SYSTEM VALIDATION GUARD ----
    if a == "OPEN_APP":
        try:
            name = (args or {}).get("name") or (args or {}).get("app") or ""
            name = str(name).strip()
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

                roots = [
                    Path("/usr/share/applications"),
                    Path.home() / ".local/share/applications",
                    Path("/var/lib/snapd/desktop/applications"),
                ]

                for root in roots:
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

    # ---- RUN_CMD ----
    if a == "RUN_CMD":
        try:
            cmd = args.get("cmd") or args.get("command") or ""
            
            # ── SECURITY GATE ──
            # When cmd is a list, join to a string for pattern matching so that
            # ["bash", "-c", "..."] cannot bypass regex checks via str(list) repr.
            if isinstance(cmd, (list, tuple)):
                _cmd_str = " ".join(str(x) for x in cmd)
            else:
                _cmd_str = str(cmd)
            _cmd_low = _cmd_str.lower().strip()

            # ── SECURITY GATE — centralised, protected denylist ──
            # Destructive-pattern + dangerous-executable denylist lives in
            # eli/execution/shell_gate.py (a protected file apply_code_patch refuses to
            # auto-edit), so a self-improvement patch can't quietly strip it. Behaviour
            # is identical to the former inline gate: returns a block result unless ELI
            # Full Control is on, in which case the hard safety floor is lifted.
            from eli.execution.shell_gate import check_command as _shell_gate_check
            _gate = _shell_gate_check(cmd)
            if _gate is not None:
                _gate["action"] = a
                return _gate

            # Warn on sudo — allow but flag
            _needs_sudo = "sudo " in _cmd_low
            cwd = args.get("cwd") or None
            timeout = int(args.get("timeout", 30))
            shell = bool(args.get("shell", False))

            # Accept both ["ls", "/path"] and "ls /path"
            if isinstance(cmd, (list, tuple)):
                cmd_list = [str(x) for x in cmd if str(x).strip() != ""]
                if not cmd_list:
                    return {"ok": False, "action": a, "error": "empty_cmd", "content": "", "response": ""}
                p = subprocess.run(
                    cmd_list,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    shell=False,
                )
            else:
                cmd_str = str(cmd).strip()
                if not cmd_str:
                    return {"ok": False, "action": a, "error": "empty_cmd", "content": "", "response": ""}
                import shlex
                argv = shlex.split(cmd_str)
                if not argv:
                    return {"ok": False, "action": a, "error": "empty_cmd", "content": "", "response": ""}
                p = subprocess.run(
                    argv,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    shell=False,
                )

            out = (p.stdout or "") + (p.stderr or "")
            return {"ok": p.returncode == 0, "action": a, "code": p.returncode, "content": out, "response": out}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}
    
    # ---- PROACTIVE (start/stop/status) ----
    if a == "GET_PROPOSALS":
        # Surface ELI's OWN self-generated agenda: the proposal-only goals that goal
        # autogenesis forms from real signals (recurring errors, frequent behaviours,
        # code-health) plus any pending capability proposals. This is what "do you have
        # any proposals for me?" should answer — live, not canned.
        try:
            lines: list[str] = []
            try:
                from eli.planning.goal_store import load_goals
                goals = [
                    g for g in (load_goals() or [])
                    if getattr(g, "enabled", True)
                    and str(getattr(g, "status", "")).lower() in ("active", "proposed", "queued")
                    and str(getattr(g, "autonomy_mode", "")).lower() == "proposal_only"
                ]
                goals.sort(key=lambda g: float(getattr(g, "priority", 0.5) or 0.5), reverse=True)
                for g in goals[:8]:
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
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "proposal_count": len(lines), "evidence_source": "proposals_runtime"}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}




    # ---- MESSAGE_TIME_QUERY ----
    # Grounded answer to "what time did I first/last send a message (today)" —
    # read from the conversation_turns log, never the wall clock.
    if a == "MESSAGE_TIME_QUERY":
        import sqlite3 as _sq3
        from datetime import datetime as _dt
        from eli.memory import get_memory as _mt_get_memory
        which = str((args or {}).get("which", "first")).lower()
        scope = str((args or {}).get("scope", "all")).lower()
        try:
            mem = _mt_get_memory()
            dbp = getattr(mem, "db_path", None)
            if not dbp or not Path(dbp).exists():
                msg = "I don't have a conversation-history database to read that from yet."
                return {"ok": False, "action": a, "content": msg, "response": msg}
            where = "role='user'"
            params: List[Any] = []
            if scope == "today":
                midnight = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                where += " AND timestamp >= ?"
                params.append(midnight)
            agg = "MAX" if which in ("last", "latest") else "MIN"
            conn = _sq3.connect(str(dbp))
            try:
                row = conn.execute(
                    f"SELECT {agg}(timestamp) FROM conversation_turns WHERE {where}",
                    params,
                ).fetchone()
            finally:
                conn.close()
            ts = (row or [None])[0]
            scope_txt = " today" if scope == "today" else ""
            if not ts:
                msg = f"I have no record of a message from you{scope_txt or ' on file'}."
                return {"ok": True, "action": a, "content": msg, "response": msg,
                        "evidence_source": "conversation_turns"}
            stamp = _dt.fromtimestamp(float(ts)).strftime("%H:%M:%S on %Y-%m-%d")
            label = "most recent" if agg == "MAX" else "first"
            msg = f"Your {label} message{scope_txt} was at {stamp}."
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "evidence_source": "conversation_turns"}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e),
                    "content": str(e), "response": str(e)}

    # ---- MEMORY_RECALL ----
    if a == "MEMORY_RECALL":
        q = str(args.get("query", "")).strip()
        try:
            lim = int(args.get("limit", 5))
        except Exception:
            lim = 5

        if not q:
            return {"ok": False, "action": a, "error": "empty_query", "content": "empty_query", "response": "empty_query"}

        # 1) Canonical Memory class (proven working)
        try:
            from eli.memory.memory import get_memory as _get_mem_recall
            _mem_r = _get_mem_recall()
            hits = _mem_r.search_memory(q, limit=20) or []
            if isinstance(hits, list):
                # Truncate each hit and cap total to prevent context overflow.
                # Large hits (e.g. repeated conversation dumps) crash llama.cpp.
                _PER_HIT_CHARS = 300
                _MAX_TOTAL_CHARS = 3000
                lines, total = [], 0
                for h in hits:
                    t = (h.get("text", "") or "").strip()
                    if not t:
                        continue
                    t = t[:_PER_HIT_CHARS]
                    lines.append(t)
                    total += len(t)
                    if total >= _MAX_TOTAL_CHARS:
                        break
                content = "Memory recall:\n" + "\n---\n".join(lines) if lines else "Memory recall:\n(no matches)"
            else:
                content = "Memory recall:\n(no matches)"
            if not content.strip():
                content = "Memory recall:\n(no matches)"
            return {"ok": True, "action": a, "hits": hits, "content": content, "response": content}
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # 2) Legacy fallback: brain.memory_adapter.memory_search (may not exist)
        try:
            from eli.memory.memory_adapter import memory_search
            result = memory_search(q, k=lim)
            # Ensure consistent envelope
            if isinstance(result, dict):
                result.setdefault("action", a)
                result.setdefault("ok", True)
                if "content" not in result:
                    # memory_adapter may return 'results' (common) or 'hits' (legacy).
                    hits = result.get("results")
                    if hits is None:
                        hits = result.get("hits")
                    if hits is None:
                        hits = result.get("items")
                    if hits is None:
                        hits = []
                    content = ""
                    if isinstance(hits, list) and hits:
                        # Friendly readable render (top-k)
                        lines = []
                        for h in hits:
                            if not isinstance(h, dict):
                                continue
                            t = (h.get("text") or "").strip()
                            if not t:
                                continue
                            score = h.get("score", None)
                            tags = h.get("tags", "")
                            if score is not None:
                                try:
                                    lines.append(f"- ({float(score):.3f}) {t} [{tags}]")
                                except Exception:
                                    lines.append(f"- {t} [{tags}]")
                            else:
                                lines.append(f"- {t} [{tags}]")
                        content = "\n".join(lines).strip()
                    result["content"] = content or "Memory recall:\n(no matches)"
                    # Also expose a consistent 'hits' key for any callers expecting it.
                    if "hits" not in result:
                        result["hits"] = hits
                result.setdefault("response", result.get("response") or result["content"])
            return result
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # 2) Fallback: canonical Memory class
        try:
            from eli.memory.memory import get_memory
            _mem = get_memory()
            hits = _mem.search_memory(q, limit=20) or []
            content = "\n".join((h.get("text", "") or "") for h in hits) if isinstance(hits, list) else "Memory recall:\n(no matches)"
            if not content.strip():
                content = "Memory recall:\n(no matches)"
            return {"ok": True, "action": a, "hits": hits, "content": content, "response": content}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # Quick commands
    if a == "CHECK_CHRONAL_ALIGNMENT":
        import time as _t_mod
        _now = _t_mod.localtime()
        msg = (f"Chronal alignment nominal. Local time: "
               f"{_t_mod.strftime('%Y-%m-%d %H:%M:%S', _now)}.")
        return {"ok": True, "action": a, "content": msg, "response": msg}

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

    if a == "DATA_FABRICATOR":
        try:
            # If a topic/prompt is provided, generate a document first then open it.
            topic = str((args or {}).get("topic") or (args or {}).get("query") or (args or {}).get("text") or (args or {}).get("message") or "").strip()
            if topic:
                r = _execute_impl(action="CREATE_DOCUMENT", args={"topic": topic, "format": (args or {}).get("format") or "md"})
                # If generation succeeded, also open in an editor if available
                if r.get("ok") and r.get("path"):
                    path = r.get("path")
                    editors = [
                        ["code", path],
                        ["codium", path],
                        ["gedit", path],
                        ["kate", path],
                        ["nano", path],
                    ]
                    for cmd in editors:
                        try:
                            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            break
                        except FileNotFoundError:
                            continue
                return r

            # Otherwise just open an editor for a new blank file.
            out_dir = Path(str(Path.home() / "Documents" / "ELI" / "artifacts")).resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"data_construct_{int(time.time())}.md"
            if not out_path.exists():
                out_path.write_text("# Data Construct\n\n", encoding="utf-8")

            editors = [
                ["code", str(out_path)],
                ["codium", str(out_path)],
                ["gedit", str(out_path)],
                ["libreoffice", "--writer", str(out_path)],
            ]
            for cmd in editors:
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    msg = f"Opened editor: {' '.join(cmd)}"
                    return {"ok": True, "action": a, "path": str(out_path), "content": msg, "response": msg}
                except FileNotFoundError:
                    continue

            # Fallback: open with default system handler
            open_file(str(out_path))
            msg = f"Opened file: {out_path}"
            return {"ok": True, "action": a, "path": str(out_path), "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



    if a == "RUN_TESTS":
        try:
            import subprocess as _ts_sp, sys as _ts_sys
            from pathlib import Path as _TP
            repo = _TP(__file__).resolve().parents[2]
            target = str(args.get("target") or "tests/claims/test_structural_claims.py")
            r = _ts_sp.run([_ts_sys.executable, "tools/run_test_report.py", target],
                           cwd=str(repo), capture_output=True, text=True, timeout=900)
            rep = repo / "artifacts" / "test_report.md"
            body = rep.read_text(encoding="utf-8")[:4000] if rep.exists() else \
                (r.stdout or r.stderr or "")[-2000:]
            return {"ok": True, "action": a, "content": body, "response": body,
                    "report_path": str(rep), "evidence_source": "pytest_test_report"}
        except Exception as e:
            msg = f"RUN_TESTS failed: {e}"
            return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    # ---- GENERATE_TESTS — ELI writes + sandbox-verifies behavioural tests ----
    # Phase 4: per-function test generation (only passing candidates kept). Heavy
    # (one model call per target); keep the chat limit small — schedule larger runs
    # ("generate tests overnight" → SCHEDULE_TASK kind=testgen).
    if a == "LORA_TRAIN":
        try:
            from eli.learning.lora_pipeline import run_pipeline
            target = str(args.get("target") or "eli_phi")
            do_execute = bool(args.get("execute", False))
            res = run_pipeline(target, execute=do_execute,
                               max_steps=int(args.get("max_steps", 1) or 1))
            detail = "\n".join(
                f"- {s['stage']}: {'ok' if s['ok'] else 'FAILED'} — {s.get('detail')}"
                for s in res.get("stages", []))
            msg = (res.get("summary", "") + "\n" + detail).strip()
            return {"ok": bool(res.get("ok")), "action": a, "content": msg,
                    "response": msg, "evidence_source": "lora_pipeline", "result": res}
        except Exception as e:
            msg = f"LORA_TRAIN failed: {e}"
            return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    # ---- WAKE_TRAIN — train the local, self-supervised wake-word model ----
    # ELI synthesises the wake phrase with its OWN Piper TTS across voices/speeds,
    # mixes it with noise/music at random SNRs (robustness over music), and trains a
    # small local classifier head. 100% local, no account, no third-party, no
    # external pre-trained model. Falls back to transcription until a model exists.
    if a == "ORCHESTRATION_STATUS":
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
        parts = []
        all_ok = True
        for c in cmds:
            try:
                sub = _mc_route(c)
                sa = (sub.get("action") or "CHAT")
                sargs = dict(sub.get("args") or {})
                sargs.setdefault("_raw_user_text", c)
                if sa == "MULTI_COMMAND":          # guard against re-entry
                    sa, sargs = "CHAT", {"message": c}
                r = execute(sa, sargs)
                txt = (r.get("response") or r.get("content") or "") if isinstance(r, dict) else str(r)
                if isinstance(r, dict) and r.get("ok") is False:
                    all_ok = False
            except Exception as e:
                txt = f"failed: {e}"
                all_ok = False
            parts.append(f"• {c.strip()} →\n{(txt or '(done)').strip()}")
        msg = "\n\n".join(parts)
        return {"ok": all_ok, "action": a, "content": msg, "response": msg,
                "meta": {"commands": cmds}}

    # ---- TEST_REVIEW — run the suite, back up + write errors, then summarise + offer options ----
    # The grounded report below is summarised by the persona; the options route to
    # existing actions (examine/propose/generate/eval) so the user can work through them.
    if a == "TEST_REVIEW":
        try:
            from eli.runtime.test_review import run_and_review
            target = str(args.get("target") or "tests/claims/test_structural_claims.py")
            res = run_and_review(target)
            if res.get("error"):
                msg = f"Test review could not run: {res['error']}"
                return {"ok": False, "action": a, "content": msg, "response": msg}
            t = res.get("totals", {})
            lines = [f"Test review of `{target}`: {t.get('passed', 0)} passed, "
                     f"{t.get('failed', 0)} failed, {t.get('errored', 0)} errored, "
                     f"{t.get('xfailed', 0)} known-gap (xfail) of {t.get('total', 0)}."]
            if res.get("failures"):
                lines.append("Failing tests (possible errors):")
                for f in res["failures"][:10]:
                    lines.append(f"  - {f['node']}: {(f.get('message') or '')[:120]}")
            fa = res.get("fix_analysis") or {}
            if fa.get("proposals"):
                lines.append("Concrete issues found in failing modules (code examiner):")
                for p in fa["proposals"][:5]:
                    for fn in (p.get("findings") or [])[:3]:
                        loc = f":{fn.get('line')}" if fn.get("line") else ""
                        lines.append(f"  - {p['module']}{loc} [{fn.get('kind')}] {fn.get('message')}")
            if res.get("error_file"):
                lines.append(f"Errors written to: {res['error_file']}")
            if res.get("backup_path"):
                lines.append(f"Previous report backed up to: {res['backup_path']}")
            if res.get("options"):
                lines.append("What would you like to do next?")
                for i, o in enumerate(res["options"], 1):
                    lines.append(f"  {i}. {o['label']}  — say: \"{o['command']}\"")
            msg = "\n".join(lines)
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "evidence_source": "test_review", "result": res}
        except Exception as e:
            msg = f"TEST_REVIEW failed: {e}"
            return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}



    # ---- DATE ----

    # ---- MEDIA_CONTROL (generic router output) ----

    # ---- VOLUME ----


    # ---- SCREENSHOT ----
    if a == "SCREENSHOT":
        try:
            from eli.perception.os_controller import take_screenshot
            region = (args.get("region") or "full").strip()
            return take_screenshot(region=region)
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- KEYBOARD ----
    # ---- OCR_IMAGE ----



    # ---- TRANSCRIBE ----


    # ---- SHELL_EXEC (alias for RUN_CMD) ----
    if a == "SHELL_EXEC":
        cmd = args.get("cmd") or args.get("command") or ""
        return _execute_impl("RUN_CMD", {"cmd": cmd, "shell": True})

    # ---- PLUGIN_STATUS ----
    if a == "ELI_IDENTITY_AUDIT":
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

    # ---- CODE_CHANGES ----






    # ---- LIST_EVENTS / ADD_EVENT (calendar integration not configured) ----


    # ---- ANALYZE_PDF ----



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

    if a == "OPEN_IN_IDE":
        path = str(args.get("path") or "").strip()
        for editor in ["code", "codium", "subl", "gedit", "kate"]:
            try:
                cmd = [editor] + ([path] if path else [])
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                msg = f"Opened {path or 'IDE'} in {editor}"
                return {"ok": True, "action": a, "content": msg, "response": msg}
            except FileNotFoundError:
                continue
        return {"ok": False, "action": a, "error": "No IDE found", "content": "No IDE found", "response": "No IDE found"}

    if a == "SHOW_DIFF":
        return chat("Show me the recent changes or diff in the project.", skip_router=True)

    # ---- MEMORY_STATS ----
    if a == "TILE_WINDOWS":
        if shutil.which("wmctrl"):
            try:
                listing = subprocess.run(
                    ["wmctrl", "-l"], capture_output=True, text=True, timeout=4
                )
                window_ids = [line.split()[0] for line in (listing.stdout or "").splitlines() if line.strip()]
                count = len(window_ids)
                if count == 0:
                    msg = "No visible windows to tile."
                    return {"ok": False, "action": a, "content": msg, "response": msg}
                geom = subprocess.run(
                    ["xdpyinfo"], capture_output=True, text=True, timeout=4
                ).stdout if shutil.which("xdpyinfo") else ""
                m = re.search(r"dimensions:\s*(\d+)x(\d+)", geom or "")
                if m:
                    sw, sh = int(m.group(1)), int(m.group(2))
                else:
                    sw, sh = 1920, 1080
                cols = 2 if count <= 4 else 3
                rows = (count + cols - 1) // cols
                cw = sw // cols
                rh = sh // rows
                for i, wid in enumerate(window_ids):
                    row = i // cols
                    col = i % cols
                    x, y = col * cw, row * rh
                    subprocess.run([
                        "wmctrl", "-i", "-r", wid, "-b", "remove,maximized_vert,maximized_horz"
                    ], check=False, capture_output=True, timeout=4)
                    subprocess.run([
                        "wmctrl", "-i", "-r", wid, "-e", f"0,{x},{y},{cw},{rh}"
                    ], check=False, capture_output=True, timeout=4)
                msg = f"Tiled {count} window{'s' if count != 1 else ''} into a {cols}×{rows} grid."
                return {"ok": True, "action": a, "content": msg, "response": msg,
                        "count": count, "grid": [cols, rows]}
            except Exception as e:
                return {"ok": False, "action": a, "error": str(e),
                        "content": str(e), "response": str(e)}
        return {"ok": False, "action": a, "error": "wmctrl not installed",
                "content": "wmctrl not installed; cannot tile windows.",
                "response": "wmctrl not installed; cannot tile windows."}


    # ---- ANALYZE_IMAGE (metadata + OCR; never confabulate visual content) ----

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

        msg = f"Executed {len(results)} step(s)." if overall_ok else f"Executed {len(results)} step(s) with failures."
        return {
            "ok": overall_ok,
            "action": a,
            "steps": results,
            "content": msg,
            "response": msg,
        }


    if a == 'SELF_UPGRADE':
        request = (args or {}).get('request', '')
        try:
            from eli.kernel.self_upgrade import SelfUpgrader
            inst = SelfUpgrader()
            result = inst.upgrade(request)
            # If the upgrade request also named files / asked to check/examine/
            # review, run the code examiner and append a tiered report + fix offer.
            try:
                from eli.runtime import code_examiner as _ce
                _low = str(request or "").lower()
                _wants_examine = bool(
                    _ce._extract_named_paths(request)
                    or re.search(r"\b(examine|review|inspect|check|scan|audit)\b", _low)
                )
                if _wants_examine:
                    _paths = _ce.resolve_targets(request)
                    if _paths:
                        _findings = _ce.examine(_paths)
                        # Same report-only policy: only offer/pend a fix for genuine breakage on
                        # explicitly-named files; a broad upgrade-audit is report-only.
                        _named = bool(_ce._extract_named_paths(request))
                        _fixable = [f for f in _findings if _ce.is_real_breakage(f)]
                        if _named and _fixable:
                            _ce.set_pending_fix(_fixable, _paths)
                        else:
                            _ce.clear_pending_fix()
                        result = result + "\n\n— Code examination —\n" + \
                            _ce.format_report(_paths, _findings,
                                              allow_fix=(_named and bool(_fixable)))
            except Exception as _ex_exc:
                log.debug(f"[SELF_UPGRADE] examine stage skipped: {_ex_exc}")
            return {'ok': True, 'action': a, 'content': result, 'response': result}
        except Exception as _exc:
            return {'ok': False, 'action': a, 'error': str(_exc), 'content': str(_exc), 'response': str(_exc)}

    # ---- EXAMINE_CODE / CONFIRM_CODE_FIX / CANCEL_CODE_FIX ----
    if a in ('CONFIRM_HABIT', 'DECLINE_HABIT'):
        try:
            from eli.planning.habits import get_pending_habit, clear_pending_habit
            from eli.memory import get_memory as _get_mem
        except Exception as _imp_exc:
            msg = f"Habit confirm unavailable: {_imp_exc}"
            return {'ok': False, 'action': a, 'error': str(_imp_exc), 'content': msg, 'response': msg}
        pending = get_pending_habit()
        if not pending:
            msg = "There's no habit offer waiting for a yes/no right now."
            return {'ok': True, 'action': a, 'content': msg, 'response': msg}
        rid = int(pending.get('rule_id', -1))
        name = pending.get('name', 'that habit')
        hh, mm = int(pending.get('hour', 0)), int(pending.get('minute', 0))
        mem = _get_mem()
        try:
            if a == 'CONFIRM_HABIT':
                ok = mem.set_habit_rule_enabled(rid, True) if hasattr(mem, 'set_habit_rule_enabled') else False
                clear_pending_habit()
                if not ok:
                    msg = "I couldn't enable that habit (it may have been removed). Nothing activated."
                    return {'ok': False, 'action': a, 'content': msg, 'response': msg,
                            'evidence_source': 'habit_confirm'}
                # Catch-up: if today's scheduled time has already passed, run it NOW so
                # the user doesn't wait until tomorrow for the habit they just approved.
                # Otherwise the live scheduler (polling every 30 s) fires it at the time.
                _cmd = str(pending.get('command') or '').strip()
                _ran_now = False
                try:
                    from datetime import datetime as _dt
                    _now = _dt.now()
                    _already_passed = (hh * 60 + mm) < (_now.hour * 60 + _now.minute)
                    if _cmd and _already_passed:
                        from eli.planning.habits_scheduler import get_scheduler
                        get_scheduler().run_now({'id': rid, 'name': name, 'command': _cmd,
                                                 'hour': hh, 'minute': mm})
                        _ran_now = True
                except Exception as _rn:
                    log.debug(f"[HABIT] catch-up run skipped: {_rn}")
                if _ran_now:
                    msg = (f"Done — “{name}” is now an active habit. Today's {hh:02d}:{mm:02d} "
                           f"slot already passed, so I'm running it now; from tomorrow it runs "
                           f"automatically at {hh:02d}:{mm:02d}. Edit or disable it any time in "
                           f"the Habits tab.")
                else:
                    msg = (f"Done — “{name}” is now an active habit; I'll run it around "
                           f"{hh:02d}:{mm:02d}. You can edit or disable it any time in the "
                           f"Habits tab.")
                return {'ok': True, 'action': a, 'content': msg, 'response': msg,
                        'evidence_source': 'habit_confirm'}
            else:  # DECLINE_HABIT — remove the suggestion so it doesn't linger
                if hasattr(mem, 'delete_habit_rule'):
                    try:
                        mem.delete_habit_rule(rid)
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                clear_pending_habit()
                msg = f"No problem — I won't add “{name}”. I'll stop suggesting it."
                return {'ok': True, 'action': a, 'content': msg, 'response': msg,
                        'evidence_source': 'habit_decline'}
        except Exception as _hx:
            clear_pending_habit()
            msg = f"Habit update failed: {_hx}"
            return {'ok': False, 'action': a, 'error': str(_hx), 'content': msg, 'response': msg}


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

    # ---- CPU_USAGE ----

    # ---- SYSTEM_STATS ----


    # ---- SPEAK ----
    if a == "SPEAK":
        try:
            text = (args.get("text") or args.get("message") or args.get("content") or "").strip()
            if not text:
                return {"ok": False, "action": a, "error": "No text to speak",
                        "content": "Provide text to speak.", "response": "Provide text to speak."}
            from eli.perception.tts_router import maybe_speak
            maybe_speak(text, enabled=True)
            msg = f"Speaking: {text[:80]}"
            return {"ok": True, "action": a, "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- SMART_HOME ----
    # Routes through ELI's OWN MQTT device server (eli.runtime.device_server) — no
    # Home Assistant. Resolves the spoken device/room name against ELI's registry and
    # publishes on/off over MQTT. Devices are set up in the web app's Home tab.
    if a == "SMART_HOME":
        try:
            from eli.runtime.device_server import get_server
            srv = get_server()
            device = (args.get("device") or args.get("name") or "").strip()
            raw = (args.get("command") or args.get("action") or args.get("state") or "").strip().lower()
            # Bluetooth verbs (connect/disconnect/pair/use_for_audio) — resolve the spoken name
            # against discovered/registered Bluetooth devices and drive the OS BT stack. Handled
            # HERE, before the on/off mapping below ("on" is a substring of "c-on-nect").
            _btc = raw.replace(" ", "_").replace("-", "_")
            if args.get("bt") or _btc in ("connect", "disconnect", "pair", "trust", "use_for_audio"):
                if _btc in ("use_for_audio", "audio", "route_audio") and device:
                    from eli.runtime.local_connectivity import route_audio_by_name
                    _ar = route_audio_by_name(device)
                    if _ar.get("ok"):
                        _label = _ar.get("display_name") or _ar.get("alias") or device
                        _say = f"Audio is now playing through {_label}."
                        return {"ok": True, "action": a, "content": _say, "response": _say}
                from eli.runtime.device_server import bluetooth_control_by_name
                _r = bluetooth_control_by_name(device, _btc or "connect")
                _nm = _r.get("device_name") or device or "the device"
                if _r.get("ok"):
                    _say = {
                        "use_for_audio": f"Audio is now playing through {_nm}.",
                        "connect": f"Connected to {_nm}.",
                        "disconnect": f"Disconnected {_nm}.",
                        "pair": f"Paired with {_nm}.",
                        "trust": f"Trusted {_nm}.",
                    }.get(_btc, f"{_nm}: {_btc}.")
                    return {"ok": True, "action": a, "content": _say, "response": _say}
                _e = _r.get("error") or "I couldn't do that with Bluetooth."
                return {"ok": False, "action": a, "error": _e, "content": _e, "response": _e}
            cmd = "on" if "on" in raw else "off" if "off" in raw else raw
            if not device:
                msg = "Which device or room? For example: 'turn on the desk lamp'."
                return {"ok": False, "action": a, "error": "no_device", "content": msg, "response": msg}
            devs = srv.list_devices()
            if not devs:
                msg = ("No devices set up yet — add them in the Home tab of ELI's web app "
                       "(ELI's own MQTT device server).")
                return {"ok": False, "action": a, "error": "no_devices", "content": msg, "response": msg}
            dl = device.lower()
            match = next((d for d in devs if d["id"].lower() == dl or (d.get("name", "").lower() == dl)), None)
            if match is None:
                match = next((d for d in devs if dl and dl in (d.get("name", "").lower())), None)
            if match is not None:
                res = srv.control(match["id"], cmd)
                label = match.get("name") or match["id"]
                if res.get("ok"):
                    msg = f"{label}: {cmd}"
                    return {"ok": True, "action": a, "content": msg, "response": msg}
                err = res.get("error") or "control failed"
                return {"ok": False, "action": a, "error": err, "content": err, "response": err}
            # Not a device — maybe a room name ("turn off the kitchen").
            room_names = {r["room"].lower(): r["room"] for r in srv.rooms()}
            if dl in room_names:
                res = srv.control_room(room_names[dl], cmd)
                if res.get("ok"):
                    msg = f"{room_names[dl]}: {cmd} ({res.get('count', 0)} device(s))"
                    return {"ok": True, "action": a, "content": msg, "response": msg}
                err = res.get("error") or "no controllable devices"
                return {"ok": False, "action": a, "error": err, "content": err, "response": err}
            # Not a device or room — maybe a scene ("activate movie mode" / "movie mode").
            scene_names = {s["name"].lower(): s["name"] for s in srv.list_scenes()}
            scene_key = next((k for k in scene_names if k in dl or dl in k), None)
            if scene_key:
                res = srv.activate_scene(scene_names[scene_key])
                if res.get("ok"):
                    msg = f"Scene '{res.get('scene')}' activated ({res.get('ran', 0)} device(s))."
                    return {"ok": True, "action": a, "content": msg, "response": msg}
                err = res.get("error") or "scene failed"
                return {"ok": False, "action": a, "error": err, "content": err, "response": err}
            msg = f"I don't have a device, room, or scene called '{device}'."
            return {"ok": False, "action": a, "error": "not_found", "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- NOTE ACTIONS (LIST_NOTES / NEW_NOTE / SEARCH_NOTES) ----
    if a in ("LIST_NOTES", "NEW_NOTE", "SEARCH_NOTES"):
        try:
            from pathlib import Path as _P
            from eli.core.paths import config_dir as _cd
            notes_dir = _P(os.environ.get("ELI_NOTES_DIR", "")) or (_cd() / "notes")
            notes_dir.mkdir(parents=True, exist_ok=True)

            if a == "NEW_NOTE":
                import time as _nt
                title = (args.get("title") or args.get("text") or "").strip()
                content = (args.get("content") or args.get("text") or "").strip()
                fname = (title[:40].replace(" ", "_").replace("/", "_") or
                         _nt.strftime("note_%Y%m%d_%H%M%S")) + ".md"
                note_path = notes_dir / fname
                note_path.write_text(f"# {title or fname}\n\n{content}\n", encoding="utf-8")
                msg = f"Note saved: {note_path}"
                return {"ok": True, "action": a, "path": str(note_path), "content": msg, "response": msg}

            if a == "LIST_NOTES":
                notes = sorted(notes_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
                if not notes:
                    msg = f"No notes found in {notes_dir}"
                    return {"ok": True, "action": a, "notes": [], "content": msg, "response": msg}
                lines = [f"Notes ({len(notes)}):"]
                for n in notes[:30]:
                    lines.append(f"  {n.name}")
                msg = "\n".join(lines)
                return {"ok": True, "action": a, "notes": [n.name for n in notes],
                        "content": msg, "response": msg}

            if a == "SEARCH_NOTES":
                query = (args.get("query") or args.get("text") or "").strip().lower()
                if not query:
                    return {"ok": False, "action": a, "error": "No query",
                            "content": "Provide a search query.", "response": "Provide a search query."}
                hits = []
                for note in notes_dir.glob("*.md"):
                    text = note.read_text(encoding="utf-8", errors="replace").lower()
                    if query in text:
                        hits.append(note.name)
                if not hits:
                    msg = f"No notes matched '{query}'"
                else:
                    msg = f"Found {len(hits)} note(s) matching '{query}':\n" + "\n".join(f"  {h}" for h in hits[:20])
                return {"ok": True, "action": a, "hits": hits, "content": msg, "response": msg}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- POMODORO (START / STOP / STATUS) ----
    if a == "DOC_GENERATE":
        try:
            import re as _dre, tempfile as _dtmp
            from pathlib import Path as _DPath
            content  = str(args.get("content")  or args.get("text")  or args.get("generated") or "")
            filename = str(args.get("filename") or args.get("filepath") or args.get("path") or "")
            title    = str(args.get("title")    or args.get("query")  or args.get("topic") or "document")
            if not filename:
                safe = _dre.sub(r"[^a-zA-Z0-9_\- ]", "", title)[:40].strip().replace(" ", "_") or "eli_doc"
                try:
                    from eli.core.paths import get_paths as _dgp
                    docs_dir = _dgp().artifacts_dir / "docs"
                except Exception:
                    docs_dir = _DPath(_dtmp.gettempdir())
                docs_dir.mkdir(parents=True, exist_ok=True)
                filename = str(docs_dir / f"{safe}.md")
            _DPath(filename).parent.mkdir(parents=True, exist_ok=True)
            if not content:
                content = f"# {title}\n\n*Document generated by ELI.*\n"
            _DPath(filename).write_text(content, encoding="utf-8")
            try:
                from eli.utils.platform_compat import open_file as _open_file
                _open_file(filename)
            except Exception:
                import logging
                logging.getLogger(__name__).debug("open saved document failed", exc_info=True)
            msg = f"Document saved to `{filename}` and opened."
            return {"ok": True, "action": a, "content": msg, "response": msg, "filepath": filename}
        except Exception as _dge:
            return {"ok": False, "action": a, "error": str(_dge), "content": str(_dge), "response": str(_dge)}

    # ── SET_AI_MODE ──────────────────────────────────────────────────────────
    if a == "SET_AI_MODE":
        try:
            # Public mode names are Quick / Normal / Advanced / Research / Expert.
            # canonical_mode() accepts those AND every legacy alias (cot/tot/…) and
            # maps to the stable internal strategy key; mode_display() gives the public
            # label so the user never sees the internal name.
            from eli.cognition.reasoning_modes import canonical_mode as _cm, mode_display as _md
            raw = str(args.get("mode") or args.get("reasoning_mode") or args.get("value") or "quick")
            canonical = _cm(raw)
            from eli.core.runtime_settings import save_settings as _ss
            _ss({"reasoning_mode": canonical})
            try:
                from eli.kernel.engine import get_engine as _ge
                _ge()._reasoning_mode = canonical
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            msg = f"Reasoning mode set to {_md(canonical)}."
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "mode": canonical, "mode_display": _md(canonical)}
        except Exception as _sme:
            return {"ok": False, "action": a, "error": str(_sme), "content": str(_sme), "response": str(_sme)}

    return {"ok": False, "action": a, "error": f"Unsupported executor action: {a}", "content": f"Unsupported executor action: {a}", "response": f"Unsupported executor action: {a}"}


# ---------------------------------------------------------------------------
# Action dispatch hooks (added by phaseBW2 — replace 5 chained wrappers)
# ---------------------------------------------------------------------------

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
      * USER INFO EXECUTOR WRAPPER         (REFRESH_USER_INFO, USER_INFO_REPORT)
      * EXECUTOR SAFE FILE WRAPPERS        (READ_FILE, WRITE_NOTE, SCREENSHOT)
      * MEMORY_RECALL USER_INFO COMPAT     (identity-shaped MEMORY_RECALL)
      * GROUNDED EXECUTOR WRAPPER          (OPEN_APP/OPEN_IDE pre-flight,
                                            CHECK_TARGET_STATUS et al)
      * CONTROL_CONTRACT_EXECUTOR_WRAPPER  (SELF_UPDATE, EXPLAIN_LAST_RESPONSE)
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

    # MEMORY_RECALL identity-shaped queries -> USER_INFO_REPORT ---------
    if a == "MEMORY_RECALL" and "_eli_is_identity_memory_query" in globals() \
            and _eli_is_identity_memory_query(args):
        return _eli_user_info_report(force=False,
                                     reason="memory_recall_identity_compat")

    # User info actions --------------------------------------------------
    if a == "REFRESH_USER_INFO":
        return _eli_refresh_user_info(
            force=args.get("force", False),
            reason=args.get("reason", "manual"),
        )
    if a == "USER_INFO_REPORT":
        return _eli_user_info_report(
            force=args.get("force", False),
            reason=args.get("reason", "identity_query"),
        )

    # Safe file/screenshot ops ------------------------------------------
    if a == "SCREENSHOT":
        return _eli_safe_screenshot(args)

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

    # CHECK_TARGET_STATUS / EXPLAIN_LAST_FAILURE / etc -----------------
    if a in {"CHECK_TARGET_STATUS", "EXPLAIN_LAST_FAILURE",
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


# Per-process failure tally for the "attempt N" anti-retry hint. Keyed by
# action+args+error; reset every restart so a fresh session never inherits a
# stale "attempt 4" from all-time DB history. (The DB still logs failures for
# learning — this only governs the user-facing repeat hint.)
_SESSION_FAILURE_COUNTS: "Dict[str, int]" = {}


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
        from eli.runtime.evidence_ledger import record_event as _eli_record_event

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
            payload={"args": args or {}, "result": result or {}},
            severity="info" if bool((result or {}).get("ok", True)) else "error",
            outcome="ok" if bool((result or {}).get("ok", True)) else "failed",
            reusable=True,
        )
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    repeat_hint = ""
    # Phase 5: non-bugs that previously poisoned the SI failure feed. These
    # are *expected* responses (user typed no query, plugin not configured,
    # action genuinely outside this build's scope) — they should not enter
    # the failure-cluster pipeline that the SelfImprovementEngine acts on.
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
        _remediation_actions = frozenset((
            "CONFIRM_PENDING_REMEDIATION",
            "CANCEL_PENDING_REMEDIATION",
        ))
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

            signature_input = f"{str(action or '').upper()} {_json.dumps(args or {}, sort_keys=True, default=str)}"
            # Failures live in ONE canonical store — the agent/self-improvement DB
            # (agent.sqlite3), alongside improvements + code_patches. Previously this
            # dual-wrote to the user DB too, splitting the failure log across two
            # databases (the Self-Improve panel reads only the agent store).
            try:
                from eli.memory import get_agent_memory as _get_agent_memory
                _get_agent_memory().log_failure(
                    signature_input,
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

    try:
        from eli.runtime import grounded_remediation as _gr
    except Exception:
        if repeat_hint and isinstance(result, dict):
            existing = str(result.get("content") or result.get("response") or result.get("error") or "").strip()
            combined = (existing + "\n\n" + repeat_hint).strip()
            result["content"] = combined
            result["response"] = combined
        return result
    try:
        _offer = _gr.capture_executor_failure(action, args or {}, result)
        if _offer:
            if repeat_hint:
                _offer = f"{_offer}\n\n{repeat_hint}"
            return _gr.as_executor_result(_offer, ok=False)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    if repeat_hint and isinstance(result, dict):
        existing = str(result.get("content") or result.get("response") or result.get("error") or "").strip()
        combined = (existing + "\n\n" + repeat_hint).strip()
        result["content"] = combined
        result["response"] = combined
    return result




def execute(action: str, args: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:

    # Unwrap dict form: execute({"action": "X", "args": {...}})
    if isinstance(action, dict):
        args = action.get("args", args) or args
        action = action.get("action", "")
    action = action if isinstance(action, str) else ""
    a = action.upper().strip()

    # Pre-dispatch action overrides (formerly applied via monkey-patch wrappers).
    _override = _action_pre_dispatch(action, args, **kwargs)
    if _override is not None:
        return _action_post_dispatch(action, args, _override)
    if a == 'USER_IDENTITY_SUMMARY':
        _question = str((args or {}).get("question") or (args or {}).get("query") or "who am i")
        try:
            _snapshot = _eli_user_info_report(force=False, reason="user_identity_summary")
            _raw = str(_snapshot.get("content") or _snapshot.get("response") or "")

            def _section(_name):
                _lines = []
                _inside = False
                _header = f"[{_name}]"
                for _ln in _raw.splitlines():
                    _s = _ln.strip()
                    if _s == _header:
                        _inside = True
                        continue
                    if _inside and _s.startswith("[") and _s.endswith("]"):
                        break
                    if _inside and _s.startswith("- "):
                        _item = _s[2:].strip()
                        if _item and _item.lower() != "none confirmed.":
                            _lines.append(_item.rstrip("."))
                return _lines

            _identity = _section("Identity")
            _prefs = _section("Communication Preferences")
            _working = _section("Working Style")
            _projects = _section("Active Projects")
            _technical = _section("Technical Environment")

            _lines = ["Current user identity summary:"]

            if _identity:
                _lines.append("")
                _lines.append("Identity:")
                for _x in _identity[:5]:
                    _lines.append(f"- {_x}.")
            else:
                _lines.append("")
                _lines.append("- I do not have a confirmed name/identity row in the current user-info snapshot.")

            _facts = []
            for _bucket in (_prefs, _working, _projects, _technical):
                for _x in _bucket:
                    if _x not in _facts:
                        _facts.append(_x)

            if _facts:
                _lines.append("")
                _lines.append("What I can say from current memory:")
                for _x in _facts[:10]:
                    _lines.append(f"- {_x}.")

            _msg = "\n".join(_lines).strip()
            if not _msg:
                _msg = "I have no clean user identity summary available right now."

            return {
                "ok": True,
                "action": a,
                "content": _msg,
                "response": _msg,
                "report": {
                    "source": "user_info_snapshot_summarized",
                    "raw_snapshot_suppressed": True,
                    "question": _question,
                },
                "evidence_source": "user_info_snapshot_summarized",
                "generation_invoked": False,
            }
        except Exception as e:
            _msg = f"User identity summary failed: {type(e).__name__}: {e}"
            return {"ok": False, "action": a, "content": _msg, "response": _msg, "error": repr(e)}
    if a == 'GUI_RUNTIME_AUDIT':
        _q = ""
        if isinstance(args, dict):
            _q = str(args.get("question") or args.get("message") or args.get("text") or "")
        rep = _gui_runtime_audit_report(_q); txt = _format_gui_runtime_audit(rep)
        return {'ok': True, 'action': a, 'report': rep, 'content': txt, 'response': txt}
    if a == 'SELF_IMPROVEMENT_LOG':
        limit = int((args or {}).get('limit') or 5) if isinstance(args, dict) else 5
        days = int((args or {}).get('days') or 30) if isinstance(args, dict) else 30
        rep = _self_improvement_log_report(limit=limit, days=days)
        return {'ok': bool(rep.get('ok', False)), 'action': a, 'report': rep, 'content': rep.get('content', ''), 'response': rep.get('response', rep.get('content', ''))}
    _mem = None  # _mkvi_memory_dispatch removed (was undefined)
    if _mem is not None:
        return _mem
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
    # Post-dispatch failure capture (formerly in GROUNDED EXECUTOR WRAPPER).
    return _action_post_dispatch(action, args, __eli_ret)


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

# --- AUTO-REGISTER CAPABILITIES (ON IMPORT) ---
try:
    from eli.tools.registry.capability_registry import register as _cap_register
    for _a in SUPPORTED_ACTIONS:
        _cap_register(_a, {"description": f"Executor action: {_a}"})
except Exception:
    log.debug("suppressed exception", exc_info=True)


# ----------------------------
# Proactive daemon control (real PID management)
# ----------------------------








class Executor:
    """Thin class wrapper around the functional execute() API."""

    def __init__(self):
        pass

    def execute(self, action: str, args: dict = None):
        return execute(action, args or {})

    def __call__(self, action: str, args: dict = None):
        return execute(action, args or {})


# Helpers preserved from former USER INFO EXECUTOR WRAPPER (phaseBW2).
# The wrapper-install scaffolding was removed; pre-dispatch in execute()
# routes REFRESH_USER_INFO and USER_INFO_REPORT through these directly.
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


# Helpers preserved from former EXECUTOR SAFE FILE WRAPPERS (phaseBW2).
# The wrapper-install scaffolding was removed; pre-dispatch in execute()
# routes READ_FILE / WRITE_NOTE / SCREENSHOT through these directly.




# Helper preserved from former MEMORY_RECALL USER_INFO COMPAT (phaseBW2).
# Pre-dispatch in execute() routes identity-shaped MEMORY_RECALL queries
# through this directly to USER_INFO_REPORT.
import re as _eli_exec_re

def _eli_is_identity_memory_query(_args):
    _args = _args or {}
    q = str(_args.get("query") or _args.get("message") or "").strip().lower()
    if _args.get("_prefer_user_info_report"):
        return True
    pats = [
        r"\bwhat do you know about me\b",
        r"\bwhat do you know from memory\b",
        r"\bwho am i(?: to you)?\b",
        r"\bshow user info\b",
        r"\buser info report\b",
        r"\bprofile report\b",
        r"\bdump (?:my )?(?:user )?(?:profile|info|memory)\b",
        r"\bshow (?:my|the) stored memor(?:y|ies)(?: about me)?\b",
    ]
    return any(_eli_exec_re.search(p, q) for p in pats)


# Local-only YouTube playback. No browser search. No external API.

# Canonical media runtime interceptor.
# Keep routing in router_enhanced/portable_intent_contract.py.
# Keep execution-side media behaviour in eli.execution.media_runtime.
try:
    from eli.execution.media_runtime import install_media_executor as _install_media_executor
    execute_action = _install_media_executor(execute_action)
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

    def _eli_schema_text_from_result(result):
        if isinstance(result, dict):
            for key in ("content", "response", "text", "answer", "message", "output"):
                value = result.get(key)
                if value is not None and str(value).strip():
                    return str(value)
            return ""
        if result is not None:
            return str(result)
        return ""

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

    def _eli_schema_extract_python_source(args):
        args = args if isinstance(args, dict) else {}
        for key in ("code", "script", "source", "content", "python", "body", "text"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value

        generator = globals().get("chat")
        if callable(generator):
            prompt = args.get("description") or args.get("prompt") or "Generate a Python script."
            result = generator(str(prompt))
            text = _eli_schema_text_from_result(result)
            if text.strip():
                return text

        prompt = args.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            stripped = prompt.lstrip()
            if "\n" in prompt or stripped.startswith(("def ", "class ", "import ", "from ", "print(", "for ", "while ", "if ", "try:")):
                return prompt

        return None

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

        if action_name in {"GENERATE_SCRIPT", "CREATE_SCRIPT", "WRITE_SCRIPT"}:
            source = _eli_schema_extract_python_source(call_args)
            if source is not None:
                try:
                    compile(str(source), "<eli-generated-script>", "exec")
                except SyntaxError as exc:
                    return _eli_schema_failure(
                        action_name,
                        f"Generated Python script failed syntax validation: {exc}",
                    )

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
        import sys

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
        import platform
        import shutil
        import subprocess

        level = max(0, min(100, int(percent)))
        system = platform.system().lower()

        if system == "linux":
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

        if system == "darwin":
            rc = subprocess.run(
                ["osascript", "-e", f"set volume output volume {level}"],
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

    def _eli_v3_error(action_name, text):
        return {
            "ok": False,
            "action": action_name,
            "error": str(text),
            "content": str(text),
            "response": str(text),
            "evidence": [str(text)],
        }

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
            from eli.system.portable_app_control import close_app
            _r = close_app(
                data.get("name") or data.get("target") or data.get("app") or "",
                force=bool(data.get("force", False)),
            )
            if isinstance(_r, dict) and _r.get("ok"):
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

        return None

    def _eli_v3_project_root():
        from pathlib import Path
        import os
        env_root = os.environ.get("ELI_PROJECT_ROOT")
        if env_root:
            return Path(env_root).expanduser().resolve()
        return Path(__file__).resolve().parents[2]

    def _eli_v3_artifacts_dir():
        from pathlib import Path
        import os
        root = _eli_v3_project_root()
        env_dir = os.environ.get("ELI_ARTIFACTS_DIR")
        if env_dir:
            p = Path(env_dir).expanduser()
            return p.resolve() if p.is_absolute() else (root / p).resolve()
        return (root / "artifacts").resolve()

    def _eli_v3_slug(text, default="generated_script"):
        import re
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text or "").strip()).strip("._-")
        return slug[:80] or default

    def _eli_v3_language_ext(language):
        mapping = {
            "python": "py", "py": "py",
            "bash": "sh", "shell": "sh", "sh": "sh", "zsh": "zsh",
            "javascript": "js", "js": "js",
            "typescript": "ts", "ts": "ts",
            "c++": "cpp", "cpp": "cpp",
            "c": "c",
            "c#": "cs", "csharp": "cs",
            "java": "java",
            "rust": "rs",
            "go": "go",
            "ruby": "rb",
            "php": "php",
            "lua": "lua",
            "r": "r",
            "swift": "swift",
            "kotlin": "kt",
            "scala": "scala",
            "sql": "sql",
            "html": "html",
            "css": "css",
            "json": "json",
            "yaml": "yaml", "yml": "yml",
        }
        lang = str(language or "auto").strip().lower()
        return mapping.get(lang, lang if lang and lang != "auto" and len(lang) <= 12 else "txt")

    def _eli_v3_extract_code(raw, language="auto"):
        import re
        if isinstance(raw, dict):
            for key in ("code", "content", "response", "text", "answer", "message", "output"):
                val = raw.get(key)
                if val is not None and str(val).strip():
                    raw = str(val)
                    break
            else:
                raw = str(raw)
        else:
            raw = str(raw or "")

        def _norm(s):
            # Some backends return code as a single physical line with escaped newlines
            # ("\n"/"\t", JSON-style) — that compiles as an "unterminated string literal
            # at line 1". If escapes are present but there are no real newlines, decode them.
            s = str(s or "").strip()
            if "\n" not in s and ("\\n" in s or "\\t" in s):
                s = (s.replace("\\r\\n", "\n").replace("\\n", "\n")
                       .replace("\\t", "\t").replace('\\"', '"').replace("\\'", "'"))
            return s.strip()

        fences = re.findall(r"```([A-Za-z0-9+#._-]*)\n(.*?)```", raw, flags=re.DOTALL)
        if fences:
            wanted = str(language or "").lower()
            for lang, code in fences:
                if wanted and wanted != "auto" and lang.lower() == wanted:
                    return _norm(code)
            return _norm(fences[0][1])

        return _norm(raw)

    def _eli_v3_generate_script(args=None):
        action_name = "GENERATE_SCRIPT"
        data = _eli_v3_args(args)

        description = (
            data.get("description")
            or data.get("prompt")
            or data.get("task")
            or data.get("query")
            or data.get("message")
            or ""
        )

        if not str(description).strip():
            return _eli_v3_error(action_name, "No script description supplied.")

        language = data.get("language") or "auto"
        if str(language).strip().lower() in {"", "auto", "none"}:
            try:
                from eli.execution.portable_intent_contract import infer_script_language
                language = infer_script_language(description)
            except Exception:
                language = "auto"

        generation_prompt = (
            "You are ELI's local code generation engine. Return a complete, runnable source file only. "
            f"Requested language: {language}.\n\n"
            "Hard requirements:\n"
            "- Solve the user's actual task; do not echo or rephrase the prompt.\n"
            "- No TODO markers, placeholder functions, fake outputs, or hardcoded demonstration answers.\n"
            "- Include imports, validation, error handling, and a clear executable entry point where the language supports one.\n"
            "- For Python: use type hints, docstrings where they clarify behaviour, and an `if __name__ == \"__main__\"` guard for scripts.\n"
            "- For CLI-style requests: use arguments with useful help text instead of burying constants in the body.\n"
            "- Output only raw source code. No markdown fences, no commentary, no postscript.\n\n"
            f"Task:\n{description}"
        )

        raw = None
        chat_fn = globals().get("chat")
        if callable(chat_fn):
            try:
                raw = chat_fn(generation_prompt, skip_router=True)
            except TypeError:
                raw = chat_fn(generation_prompt)

        if raw is None:
            try:
                from eli.cognition import gguf_inference
                raw = gguf_inference.generate(
                    generation_prompt,
                    max_tokens=int(data.get("max_tokens") or 1200),
                )
            except Exception as exc:
                return _eli_v3_error(action_name, f"Script generation backend unavailable: {exc}")

        code = _eli_v3_extract_code(raw, language=language)
        if not code:
            return _eli_v3_error(action_name, "Script generation returned no source code.")

        bad_markers = (
            "TODO",
            "Add code here",
            "placeholder",
            "Generate only the requested source code",
            "This is a request for",
        )
        if any(marker.lower() in code.lower() for marker in bad_markers):
            return _eli_v3_error(
                action_name,
                "Generated script rejected: output contained stub/template markers.",
            )
        if re.fullmatch(r"\s*(?:pass|return\s+None)\s*", code):
            return _eli_v3_error(
                action_name,
                "Generated script rejected: output was an empty implementation.",
            )

        ext = _eli_v3_language_ext(language)

        if ext == "py":
            try:
                compile(code, "<eli-generated-script>", "exec")
            except SyntaxError as _v3_syn_exc:
                # One retry with a stricter prompt: standard library only, no
                # third-party packages. Catches hallucinated APIs like
                # pytz.utc.location.LocationIn that pass regex checks but fail compile.
                _v3_retry_prompt = (
                    "You are ELI's local code generation engine. The previous attempt had a syntax error: "
                    f"{_v3_syn_exc}\n\n"
                    "Write a CORRECTED, complete, runnable Python script for the task below.\n"
                    "STRICT REQUIREMENTS:\n"
                    "- Standard library ONLY. Do not import any third-party package (no ephem, pytz, astral, requests, etc.).\n"
                    "- No markdown fences, no commentary, no TODO markers.\n"
                    "- Must be syntactically valid Python 3.\n"
                    "- Use an `if __name__ == '__main__':` entry point.\n\n"
                    f"Task:\n{description}"
                )
                _v3_retry_raw = None
                _v3_chat = globals().get("chat")
                if callable(_v3_chat):
                    try:
                        _v3_retry_raw = _v3_chat(_v3_retry_prompt, skip_router=True)
                    except TypeError:
                        _v3_retry_raw = _v3_chat(_v3_retry_prompt)
                if _v3_retry_raw is None:
                    try:
                        from eli.cognition import gguf_inference as _v3_gi
                        _v3_retry_raw = _v3_gi.generate(
                            _v3_retry_prompt,
                            max_tokens=int(data.get("max_tokens") or 1200),
                        )
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                if _v3_retry_raw:
                    code = _eli_v3_extract_code(_v3_retry_raw, language=language)
                    try:
                        compile(code, "<eli-generated-script>", "exec")
                    except SyntaxError as _v3_retry_exc:
                        return _eli_v3_error(
                            action_name,
                            f"Generated Python script failed syntax validation (after retry): {_v3_retry_exc}",
                        )
                else:
                    return _eli_v3_error(
                        action_name,
                        f"Generated Python script failed syntax validation: {_v3_syn_exc}",
                    )

        out_dir = _eli_v3_artifacts_dir() / "scripts"
        out_dir.mkdir(parents=True, exist_ok=True)

        stem = _eli_v3_slug(data.get("title") or description)
        path = out_dir / f"{stem}.{ext}"

        if path.exists() and not bool(data.get("overwrite", False)):
            idx = 2
            while True:
                candidate = out_dir / f"{path.stem}_{idx}{path.suffix}"
                if not candidate.exists():
                    path = candidate
                    break
                idx += 1

        path.write_text(code, encoding="utf-8")

        text = json.dumps(
            {
                "event": "artifact_generated",
                "kind": "script",
                "path": str(path),
                "language": language,
            },
            ensure_ascii=False,
            default=str,
        )
        return {
            "ok": True,
            "action": action_name,
            "script_path": str(path),
            "path": str(path),
            "language": language,
            "destination": data.get("destination") or "labs_sim_ide",
            "open_in_labs": bool(data.get("open_in_labs", True)),
            "open_in_ide": bool(data.get("open_in_ide", True)),
            "content": text,
            "response": text,
        }

    if callable(_ELI_PORTABLE_V3_ORIG_EXECUTE):
        def execute(action, args=None, *pargs, **kwargs):
            action_name = _eli_v3_action_name(action)

            direct = _eli_v3_direct_system_action(action, args)
            if direct is not None:
                return direct

            if action_name in {
                "GENERATE_SCRIPT", "CREATE_SCRIPT", "WRITE_SCRIPT",
                "GENERATE_CODE", "WRITE_CODE",
            }:
                return _eli_v3_generate_script(args)

            return _ELI_PORTABLE_V3_ORIG_EXECUTE(action, args, *pargs, **kwargs)

    if callable(_ELI_PORTABLE_V3_ORIG_EXECUTE_ACTION):
        def execute_action(action, args=None, *pargs, **kwargs):
            action_name = _eli_v3_action_name(action)

            direct = _eli_v3_direct_system_action(action, args)
            if direct is not None:
                return direct

            if action_name in {
                "GENERATE_SCRIPT", "CREATE_SCRIPT", "WRITE_SCRIPT",
                "GENERATE_CODE", "WRITE_CODE",
            }:
                return _eli_v3_generate_script(args)

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


# ELI_EXECUTOR_VISIBLE_TILE_SECOND_FIX_20260505
# Terminal action wrappers. Must sit late in file to override previous execute wrappers.
import math as _eli_tile_math
import re as _eli_tile_re
import subprocess as _eli_tile_subprocess

_ELI_TILE_ORIG_EXECUTE = globals().get("execute")
_ELI_TILE_ORIG_EXECUTE_ACTION = globals().get("execute_action")








def _eli_second_execute(action, args=None, *pargs, **kwargs):
    action_name = str(action or "").upper()
    args = args or {}

    if action_name == "REASONING_MODE_STATUS":
        try:
            from eli.runtime.reasoning_status import current_reasoning_mode_text
            msg = current_reasoning_mode_text()
        except Exception:
            msg = "Current reasoning mode: unavailable"
        return {"ok": True, "action": "REASONING_MODE_STATUS", "content": msg, "response": msg}

    if action_name == "TILE_WINDOWS":
        return _eli_tile_windows(args)

    if callable(_ELI_TILE_ORIG_EXECUTE):
        return _ELI_TILE_ORIG_EXECUTE(action, args, *pargs, **kwargs)
    if callable(_ELI_TILE_ORIG_EXECUTE_ACTION):
        return _ELI_TILE_ORIG_EXECUTE_ACTION(action, args, *pargs, **kwargs)

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

# =============================================================================
# ELI IDENTITY-ONLY EXECUTOR CONTRACT
# Keeps USER_IDENTITY_SUMMARY from leaking preferences / working-style / project
# memory into name/identity questions. No hardcoded user names.
# =============================================================================
try:
    def _eli_identity_only_name():
        try:
            from eli.kernel.state import get_user_name, set_user_name
            name = str(get_user_name("") or "").strip()
            if name:
                return name
            # Fallback: recover from user_patterns SQLite if profile lacks name.
            # This handles the case where the user stated their name in a past
            # session but the profile was never updated from user_patterns.
            try:
                import sqlite3 as _sq3, re as _re2
                from eli.core.paths import user_db_path
                _db = str(user_db_path())
                _con = _sq3.connect(_db)
                _rows = _con.execute(
                    "SELECT pattern_data FROM user_patterns "
                    "WHERE pattern_type='identity.name' "
                    "  AND length(COALESCE(pattern_data,''))>3 "
                    "ORDER BY COALESCE(ts, timestamp, id) DESC LIMIT 5"
                ).fetchall()
                _con.close()
                for (_pdata,) in _rows:
                    _m = _re2.search(
                        r"(?:name is|preferred name is|called|known as)\s+([A-Za-z][A-Za-z' -]{1,29})\b",
                        str(_pdata or ""), _re2.I,
                    )
                    if _m:
                        _candidate = _m.group(1).strip(" .,;")
                        if _candidate.lower() not in {
                            "unknown", "none", "the", "a", "an", "no", "not", "asking", "user",
                            "screenshot", "name", "unnamed", "anonymous", "guest", "admin",
                            "root", "system", "default", "test", "sample", "example",
                            "placeholder", "null", "undefined",
                        }:
                            try:
                                set_user_name(_candidate)  # persist for future sessions
                            except Exception:
                                log.debug("suppressed exception", exc_info=True)
                            return _candidate
            except Exception:
                log.debug("suppressed exception", exc_info=True)
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

# Makes PERSONAL_MEMORY_SUMMARY mode-aware: inventory_only = just the category counts
# (no preference dump), preferences_detail = the actual preference/working-style stuff,
# full_profile = the whole active-user snapshot. I also strip the identity/name chatter
# out of EXPLAIN_MEMORY_RUNTIME here. No user names hardcoded anywhere in this.
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

# =============================================================================
# ELI MEMORY RUNTIME REPORT SANITIZER
# EXPLAIN_MEMORY_RUNTIME is architecture/runtime evidence only.
# It must not carry identity guesses, profile facts, preference facts, or noisy
# "Session context" identity hits in report metadata.
# =============================================================================
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

# =============================================================================
# ELI MEMORY COUNT COMPACT EVIDENCE PROVIDER
# Provides structured evidence only. Non-quick final wording belongs to cognition.
# =============================================================================
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

# =============================================================================
# ELI RECENT MEMORY PROCESSING EVIDENCE PROVIDER
# MEMORY_STATUS + memory_scope=recent_processing returns compact SQLite-backed
# evidence. No invented "I was processing equations" unless the DB actually says
# that.
# =============================================================================
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

# Second pass at the recent-memory-processing cleanup. My first provider was way too
# loose — it'd surface runtime echoes, the current prompt, model-not-ready messages,
# even old hallucinated answers as "recent memory processing". This wrapper scrubs the
# report before it ever reaches user-visible synthesis.
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

# =============================================================================
# ELI SELF-REPORT RECENT UPDATES EVIDENCE PROVIDER
# Provides deterministic evidence for questions like:
# "Tell me about yourself. What updates/checks have been performed lately?"
# =============================================================================
try:
    import json as _eli_self_json
    import subprocess as _eli_self_subprocess
    from pathlib import Path as _eli_self_Path


    def _eli_self_project_root():
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
        try:
            p = _eli_self_Path(path)
            if not p.exists():
                return {}
            return _eli_self_json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _eli_self_split_lines(text, limit=12):
        lines = [x.rstrip() for x in str(text or "").splitlines() if x.strip()]
        return lines[:limit]

    def _eli_self_build_recent_updates_report(question=""):
        root = _eli_self_project_root()

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

        runtime_snapshot = _eli_self_read_json(root / "artifacts" / "runtime_snapshot.json")
        manifest = _eli_self_read_json(root / "capability_manifest.json")

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
        if commits:
            for c in commits:
                lines.append(f"- {c}")
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
        else:
            lines.append("- clean according to git status --short")

        lines.append("")
        lines.append("Grounding rule: if an update/check is not listed above, ELI must not claim it happened.")

        content = "\n".join(lines)

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

# =============================================================================
# ELI_GUI_RUNTIME_AUDIT_VISIBLE_RESULT_CONTRACT
# Normalizes GUI_RUNTIME_AUDIT executor output so GUI receives visible content.
# This does not change routing, cognition, GGUF, or the audit evidence source.
# =============================================================================
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

# =============================================================================
# ELI_EXECUTOR_FINAL_EXECUTE_ACTION_ALIAS_SYNC_V1
# Final safety sync: execute_action must expose the same final contract surface as
# execute. Several historical wrappers reassigned execute without also rebinding
# execute_action, leaving execute_action pinned to an older wrapper.
# =============================================================================
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

# RUNTIME_STATUS evidence metadata normalization is now done inline by
# the mw_runtime_status_metadata middleware in the canonical table below.
# The legacy inert wrapper that lived here has been removed.
# =============================================================================


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

# =============================================================================
# ELI_EXECUTOR_CANONICAL_MIDDLEWARE_TABLE_V1
# Consolidates stacked execute wrappers into one explicit middleware chain.
# Legacy wrapper blocks above are retained as retired compatibility helpers.
# =============================================================================
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
