"""Bounded, contained execution substrate for ELI's coding agent.

The execution-feedback loop is mandatory in the coding agent, and this module is
its foundation: run candidate code (or a test) in a separate process with a
temp working dir, an allowlisted environment, no stdin, a non-interactive
matplotlib backend, a wall-clock timeout and a CPU rlimit. It NEVER runs code
in-process.

Model-written code runs automatically (every candidate, every test harness, the
background self-repair proposer), so it runs contained by default: through
eli.plugins.subprocess_sandbox (bubblewrap on Linux: no network, read-only root,
the home directory, /tmp and the user's session sockets masked, only the
candidate's own temp dir writable). Where no containment is available here the
result says so (`contained=False`) and capabilities() reports it; nothing claims
containment it did not apply.

Every run has one of three outcomes (RunResult.verdict):
- passed: exited 0.
- failed: a genuine crash — an unhandled traceback, a non-zero exit, a crash
  signal (SIGSEGV, SIGABRT, ...), or an import of something the candidate itself
  names that is not available here.
- inconclusive: nothing was learned about the code — it ran past its timeout,
  was killed for its CPU or memory budget (SIGXCPU/SIGKILL), a library it uses is
  missing one of ITS dependencies, no runner/interpreter/sandbox could start it,
  or execution is switched off (ELI_CODING_SANDBOX=0). Inconclusive is never
  "clean" and earns no execution credit.
"""

from __future__ import annotations

import ast
import os
import re
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

_TRACEBACK_MARKER = "Traceback (most recent call last)"

# A frozen ELI is its own interpreter: packaging/pyinstaller/eli_entry.py runs a python command line
# it is handed (-c, -m, a script) as python. Through -c, the runner runs the script as __main__ with
# its directory first on sys.path, as `python script.py` does.
_FROZEN_RUNNER = ("import os, runpy, sys\n"
                  "p = sys.argv[1]\n"
                  "sys.argv = sys.argv[1:]\n"
                  "sys.path.insert(0, os.path.dirname(p))\n"
                  "runpy.run_path(p, run_name='__main__')\n")


def _python_argv(path: str) -> List[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "-c", _FROZEN_RUNNER, path]
    return [sys.executable, path]


# language -> (file extension, argv builder given the script path)
_RUNNERS: Dict[str, tuple] = {
    "python": (".py", _python_argv),
    "bash": (".sh", lambda p: ["bash", p]),
    "javascript": (".js", lambda p: ["node", p]),
    "typescript": (".ts", lambda p: ["ts-node", p]),
    "ruby": (".rb", lambda p: ["ruby", p]),
    "go": (".go", lambda p: ["go", "run", p]),
    "lua": (".lua", lambda p: ["lua", p]),
}

# Signals that mean the run was stopped for its budget, not that the code crashed: the CPU rlimit
# (SIGXCPU) and the kernel's out-of-memory / hard-limit kill (SIGKILL).
_BUDGET_SIGNALS = frozenset(int(getattr(signal, n)) for n in ("SIGXCPU", "SIGKILL") if hasattr(signal, n))

# What a candidate's process sees of ELI's environment: enough to start an interpreter and read
# text in the user's locale. Nothing that reaches a session, an agent or a credential.
_ENV_KEEP = ("PATH", "LANG", "LANGUAGE", "TZ", "TERM", "PYTHONPATH",
             "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")     # Windows cannot start Python without these
_ENV_KEEP_PREFIX = ("LC_",)
# A frozen interpreter loads its own libraries through these.
_FROZEN_ENV_KEEP = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH")

_MISSING_MODULE_RE = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")
_IMPORT_NAME_RE = re.compile(r"ImportError: cannot import name '[^']+' from '([^']+)'")


@dataclass
class RunResult:
    """Outcome of one sandboxed execution."""
    ran: bool                      # did we manage to launch + observe it at all
    crashed: bool                  # genuine failure of the code (verdict "failed")
    returncode: Optional[int]
    timed_out: bool
    stdout: str
    stderr: str
    traceback_tail: str = ""       # last lines of a real traceback, for repair feedback
    note: str = ""                 # human-readable explanation of the verdict
    inconclusive: bool = False     # ran (or tried to) but showed nothing about the code
    contained: bool = False        # an OS sandbox was applied to the run
    containment: List[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.crashed:
            return "failed"
        if not self.ran or self.inconclusive:
            return "inconclusive"
        return "passed"

    @property
    def clean(self) -> bool:
        """True only when the program ran and exited cleanly."""
        return self.verdict == "passed"

    def to_dict(self) -> Dict:
        return {
            "ran": self.ran, "crashed": self.crashed, "returncode": self.returncode,
            "timed_out": self.timed_out, "clean": self.clean, "verdict": self.verdict,
            "contained": self.contained, "containment": list(self.containment),
            "traceback_tail": self.traceback_tail, "note": self.note,
            "stdout": self.stdout[-2000:], "stderr": self.stderr[-2000:],
        }


def sandbox_enabled() -> bool:
    return os.environ.get("ELI_CODING_SANDBOX", "1").strip().lower() not in ("0", "false", "no", "off")


def containment() -> Dict[str, object]:
    """What run_code applies to a candidate on this machine (eli.plugins.subprocess_sandbox's
    probed capabilities): {contained, network_isolation, filesystem_isolation, notes}."""
    try:
        from eli.plugins.subprocess_sandbox import capabilities as _sandbox_caps
        pc = _sandbox_caps()
    except Exception as exc:
        return {"contained": False, "network_isolation": False, "filesystem_isolation": False,
                "notes": [f"the sandbox could not be probed: {exc}"]}
    net, fs = bool(pc.get("network_isolation")), bool(pc.get("filesystem_isolation"))
    return {"contained": net or fs, "network_isolation": net, "filesystem_isolation": fs,
            "notes": list(pc.get("notes") or [])}


def capabilities() -> Dict[str, object]:
    """Cross-platform sandbox capability probe — what run_code actually applies here."""
    import platform
    c = containment()
    return {
        "platform": platform.system(),
        "subprocess_isolation": True,
        "cpu_rlimit": os.name == "posix",
        "contained": c["contained"],
        "network_isolation": c["network_isolation"],
        "filesystem_isolation": c["filesystem_isolation"],
        "stdin": "closed",
        "env": "allowlist",
        "notes": c["notes"],
    }


def _run_env(workdir: str) -> Dict[str, str]:
    """The candidate's environment: an allowlist, with HOME and the temp dir pointed at its own
    working directory."""
    keep = _ENV_KEEP + (_FROZEN_ENV_KEEP if getattr(sys, "frozen", False) else ())
    env = {k: v for k, v in os.environ.items() if k in keep or k.startswith(_ENV_KEEP_PREFIX)}
    env.update({
        "HOME": workdir, "USERPROFILE": workdir, "TMPDIR": workdir, "TEMP": workdir, "TMP": workdir,
        "MPLBACKEND": "Agg",       # never open / block on a GUI window (plt.show())
        "ELI_SANDBOX": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def _imported_roots(sources: List[str]) -> set:
    """Top-level module names the given Python sources import themselves."""
    roots: set = set()
    for src in sources:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                roots.add(node.module.split(".")[0])
    return roots


def _signal_name(code: int) -> str:
    try:
        return signal.Signals(code).name
    except (ValueError, AttributeError):
        return f"signal {code}"


def run_code(
    code: str,
    language: str = "python",
    *,
    timeout: float = 20.0,
    cpu_seconds: int = 30,
    extra_files: Optional[Dict[str, str]] = None,
    argv_suffix: Optional[List[str]] = None,
    entry_name: str = "candidate",
) -> RunResult:
    """Run `code` contained in a subprocess and classify the outcome (see the module docstring).

    extra_files: {relative_name: contents} written alongside the entry file
                 (e.g. a synthesised test module that imports the candidate).
    argv_suffix: extra args appended to the runner argv.
    """
    language = (language or "python").lower()
    if not sandbox_enabled():
        return RunResult(ran=False, crashed=False, returncode=None, timed_out=False, stdout="", stderr="",
                         inconclusive=True, note="not run: execution is switched off (ELI_CODING_SANDBOX=0)")

    runner = _RUNNERS.get(language)
    if runner is None:
        return RunResult(ran=False, crashed=False, returncode=None, timed_out=False, stdout="", stderr="",
                         inconclusive=True, note=f"not run: no sandbox runner for language {language!r}")

    ext, argv_builder = runner
    try:
        timeout = float(os.environ.get("ELI_CODING_RUN_TIMEOUT", "") or timeout)
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    from eli.plugins import subprocess_sandbox as _sb
    try:
        with tempfile.TemporaryDirectory(prefix="eli_code_") as td:
            entry = Path(td) / f"{entry_name}{ext}"
            entry.write_text(code, encoding="utf-8")
            for rel, contents in (extra_files or {}).items():
                fp = Path(td) / rel
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(contents, encoding="utf-8")
            plan = _sb.build_command(argv_builder(str(entry)) + list(argv_suffix or []),
                                     allow_network=False, write_paths=[td], cwd=td)
            contained, applied = bool(plan.get("contained")), list(plan.get("applied") or [])
            if not contained:
                log.warning("[CODING_SANDBOX] running model-written code UNCONTAINED: %s",
                            "; ".join(plan.get("notes") or []) or "no sandbox available")
            preexec = _sb._make_preexec(not contained, cpu_seconds=cpu_seconds) if os.name == "posix" else None
            try:
                proc = subprocess.run(
                    plan["argv"], cwd=td, env=_run_env(td), stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=timeout, preexec_fn=preexec,
                )
            except subprocess.TimeoutExpired as te:
                return RunResult(
                    ran=True, crashed=False, returncode=None, timed_out=True, inconclusive=True,
                    stdout=(te.stdout or "") if isinstance(te.stdout, str) else "",
                    stderr=(te.stderr or "") if isinstance(te.stderr, str) else "",
                    contained=contained, containment=applied,
                    note=f"inconclusive: ran past its {timeout:.0f}s timeout",
                )
            except FileNotFoundError:
                return RunResult(ran=False, crashed=False, returncode=None, timed_out=False, stdout="", stderr="",
                                 inconclusive=True, contained=contained, containment=applied,
                                 note=f"not run: the interpreter for {language} is not installed")
    except Exception as exc:
        return RunResult(ran=False, crashed=False, returncode=None, timed_out=False, stdout="", stderr="",
                         inconclusive=True, note=f"not run: sandbox infra error: {exc}")

    out, err = proc.stdout or "", proc.stderr or ""
    base = dict(contained=contained, containment=applied)
    uncontained = "" if contained else " (uncontained)"
    if proc.returncode == 0:
        return RunResult(True, False, 0, False, out, err, note="exited cleanly" + uncontained, **base)

    tail = "\n".join(err.strip().splitlines()[-8:])
    # The sandbox itself could not start the program: nothing is known about the code.
    if contained and err.lstrip().startswith(("bwrap:", "unshare:")) and _TRACEBACK_MARKER not in err:
        return RunResult(False, False, proc.returncode, False, out, err, inconclusive=True,
                         note=f"not run: the sandbox could not start it ({tail[:200]})", **base)
    # A signal death: a negative status, or 128+signal from bubblewrap, which reports its child's
    # death that way (it is the child's parent inside the sandbox).
    sig = -proc.returncode if proc.returncode < 0 else None
    if (sig is None and contained and os.path.basename(str(plan["argv"][0])) == "bwrap"
            and proc.returncode > 128 and _TRACEBACK_MARKER not in err):
        sig = proc.returncode - 128
    if sig is not None:
        if sig in _BUDGET_SIGNALS:
            return RunResult(True, False, proc.returncode, False, out, err, inconclusive=True,
                             note=f"inconclusive: stopped by {_signal_name(sig)} (its CPU or memory budget)", **base)
        return RunResult(True, True, proc.returncode, False, out, err, traceback_tail=tail or _signal_name(sig),
                         note=f"crashed: killed by {_signal_name(sig)}", **base)
    missing = _MISSING_MODULE_RE.search(err) or _IMPORT_NAME_RE.search(err)
    if missing and language == "python":
        root = missing.group(1).split(".")[0]
        if root in _imported_roots([code, *[c for n, c in (extra_files or {}).items() if n.endswith(".py")]]):
            return RunResult(True, True, proc.returncode, False, out, err, traceback_tail=tail,
                             note=f"failed: it imports {missing.group(1)}, which is not available here", **base)
        return RunResult(True, False, proc.returncode, False, out, err, inconclusive=True, traceback_tail=tail,
                         note=f"inconclusive: a library it uses needs {missing.group(1)}, which is not installed here",
                         **base)
    if _TRACEBACK_MARKER not in err and language == "python":
        return RunResult(True, True, proc.returncode, False, out, err, traceback_tail=tail,
                         note=f"exited with status {proc.returncode} and no traceback "
                              "(sys.exit()/os._exit() or similar) — treated as a failure", **base)
    return RunResult(True, True, proc.returncode, False, out, err, traceback_tail=tail,
                     note="genuine runtime crash", **base)

