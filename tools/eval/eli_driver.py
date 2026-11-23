"""Shared ELI eval driver.

ONE place that runs a prompt through ELI's real machinery, used by both the
pure-Python harness (run_eval.py) and the promptfoo custom provider
(promptfoo/eli_provider.py). No duplication of how-to-call-ELI.

Three targets:
  • route_only(prompt)      → fast, model-free: just the router's decision
                              (action / matched_by). Covers routing regressions.
  • run_executor(action,..) → fast, model-free: drives execute_action(action,args)
                              directly and returns the REAL executor result
                              (ok / text / error). Covers "routed but not handled
                              / fabricated result" bugs that the router can't see.
  • run_engine(prompt, ...) → the full CognitiveEngine.process() pipeline
                              (needs a model loaded): text + grounding +
                              response_mode + latency.

The network state a case is written for is never imposed on the rest of ELI. Swapping
config.network_allowed for the whole process opened the network against the user's Net-off
setting for as long as an engine case ran, for every thread: the proactive news fetch, the
self-upgrade check. Now a case sees its state only in its own request context, and a case that
needs the network the user has turned off is skipped, except a router case, which only decides
and fetches nothing.

Engine cases run the whole pipeline, which keeps what it is told: every prompt was stored as a
turn the user typed. They run only in a throwaway copy of ELI's folders (enter_sandbox(), which
the command line does before eli is imported); in a process using the real folders, such as the
window, they are skipped and say why. Cases marked `desktop: true` act on the real desktop or
read the real screen and run only when asked for (run_eval.py --desktop).

The replay (replay.py) runs one throwaway copy across several processes, one per conversation,
as the app is started once per sitting: new_sandbox() makes the folder, isolated_env() is the
environment each process is started with (the copy's folders, no desktop session, the network
off), and start_engine(STUB, background=False) builds the engine with the stand-in model
(StubModel) and none of the background loops, so what is measured is what the turns did.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

os.environ.setdefault("ELI_HEADLESS", "1")
os.environ.setdefault("ELI_NO_GUI", "1")

_ROOT = Path(__file__).resolve().parents[2]

# The network state the running case is written for, in its own request context only.
_CASE_NETWORK: contextvars.ContextVar = contextvars.ContextVar("eli_eval_case_network", default=None)


def _user_network_on() -> bool:
    from eli.core.config import network_allowed
    return bool(network_allowed())


def _needs_closed_network(state: Optional[bool]) -> Optional[Dict[str, Any]]:
    """A skip result when the case needs the network and the user has it off, else None."""
    if state is True and not _user_network_on():
        return {"skipped": True,
                "reason": "needs the network, which is off in your settings; the eval does not turn it on"}
    return None


@contextlib.contextmanager
def _network(state: Optional[bool]):
    """Run the case under `state` (None = the user's own setting) in this request context alone.
    process() runs each turn in a copy of the caller's context, so the turn sees it; the rest of
    ELI keeps reading the user's setting."""
    if state is None:
        yield
        return
    import eli.core.config as _C
    real = _C.network_allowed

    def network_allowed() -> bool:
        forced = _CASE_NETWORK.get()
        return real() if forced is None else forced

    token = _CASE_NETWORK.set(bool(state))
    _C.network_allowed = network_allowed
    try:
        yield
    finally:
        _C.network_allowed = real
        _CASE_NETWORK.reset(token)


# ── a throwaway copy of ELI's folders for engine cases ──────────────────────────
SANDBOX: Optional[Path] = None
REAL_DATA_DIR: Optional[Path] = None      # where the run's history still goes
# Kept from the environment: the code, the models (only read) and the licence keys (only read).
_KEEP_FROM_ENV = ("ELI_PROJECT_ROOT", "ELI_MODELS_DIR", "ELI_KEYS_DIR")
_PATH_ENV_ENDINGS = ("_DB", "_DIR", "_PATH", "_FILE", "_ROOT", "_INDEX", "_META")


def _real_state_paths() -> Dict[str, str]:
    """Where this checkout keeps its settings, data and persona overlay, read by another process
    so that nothing in this one resolves a path before the redirect."""
    code = ("import json; from eli.core import paths as p; print(json.dumps({"
            "'config': str(p.config_dir()), 'data': str(p.data_dir()), "
            "'persona': str(p.persona_auto_path())}))")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(_ROOT), capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"could not find ELI's folders: {(out.stderr or out.stdout).strip()[-400:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def sandbox_parent(data_dir: Optional[str] = None) -> Path:
    """Where the throwaway copies are made: inside the project (ELI drops path settings that point
    outside it), in the data folder in force (`data_dir`, else found by another process, as
    _real_state_paths does) when that is inside the project, as a test run's own sandbox is, else the
    project's artifacts/. It was always the project's artifacts/, so a test run left an empty
    artifacts/_eval_sandbox in the checkout."""
    base = Path(data_dir or _real_state_paths()["data"]).resolve()
    if _ROOT.resolve() not in base.parents:
        base = _ROOT / "artifacts"
    return base / "_eval_sandbox"


def new_sandbox(data_dir: Optional[str] = None) -> Path:
    """A fresh, empty folder for one throwaway copy of ELI's state (in sandbox_parent()); whoever
    made it removes it, with remove_sandbox()."""
    parent = sandbox_parent(data_dir)
    for attempt in (1, 2):
        if not parent.is_dir():
            parent.mkdir(parents=True, exist_ok=True)
            _MADE_PARENTS.add(parent)
        try:
            return Path(tempfile.mkdtemp(prefix="run-", dir=str(parent)))
        except FileNotFoundError:           # another run's remove_sandbox took the empty folder away
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


_MADE_PARENTS: set = set()                  # the copies' folders this process made


def remove_sandbox(box: Path) -> None:
    """Remove a throwaway copy, and the folder the copies are made in once none is left in it, when
    this process made that folder: a run with its data folder outside the project made them in the
    checkout's artifacts/ and left artifacts/_eval_sandbox there."""
    box = Path(box)
    shutil.rmtree(box, ignore_errors=True)
    if box.parent in _MADE_PARENTS:
        try:
            box.parent.rmdir()
            _MADE_PARENTS.discard(box.parent)
        except OSError:
            pass                            # another copy is still in it, or it is already gone


def sandbox_env(box: Path) -> Dict[str, str]:
    """Where every place ELI keeps state points for a copy kept in `box`."""
    box = Path(box)
    return {
        "ELI_DATA_DIR": str(box / "artifacts"),
        "ELI_ARTIFACTS_DIR": str(box / "artifacts"),
        "ELI_CONFIG_DIR": str(box / "config"),
        "ELI_LEARNING_DIR": str(box / "training"),
        "ELI_CACHE_DIR": str(box / "cache"),
        "ELI_PERSONA_AUTO_PATH": str(box / "persona.auto.txt"),
        "ELI_NOTEBOOK_DIR": str(box / "notebook"),
        "ELI_VOICE_REFS_DIR": str(box / "voice_profiles"),
        # a starting engine writes the capabilities table, by default over the tracked blueprint
        "ELI_CAPABILITIES_DOC": str(box / "capabilities_and_actions.md"),
    }


def _without_state_paths(env) -> None:
    """Take every ELI_*_DB/_DIR/_PATH/... override out of `env` (the code, the models and the keys
    stay), so nothing resolves to the folders they named."""
    for name in list(env):
        if name.startswith("ELI_") and name.endswith(_PATH_ENV_ENDINGS) and name not in _KEEP_FROM_ENV:
            env.pop(name)


_LAUNCHERS = ("xdg-open", "gio", "gtk-launch", "open")


def isolated_env(box: Path) -> Dict[str, str]:
    """The environment a process of a measured run is started with: ELI's state in `box`, the
    user's session out of reach (eli/plugins/subprocess_sandbox.scrub_env: no display, session
    bus, sound server or agent; the desktop's openers do nothing) and the network off, so nothing
    a run says reaches this desktop or the internet. Test mode is off: the run is the product's."""
    from eli.plugins.subprocess_sandbox import scrub_env
    env = scrub_env(dict(os.environ))
    _without_state_paths(env)
    # ELI reads either as test mode (kernel/stages/_common._eli_test_mode): started from a test,
    # the run skipped the session-end flush and capped every reply
    for name in [n for n in env if n == "ELI_TEST_MODE" or n.startswith("PYTEST_")]:
        env.pop(name)
    env.update(sandbox_env(box))
    openers = Path(box) / "no-launchers"
    openers.mkdir(parents=True, exist_ok=True)
    for name in _LAUNCHERS:
        (openers / name).write_text("#!/bin/sh\nexit 0\n")
        (openers / name).chmod(0o755)
    env["PATH"] = str(openers) + os.pathsep + env.get("PATH", "")
    env.update({"ELI_OFFLINE": "1", "ELI_HEADLESS": "1", "ELI_NO_GUI": "1",
                "PYTHONPATH": str(_ROOT) + os.pathsep + env.get("PYTHONPATH", "")})
    return env


def enter_sandbox(box: Optional[Path] = None) -> Path:
    """Point every place ELI keeps state at a fresh folder for the rest of this process, with a
    copy of the user's settings and persona overlay so the same model and character are used.
    Must run before eli is imported. The folder sits inside the project (ELI drops path settings
    that point outside it) and is removed when the process ends.

    With `box`, the copy already made there by the process that started this one (a replay's
    conversation) is used as it is, and left for that process to remove."""
    global SANDBOX, REAL_DATA_DIR
    if SANDBOX is not None:
        return SANDBOX
    if any(name == "eli" or name.startswith("eli.") for name in sys.modules):
        raise RuntimeError("the eval sandbox has to be entered before eli is imported")
    if box is not None:
        _without_state_paths(os.environ)
        os.environ.update(sandbox_env(box))
        SANDBOX = Path(box)
        return SANDBOX
    real = _real_state_paths()
    box = new_sandbox(real["data"])
    import atexit
    atexit.register(remove_sandbox, box)
    if Path(real["config"]).is_dir():
        shutil.copytree(real["config"], box / "config")
    if Path(real["persona"]).is_file():
        shutil.copy2(real["persona"], box / "persona.auto.txt")
    _without_state_paths(os.environ)
    os.environ.update(sandbox_env(box))
    SANDBOX, REAL_DATA_DIR = box, Path(real["data"])
    return box


def route_only(prompt: str, network: Optional[bool] = None) -> Dict[str, Any]:
    """Router decision only — no model required."""
    from eli.execution.router_enhanced import route
    t0 = time.perf_counter()
    with _network(network):
        r = route(prompt) or {}
    meta = r.get("meta") or {}
    return {
        "target": "router",
        "action": str(r.get("action") or ""),
        "matched_by": str(meta.get("matched_by") or ""),
        "confidence": float(r.get("confidence") or 0.0),
        "args": r.get("args") or {},
        "text": "",
        "grounding": None,
        "response_mode": "",
        "latency_s": round(time.perf_counter() - t0, 4),
        "raw": r,
    }


def run_executor(action: str, args: Optional[Dict[str, Any]] = None,
                 network: Optional[bool] = None) -> Dict[str, Any]:
    """Drive the executor directly and return its REAL result. Model-free and
    deterministic — catches 'SUPPORTED_ACTION routed but unhandled / fabricated'
    regressions (e.g. the MINIMIZE_APP fake-"Done." bug) that route_only can't see.

    Eval cases for this target MUST use side-effect-free / read-only actions
    (status, introspection, time) so the board never disturbs the host."""
    from eli.execution.executor_enhanced import execute_action
    a = str(action or "").strip().upper()
    data = args if isinstance(args, dict) else {}
    skip = _needs_closed_network(network)
    if skip:
        return {"target": "executor", "action": a, "args": data, **skip}
    t0 = time.perf_counter()
    with _network(network):
        try:
            res = execute_action(a, data)
        except Exception as e:  # pragma: no cover
            return {"target": "executor", "action": a, "args": data, "ok": False,
                    "text": f"[error] {e}", "error": str(e), "grounding": None,
                    "response_mode": "", "latency_s": round(time.perf_counter() - t0, 4)}
    latency = round(time.perf_counter() - t0, 4)
    if isinstance(res, dict):
        text = str(res.get("response") or res.get("content")
                   or res.get("message") or res.get("error") or "").strip()
        return {
            "target": "executor",
            "action": str(res.get("action") or a),
            "args": data,
            "ok": bool(res.get("ok", True)),
            "text": text,
            "error": str(res.get("error") or ""),
            "grounding": _maybe_float(res.get("grounding")),
            "response_mode": "",
            "latency_s": latency,
            "raw": res,
        }
    return {"target": "executor", "action": a, "args": data, "ok": True,
            "text": str(res or "").strip(), "error": "", "grounding": None,
            "response_mode": "", "latency_s": latency, "raw": res}


_ENGINE = None

STUB = "stub"           # start_engine(STUB): the stand-in model below instead of a GGUF
STUB_REPLY = "I see."   # what the stand-in says when its caller gives it nothing to say


class StubModel:
    """A stand-in for llama.cpp's Llama, as far as gguf_inference calls it, so the whole engine
    runs with nothing behind the generation: every free generation returns `reply` (its caller
    sets it per turn), a call held to a grammar (a structured, JSON-shaped call) returns an empty
    object, as a model that found nothing to fill in would. Its window is the one ELI asks for
    (gguf_inference._requested_ctx: the operator's setting, else the smallest window). It has no
    tokenizer, so ELI's own estimate sizes and fits the prompt, as for any model it cannot
    tokenize; no token figures come back, as from a model that gives none."""

    def __init__(self, window: int, reply: str = STUB_REPLY) -> None:
        self._window = int(window)
        self.reply = reply

    def n_ctx(self) -> int:
        return self._window

    def __call__(self, prompt: str, *, grammar: Any = None, stream: bool = False, **_: Any):
        out = {"choices": [{"text": "{}" if grammar is not None else str(self.reply)}]}
        return iter([out]) if stream else out

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


def start_engine(model: Optional[str] = None, *, background: bool = True):
    """Build one headless CognitiveEngine: with `model` None the configured model, with STUB the
    StubModel (gguf_inference holds it as the loaded model, so every generation path reaches it).
    background=False builds it without its background loops (reflection, habits, self-improvement,
    the schedulers, the proactive daemon), the hardware check and the exit hooks, as the engine
    does in test mode, and then leaves test mode, so its turns run as the product's do and nothing
    else writes while they run; its caller ends it with shutdown()."""
    from eli.cognition import gguf_inference as G
    stub = None
    if model == STUB:
        stub = StubModel(G._requested_ctx(G._load_runtime_settings(), None))
        G._llm = stub
    prior = os.environ.get("ELI_TEST_MODE")
    if not background:
        os.environ["ELI_TEST_MODE"] = "1"
    try:
        from eli.kernel.engine import CognitiveEngine
        engine = CognitiveEngine(auto_init_gguf=stub is None)
    finally:
        if prior is None:
            os.environ.pop("ELI_TEST_MODE", None)
        else:
            os.environ["ELI_TEST_MODE"] = prior
    if stub is not None:
        engine._gguf_available, engine._gguf_load_error = True, None
        engine._model_path, engine._ctx = STUB, engine._runtime_n_ctx()
    return engine


def get_engine(model: Optional[str] = None, *, background: bool = True):
    """Lazily build (and cache) one headless CognitiveEngine (start_engine; the first call says
    how). Returns None if it can't initialise (e.g. no model) so engine cases degrade to SKIP,
    not crash."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    try:
        _ENGINE = start_engine(model, background=background)
    except Exception as e:  # pragma: no cover
        print(f"[eli_driver] engine init failed: {e}")
        _ENGINE = None
    return _ENGINE


def run_engine(
    prompt: str,
    network: Optional[bool] = None,
    reasoning_mode: str = "quick",
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Full pipeline. Returns normalised fields; on failure 'text' carries the error."""
    if SANDBOX is None:
        return {"target": "engine", "skipped": True,
                "reason": "engine cases run only in a throwaway copy of ELI's folders "
                          "(python tools/eval/run_eval.py --target engine); here each prompt would "
                          "be kept as something you typed",
                "text": "", "action": "", "grounding": None, "response_mode": "", "latency_s": 0.0}
    skip = _needs_closed_network(network)
    if skip:
        return {"target": "engine", "text": "", "action": "", "grounding": None,
                "response_mode": "", "latency_s": 0.0, **skip}
    eng = get_engine()
    if eng is None:
        return {"target": "engine", "skipped": True, "reason": "no engine/model",
                "text": "", "action": "", "grounding": None, "response_mode": "",
                "latency_s": 0.0}

    sid = session_id or f"eval-{int(time.time())}"
    t0 = time.perf_counter()
    with _network(network):
        try:
            res = eng.process(prompt, source="user", stream=False,
                              reasoning_mode=reasoning_mode, session_id=sid)
        except Exception as e:  # pragma: no cover
            return {"target": "engine", "text": f"[error] {e}", "action": "",
                    "grounding": None, "response_mode": "",
                    "latency_s": round(time.perf_counter() - t0, 4), "error": str(e)}
    return engine_result(res, eng, round(time.perf_counter() - t0, 4))


def engine_result(res: Any, eng: Any, latency: float) -> Dict[str, Any]:
    """process()'s return, whatever shape it took, as the driver's normalised fields."""
    if isinstance(res, str):
        # Some quick paths intentionally return DIRECT VISIBLE TEXT (a bare string),
        # which carries no 'action' field — read it from the engine's side channel
        # (_last_response_action) so routing telemetry stays accurate for those paths.
        _act = str(getattr(eng, "_last_response_action", "") or "")
        return {"target": "engine", "text": res.strip(), "action": _act, "grounding": None,
                "response_mode": "", "latency_s": latency, "raw": res}
    if not isinstance(res, dict):
        # generator → consume
        toks = []
        for c in res:
            toks.append(c.get("response") or c.get("token") or "" if isinstance(c, dict) else str(c))
        return {"target": "engine", "text": "".join(toks).strip(), "action": "",
                "grounding": None, "response_mode": "", "latency_s": latency}

    meta = res.get("meta") or {}
    trace = res.get("trace") or {}
    text = str(res.get("response") or res.get("content") or res.get("text") or "").strip()
    # Grounding usually lives in the trace; deterministic grounded responders
    # (e.g. RUNTIME_STATUS) declare it at the top level instead — read both.
    _grounding = _maybe_float(trace.get("grounding_confidence"))
    if _grounding is None:
        _grounding = _maybe_float(res.get("grounding"))
    return {
        "target": "engine",
        "text": text,
        "action": str(res.get("action") or ""),
        "matched_by": str(meta.get("matched_by") or ""),
        "grounding": _grounding,
        "confidence": _maybe_float(res.get("confidence")),
        "response_mode": str(meta.get("response_mode") or ""),
        "latency_s": latency,
        "raw": res,
    }


def run_coding_agent(
    prompt: str,
    *,
    mode: str = "",
    stub: Optional[bool] = None,
) -> Dict[str, Any]:
    """Drive CodeAgent with a stub generator — model-free and deterministic."""
    import time
    t0 = time.perf_counter()

    mode = str(mode or "").strip().lower()
    if mode == "context_only":
        from eli.coding.repo_context import gather_repo_context
        text = gather_repo_context(prompt)
        return {
            "target": "coding_agent", "text": text, "ok": bool(text.strip()),
            "solved": False, "score": 0.0, "gate_failed": "",
            "latency_s": round(time.perf_counter() - t0, 4),
        }
    if mode == "sandbox_caps":
        from eli.coding.sandbox import capabilities
        import json
        caps = capabilities()
        text = json.dumps(caps, indent=2)
        return {
            "target": "coding_agent", "text": text, "ok": True,
            "solved": False, "score": 0.0, "gate_failed": "",
            "latency_s": round(time.perf_counter() - t0, 4),
        }

    def _stub_gen(p, system="", max_tokens=1000, temperature=0.2, **k):
        low = (p or "").lower()
        if "fibonacci" in low or "fib(n)" in low:
            return (
                "def fib(n):\n"
                "    a, b = 0, 1\n"
                "    for _ in range(n):\n"
                "        a, b = b, a + b\n"
                "    return a\n"
                "if __name__ == '__main__':\n"
                "    print(fib(10))\n"
            )
        if "sum" in low and "1 to 10" in low:
            return "print(sum(range(1, 11)))\n"
        if "x[9]" in low or "index" in low:
            return "x = [1, 2]\nprint(x[0] if x else None)\n"
        if "json" in low and "plan" in low:
            return '{"approach": "direct", "steps": ["implement"]}'
        return "print('ok')\n"

    from eli.coding.agent import CodeAgent
    agent = CodeAgent(generate=_stub_gen if stub is not False else None)
    try:
        result = agent.solve(str(prompt or ""), beam=2, max_iterations=3, run_timeout=8.0)
        d = result.to_dict()
    except Exception as e:
        return {"target": "coding_agent", "text": f"[error] {e}", "ok": False,
                "solved": False, "score": 0.0, "gate_failed": "error",
                "latency_s": round(time.perf_counter() - t0, 4), "error": str(e)}

    search = d.get("search") or {}
    best = search.get("best") or {}
    return {
        "target": "coding_agent",
        "text": str(d.get("code") or d.get("message") or ""),
        "ok": bool(d.get("ok")),
        "solved": bool(d.get("solved")),
        "score": float(d.get("score") or 0.0),
        "gate_failed": str(best.get("gate_failed") or ""),
        "bug_class": d.get("bug_class"),
        "latency_s": round(time.perf_counter() - t0, 4),
        "raw": d,
    }


def _maybe_float(v):
    try:
        return float(v)
    except Exception:
        return None
