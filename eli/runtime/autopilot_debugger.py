"""Autopilot debugger — one loop that turns a failure into a plan.

Assembles ELI's existing diagnostic parts (it does NOT reinvent them):
  • traceback / pytest-output parsing        → the affected files + the exception
  • git history (last commit per file)        → the suspect change + a rollback
  • ``code_examiner`` (tier1/2 static scan)   → concrete defects to patch
  • cheap config-consistency checks           → version / manifest drift

and produces a single structured verdict: **root cause, affected files, rollback
plan, patch plan, validation commands.** Everything is derived from the real repo
and the real error text — nothing here fabricates a diagnosis. Wired as the
``AUTOPILOT_DEBUG`` action; safe to call with just an error string.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_TB_FRAME = re.compile(r'File "([^"]+)", line (\d+)')
_EXC_LINE = re.compile(r'^([A-Za-z_][\w.]*(?:Error|Exception|Warning)): (.+)$', re.M)
_PYTEST_FAIL = re.compile(r'^(?:FAILED|ERROR)\s+([^\s:]+\.py)(?:::(\S+))?', re.M)
_ASSERT = re.compile(r'^E\s+(assert .+|.*Error.*)$', re.M)


def _repo_root() -> Path:
    try:
        from eli.core.paths import get_paths
        return Path(get_paths().project_root)
    except Exception:
        return Path(__file__).resolve().parents[2]


def _git(root: Path, *args: str, timeout: int = 15) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, text=True, timeout=timeout)
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        log.debug("autopilot: git %s failed", args, exc_info=True)
        return ""


def _rel(root: Path, p: str) -> Optional[str]:
    """A project-relative path for a frame, or None if it's outside the repo (stdlib/venv)."""
    try:
        pp = Path(p)
        if not pp.is_absolute():
            pp = (root / pp)
        rel = pp.resolve().relative_to(root.resolve())
        s = str(rel)
        if s.startswith((".venv", "venv")) or "site-packages" in s:
            return None
        return s
    except Exception:
        return None


def _parse_error(root: Path, text: str) -> Tuple[List[Tuple[str, int]], str]:
    """From a traceback or pytest output → in-repo (file, line) frames + the exception line."""
    frames: List[Tuple[str, int]] = []
    for m in _TB_FRAME.finditer(text or ""):
        rel = _rel(root, m.group(1))
        if rel:
            frames.append((rel, int(m.group(2))))
    seen: set = set()
    frames = [f for f in frames if not (f in seen or seen.add(f))]
    exc_ms = _EXC_LINE.findall(text or "")
    exc = f"{exc_ms[-1][0]}: {exc_ms[-1][1].strip()}" if exc_ms else ""
    if not exc:
        am = _ASSERT.findall(text or "")
        exc = am[-1].strip() if am else ""
    return frames, exc


def _parse_pytest(text: str) -> List[str]:
    """Failing test node-ids / files from pytest output."""
    out: List[str] = []
    for m in _PYTEST_FAIL.finditer(text or ""):
        node = m.group(1) + (f"::{m.group(2)}" if m.group(2) else "")
        if node not in out:
            out.append(node)
    return out


def _last_commit_for(root: Path, rel: str) -> Optional[Dict[str, str]]:
    line = _git(root, "log", "-1", "--format=%h\t%s\t%an\t%ad", "--date=short", "--", rel).strip()
    if not line:
        return None
    parts = line.split("\t")
    if len(parts) < 2:
        return None
    return {"sha": parts[0], "subject": parts[1],
            "author": parts[2] if len(parts) > 2 else "", "date": parts[3] if len(parts) > 3 else ""}


def _recently_changed(root: Path, rels: List[str]) -> Dict[str, Dict[str, str]]:
    """Map each affected file → the commit that last touched it (the prime suspect)."""
    out: Dict[str, Dict[str, str]] = {}
    for rel in rels:
        c = _last_commit_for(root, rel)
        if c:
            out[rel] = c
    return out


def _static_findings(root: Path, rels: List[str]) -> List[Dict[str, Any]]:
    """Tier1/2 (syntax/import/lint) scan of the affected files — fast, deterministic, no LLM."""
    try:
        from eli.runtime import code_examiner as ce
        paths = [root / r for r in rels if (root / r).is_file() and r.endswith(".py")]
        if not paths:
            return []
        findings = ce.examine(paths, run_tier3=False)
        return [f.to_dict() for f in findings]
    except Exception:
        log.debug("autopilot: static scan failed", exc_info=True)
        return []


def _config_checks(root: Path) -> List[str]:
    """Cheap consistency checks that commonly cause 'works-on-my-machine' failures."""
    issues: List[str] = []
    try:
        pyproj = (root / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproj, re.M)
        ver = m.group(1) if m else None
        su = (root / "eli/kernel/self_upgrade.py")
        if ver and su.is_file():
            sm = re.search(r'v?(\d+\.\d+\.\d+)', su.read_text(encoding="utf-8"))
            if sm and sm.group(1) != ver:
                issues.append(f"version drift: pyproject={ver} but self_upgrade={sm.group(1)}")
    except Exception:
        log.debug("autopilot: version check failed", exc_info=True)
    return issues


# pytest colourises when FORCE_COLOR/PY_COLORS is set, even through capture_output. The escape
# sequences end in a letter that glues to the next text and breaks word-boundary matching
# ('\x1b[31m\x1b[1m1 failed\x1b[0m' doesn't match \b\d+\s+failed\b), so a plainly failing test
# was reported "did not reproduce". Colour is turned off in the child and stripped from what it
# returns, which also keeps reports readable.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", str(text or ""))


def _run_pytest(root: Path, targets: List[str], timeout: int = 300,
                with_status: bool = False):
    """Actually run pytest on the targets and return its output (only when asked).

    `with_status=True` also returns pytest's exit code, which is the one signal that
    survives any output formatting: 0 = all passed, 1 = tests failed, 2-5 = usage,
    collection or internal error. A crash that never prints a "N failed" summary is
    still not a pass, and the text alone cannot tell the difference.
    """
    try:
        py = str(root / ".venv" / "bin" / "python")
        if not Path(py).is_file():
            py = "python"
        cmd = [py, "-m", "pytest", "-x", "--tb=short", "-q", "--color=no",
               "-p", "no:cacheprovider", *targets]
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        env["PY_COLORS"] = "0"
        env.pop("FORCE_COLOR", None)
        out = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                             timeout=timeout, env=env)
        text = _strip_ansi((out.stdout or "") + "\n" + (out.stderr or ""))
        return (text, out.returncode) if with_status else text
    except Exception:
        log.debug("autopilot: pytest run failed", exc_info=True)
        # No run happened, so there is no verdict to report — None, not 0 ("passed").
        return ("", None) if with_status else ""


def _test_for(rel: str) -> Optional[str]:
    """Guess the test file that exercises a source file (tests/test_<stem>.py)."""
    stem = Path(rel).stem
    return f"tests/test_{stem}.py"


def diagnose(error_text: str = "", targets: Optional[List[str]] = None,
             run_tests: bool = False) -> Dict[str, Any]:
    """Turn a failure into a plan. Returns a structured verdict (see module docstring).

    error_text : a traceback and/or pytest output (the usual input)
    targets    : explicit files/test-ids to focus on (optional)
    run_tests  : if True, actually run pytest on the targets and fold its output in
    """
    root = _repo_root()
    targets = list(targets or [])

    if run_tests and targets:
        error_text = (error_text or "") + "\n" + _run_pytest(root, targets)

    frames, exc = _parse_error(root, error_text)
    failing_tests = _parse_pytest(error_text)

    affected: List[str] = []
    for rel, _ln in frames:
        if rel not in affected:
            affected.append(rel)
    for t in failing_tests + targets:
        f = t.split("::")[0]
        if f.endswith(".py") and f not in affected:
            affected.append(f)

    suspects = _recently_changed(root, affected)
    findings = _static_findings(root, affected)
    config_issues = _config_checks(root)
    fail_frame = frames[-1] if frames else None

    # Split static findings into those RELEVANT to this failure (on a traceback frame's
    # file, within ~15 lines of where it raised) vs incidental pre-existing lint.
    def _relevant(f: Dict[str, Any]) -> bool:
        fl = f.get("line")
        for (ff, fline) in frames:
            if f.get("file") == ff and (fl is None or abs(int(fl) - fline) <= 15):
                return True
        return False

    relevant = [f for f in findings if _relevant(f)] if frames else findings
    incidental = [f for f in findings if f not in relevant]

    # ---- Root cause (best grounded hypothesis) ----
    if exc and fail_frame:
        if relevant:
            f0 = relevant[0]
            root_cause = (f"{exc}  →  likely from a {f0['kind']} at {f0['file']}:{f0.get('line')} "
                          f"({f0['message']})")
        else:
            root_cause = f"{exc}  →  raised at {fail_frame[0]}:{fail_frame[1]}"
    elif exc:
        root_cause = exc
    elif relevant:
        f0 = relevant[0]
        root_cause = (f"Static defect ({f0['kind']}) in {f0['file']}"
                      + (f":{f0['line']}" if f0.get("line") else "") + f" — {f0['message']}")
    elif config_issues:
        root_cause = "Configuration mismatch: " + "; ".join(config_issues)
    else:
        root_cause = "No traceback/pytest failure detected in the input — nothing to diagnose."

    # ---- Rollback plan (grounded in git history of the affected files) ----
    rollback: List[str] = []
    for rel, c in suspects.items():
        rollback.append(f"# {rel} last changed by {c['sha']} \"{c['subject']}\" ({c['date']})")
        rollback.append(f"git checkout {c['sha']}~1 -- {rel}   # revert just this file")
    if suspects:
        uniq = {c["sha"]: c["subject"] for c in suspects.values()}
        if len(uniq) == 1:
            sha, subj = next(iter(uniq.items()))
            rollback.append(f"git revert --no-edit {sha}   # revert the whole suspect commit \"{subj}\"")
    if not rollback:
        rollback.append("# no in-repo file history to roll back (error may be in a dependency or runtime state)")

    # ---- Patch plan (relevant defects first; incidental lint summarised, not dumped) ----
    patch: List[str] = []
    for f in relevant[:8]:
        loc = f"{f['file']}:{f['line']}" if f.get("line") else f["file"]
        patch.append(f"[{f['kind']}] {loc} — {f['message']}")
    if fail_frame and not any(fail_frame[0] in p for p in patch):
        patch.append(f"[inspect] {fail_frame[0]}:{fail_frame[1]} — the frame that raised {exc or 'the error'}")
    for issue in config_issues:
        patch.append(f"[config] {issue} — align the version in both spots")
    if incidental:
        by_file: Dict[str, int] = {}
        for f in incidental:
            by_file[f["file"]] = by_file.get(f["file"], 0) + 1
        summ = ", ".join(f"{n} in {fp}" for fp, n in by_file.items())
        patch.append(f"[note] {len(incidental)} unrelated pre-existing lint item(s) ({summ}) — not this failure.")
    if not patch:
        patch.append("No static defect found — reproduce with the validation commands and inspect the failing frame.")

    # ---- Validation commands ----
    validation: List[str] = []
    for t in failing_tests:
        validation.append(f".venv/bin/python -m pytest {t} -q")
    seen_tests = set(failing_tests)
    for rel in affected:
        if rel.startswith("tests/"):
            continue
        tf = _test_for(rel)
        if tf and tf not in seen_tests and (root / tf).is_file():
            validation.append(f".venv/bin/python -m pytest {tf} -q")
            seen_tests.add(tf)
    validation.append(".venv/bin/python -m pytest tests/claims/test_no_silent_swallow.py -q  # swallow ratchet")

    return {
        "ok": True,
        "root_cause": root_cause,
        "exception": exc,
        "affected_files": affected,
        "suspect_commits": suspects,
        "static_findings": findings,
        "config_issues": config_issues,
        "rollback_plan": rollback,
        "patch_plan": patch,
        "validation_commands": validation,
    }


def _pytest_targets(verdict: Dict[str, Any]) -> List[str]:
    """The failing-test node-ids to (re)run for verification — never the ratchet."""
    out: List[str] = []
    for c in verdict.get("validation_commands", []):
        if "no_silent_swallow" in c:
            continue
        m = re.search(r"pytest\s+(\S+)", c)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _run_validation(root: Path, targets: List[str]) -> Dict[str, Any]:
    """Run the validation targets and report whether any FAILED (the bug reproduces)."""
    if not targets:
        return {"ran": False, "failed": False, "output": ""}
    out, status = _run_pytest(root, targets, with_status=True)
    # Exit code first — it cannot be broken by formatting. Text is the fallback
    # for the case where the run itself could not be started (status None).
    failed = bool(re.search(r"\b\d+\s+failed\b", out) or re.search(r"^(?:FAILED|ERROR)\s", out, re.M)
                  or "errors" in out.lower() and "0 errors" not in out.lower())
    if status is not None:
        failed = status != 0
    passed = bool(re.search(r"\b\d+\s+passed\b", out)) and not failed
    return {"ran": True, "failed": failed, "passed": passed,
            "exit_status": status, "output": out[-4000:]}


def resolve(error_text: str = "", targets: Optional[List[str]] = None,
            max_rounds: int = 2, apply: bool = False) -> Dict[str, Any]:
    """Close the loop: diagnose → REPRODUCE (run validation) → (governed) attempt a
    validated fix, re-run, keep-if-green / revert-if-not, iterate.

    apply=False (default): diagnose + reproduce only — a *verified* diagnosis (read-only,
      no source touched). Upgrades "here's what I think" to "I ran it: confirmed failing."
    apply=True (governed): also attempt code_examiner's VALIDATED fixes for the relevant
      findings, applied via SelfImprovementEngine (syntax + import verify + auto-revert),
      re-running the validation each round. Any fix that doesn't turn the tests green is
      reverted. Never commits — the human / approval gate owns that.
    """
    root = _repo_root()
    verdict = diagnose(error_text, targets)
    pt = _pytest_targets(verdict)
    repro = _run_validation(root, pt)
    trace: Dict[str, Any] = {
        "ok": True, "verdict": verdict, "reproduced": repro["failed"],
        "reproduction": repro, "applied": False, "resolved": False, "rounds": [],
    }
    if not apply or not repro["failed"]:
        # verified diagnosis (or nothing to reproduce / nothing to fix)
        return trace

    trace["applied"] = True
    try:
        from eli.runtime import code_examiner as ce
        from eli.runtime.self_improvement import SelfImprovementEngine
        eng = SelfImprovementEngine()
    except Exception as e:
        trace["rounds"].append({"error": f"fix machinery unavailable: {e}"})
        return trace

    findings = [f for f in verdict.get("static_findings", []) if str(f.get("file", "")).endswith(".py")]
    tried: set = set()
    for _rnd in range(max_rounds):
        target = next((f for f in findings if (f["file"], f.get("line")) not in tried), None)
        if not target:
            break
        tried.add((target["file"], target.get("line")))
        patch = ce.generate_fix_patch(target)
        if not patch.get("ok"):
            trace["rounds"].append({"finding": target, "patch_ok": False, "why": patch.get("error")})
            continue
        applied = eng.apply_code_patch(patch)
        if not applied.get("applied"):
            trace["rounds"].append({"finding": target, "applied": False, "why": applied.get("message")})
            continue
        after = _run_validation(root, pt)
        if not after["failed"]:
            trace["rounds"].append({"finding": target, "applied": True, "verified_green": True,
                                    "fix": patch.get("description")})
            trace["resolved"] = True
            return trace
        # applied cleanly but tests still red → revert (never leave a non-green change)
        try:
            eng.revert_patch(patch["file"])
        except Exception:
            log.debug("autopilot: revert failed for %s", patch.get("file"), exc_info=True)
        trace["rounds"].append({"finding": target, "applied": True, "verified_green": False, "reverted": True})
    return trace


def format_resolution(t: Dict[str, Any]) -> str:
    """Human-readable rendering of a resolve() loop."""
    L = [format_report(t["verdict"]), ""]
    r = t.get("reproduction", {})
    if r.get("ran"):
        L.append("Reproduction: " + ("CONFIRMED failing (ran the validation)." if t["reproduced"]
                                     else "did NOT reproduce — the tests pass as-is."))
    if not t.get("applied"):
        L.append("\n(diagnosis + reproduction only — pass apply=True to attempt a verified auto-fix)")
        return "\n".join(L)
    if t.get("resolved"):
        won = next((x for x in t["rounds"] if x.get("verified_green")), {})
        L.append(f"\n✅ RESOLVED: applied a validated fix and the tests went green — {won.get('fix','')}")
    else:
        L.append(f"\n⚠️ Not auto-resolved after {len(t['rounds'])} round(s); every attempted fix was "
                 "reverted (tests stayed red or no validated patch). Diagnosis + plan above stand.")
    return "\n".join(L)


def format_report(d: Dict[str, Any]) -> str:
    """Human-readable rendering of a diagnose() verdict."""
    if not d.get("ok"):
        return f"autopilot debugger error: {d.get('error')}"
    L: List[str] = []
    L.append("🔧 Autopilot debugger")
    L.append(f"\nRoot cause:\n  {d['root_cause']}")
    if d["affected_files"]:
        L.append("\nAffected files:\n  " + "\n  ".join(d["affected_files"]))
    if d["suspect_commits"]:
        L.append("\nSuspect changes:")
        for rel, c in d["suspect_commits"].items():
            L.append(f"  {rel} ← {c['sha']} \"{c['subject']}\" ({c['date']})")
    L.append("\nRollback plan:\n  " + "\n  ".join(d["rollback_plan"]))
    L.append("\nPatch plan:\n  " + "\n  ".join(d["patch_plan"]))
    L.append("\nValidation commands:\n  " + "\n  ".join(d["validation_commands"]))
    return "\n".join(L)
