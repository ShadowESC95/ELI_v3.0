from __future__ import annotations

import ast
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from eli.memory import get_agent_memory
from eli.cognition.inference_broker import get_broker
from eli.memory import Memory


from eli.runtime.self_maintenance_config import (
    ANALYSIS_MIN_CLUSTER,
    DEFAULT_ANALYSIS_DAYS,
    PATCH_MIN_CLUSTER,
)
from eli.utils.log import get_logger
log = get_logger(__name__)

def _eli_canonical_root_PROJECT_ROOT() -> Path:
    # Canonical env-honoring root — __file__ resolves into the read-only
    # bundle in frozen builds (identical to this path in source installs).
    try:
        from eli.core.paths import project_root
        return Path(project_root())
    except Exception:
        return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _eli_canonical_root_PROJECT_ROOT()


_PROTECTED_PATCH_PATHS = {
    "eli/runtime/security.py",
    "eli/core/netguard.py",
    "eli/core/full_control.py",
    "eli/runtime/approval_engine.py",
    "eli/runtime/self_improvement.py",
    "eli/runtime/deterministic_grounding_gate.py",
    "eli/execution/shell_gate.py",        # shell denylist (extracted from executor)
    "eli/runtime/authority_gate.py",      # action allow/check gate
    "eli/runtime/persistence_gate.py",    # upstream action/persistence gate
    "eli/runtime/evidence_ledger.py",     # the record calibration and reliability are judged from
    "eli/runtime/lessons.py",             # decides which lessons survive
    "eli/runtime/failure_taxonomy.py",    # classifies what failed
    # what contains and judges model-written code: a candidate cannot loosen its own sandbox
    "eli/coding/sandbox.py",
    "eli/coding/verification.py",
    "eli/coding/restricted_exec.py",
    "eli/plugins/sandbox.py",
    "eli/plugins/subprocess_sandbox.py",
    "eli/plugins/permissions.py",
}
# A candidate may change the code under test, never what tests it or what counts as passing.
_PROTECTED_PATCH_PREFIXES = ("tests/", "tools/eval/", "conftest.py", "pytest.ini")


def is_protected_patch_path(p: Path) -> bool:
    """True when `p` is one of ELI's own safety guardrail files — must never
    be auto-patched, by self-improvement's autonomous path or a user-triggered
    fix/improve request (FIX_FILE). Only matches paths inside the project
    source root; a user's own unrelated file is never "protected" by this.
    Env-extensible via ELI_PROTECTED_PATCH_PATHS (comma-separated posix paths).
    """
    try:
        root = _patch_root()
        rel = Path(p).resolve().relative_to(root).as_posix()
    except Exception:
        return False
    protected = _PROTECTED_PATCH_PATHS | {
        x.strip() for x in os.environ.get("ELI_PROTECTED_PATCH_PATHS", "").split(",") if x.strip()
    }
    return rel in protected or rel.startswith(_PROTECTED_PATCH_PREFIXES)


def _patch_root() -> Path:
    """Tree where self-improvement may read/write Python source."""
    from eli.core.paths import source_root
    return source_root()


def _safe_str(x: Any) -> str:
    try:
        return "" if x is None else str(x)
    except Exception:
        return ""


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except Exception:
        return float(default)


def _reload_patched_module(dotted: Optional[str]) -> bool:
    """Evict cached module(s) and re-import so disk patches affect this process."""
    if not dotted:
        return False
    import importlib

    prefix = dotted + "."
    for name in list(sys.modules):
        if name == dotted or name.startswith(prefix):
            sys.modules.pop(name, None)
    try:
        importlib.import_module(dotted)
        return True
    except Exception as exc:
        log.debug("[SELF-IMPROVE] post-patch reload failed for %s: %s", dotted, exc)
        return False


def _dotted_module_for_path(p: Path) -> Optional[str]:
    """Return the importable dotted module name for a project .py file, or None
    if it isn't an importable module under the `eli` package."""
    try:
        rel = p.resolve().relative_to(_patch_root())
    except Exception:
        return None
    parts = list(rel.parts)
    if not parts or parts[0] != "eli" or not parts[-1].endswith(".py"):
        return None
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


# Detail of an import check that could not run at all: not a pass, so the patch is labelled unverified.
_NOT_RUN = "could not run: "


def _smoke_import_module(dotted: str, timeout: float = 30.0) -> Tuple[bool, str]:
    """Import a module in an isolated subprocess; return (ok, detail).

    `ok=False` on any import exception (including a bad/typo'd import the patch
    introduced) or on timeout — a self-modifying engine treats "can't confirm it
    loads within budget" as unsafe. Optional-dependency gaps are handled by the
    *caller* via a differential check (import before vs after the patch), so this
    deliberately does NOT special-case ModuleNotFoundError. Inability to launch
    the subprocess at all (infra error) returns ok=True so we never falsely
    revert on our own tooling failure, with a detail starting _NOT_RUN so the
    caller reports the patch unverified instead of checked.
    """
    code = (
        "import importlib, sys\n"
        f"m = {dotted!r}\n"
        "try:\n"
        "    importlib.import_module(m)\n"
        "except Exception:\n"
        "    import traceback; traceback.print_exc(); sys.exit(3)\n"
    )
    env = dict(os.environ)
    env["ELI_PATCH_SMOKE"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"import of {dotted} exceeded {timeout:.0f}s"
    except Exception as exc:
        return True, f"{_NOT_RUN}{exc}"
    if proc.returncode == 0:
        return True, ""
    tail = "\n".join((proc.stderr or "").splitlines()[-6:])
    return False, tail


def _run_targeted_tests(module_path: Path, timeout: float = 120.0) -> Tuple[bool, bool, str]:
    """CI-grade verification: run the tests most related to a just-patched module
    (`pytest -k <module-stem>`). Returns (ran, passed, detail):

      • ran=False  → no matching tests, a timeout, or an infra error. Never a false revert,
                     but the patch is reported as unverified (ELI_SELFPATCH_REQUIRE_TESTS=1
                     reverts it instead).
      • ran=True, passed=False → a genuine regression: the caller reverts.

    pytest exit codes: 0 all-pass, 1 failures, 5 no-tests-collected."""
    try:
        stem = module_path.stem
        if not stem or stem in ("__init__",):
            return (False, True, "no test target")
        base = [sys.executable, "-m", "pytest", "tests/", "-k", stem,
                "-p", "no:cacheprovider", "-q"]
        co = subprocess.run(base + ["--collect-only"], cwd=str(PROJECT_ROOT),
                            capture_output=True, text=True, timeout=60)
        if co.returncode == 5:
            return (False, True, "no matching tests")
        proc = subprocess.run(base + ["-x"], cwd=str(PROJECT_ROOT),
                              capture_output=True, text=True, timeout=timeout)
        if proc.returncode == 0:
            return (True, True, "targeted tests passed")
        if proc.returncode == 5:
            return (False, True, "no matching tests")
        tail = "\n".join((proc.stdout or "").strip().splitlines()[-6:])
        return (True, False, tail or "targeted tests failed")
    except subprocess.TimeoutExpired:
        return (False, True, "targeted test run timed out — tolerated")
    except Exception as exc:
        return (False, True, f"targeted test infra error — tolerated: {exc}")


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _ensure_failure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_input TEXT
        )
        """
    )
    _ensure_columns(conn, "failures", {
        "source": "TEXT",
        "status": "TEXT",
        "command": "TEXT",
        "error": "TEXT",
        "context": "TEXT",
        "occurrence_count": "INTEGER DEFAULT 1",
        "timestamp": "REAL",
        "first_seen": "REAL",
        "last_seen": "REAL",
        "confidence": "REAL DEFAULT 0.0",
    })

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS self_repair_steps (
            step_key TEXT PRIMARY KEY,
            done_at REAL NOT NULL,
            proposal TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS code_patches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            description TEXT,
            old_code TEXT,
            new_code TEXT,
            status TEXT DEFAULT 'applied',
            timestamp REAL,
            failure_ref TEXT
        )
        """
    )
    _ensure_columns(conn, "code_patches", {
        "hypothesis": "TEXT", "verdict": "TEXT", "results": "TEXT", "cost_s": "REAL", "parent_id": "INTEGER",
        "probation_actions": "TEXT", "probation_baseline": "TEXT", "probation_started": "REAL",
    })
    conn.commit()



_WORKSPACE_DIRS = ("eli", "api", "tests", "tools", "config")
_WORKSPACE_FILES = ("pytest.ini", "pyproject.toml", "conftest.py")
_REPLAYABLE_ACTIONS = frozenset({"DATE", "TIME", "GPU_STATUS", "RUNTIME_STATUS", "MEMORY_STATUS", "MEMORY_STATS",
                                 "SYSTEM_STATUS", "CPU_USAGE", "LIST_DIR", "EXPLAIN_MEMORY_RUNTIME", "IMAGE_STATUS"})

# Staged deployment: invocations needed for a verdict, max reliability drop before it
# counts as a regression, and how long to watch before giving up on low traffic.
_PROBATION_MIN_INVOCATIONS = 5
_PROBATION_REGRESSION_DROP = 0.15
_PROBATION_MAX_DAYS = 14.0


def _actions_for_patch_file(file_path: str) -> List[str]:
    """Actions worth watching after patching this file — only where the file->action
    mapping is unambiguous (a plugin's own plugin.py). executor_enhanced.py/
    router_enhanced.py each back hundreds of actions; a patch there can't be
    attributed to any one of them, so those patches keep today's immediate-adopt
    behaviour rather than a misleading probation.
    """
    try:
        rel = Path(file_path).resolve().relative_to(_patch_root()).as_posix()
    except Exception:
        rel = str(file_path or "")
    m = re.match(r"eli/plugins/([^/]+)/plugin\.py$", rel)
    if not m:
        return []
    plugin_name = m.group(1)
    try:
        from eli.runtime.capability_sync import CapabilitySync
        caps = CapabilitySync(repo_root=_patch_root()).discover()
    except Exception:
        return []
    return sorted(a for a, meta in caps.items() if meta.get("plugin") == plugin_name)


def _failure_context(failure: Dict[str, Any]) -> Dict[str, Any]:
    ctx = failure.get("context")
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except (ValueError, TypeError):
            ctx = {}
    return ctx if isinstance(ctx, dict) else {}


def _traceback_location(error: str) -> Optional[Tuple[Path, int]]:
    """The deepest traceback frame that is a file in ELI's source tree: where the error happened."""
    root = _patch_root()
    found: Optional[Tuple[Path, int]] = None
    for m in re.finditer(r'File "([^"]+\.py)", line (\d+)', error or ""):
        p = Path(m.group(1))
        try:
            p = (root / p if not p.is_absolute() else p).resolve()
            p.relative_to(root)
        except (ValueError, OSError):
            continue
        if p.is_file():
            found = (p, int(m.group(2)))
    return found


def _error_summary(error: str) -> str:
    """The exception line of a traceback (its last line), or the error as it is."""
    lines = [ln.strip() for ln in str(error or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def repair_evidence(failure: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What a coding task may be built from, or None when the failure is not a fault in ELI's code.

    A repair needs a reproducible failure: the error with a traceback into ELI's own source (the
    code location), plus the action and arguments that ran when they were recorded. Rows from
    note_turn_anomalies (source 'anomaly': the person complained, corrected ELI, or a reply was
    caught claiming or promising what did not happen) are evidence for calibration, not code
    defects, and the person's own words never become a coding task. A failure with no location in
    ELI's source gives the agent nothing to change, and an environmental one (offline, a timeout,
    a missing device) is not a code defect either (failure_taxonomy.is_actionable).
    """
    if str(failure.get("source") or "").strip().lower() == "anomaly":
        return None
    error = _safe_str(failure.get("error")).strip()
    if not error:
        return None
    where = _traceback_location(error)
    if where is None:
        return None
    ctx = _failure_context(failure)
    intent = ctx.get("intent") if isinstance(ctx.get("intent"), dict) else {}
    capsule = ctx.get("capsule") if isinstance(ctx.get("capsule"), dict) else {}
    action = str(ctx.get("action") or intent.get("action") or capsule.get("action") or "").upper()
    args = next((a for a in (ctx.get("args"), intent.get("args"), capsule.get("args")) if isinstance(a, dict)), {})
    try:
        from eli.runtime.failure_taxonomy import classify, is_actionable
        if not is_actionable(classify(error, action)["category"]):
            return None
    except (ImportError, KeyError, TypeError, ValueError):
        log.debug("failure not classified for repair", exc_info=True)
    path, line = where
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    from eli.runtime.code_examiner import _build_fix_context, _max_file_chars
    ev: Dict[str, Any] = {"action": action, "args": args, "error": error, "line": line,
                          "file": path.relative_to(_patch_root()).as_posix(),
                          "whole_file": len(source) <= _max_file_chars()}
    if ev["whole_file"]:
        ev["source"] = source
    else:
        ev["context"] = _build_fix_context(source, line)
    return ev


def failure_capsule(action: str, args: Any, result: Any, *, request_id: str = "") -> Dict[str, Any]:
    """Everything needed to look at a failure again: input, versions, model, error class and the error itself."""
    from eli.runtime.failure_taxonomy import classify
    err = ""
    if isinstance(result, dict):
        err = str(result.get("error") or result.get("content") or "")[:500]
    model = ""
    try:
        from eli.core.paths import get_paths
        snap = json.loads((Path(get_paths().artifacts_dir) / "runtime_snapshot.json").read_text(encoding="utf-8"))
        model = str(snap.get("model_name") or "")
    except Exception:
        model = ""
    try:
        from importlib.metadata import version
        eli_version = version("eli-v2.0")
    except Exception:
        eli_version = ""
    return {"action": str(action or "").upper(), "args": args if isinstance(args, dict) else {}, "error": err,
            "classification": classify(err, command=str(action or "")), "eli_version": eli_version, "model": model,
            "python": sys.version.split()[0], "request_id": request_id, "captured_at": time.time()}


def capsule_reproducer(capsule: Dict[str, Any]) -> Optional[List[str]]:
    """A command that re-runs the failed action and exits non-zero when it fails again, or None when replay is unsafe."""
    action = str(capsule.get("action") or "").upper()
    if action not in _REPLAYABLE_ACTIONS:
        return None
    code = ("import json,sys;from eli.execution.executor_enhanced import execute;"
            f"r=execute({action!r}, json.loads({json.dumps(json.dumps(capsule.get('args') or {}))}));"
            "sys.exit(0 if isinstance(r, dict) and r.get('ok') else 1)")
    return [sys.executable, "-c", code]


def _make_workspace(root: Path, dest: Path) -> Path:
    """A working copy of the source tree. Files are hard-linked, so it is instant and costs no space until one is changed."""
    def link(src: str, dst: str) -> None:
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    skip = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.eli_bak", "artifacts", "models", "*.gguf")
    for d in _WORKSPACE_DIRS:
        if (root / d).is_dir():
            shutil.copytree(root / d, dest / d, ignore=skip, copy_function=link, symlinks=True)
    for f in _WORKSPACE_FILES:
        if (root / f).is_file():
            link(str(root / f), str(dest / f))
    return dest


def _replace_exactly_once(text: str, old: str, new: str) -> Tuple[Optional[str], str]:
    """``text`` with ``old`` replaced by ``new``, or (None, why) when ``old`` is not there exactly
    once. A patch whose text occurs twice does not say which place it means, and replacing the
    first one rewrote the wrong function."""
    count = text.count(old) if old else 0
    if count == 1:
        return text.replace(old, new, 1), ""
    if count == 0:
        return None, "old_code not found verbatim (the patch is stale or incorrect)"
    return None, f"old_code occurs {count} times, so the patch does not say which one it changes"


def _change_in_workspace(path: Path, old: str, new: str) -> bool:
    """Replace text in a workspace file without touching the original it is linked to."""
    text = path.read_text(encoding="utf-8")
    changed, _why = _replace_exactly_once(text, old, new)
    if changed is None:
        return False
    path.unlink()
    path.write_text(changed, encoding="utf-8")
    return True


# Colour codes inside "FAILED tests/x.py::t" hid every failure from the parse below when the user's
# shell sets FORCE_COLOR: a regression read as "unchanged".
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _run_cmd(cmd: List[str], cwd: Path, timeout: float) -> Dict[str, Any]:
    env = dict(os.environ, PYTHONPATH=str(cwd), ELI_TEST_MODE="1", PYTHONDONTWRITEBYTECODE="1",
               PY_COLORS="0", NO_COLOR="1")
    env.pop("FORCE_COLOR", None)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "seconds": round(time.time() - t0, 1), "failed": []}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "seconds": round(time.time() - t0, 1), "failed": []}
    out = _ANSI.sub("", proc.stdout or "")
    failed = sorted(set(re.findall(r"^FAILED (\S+)", out, re.M)))
    if proc.returncode == 5:
        status = "no_tests"
    elif proc.returncode in (0, 1):
        status = "ok" if proc.returncode == 0 else "failed"
    else:
        status = "error"
    return {"status": status, "returncode": proc.returncode, "seconds": round(time.time() - t0, 1), "failed": failed,
            "tail": "\n".join((out or _ANSI.sub("", proc.stderr or "")).strip().splitlines()[-4:])}


def compare_runs(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> str:
    """fixed, regression, unchanged or inconclusive. A run that did not really run proves nothing either way."""
    if baseline["status"] in ("timeout", "error", "no_tests") or candidate["status"] in ("timeout", "error", "no_tests"):
        return "inconclusive"
    before, after = set(baseline["failed"]), set(candidate["failed"])
    if after - before:
        return "regression"
    if before - after:
        return "fixed"
    if baseline["returncode"] != candidate["returncode"]:
        return "fixed" if candidate["returncode"] == 0 else "regression"
    return "unchanged"

def note_turn_anomalies(facts: Dict[str, Any], action: str, outcome: str) -> List[str]:
    """Turns that went wrong without anything raising. The failure record only held actions that
    returned an error, so asked to analyse its failures ELI found two in an afternoon where it had
    promised work five times and done none, and every one of those turns says "ok" in the ledger.

    Each goes on the same record self-analysis reads, with what the pipeline did:
      - the person rejected the last reply (the turn being complained about is the one wrong);
      - an action the model chose was turned into chat;
      - the reply claimed something and the claim was dropped;
      - the reply promised something and nothing ran.
    """
    said = str(facts.get("user_input") or "").strip()
    if not said or facts.get("parent_request_id"):
        return []
    kinds: List[tuple] = []
    from eli.cognition.correction_patterns import rejects_reply
    if rejects_reply(said):
        try:
            from eli.runtime import orchestrator_audit_ledger as oal
            last = next((r for r in oal.recent_turns(4) if not r.get("parent_request_id")), None)
            if last:
                kinds.append(("rejected", f"the person rejected the reply to the turn before: that turn was "
                                          f"{last.get('action') or '?'}, {str(last.get('outcome') or '')}"))
        except Exception as e:
            kinds.append(("rejected", f"the person rejected the reply to the turn before, which could not be read: {e}"))
    if facts.get("downgraded_from"):
        kinds.append(("downgraded", f"the model chose {facts['downgraded_from']} and it was answered as chat"))
    if facts.get("claim_dropped"):
        kinds.append(("claim", f"the reply said what the record does not back: {facts['claim_dropped']}"))
    if facts.get("promise_unkept"):
        kinds.append(("promise", f"the reply promised something and nothing ran: {facts['promise_unkept']}"))
    if not kinds:
        return []
    memory = get_self_improvement().memory
    for kind, what in kinds:
        memory.log_failure(said, error=f"anomaly ({kind}): {what}", context={"action": action, "outcome": outcome},
                           source="anomaly")
    return [k for k, _ in kinds]


class SelfImprovementEngine:
    """
    Self-improvement engine: analyzes failures, generates code patches, and applies them.
    Runs on the AGENT DB by default.
    """

    def __init__(self, memory: Optional[Memory] = None):
        self.memory: Memory = memory or get_agent_memory()

    # ─────────────────────────────────────────────────────────────────────────
    # Logging
    # ─────────────────────────────────────────────────────────────────────────

    def log_failure(self, input_text: str, error: str = "", confidence: float = 0.0, context: dict = None):
        # Drop MagicMock reprs at the source — a patched subprocess.run in a test leaked
        # "<MagicMock name=...>" into the live failures DB and polluted SELF_ANALYZE.
        import re as _re_mock
        if _re_mock.search(r"<\s*(?:Magic)?Mock\b|(?:Magic)?Mock\s+name=|\bMock\s+id=0x",
                           f"{error} {input_text}"):
            return
        ctx = dict(context or {}) if isinstance(context, dict) else (context or {})
        if isinstance(ctx, dict) and ctx.get("action") and "capsule" not in ctx:
            try:
                ctx["capsule"] = failure_capsule(ctx.get("action"), ctx.get("args"), ctx.get("result") or {"error": error})
            except Exception:
                log.debug("failure capsule not built", exc_info=True)
        # One failure store: the failures table (Memory.log_failure), which self-analysis, self-repair
        # and lessons all read. Its occurrence count is the recurrence the escalation below counts.
        try:
            row_id = self.memory.log_failure(input_text, error=error, confidence=confidence, context=ctx)
        except Exception:
            log.debug("failure not recorded", exc_info=True)
            return
        if row_id is None:
            return                      # not an actionable failure (Memory._NON_ACTIONABLE_FAILURE_PATTERNS)
        error_type = _safe_str(error) or _safe_str(input_text)
        conn = self.memory._get_connection()
        try:
            row = conn.execute("SELECT occurrence_count FROM failures WHERE id = ?", (int(row_id),)).fetchone()
        finally:
            conn.close()
        new_count = int((row or [1])[0] or 1)

        # Escalation: >=5x notices next turn, >=10x also attempts self-resolution.
        # Skips fault=False (user-input/clarification, not real faults).
        if new_count in (5, 10) or (new_count > 10 and new_count % 5 == 0):
            if not (isinstance(ctx, dict) and ctx.get("fault") is False):
                stage = "notice" if new_count < 10 else "act"
                head = (f"Heads up — I keep hitting an error: “{error_type[:90]}” "
                        f"({new_count}× now).")
                tail = ("I'm flagging it and keeping watch." if stage == "notice"
                        else "I'm going to try to resolve it myself and let you know.")
                _push_self_heal_notice({
                    "error_type": error_type[:140], "count": int(new_count),
                    "stage": stage, "message": f"{head} {tail}",
                })
        # Auto-trigger improvement analysis when an error pattern recurs 5× (and every 5× after)
        if new_count >= 5 and new_count % 5 == 0:
            log.debug(f"[SELF-IMPROVE] Recurring error pattern detected ({new_count}×) — auto-triggering capability analysis")
            threading.Thread(target=self._background_analyze, daemon=True,
                             args=(error_type, int(new_count))).start()

    def _background_analyze(self, error_type: str = "", count: int = 0) -> None:
        """Run analyze_and_improve then attempt code patches for high-recurrence failures.
        For a ≥10× error this also records a user-facing OUTCOME notice so ELI can report
        what it actually tried (proposals generated / patch applied / logged for review)."""
        _proposals = 0
        _patched: List[str] = []
        _outcome = "logged it for review — I couldn't auto-resolve it this pass"

        def _report():
            if count >= 10:
                if _patched:
                    out = "applied a fix to " + ", ".join(_patched)
                elif _proposals:
                    out = f"worked up {_proposals} fix proposal(s) for it"
                else:
                    out = _outcome
                _push_self_heal_notice({
                    "error_type": (error_type or "a recurring error")[:140],
                    "count": int(count), "stage": "resolved",
                    "message": f"Update on “{(error_type or 'that error')[:80]}”: I {out}.",
                })

        try:
            result = self.analyze_and_improve()
            imps = result.get("improvements", [])
            _proposals = len(imps)
            if imps:
                log.debug(f"[SELF-IMPROVE] Auto-analysis complete: {len(imps)} proposal(s) generated")
            else:
                log.debug("[SELF-IMPROVE] Auto-analysis complete: no new proposals")
        except Exception as _ae:
            log.debug(f"[SELF-IMPROVE] Auto-analysis failed: {_ae}")
            _report()
            return

        # Patches failures with file tracebacks + high recurrence; gated behind
        # auto_patch_enabled (default off, opt-in via settings.json).
        try:
            from eli.core.full_control import is_full_control as _ifc
        except Exception:
            _ifc = lambda: False
        try:
            _settings = json.loads((PROJECT_ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
            if not _settings.get("auto_patch_enabled", False) and not _ifc():
                log.debug("[SELF-IMPROVE] auto_patch_enabled is off — patch proposals logged but not applied")
                _report()
                return
        except Exception:
            if not _ifc():
                log.debug("[SELF-IMPROVE] Could not read settings — skipping auto-patch for safety")
                _report()
                return

        # Only runs when an inference broker is available (model loaded).
        try:
            broker = get_broker()
            if broker is None:
                _report()
                return
        except Exception:
            _report()
            return

        try:
            high_recurrence = self.analyze_failures(limit=5, days=14, min_cluster_size=5)
            patchable = [
                f for f in high_recurrence
                if f.get("error") and 'File "' in str(f.get("error", ""))
            ]
            if not patchable:
                _report()
                return
            log.debug(f"[SELF-IMPROVE] Attempting code patches for {len(patchable)} high-recurrence failure(s)")
            for failure in patchable[:2]:  # cap at 2 patches per cycle to limit LLM load
                try:
                    patch = self.generate_code_patch(failure)
                    if not patch.get("ok"):
                        log.debug(f"[SELF-IMPROVE] Patch generation skipped: {patch.get('error', '?')}")
                        continue
                    apply_result = self.apply_autonomously(patch, failure)
                    if apply_result.get("ok"):
                        log.debug(f"[SELF-IMPROVE] Patch applied to {patch.get('file')}: {patch.get('description')}")
                        _patched.append(str(patch.get("file") or "a file"))
                    else:
                        log.debug(f"[SELF-IMPROVE] Patch rejected: {apply_result.get('error', '?')}")
                except Exception as _patch_err:
                    log.debug(f"[SELF-IMPROVE] Patch attempt failed: {_patch_err}")
        except Exception as _chain_err:
            log.debug(f"[SELF-IMPROVE] Patch chain failed: {_chain_err}")
        _report()

    def log_improvement(self, category: str, description: str, area: str = "runtime",
                        code_before: str = "", code_after: str = ""):
        self.memory.log_improvement(category, description, area=area,
                                    code_before=code_before, code_after=code_after)

    def handle_correction(self, original: str, corrected_action: str, _corrected_args: dict = None):
        self.memory.log_correction(_safe_str(original), _safe_str(corrected_action))
        return {"ok": True}

    # ─────────────────────────────────────────────────────────────────────────
    # Analysis
    # ─────────────────────────────────────────────────────────────────────────

    def failures_outside_window(self, days: int = 7) -> Dict[str, Any]:
        """Open failures OLDER than the analysis window.

        `analyze_failures` looks back `days` and says nothing about what it did not
        look at. Live at 2.3.10 that produced two self-reports contradicting each
        other seconds apart: `self improve` reported "failures_inspected: 3" and
        printed the newest error, while `analyse yourself` reported "0 recent
        issues — No recent failures found." Both were correct — the seven stored
        failures were 9.8 to 15.1 days old, outside SELF_ANALYZE's 7-day window —
        and together they read as ELI contradicting itself.

        Saying "none in the last 7 days" is true. Saying "no failures found" when
        seven are open is not.
        """
        conn = self.memory._get_connection()
        try:
            since = time.time() - (days * 86400)
            row = conn.execute(
                """SELECT COUNT(*), MIN(timestamp)
                   FROM failures
                   WHERE timestamp < ?
                     AND COALESCE(status, 'open') NOT IN ('resolved', 'closed')""",
                (since,),
            ).fetchone()
            count = int((row or [0])[0] or 0)
            oldest_ts = (row or [0, None])[1]
            oldest_days = ((time.time() - oldest_ts) / 86400.0) if oldest_ts else 0.0
            return {"count": count, "oldest_days": round(oldest_days, 1)}
        except Exception as exc:
            log.debug("[SELF_IMPROVE] could not count older failures: %s", exc)
            return {"count": 0, "oldest_days": 0.0}
        finally:
            try:
                conn.close()
            except Exception:
                log.debug("suppressed exception", exc_info=True)

    def analyze_failures(
        self,
        limit: int = 50,
        days: int = DEFAULT_ANALYSIS_DAYS,
        min_cluster_size: int = ANALYSIS_MIN_CLUSTER,
    ) -> List[Dict[str, Any]]:
        conn = self.memory._get_connection()
        try:
            _ensure_failure_tables(conn)
            since = time.time() - (days * 86400)
            cur = conn.execute(
                """SELECT user_input, command, error, context, occurrence_count, timestamp, id, source
                   FROM failures
                   WHERE timestamp >= ?
                     AND COALESCE(status, 'open') NOT IN ('resolved', 'closed')
                   ORDER BY occurrence_count DESC, timestamp DESC LIMIT ?""",
                (since, int(limit)),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            if min_cluster_size > 1:
                rows = [r for r in rows if (r.get("occurrence_count") or 1) >= min_cluster_size]
            return rows
        finally:
            try:
                conn.close()
            except Exception:
                log.debug("suppressed exception", exc_info=True)

    def analyze_and_improve(self, propose: Optional[bool] = None, *, repair: bool = False) -> Dict[str, Any]:
        """Analyse recent failures and log improvement records.

        `repair=False` (the default) keeps this method what its name promises: a
        fast, read-mostly pass over the failure table. `repair=True` (or the older
        `propose=True`) belongs only on a BOUNDED BACKGROUND job — it invokes the coding agent, which fans out to
        tens of sequential model calls (see propose_via_agent).
        """
        try:
            self._check_probationary_patches()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        failures = self.analyze_failures(limit=25, min_cluster_size=1)
        improvements: List[Dict[str, Any]] = []

        existing_descs: set = set()
        try:
            conn = self.memory._get_connection()
            try:
                # Only skip failures already investigated in the last 14 days.
                # Older entries are re-queued so stale failures don't block forever.
                _cutoff = time.time() - (14 * 86400)
                rows = conn.execute(
                    "SELECT description FROM improvements "
                    "WHERE COALESCE(timestamp, ts, 0) > ? "
                    "ORDER BY COALESCE(timestamp, ts, 0) DESC LIMIT 50",
                    (_cutoff,),
                ).fetchall()
                existing_descs = {str(r[0]).strip().lower() for r in rows if r[0]}
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            finally:
                conn.close()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        for f in failures[:10]:
            ui = _safe_str(f.get("user_input"))
            err = _safe_str(f.get("error"))
            if not ui and not err:
                continue
            desc = f"Investigate failure: {ui} → {err}".strip()
            if desc.lower() in existing_descs:
                continue
            # Classified from the failure itself. This was a hardcoded stability/runtime for every
            # proposal, so the improvements table carried no signal to prioritise or filter on: a
            # CUDA OOM and a wrong dict key were indistinguishable.
            try:
                from eli.runtime.failure_taxonomy import classify
                tags = classify(err, _safe_str(f.get("command")), ui)
            except Exception:
                log.debug("failure classification unavailable", exc_info=True)
                tags = {"category": "stability", "area": "runtime",
                        "severity": "unknown", "exception": ""}
            improvements.append({
                "category": tags["category"],
                "area": tags["area"],
                "severity": tags["severity"],
                "exception": tags.get("exception", ""),
                "description": desc,
            })

        # Real defects before environmental noise. Only five are logged per pass, and a deliberately
        # offline machine can fill that slice with network failures, burying the TypeError that is an
        # actual bug. Order is stable within each group, so the newest actionable failure still leads.
        try:
            from eli.runtime.failure_taxonomy import is_actionable
            improvements.sort(key=lambda i: not is_actionable(i.get("category", "")))
        except Exception:
            log.debug("could not prioritise improvements", exc_info=True)

        for imp in improvements[:5]:
            try:
                self.log_improvement(imp["category"], imp["description"], area=imp.get("area", "runtime"))
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            try:
                from eli.cognition.thoughts import record as _thought
                _thought("diagnosis", str(imp.get("description") or "")[:140],
                         why=[f"a {imp.get('category', 'stability')} failure in {imp.get('area', 'runtime')}",
                              f"its severity is {imp.get('severity', 'unknown')}"],
                         decision="proposed", reason="it recurred and is worth a fix" if imp.get("severity") in ("bug", "unknown")
                         else "it is the environment's, not a defect",
                         subject=str(imp.get("area") or ""), ref=f"diagnosis:{str(imp.get('description') or '')[:80]}")
            except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
                log.debug("diagnosis thought not kept", exc_info=True)

        # Frontier self-repair: when there are NEW (un-investigated) failures and the model
        # is resident, route them through the coding agent (decompose→solve→VERIFY) and
        # PERSIST the verified/candidate fixes as proposal-only goals — so they survive and
        # surface via GET_PROPOSALS. Previously analyze_and_improve only logged 'investigate'
        # stubs that never became anything (the proposals=0 root cause). Propose-only:
        # nothing is auto-applied. Gated so the daemon never thrashes the GGUF/coding agent.
        #
        # 2026-08-02: this used to fire on `_model_ready` ALONE, so a read-sounding
        # "analyze" detonated the full coding agent (~85-130 sequential model calls,
        # 30-500s each) on whatever thread called it — including the GUI chat worker
        # and the shutdown path. That is the 2-hour freeze. Repair is now opt-in and
        # belongs on a bounded background job; the caller owns the wall-clock budget.
        repair = bool(repair or propose)
        proposals_made = 0
        if improvements and repair:
            try:
                from eli.cognition import gguf_inference as _gi
                _model_ready = bool(getattr(_gi, "is_loaded", lambda: False)())
            except Exception:
                _model_ready = False
            if _model_ready:
                proposals_made = self._generate_and_persist_fix_proposals(max_items=2)

        try:
            from eli.cognition.persona_updater import update_persona_overlay
            update_persona_overlay(memory=self.memory)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        return {"improvements": improvements, "proposals_made": proposals_made}

    def _generate_and_persist_fix_proposals(self, max_items: int = 2,
                                            budget_s: Optional[float] = None) -> int:
        """Run the coding-agent self-repair proposer and persist each result as a
        proposal-only goal (visible via GET_PROPOSALS). Returns the count persisted.
        Best-effort; never raises into the caller."""
        made = 0
        try:
            gen = self.propose_via_agent(max_items=max_items, budget_s=budget_s)
            for pr in (gen.get("proposals") or []):
                if not isinstance(pr, dict):
                    continue
                fail = _safe_str(pr.get("failure")).strip()
                if not fail:
                    continue
                verified = bool(pr.get("verified"))
                approach = _safe_str(pr.get("approach")).strip()
                vtag = "verified fix" if verified else "candidate fix"
                try:
                    import hashlib as _hl
                    from eli.planning.goal_store import list_active_goals, load_goals, upsert_goal
                    from eli.planning.goal_models import GoalSpec
                    gid = "selfrepair_" + _hl.sha1(fail.encode("utf-8", "ignore")).hexdigest()[:12]
                    # A goal the work loop (or the owner) closed stays closed: only a new goal, or one
                    # that is still open, is written. Re-upserting as active reopened finished work.
                    if (any(g.goal_id == gid for g in load_goals())
                            and not any(g.goal_id == gid for g in list_active_goals())):
                        continue
                    upsert_goal(GoalSpec.from_any({
                        "goal_id": gid,
                        "title": f"Self-repair ({vtag}): {fail[:70]}",
                        "objective": (
                            f"A {vtag} for the recurring failure '{fail[:120]}' is ready"
                            + (f" — approach: {approach[:120]}" if approach else "")
                            + (f" (candidate {pr['candidate_id']} for {pr.get('file')}, applied only when approved "
                               "and a copy of the tree shows it fixes the failure)." if pr.get("candidate_id")
                               else " (not a new version of the failing file: nothing to apply).")
                        ),
                        "priority": 0.6 if verified else 0.45,
                        "autonomy_mode": "proposal_only",
                        "tags": ["self_improve", "verified_fix" if verified else "candidate_fix"],
                        "enabled": True,
                        "status": "active",
                    }))
                    made += 1
                except Exception:
                    continue
        except Exception as exc:
            log.debug("[SELF_IMPROVEMENT] generate/persist proposals failed: %s", exc)
        return made

    # ─────────────────────────────────────────────────────────────────────────
    # Coding-agent route — decompose → solve → VERIFY (propose-only)
    # ─────────────────────────────────────────────────────────────────────────
    def _build_fix_task(self, failure: dict, evidence: Optional[dict] = None) -> str:
        """A coding task built from a reproducible failure: the action that failed, its arguments,
        the error and the code location. Never from what the person said — a complaint or a
        correction is evidence for calibration, not a description of a bug.

        The file the error points at is inlined whole when it fits the model's window, and the task
        then asks for that file back; otherwise the code around the failing line is shown and the
        task asks for the corrected code only (it is then not a whole-file candidate)."""
        ev = evidence if evidence is not None else repair_evidence(failure)
        if not ev:
            return ""
        parts = [f"Fix the bug that causes this failure: {ev['error'][:500]}"]
        if ev.get("action"):
            parts.append(f"The action that failed: {ev['action']}"
                         + (f" with arguments {json.dumps(ev.get('args') or {}, default=str, ensure_ascii=False)[:300]}"
                            if ev.get("args") else ""))
            # what the record says of this action: how it has fared here, the causes, what worked instead
            try:
                from eli.runtime.action_outcomes import report as _outcomes
                told = _outcomes(action=ev["action"]).get("lines") or []
                if told:
                    parts.append("What ELI's own record says of this action: " + " ".join(told))
            except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
                log.debug("action record unavailable to the fix task", exc_info=True)
        if ev.get("whole_file"):
            parts.append(f"Correct this file ({ev['file']}) and return the complete fixed version:\n"
                         f"```python\n{ev['source']}\n```")
        else:
            parts.append(f"The code around line {ev.get('line') or '?'} of {ev['file']}:\n"
                         f"```python\n{ev['context']}\n```\nPropose a minimal corrected implementation.")
        return "\n".join(parts)

    @staticmethod
    def _step_key(failure: dict, ev: dict) -> str:
        """One self-repair step: this failure as it stands. It recurring (its count growing) is a new step."""
        import hashlib
        raw = f"{ev['file']}|{ev['error']}|{failure.get('id')}|{failure.get('occurrence_count')}"
        return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()

    def _finished_step(self, key: str) -> Optional[dict]:
        """The proposal a finished step produced, or None when the step has not finished."""
        try:
            conn = self.memory._get_connection()
            try:
                _ensure_failure_tables(conn)
                row = conn.execute("SELECT proposal FROM self_repair_steps WHERE step_key = ?", (key,)).fetchone()
            finally:
                conn.close()
            return json.loads(row[0]) if row and row[0] else None
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            log.debug("self-repair checkpoint unreadable", exc_info=True)
            return None

    def _finish_step(self, key: str, proposal: dict) -> None:
        """Checkpoint one finished step, so a job cut off later resumes after it instead of redoing it.
        Steps older than the analysis window are dropped: their failures are outside it too."""
        try:
            conn = self.memory._get_connection()
            try:
                _ensure_failure_tables(conn)
                now = time.time()
                conn.execute("INSERT OR REPLACE INTO self_repair_steps (step_key, done_at, proposal) VALUES (?,?,?)",
                             (key, now, json.dumps(proposal, default=str)))
                conn.execute("DELETE FROM self_repair_steps WHERE done_at < ?", (now - DEFAULT_ANALYSIS_DAYS * 86400,))
                conn.commit()
            finally:
                conn.close()
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            log.debug("self-repair checkpoint not written", exc_info=True)

    def _repairable_failures(self, max_items: int) -> List[Tuple[dict, dict]]:
        """(failure, evidence) for the newest failures a code change could fix, most frequent first."""
        out: List[Tuple[dict, dict]] = []
        for f in self.analyze_failures(limit=25, min_cluster_size=1):
            ev = repair_evidence(f)
            if ev:
                out.append((f, ev))
            if len(out) >= max_items:
                break
        return out

    def propose_via_agent(self, max_items: int = 3, run_timeout: float = 20.0,
                          budget_s: Optional[float] = None) -> Dict[str, Any]:
        """Route self-improvement through the CODING AGENT: per recent failure that names a code
        location in ELI's source, the agent decomposes → solves → VERIFIES a fix (its tree-search +
        execution gate). Propose-only: nothing is applied. A candidate that is a new version of the
        failing file is kept in the candidate archive (record_candidate) for the owner to approve;
        approve_candidate is the only way it reaches the source.

        `run_timeout` is the SANDBOX subprocess timeout for executing candidate code.
        `budget_s` is the WALL-CLOCK budget shared across every failure in this call —
        without it this method is unbounded (the 2-hour freeze).
        """
        picked = self._repairable_failures(max_items)
        if not picked:
            return {"ok": True, "proposals": [],
                    "reason": "no recent failure names a code location in ELI's source"}
        try:
            from eli.coding.sandbox import containment
            if not containment().get("contained"):
                return {"ok": True, "proposals": [],
                        "reason": "model-written code cannot be contained on this machine "
                                  f"({'; '.join(containment().get('notes') or []) or 'no sandbox available'}), "
                                  "so self-repair does not run it"}
        except Exception as exc:
            return {"ok": False, "proposals": [], "error": f"the code sandbox could not be checked: {exc}"}
        try:
            from eli.core.dag import Task, run_graph
            from eli.coding.agent import CodeAgent
            from eli.coding.budget import Deadline, DEFAULT_SOLVE_BUDGET_S
            agent = CodeAgent()
            # ONE absolute deadline shared by every failure, so N failures cannot
            # multiply the wall clock. Checked before each model call downstream.
            deadline = Deadline(
                float(budget_s) if budget_s else _env_float(
                    "ELI_SELF_REPAIR_BUDGET_S", DEFAULT_SOLVE_BUDGET_S),
                label="self_repair",
            )

            def _mk(failure, ev):
                key = self._step_key(failure, ev)

                def _run(ctx):
                    finished = self._finished_step(key)
                    if finished is not None:
                        return {**finished, "resumed": True}
                    # The calls are background work (preemptible by a foreground turn, and they must
                    # not stamp _last_foreground_ts). run_graph may run this on the caller's own
                    # thread, so the marker is restored afterwards: the caller's later calls are not
                    # background calls.
                    try:
                        from eli.cognition.gguf_inference import is_background_inference, set_background_inference
                    except Exception:
                        is_background_inference = set_background_inference = None
                    was_bg = bool(is_background_inference()) if is_background_inference else False
                    if set_background_inference:
                        set_background_inference(True)
                    try:
                        cr = agent.solve(self._build_fix_task(failure, ev), run_timeout=run_timeout, deadline=deadline,
                                         target_file=(_patch_root() / ev["file"]) if ev.get("whole_file") else None)
                    finally:
                        if set_background_inference:
                            set_background_inference(was_bg)
                    label = f"{ev.get('action') or ev['file']}: {_error_summary(ev['error'])}"[:140]
                    proposal = {
                        "failure": label,
                        "file": ev["file"],
                        "verified": bool(getattr(cr, "solved", False)),
                        "score": round(float(getattr(cr, "score", 0.0) or 0.0), 2),
                        "approach": (getattr(cr, "plan", {}) or {}).get("approach"),
                        "message": getattr(cr, "message", ""),
                        "code": (getattr(cr, "code", "") or "")[:1500],
                    }
                    patch = getattr(cr, "patch", None)
                    if isinstance(patch, dict) and patch.get("ok"):
                        proposal["candidate_id"] = self.record_candidate(
                            {"file": patch["file"], "old": patch["old"], "new": patch["new"],
                             "description": f"coding-agent fix for {label}"[:300],
                             "failure_ref": label},
                            hypothesis=str(proposal["approach"] or "")[:400])
                        proposal["hunks"] = len(patch.get("hunks") or [])
                    elif isinstance(patch, dict):
                        proposal["not_a_patch"] = "; ".join(patch.get("problems") or [])
                    if not deadline.expired():
                        self._finish_step(key, proposal)        # a step the deadline (or shutdown) cut short is not finished
                    try:
                        from eli.runtime.background_tasks import report as _report
                        _report(sum(1 for _f, _e in picked if self._finished_step(self._step_key(_f, _e))),
                                len(picked), "proposing fixes")
                    except (ImportError, AttributeError, TypeError):
                        log.debug("self-repair progress not reported", exc_info=True)
                    return proposal
                return _run

            tasks = [Task(id=f"fix_{i}", run=_mk(f, ev), critical=False)
                     for i, (f, ev) in enumerate(picked)]
            # Serial on purpose: there is ONE model behind a single call lock
            # (gguf_inference._LLM_CALL_LOCK), so extra workers buy scheduling noise, not
            # throughput. With one worker and no task timeout run_graph runs the tasks on this
            # thread (see _run above). The shared deadline bounds the total either way.
            report = run_graph(tasks, max_workers=1)
            proposals = [o.result for _tid, o in report.outcomes.items() if o.ok and o.result]
            return {"ok": True, "proposals": proposals, "count": len(proposals),
                    "orchestration": report.to_dict()}
        except Exception as e:
            return {"ok": False, "error": str(e), "proposals": []}

    # ─────────────────────────────────────────────────────────────────────────
    # Code Patching — generate → validate → apply
    # ─────────────────────────────────────────────────────────────────────────

    def generate_code_patch(self, failure: dict) -> dict:
        """A targeted {file, old, new, description} patch for a recorded failure.

        The failure must be one repair_evidence accepts (a traceback into ELI's own source, not a
        complaint or an environmental fault); the patch itself is generated, validated and retried
        by the code examiner's one patch generator (code_examiner.generate_fix_patch), given the
        error, the action that failed and any prior fix for the same bug class.
        """
        ev = repair_evidence(failure)
        if not ev:
            ui = _safe_str(failure.get("user_input", ""))
            err = _safe_str(failure.get("error", ""))
            return {
                "ok": False,
                "error": "no_groundable_file",
                "reason": ("This failure has no traceback into ELI's own source (or is not a code defect), so "
                           "it is not code-patchable from its error text — routed to goal/self-heal "
                           "surfacing instead of guessing a file."),
                "failure_ref": f"{ui[:60]} → {err[:60]}",
            }
        message = [ev["error"][:600]]
        if ev.get("action"):
            message.append(f"The action that failed: {ev['action']}")
        # Self-upgrade through the coding engine's long-term bug memory: a prior fix for the same bug
        # class is the approach to try first.
        try:
            from eli.coding.bug_memory import classify_bug, get_bug_memory
            _dg = classify_bug(traceback_text=ev["error"], code=ev.get("source") or ev.get("context") or "")
            _recalls = get_bug_memory().recall(_dg, limit=2)
            if _recalls:
                message.append(f"This looks like a {_dg.bug_class.value} bug. Prior fixes that worked for this class: "
                               + "; ".join(f"({r.bug_class}, used {r.success_count}x) {r.fix_summary}" for r in _recalls))
        except Exception as _bm_e:
            log.debug(f"[SELF-IMPROVE] bug-memory recall skipped: {_bm_e}")
        from eli.runtime.code_examiner import generate_fix_patch
        patch = generate_fix_patch({"file": ev["file"], "line": ev.get("line"),
                                    "kind": f"recurring runtime failure ({failure.get('occurrence_count', 1)}x)",
                                    "message": "\n".join(message)})
        if patch.get("ok"):
            patch.setdefault("description", "self-improvement patch")
            patch["failure_ref"] = f"{ev.get('action') or ev['file']}: {_error_summary(ev['error'])}"[:140]
        return patch

    def apply_code_patch(self, patch: dict, verify: bool = True) -> dict:
        """Apply one ``{file, old, new}`` patch through the gated applier (apply_patch_set).

        Returns {"ok", "applied", "file", "backup", "reloaded", "verification", "message"}.
        """
        res = self.apply_patch_set([patch], verify=verify)
        files = res.get("files") or []
        out = {"ok": bool(res.get("ok")), "applied": bool(res.get("applied")), "message": res.get("message", "")}
        for key in ("verification", "reloaded"):
            if key in res:
                out[key] = res[key]
        if files:
            out["file"] = files[0]
        if res.get("backups"):
            out["backup"] = res["backups"][0]
        if isinstance(out.get("reloaded"), list):
            out["reloaded"] = bool(out["reloaded"])
        return out

    def apply_patch_set(self, patches: list, *, verify: bool = True) -> dict:
        """The one path by which ELI's own source is changed: a SET of ``{file, old, new}``
        patches, all or nothing.

          1. In memory, no writes: each target is inside the source tree, is not one of the
             protected guardrail files, is an existing .py file; each ``old`` occurs exactly once
             in the text it is applied to (hunks for one file apply in order); the result parses.
             ``new`` may be empty (a deletion).
          2. Import baseline of every touched module (only a break this set causes is blamed on it).
          3. Back up every target (timestamped + canonical ``.eli_bak``), then write.
          4. Compile; when ``verify``: import every touched module and run each file's targeted
             tests. ANY failure rolls back EVERY file. Tests that could not run leave the set
             reported unverified, or rolled back when ELI_SELFPATCH_REQUIRE_TESTS=1.
          5. Record each file's change (improvements + code_patches) and reload the modules.

        Returns {"ok", "applied", "files", "backups", "reloaded", "verification", "message"}.
        """
        if not patches or not isinstance(patches, list):
            return {"ok": False, "applied": False, "message": "no patches supplied"}

        # ── 1: validate ALL in memory (zero writes) ─────────────────────────────
        _root = _patch_root()
        by_path: Dict[Path, str] = {}
        original: Dict[Path, str] = {}
        order: List[Path] = []
        described: Dict[Path, List[dict]] = {}
        def _at(i: int) -> str:
            return f"patch {i}: " if len(patches) > 1 else ""

        for i, patch in enumerate(patches):
            file_str = (patch.get("file") or "").strip() if isinstance(patch, dict) else ""
            old_code = patch.get("old") if isinstance(patch, dict) else None
            new_code = patch.get("new") if isinstance(patch, dict) else None
            if not file_str or not isinstance(old_code, str) or not old_code or not isinstance(new_code, str):
                return {"ok": False, "applied": False,
                        "message": f"{_at(i)}missing required fields (file/old/new)"}
            if old_code == new_code:
                return {"ok": False, "applied": False, "message": f"{_at(i)}old and new are identical, no change"}
            p = Path(file_str)
            p = (_root / p if not p.is_absolute() else p).resolve()
            try:
                rel = p.relative_to(_root).as_posix()
            except ValueError:
                return {"ok": False, "applied": False, "message": f"{_at(i)}Refused: {p} is outside source root"}
            # The self-improver must never change the guardrails that contain it (network fail-closed,
            # shell denylist, Full Control, grounding, the patcher, the tests that judge it).
            if is_protected_patch_path(p):
                return {"ok": False, "applied": False,
                        "message": f"{_at(i)}Refused: {rel} is a protected safety guardrail and cannot be auto-patched"}
            if not p.exists() or p.suffix != ".py":
                return {"ok": False, "applied": False, "message": f"{_at(i)}{rel} is not an existing .py file"}
            try:
                content = by_path[p] if p in by_path else p.read_text(encoding="utf-8")
            except Exception as exc:
                return {"ok": False, "applied": False, "message": f"{_at(i)}read error {exc}"}
            if p not in by_path:
                original[p] = content
                order.append(p)
            changed, why = _replace_exactly_once(content, old_code, new_code)
            if changed is None:
                return {"ok": False, "applied": False, "message": f"{_at(i)}{why} in {p.name}"}
            try:
                ast.parse(changed)
            except SyntaxError as exc:
                return {"ok": False, "applied": False,
                        "message": f"{_at(i)}introduces a syntax error in {p.name} (line {exc.lineno}: {exc.msg})"}
            by_path[p] = changed
            described.setdefault(p, []).append(patch)

        # ── 2: import baselines — only blame a broken import on us if it was clean before ─
        dotted: Dict[Path, Optional[str]] = {}
        pre_ok: Dict[Path, bool] = {}
        if verify:
            for p in order:
                d = _dotted_module_for_path(p)
                dotted[p] = d
                pre_ok[p] = _smoke_import_module(d)[0] if d else False

        # ── 3: back up, then write ──────────────────────────────────────────────
        backups: Dict[Path, Path] = {}
        ts = int(time.time())
        try:
            for p in order:
                shutil.copy2(str(p), str(p.with_suffix(f".py.eli_bak.{ts}")))
                canon = p.with_suffix(".py.eli_bak")
                shutil.copy2(str(p), str(canon))
                backups[p] = canon
        except Exception as exc:
            return {"ok": False, "applied": False, "message": f"Could not create backup (nothing written): {exc}"}

        def _rollback_all():
            for _p in order:
                try:
                    _p.write_text(original[_p], encoding="utf-8")
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

        try:
            for p in order:
                p.write_text(by_path[p], encoding="utf-8")
        except Exception as exc:
            _rollback_all()
            return {"ok": False, "applied": False, "message": f"Write failed (all reverted): {exc}"}

        # ── 4: verify; roll back ALL on any failure ─────────────────────────────
        import py_compile
        for p in order:
            try:
                py_compile.compile(str(p), doraise=True)
            except Exception as exc:
                _rollback_all()
                return {"ok": False, "applied": False, "message": f"Compile error in {p.name} after patch (all reverted): {exc}"}
        unchecked: List[str] = []
        if verify:
            for p in order:
                d = dotted.get(p)
                if d and pre_ok.get(p):
                    ok, detail = _smoke_import_module(d)
                    if detail.startswith(_NOT_RUN):
                        unchecked.append(f"the import check of {p.name} {detail}")
                    if not ok:
                        _rollback_all()
                        log.debug(f"[SELF-IMPROVE] Patch reverted — import verification failed: {detail}")
                        return {"ok": False, "applied": False,
                                "message": f"Patch broke module import of {p.name} (all reverted): {detail}"}
        verification = "not run"
        if verify and os.environ.get("ELI_SELFPATCH_VERIFY_TESTS", "1").strip().lower() not in ("0", "false", "no", "off"):
            not_ran: List[str] = []
            for p in order:
                t_ran, t_passed, t_detail = _run_targeted_tests(p)
                if t_ran and not t_passed:
                    _rollback_all()
                    log.debug(f"[SELF-IMPROVE] Patch reverted — targeted tests failed: {t_detail[:200]}")
                    return {"ok": False, "applied": False,
                            "message": f"Patch broke targeted tests of {p.name} (all reverted): {t_detail[:200]}"}
                if not t_ran:
                    not_ran.append(f"{p.name}: {t_detail}")
            if not not_ran:
                verification = "targeted tests passed"
            else:
                verification = "unverified: " + "; ".join(not_ran)
                if os.environ.get("ELI_SELFPATCH_REQUIRE_TESTS", "0").strip().lower() in ("1", "true", "yes", "on"):
                    _rollback_all()
                    return {"ok": False, "applied": False, "verification": verification,
                            "message": f"Patch reverted, its tests could not run ({'; '.join(not_ran)[:160]})"}
        if unchecked:
            verification = "unverified: " + "; ".join(unchecked)

        # ── 5: record and reload ────────────────────────────────────────────────
        files = [p.relative_to(_root).as_posix() for p in order]
        status = "applied" if verification == "targeted tests passed" else "applied_unverified"
        for p, rel in zip(order, files):
            for patch in described[p]:
                description = str(patch.get("description") or "self-improvement patch")
                try:
                    self.log_improvement("code_patch", f"Patched {rel}: {description}", area="code",
                                         code_before=str(patch.get("old"))[:500], code_after=str(patch.get("new"))[:500])
                except Exception:
                    log.debug("patch not logged as an improvement", exc_info=True)
                try:
                    conn = self.memory._get_connection()
                    try:
                        _ensure_failure_tables(conn)
                        conn.execute(
                            "INSERT INTO code_patches (file_path, description, old_code, new_code, status, timestamp, failure_ref) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (rel, description, str(patch.get("old"))[:1000], str(patch.get("new"))[:1000], status,
                             time.time(), str(patch.get("failure_ref") or "")))
                        conn.commit()
                    finally:
                        conn.close()
                except Exception:
                    log.debug("patch not recorded in code_patches", exc_info=True)
        reloaded: List[str] = []
        if verify:
            for p in order:
                d = dotted.get(p)
                if d and _reload_patched_module(d):
                    reloaded.append(d)
        names = ", ".join(p.name for p in order)
        what = "; ".join(str(pt.get("description") or "self-improvement patch") for pt in patches)
        log.debug(f"[SELF-IMPROVE] Patch set applied across {len(files)} file(s): {files} — {what}")
        return {"ok": True, "applied": True, "files": files, "backups": [str(backups[p]) for p in order],
                "reloaded": reloaded, "verification": verification,
                "message": f"Patch applied to {names}: {what} ({verification})"}

    def verify_in_workspace(self, patch: dict, *, tests: Optional[List[str]] = None, reproducer: Optional[List[str]] = None,
                            timeout: float = 240.0) -> dict:
        """Test a candidate patch on a copy of the tree, and compare with the same tests on the untouched tree.

        The running install is never modified. fixed = tests that failed before pass now and nothing new fails;
        regression = something that passed now fails; a timeout, an error or no matching tests is inconclusive.
        """
        root = _patch_root()
        file_str = (patch.get("file") or "").strip()
        p = (root / file_str).resolve() if not Path(file_str).is_absolute() else Path(file_str).resolve()
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            return {"verdict": "rejected", "reason": "outside the source tree"}
        if is_protected_patch_path(p):
            return {"verdict": "rejected", "reason": f"{rel} is protected: a candidate cannot change its own guardrails or evaluator"}
        stem = p.stem
        cmd = reproducer or [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no", "-rf",
                             *(tests or ["tests/", "-k", stem])]
        ws_parent = root / "artifacts" / "self_improve"
        ws_parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        ws = Path(tempfile.mkdtemp(prefix="candidate_", dir=str(ws_parent)))
        t0 = time.time()
        try:
            _make_workspace(root, ws)
            _changed, _why = _replace_exactly_once((ws / rel).read_text(encoding="utf-8"),
                                                   str(patch.get("old") or ""), str(patch.get("new") or ""))
            if _changed is None or not _change_in_workspace(ws / rel, patch.get("old", ""), patch.get("new", "")):
                return {"verdict": "rejected", "reason": _why or "the text to replace is not in the file"}
            try:
                ast.parse((ws / rel).read_text(encoding="utf-8"))
            except SyntaxError as exc:
                return {"verdict": "rejected", "reason": f"the candidate does not parse: {exc}"}
            baseline = _run_cmd(cmd, root, timeout)
            candidate = _run_cmd(cmd, ws, timeout)
        finally:
            shutil.rmtree(ws, ignore_errors=True)
        return {"verdict": compare_runs(baseline, candidate), "baseline": baseline, "candidate": candidate,
                "cost_s": round(time.time() - t0, 1)}

    def propose_candidate(self, patch: dict, *, hypothesis: str = "", tests: Optional[List[str]] = None,
                          reproducer: Optional[List[str]] = None, parent_id: Optional[int] = None) -> dict:
        """Evaluate a patch in a workspace and keep the result, whether or not it is adopted."""
        result = self.verify_in_workspace(patch, tests=tests, reproducer=reproducer)
        result["candidate_id"] = self.record_candidate(patch, hypothesis=hypothesis, parent_id=parent_id)
        status = self._settle_candidate_check(result["candidate_id"], result)
        result["status"] = status
        self._assess_proposal(patch, hypothesis, result, status)
        return result

    def record_candidate(self, patch: dict, *, hypothesis: str = "", parent_id: Optional[int] = None) -> int:
        """Keep a candidate patch, unchecked, in the archive (status 'proposed', no verdict yet).

        The old and new text are kept whole: adoption applies exactly what is stored, and a cut-off
        copy of a whole-file candidate would replace the start of the file with the start of the fix.
        """
        conn = self.memory._get_connection()
        try:
            _ensure_failure_tables(conn)
            cur = conn.execute(
                "INSERT INTO code_patches (file_path, description, old_code, new_code, status, timestamp, failure_ref, "
                "hypothesis, parent_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (str(patch.get("file") or ""), str(patch.get("description") or "")[:300], str(patch.get("old", "")),
                 str(patch.get("new", "")), "proposed", time.time(), str(patch.get("failure_ref") or ""),
                 hypothesis[:400], parent_id))
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _settle_candidate_check(self, candidate_id: int, result: dict) -> str:
        """Store what the workspace check said of a candidate; returns its status."""
        verdict = str(result.get("verdict") or "")
        status = "verified" if verdict == "fixed" else "rejected" if verdict in ("rejected", "regression") else "proposed"
        conn = self.memory._get_connection()
        try:
            conn.execute(
                "UPDATE code_patches SET status = ?, verdict = ?, results = ?, cost_s = ? WHERE id = ?",
                (status, verdict, json.dumps({k: result.get(k) for k in ("baseline", "candidate", "reason")}, default=str)[:6000],
                 float(result.get("cost_s") or 0.0), int(candidate_id)))
            conn.commit()
        finally:
            conn.close()
        return status

    def approve_candidate(self, candidate_id: int) -> dict:
        """The owner approved this candidate: check it on a copy of the tree if that has not been
        done, and apply it through the gated applier only when the check shows it fixes what failed
        and breaks nothing. Anything else leaves the source as it is and says why."""
        conn = self.memory._get_connection()
        try:
            _ensure_failure_tables(conn)
            row = conn.execute("SELECT file_path, description, old_code, new_code, status, verdict, hypothesis, failure_ref "
                               "FROM code_patches WHERE id = ?", (int(candidate_id),)).fetchone()
        finally:
            conn.close()
        if not row:
            return {"ok": False, "applied": False, "message": f"there is no candidate {candidate_id}"}
        if row[4] in ("adopted", "probationary"):
            return {"ok": False, "applied": False, "message": f"candidate {candidate_id} is already applied ({row[4]})"}
        if row[5] is None:
            patch = {"file": row[0], "description": row[1], "old": row[2], "new": row[3], "failure_ref": row[7]}
            result = self.verify_in_workspace(patch)
            status = self._settle_candidate_check(candidate_id, result)
            self._assess_proposal(patch, str(row[6] or ""), {**result, "candidate_id": int(candidate_id)}, status)
            if result.get("verdict") != "fixed":
                return {"ok": False, "applied": False, "candidate_id": int(candidate_id), "verdict": result.get("verdict"),
                        "message": f"Candidate {candidate_id} not applied: {result.get('verdict')}"
                                   + (f" ({result.get('reason')})" if result.get("reason") else "")}
        return self.adopt_candidate(candidate_id)

    def _assess_proposal(self, patch: dict, hypothesis: str, result: dict, status: str) -> None:
        # The proposal as a figure (eli/cognition/epistemics, kind proposal: the chance a candidate with
        # this verdict is adopted and holds, fitted on what became of earlier ones) and a thought in
        # the Upgrade Bay; a rejected or regressing candidate is settled wrong at once, the rest when
        # it is adopted, rolled back or promoted.
        try:
            from eli.cognition import epistemics as _ep
            from eli.cognition.thoughts import record as _thought
            verdict = str(result.get("verdict") or "")
            recurrence = 0
            try:
                recurrence = int(str(patch.get("failure_ref") or "").count("×"))
            except (TypeError, ValueError):
                recurrence = 0
            prob = _ep.assess(_ep.PROPOSAL, 0.5, request_id=f"proposal:{result['candidate_id']}", class_=verdict,
                              features={"recurrence": recurrence})
            _thought("proposal", f"a fix for {str(patch.get('file') or '?')}: {str(patch.get('description') or hypothesis or '')[:120]}",
                     why=[f"tried on a copy of the tree: {verdict}", f"hypothesis: {hypothesis[:120]}" if hypothesis else "no hypothesis given"],
                     chance=float(prob.p), decision="proposed" if status != "rejected" else "dropped",
                     reason={"fixed": "it fixed what failed and broke nothing", "regression": "it broke something",
                             "rejected": str(result.get("reason") or "rejected"), "unchanged": "it changed nothing",
                             "inconclusive": "its tests could not run"}.get(verdict, verdict),
                     subject=str(patch.get("file") or ""), ref=f"proposal:{result['candidate_id']}")
            if status == "rejected":
                _ep.settle(f"proposal:{result['candidate_id']}", False, how=verdict, kind=_ep.PROPOSAL)
                from eli.cognition.thoughts import settle as _settle_thought
                _settle_thought(f"proposal:{result['candidate_id']}", "rejected", how=verdict)
        except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
            log.debug("proposal not assessed", exc_info=True)

    def waiting_candidates(self, limit: Optional[int] = None) -> List[dict]:
        """The fix candidates waiting for the owner's yes or no (proposed, or verified on a copy of
        the tree, and not applied), newest first: what each changes (its file, description, the old
        and the new text) and what the check said of it (verdict, None when not checked yet)."""
        conn = self.memory._get_connection()
        try:
            _ensure_failure_tables(conn)
            sql = ("SELECT id, file_path, description, old_code, new_code, status, verdict, timestamp FROM code_patches "
                   "WHERE status IN ('proposed', 'verified') ORDER BY id DESC")
            rows = conn.execute(sql + (" LIMIT ?" if limit else ""), (int(limit),) if limit else ()).fetchall()
        finally:
            conn.close()
        names = ("id", "file", "description", "old", "new", "status", "verdict", "timestamp")
        return [dict(zip(names, r)) for r in rows]

    def decline_candidate(self, candidate_id: int) -> dict:
        """The owner said no to this candidate: it stays in the archive, marked declined, and is never
        applied."""
        conn = self.memory._get_connection()
        try:
            _ensure_failure_tables(conn)
            row = conn.execute("SELECT status FROM code_patches WHERE id = ?", (int(candidate_id),)).fetchone()
            if not row:
                return {"ok": False, "declined": False, "message": f"there is no candidate {candidate_id}"}
            if row[0] in ("adopted", "probationary"):
                return {"ok": False, "declined": False,
                        "message": f"candidate {candidate_id} is already applied ({row[0]}); undo it with a revert"}
            conn.execute("UPDATE code_patches SET status = 'declined' WHERE id = ?", (int(candidate_id),))
            conn.commit()
        finally:
            conn.close()
        self._settle_proposal(candidate_id, "declined")
        return {"ok": True, "declined": True, "candidate_id": int(candidate_id),
                "message": f"Candidate {candidate_id} declined: it is kept in the archive and not applied."}

    def adopt_candidate(self, candidate_id: int) -> dict:
        """Apply a verified candidate to the real tree. Nothing else is adopted; a failed adoption is rolled back and recorded."""
        conn = self.memory._get_connection()
        try:
            _ensure_failure_tables(conn)
            row = conn.execute("SELECT file_path, description, old_code, new_code, status, verdict FROM code_patches WHERE id = ?",
                               (int(candidate_id),)).fetchone()
        finally:
            conn.close()
        if not row or row[4] != "verified" or row[5] != "fixed":
            return {"ok": False, "applied": False, "message": "only a candidate that fixed its reproducer with no regression can be adopted"}
        watched = _actions_for_patch_file(row[0])
        baseline: Dict[str, float] = {}
        if watched:
            from eli.runtime.evidence_ledger import action_reliability
            baseline = {a: action_reliability(a)["p"] for a in watched}
        res = self.apply_code_patch({"file": row[0], "old": row[2], "new": row[3], "description": row[1]}, verify=True)
        if res.get("applied") and watched:
            status = "probationary"
        else:
            status = "adopted" if res.get("applied") else "rolled_back"
        self._settle_proposal(candidate_id, status)
        conn = self.memory._get_connection()
        try:
            conn.execute(
                "UPDATE code_patches SET status = ?, probation_actions = ?, probation_baseline = ?, "
                "probation_started = ? WHERE id = ?",
                (status, json.dumps(watched) if watched else None, json.dumps(baseline) if watched else None,
                 time.time() if status == "probationary" else None, int(candidate_id)))
            conn.commit()
        finally:
            conn.close()
        if res.get("applied"):
            # A verified fix becomes a lesson for that file: the same failure pattern next
            # time should try the same change first, not be re-diagnosed from scratch.
            try:
                from eli.runtime import lessons as _lessons
                _lessons.propose(
                    applies_to=str(row[0]), trigger=f"recurrence of: {row[1][:200]}",
                    evidence=[f"candidate {candidate_id} verified fixed, no regression"],
                    proposed_change=str(row[1])[:400], hypothesis="same root cause as the verified candidate",
                    predicted_outcome="fixed", category="self_repair",
                )
            except Exception:
                log.debug("self_improvement: lesson recording failed", exc_info=True)
        return res

    def apply_autonomously(self, patch: dict, failure: Optional[dict] = None) -> dict:
        """The self-repair path: a patch goes live only when a copy of the tree shows it fixes something.

        The failure's capsule supplies the reproducer when the action is safe to replay; otherwise the module's own
        tests are the evidence. Anything short of a demonstrated fix stays in the archive, unapplied.
        """
        if os.environ.get("ELI_SELFPATCH_UNPROVEN", "0").strip().lower() in ("1", "true", "yes", "on"):
            return self.apply_code_patch(patch)
        capsule = ((failure or {}).get("context") or {}).get("capsule") if isinstance((failure or {}).get("context"), dict) else None
        reproducer = capsule_reproducer(capsule) if isinstance(capsule, dict) else None
        cand = self.propose_candidate(patch, hypothesis=str(patch.get("description") or ""), reproducer=reproducer)
        if cand.get("verdict") == "fixed":
            return self.adopt_candidate(cand["candidate_id"])
        return {"ok": False, "applied": False, "candidate_id": cand.get("candidate_id"), "verdict": cand.get("verdict"),
                "message": f"Candidate not applied: {cand.get('verdict')}"
                           + (f" ({cand.get('reason')})" if cand.get("reason") else " (no evidence it fixes anything)")}

    def candidate_archive(self, limit: int = 20) -> List[dict]:
        """Every candidate evaluated, with its verdict and cost, newest first."""
        conn = self.memory._get_connection()
        try:
            _ensure_failure_tables(conn)
            rows = conn.execute("SELECT id, file_path, description, status, verdict, hypothesis, cost_s, parent_id, timestamp FROM code_patches "
                                "WHERE verdict IS NOT NULL ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        finally:
            conn.close()
        names = ("id", "file", "description", "status", "verdict", "hypothesis", "cost_s", "parent_id", "timestamp")
        return [dict(zip(names, r)) for r in rows]

    def revert_patch(self, file_path: str) -> dict:
        """Restore the most recent .eli_bak backup for a file."""
        p = Path(file_path)
        if not p.is_absolute():
            p = _patch_root() / p
        p = p.resolve()
        backup = p.with_suffix(".py.eli_bak")
        if not backup.exists():
            return {"ok": False, "message": f"No backup found for {p.name}"}
        try:
            shutil.copy2(str(backup), str(p))
            return {"ok": True, "message": f"Reverted {p.name} from backup"}
        except Exception as exc:
            return {"ok": False, "message": f"Revert failed: {exc}"}

    def _try_deterministic_patch(self, failure: dict) -> dict:
        """Apply a known rule-based fix when the failure pattern is unambiguous."""
        try:
            from eli.runtime.deterministic_failure_patches import propose_deterministic_patch
            patch = propose_deterministic_patch(failure)
            return patch if patch else {"ok": False}
        except Exception as exc:
            log.debug("[SELF-IMPROVE] deterministic patch lookup failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def _test_verify_patch(self, patch: dict) -> dict:
        """Run the affected file's tests after a patch — the TEST-verify gate that
        import-verify alone can't give. Returns {verified: True|False|None, target}.
        None = no test file to run (can't verify → kept but marked unverified)."""
        rel = str(patch.get("file") or "").strip()
        if not rel:
            return {"verified": None, "target": None}
        try:
            from eli.runtime import autopilot_debugger as _dbg
            root = _dbg._repo_root()
            tf = _dbg._test_for(rel)
            if not tf or not (root / tf).is_file():
                return {"verified": None, "target": None}
            res = _dbg._run_validation(root, [tf])
            if not res.get("ran"):
                return {"verified": None, "target": tf}
            return {"verified": not res["failed"], "target": tf}
        except Exception:
            log.debug("self-improve: test-verify failed", exc_info=True)
            return {"verified": None, "target": None}

    def _resolve_probation(self, candidate_id: int, status: str) -> None:
        conn = self.memory._get_connection()
        try:
            conn.execute("UPDATE code_patches SET status = ? WHERE id = ?", (status, int(candidate_id)))
            conn.commit()
        finally:
            conn.close()
        self._settle_proposal(candidate_id, status)

    @staticmethod
    def _settle_proposal(candidate_id: int, status: str) -> None:
        """What became of a proposal: adopted (and held through probation) is right, rolled back or
        declined by the owner is wrong."""
        if status not in ("adopted", "rolled_back", "declined"):
            return
        try:
            from eli.cognition import epistemics as _ep, thoughts as _th
            right = status == "adopted"
            _ep.settle(f"proposal:{int(candidate_id)}", right, how=status, kind=_ep.PROPOSAL)
            _th.settle(f"proposal:{int(candidate_id)}", "right" if right else "wrong", how=status)
        except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError):
            log.debug("proposal not settled", exc_info=True)

    def _check_probationary_patches(self) -> None:
        """Re-check every probationary patch's watched actions against its pre-adoption
        baseline. A real regression auto-reverts via the existing revert_patch(); enough
        clean invocations promotes it to permanent 'adopted'; too little real traffic to
        ever judge it flips it to 'adopted' anyway once the time ceiling is hit — it
        can't sit in limbo forever on a single-user desktop app with sparse usage.
        """
        conn = self.memory._get_connection()
        try:
            _ensure_failure_tables(conn)
            rows = conn.execute(
                "SELECT id, file_path, probation_actions, probation_baseline, probation_started "
                "FROM code_patches WHERE status = 'probationary'").fetchall()
        finally:
            conn.close()
        if not rows:
            return
        from eli.runtime.evidence_ledger import action_reliability
        now = time.time()
        for pid, file_path, actions_json, baseline_json, started in rows:
            try:
                watched = json.loads(actions_json or "[]")
                baseline = json.loads(baseline_json or "{}")
            except Exception:
                watched, baseline = [], {}
            if not watched:
                self._resolve_probation(pid, "adopted")
                continue
            regressed = False
            all_clean = True
            for a in watched:
                r = action_reliability(a, since=float(started) if started else None)
                if r["n"] < _PROBATION_MIN_INVOCATIONS:
                    all_clean = False
                    continue
                base_p = baseline.get(a)
                if base_p is not None and r["p"] < base_p - _PROBATION_REGRESSION_DROP:
                    regressed = True
            if regressed:
                self.revert_patch(file_path)
                self._resolve_probation(pid, "rolled_back")
            elif all_clean:
                self._resolve_probation(pid, "adopted")
            elif started and now - float(started) > _PROBATION_MAX_DAYS * 86400.0:
                self._resolve_probation(pid, "adopted")

    def run_patch_cycle(
        self,
        max_patches: int = 3,
        dry_run: bool = False,
        verify_tests: bool = True,
        *,
        days: int = DEFAULT_ANALYSIS_DAYS,
        min_cluster_size: int = PATCH_MIN_CLUSTER,
    ) -> dict:
        """
        Full automated patch cycle (a governed closed loop):
        1. Analyze recurring failures (default at least 2 occurrences)
        2. For each, try deterministic rules, then an LLM code fix
        3. Validate syntax + apply the fix (import-verify + auto-revert)
        4. TEST-verify: run the affected file's tests; revert any patch that goes red
        5. Report results

        `verify_tests=False` restores the old import-only behaviour. Returns a detailed
        dict with per-patch outcomes.
        """
        failures = self.analyze_failures(limit=20, days=int(days), min_cluster_size=int(min_cluster_size))

        results: Dict[str, Any] = {
            "failures_analyzed": len(failures),
            "patches_generated": 0,
            "patches_applied": 0,
            "patches_skipped": 0,
            "patches_failed": 0,
            "dry_run": dry_run,
            "details": [],
        }
        try:
            from eli.core.paths import patch_capability
            results["patch_capability"] = patch_capability()
        except Exception:
            log.debug("patch_capability unavailable", exc_info=True)

        if not failures:
            _need = "≥1 occurrence" if int(min_cluster_size) <= 1 else f"≥{int(min_cluster_size)} occurrences"
            results["summary"] = f"No failures in the analysis window ({_need}). System appears stable."
            return results

        if (results.get("patch_capability") or {}).get("install_kind") in {"frozen", "packaged"}:
            results["packaged_install"] = True

        for failure in failures[:max_patches]:
            err_preview = (failure.get("error") or failure.get("user_input") or "?")[:80]
            count = failure.get("occurrence_count", 1)

            try:
                from eli.runtime.failure_taxonomy import classify, is_actionable
                tags = classify(
                    _safe_str(failure.get("error")),
                    _safe_str(failure.get("command")),
                    _safe_str(failure.get("user_input")),
                )
                if tags and not is_actionable(tags.get("category", "")):
                    results["patches_skipped"] += 1
                    _cat = tags.get("category", "network/resource")
                    results["details"].append({
                        "failure": err_preview,
                        "count": count,
                        "status": "skipped_non_actionable",
                        "reason": "user_input_validation" if _cat == "user_input" else _cat,
                    })
                    continue
            except Exception:
                log.debug("actionable filter skipped", exc_info=True)

            try:
                patch = self._try_deterministic_patch(failure)
                if patch.get("already_applied"):
                    results["patches_skipped"] += 1
                    results["details"].append({
                        "failure": err_preview,
                        "count": count,
                        "status": "already_fixed_in_codebase",
                        "reason": patch.get("description") or "fix already present",
                    })
                    continue
                if not patch or not patch.get("ok"):
                    patch = self.generate_code_patch(failure)
                if not patch.get("ok"):
                    reason = patch.get("error") or patch.get("reason") or "unknown"
                    results["patches_skipped"] += 1
                    detail = {
                        "failure": err_preview,
                        "count": count,
                        "status": "patch_generation_failed",
                        "reason": reason,
                    }
                    if reason == "no_groundable_file":
                        detail["hint"] = (
                            "No Python traceback in this failure: it is a parameter or routing bug, not a crash. "
                            "Deterministic patches are tried first; if none matched, say which failure to fix "
                            "or run EXAMINE_CODE."
                        )
                    results["details"].append(detail)
                    log.debug(f"[SELF-IMPROVE] Skipped (generation failed): {err_preview[:60]} — {reason}")
                    continue

                results["patches_generated"] += 1

                if dry_run:
                    # kept whole as a candidate, so the owner's yes applies exactly what they were shown
                    # (approve_candidate), never a second generation of it
                    _cid = self.record_candidate(patch, hypothesis=str(patch.get("description") or ""))
                    results["details"].append({
                        "failure": err_preview,
                        "count": count,
                        "status": "dry_run",
                        "candidate_id": _cid,
                        "patch": {
                            "file": patch.get("file"),
                            "description": patch.get("description"),
                            "old_preview": (patch.get("old") or "")[:80],
                            "new_preview": (patch.get("new") or "")[:80],
                        },
                    })
                    continue

                apply_result = self.apply_autonomously(patch, failure)
                if apply_result.get("applied"):
                    # Frontier #4: TEST-verify, not just import-verify. A patch can import
                    # cleanly yet break behaviour — run the affected file's tests and revert
                    # any patch that doesn't stay green. This closes the self-improve loop.
                    _tv = self._test_verify_patch(patch) if verify_tests else {"verified": None}
                    if _tv.get("verified") is False:
                        self.revert_patch(patch.get("file"))
                        results["patches_reverted"] = results.get("patches_reverted", 0) + 1
                        results["details"].append({
                            "failure": err_preview,
                            "count": count,
                            "status": "reverted_tests_red",
                            "file": patch.get("file"),
                            "tests": _tv.get("target"),
                        })
                        log.debug(f"[SELF-IMPROVE] Reverted (tests red): {patch.get('file')} — {_tv.get('target')}")
                        continue
                    results["patches_applied"] += 1
                    results["details"].append({
                        "failure": err_preview,
                        "count": count,
                        "status": "applied_test_verified" if _tv.get("verified") else "applied",
                        "file": apply_result.get("file"),
                        "message": apply_result.get("message"),
                        "tests_verified": _tv.get("verified"),
                    })
                else:
                    results["patches_failed"] += 1
                    results["details"].append({
                        "failure": err_preview,
                        "count": count,
                        "status": "apply_failed",
                        "reason": apply_result.get("message", "unknown"),
                    })
                    log.debug(f"[SELF-IMPROVE] Apply failed: {apply_result.get('message')}")

            except Exception as exc:
                results["patches_failed"] += 1
                results["details"].append({
                    "failure": err_preview,
                    "count": count,
                    "status": "exception",
                    "reason": str(exc)[:120],
                })
                log.debug(f"[SELF-IMPROVE] Exception during patch cycle: {exc}")

        applied = results["patches_applied"]
        total = results["patches_generated"]
        reverted = results.get("patches_reverted", 0)
        results["summary"] = (
            (f"Patch cycle complete: {total} fix(es) proposed, none applied "
             if dry_run else f"Patch cycle complete: {applied}/{total} patches applied ")
            + f"({results['patches_failed']} failed, {results['patches_skipped']} skipped"
            + (f", {reverted} reverted after tests went red" if reverted else "")
            + f"). Analyzed {len(failures)} recurring failures."
        )
        if results.get("packaged_install") and applied == 0 and not dry_run:
            _hint = (results.get("patch_capability") or {}).get("hint") or ""
            results["summary"] += " Packaged install: patches write to the user eli/ tree when the runtime overlay is active."
            if _hint:
                results["summary"] += f" {_hint}"
        log.debug(f"[SELF-IMPROVE] {results['summary']}")
        return results

    def list_applied_patches(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return history of applied code patches from the agent DB."""
        conn = self.memory._get_connection()
        try:
            _ensure_failure_tables(conn)
            rows = conn.execute(
                "SELECT id, file_path, description, status, timestamp FROM code_patches "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [
                {"id": r[0], "file": r[1], "description": r[2],
                 "status": r[3], "timestamp": r[4]}
                for r in rows
            ]
        except Exception:
            return []
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Plugin stub generators
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_plugin_name(self, text):
        return self.generate_plugin_name(text)

    def _generate_plugin_stub(self, name, description="", examples=None):
        return self.generate_plugin_stub(name, description=description, examples=examples)

    def generate_plugin_name(self, idea: str) -> str:
        base = "".join(ch.lower() if ch.isalnum() else "_" for ch in (idea or "plugin").strip())
        base = "_".join([p for p in base.split("_") if p])
        if not base:
            base = "plugin"
        if base[0].isdigit():
            base = "p_" + base
        return base

    def generate_plugin_stub(self, name: str, description: str = "", examples: list = None) -> str:
        mod = self.generate_plugin_name(name)
        desc = description or "Auto-generated plugin"
        examples_str = f"    examples = {examples!r}\n" if examples else ""
        return (
            f"# Auto-generated plugin: {mod}\n"
            "from eli.plugins.base import BasePlugin\n"
            "from eli.memory import get_memory\n\n"
            f"class Plugin(BasePlugin):\n"
            f"    name = '{mod}'\n"
            f"    description = '{desc}'\n"
            f"{examples_str}"
            "\n"
            "    def run(self, args: dict) -> dict:\n"
            "        try:\n"
            f"            # Plugin logic for: {desc}\n"
            "            query = args.get('query', args.get('text', ''))\n"
            "            mem = get_memory()\n"
            "            results = mem.recall_memory(query, limit=5) if query else []\n"
            "            context = '; '.join(r.get('text', '') for r in results[:3])\n"
            "            return {\n"
            "                'ok': True,\n"
            "                'content': f'[{mod}] Processed: {query}. Context: {context or \"none\"}',\n"
            "                'response': f'[{mod}] Done.',\n"
            "                'results': results,\n"
            "            }\n"
            "        except Exception as e:\n"
            "            return {'ok': False, 'error': str(e), 'content': f'Plugin error: {e}'}\n"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Background loop
    # ─────────────────────────────────────────────────────────────────────────

    def start_self_improvement_loop(self, interval_hours: int = 24):
        def loop():
            # Mark this daemon thread's model calls BACKGROUND so they yield to a
            # foreground turn (cooperative abort) and are token-capped — ambient
            # self-improvement must never hold the model lock against the user.
            try:
                from eli.cognition.gguf_inference import set_background_inference as _set_bg
                _set_bg(True)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            while True:
                try:
                    # The one legitimate autonomous repair path: already marked
                    # background (preemptible + token-capped) and now bounded by a
                    # shared wall-clock budget inside propose_via_agent.
                    self.analyze_and_improve(repair=True)
                except Exception as _exc:
                    log.warning("[SELF_IMPROVEMENT] analyze_and_improve error: %s", _exc)
                time.sleep(interval_hours * 3600)

        threading.Thread(target=loop, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singletons
# ─────────────────────────────────────────────────────────────────────────────

# Self-heal notices (recurring error -> proactive conversation surface). When an error recurs
# >=5x (flag) or >=10x (act + report), a user-facing notice is queued here. The engine pops the
# most pressing one at the start of a conversational turn and mentions it, so ELI raises
# recurring problems itself and reports what it tried instead of failing silently.
def _self_heal_notices_path() -> Path:
    # A write path. PROJECT_ROOT is the install tree, read-only in a packaged build, so the notice
    # queue could never persist there and ELI silently failed to raise recurring problems, the point
    # of this file. The artifacts dir is user-writable.
    try:
        from eli.core.paths import data_dir as _dd
        return Path(_dd()) / "runtime" / "self_heal_notices.json"
    except Exception:
        return PROJECT_ROOT / "artifacts" / "runtime" / "self_heal_notices.json"


def _read_notices() -> List[Dict[str, Any]]:
    try:
        p = _self_heal_notices_path()
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, list) else []
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return []


def _push_self_heal_notice(notice: Dict[str, Any]) -> None:
    """Queue a user-facing self-heal notice (deduped by error_type+stage). Never raises."""
    try:
        p = _self_heal_notices_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = _read_notices()
        key = (notice.get("error_type"), notice.get("stage"))
        data = [n for n in data if (n.get("error_type"), n.get("stage")) != key]
        notice["ts"] = time.time()
        data.append(notice)
        p.write_text(json.dumps(data[-12:], indent=2), encoding="utf-8")
    except Exception:
        log.debug("suppressed exception", exc_info=True)


def consume_self_heal_notice() -> Optional[Dict[str, Any]]:
    """Pop the most pressing un-surfaced notice (highest count, then most recent). The
    engine calls this once per conversational turn. Never raises."""
    try:
        data = _read_notices()
        if not data:
            return None
        data.sort(key=lambda n: (int(n.get("count", 0)), float(n.get("ts", 0))), reverse=True)
        top, rest = data[0], data[1:]
        _self_heal_notices_path().write_text(json.dumps(rest, indent=2), encoding="utf-8")
        return top
    except Exception:
        return None


_self_engine: Optional[SelfImprovementEngine] = None


def get_self_improvement() -> SelfImprovementEngine:
    global _self_engine
    if _self_engine is None:
        _self_engine = SelfImprovementEngine(memory=get_agent_memory())
    return _self_engine


def run_improvement_cycle() -> Dict[str, Any]:
    return get_self_improvement().analyze_and_improve()


def candidate_lines(candidates: List[dict]) -> List[str]:
    """Each candidate as the owner reads it before saying yes or no: its number, file and what it is
    for, what the check on a copy of the tree said, and the change itself (the lines it removes with
    -, the ones it adds with +; for a whole-file candidate only the lines that differ). The one
    wording of a candidate, in chat and in the Self-Improve panel."""
    import difflib
    out: List[str] = []
    for c in candidates or []:
        verdict = c.get("verdict")
        checked = (f"checked on a copy of the tree: {verdict}" if verdict
                   else "not checked yet: approving it checks it on a copy of the tree first")
        out.append(f"#{c.get('id')} {c.get('file')}: {c.get('description') or '(no description)'} ({checked})")
        diff = difflib.unified_diff(str(c.get("old") or "").splitlines(), str(c.get("new") or "").splitlines(),
                                    lineterm="", n=0)
        out.extend(f"    {line}" for line in diff if not line.startswith(("---", "+++", "@@")))
    return out


def run_patch_cycle(max_patches: int = 3, dry_run: bool = False) -> Dict[str, Any]:
    return get_self_improvement().run_patch_cycle(max_patches=max_patches, dry_run=dry_run)
