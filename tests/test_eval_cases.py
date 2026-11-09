"""Run the eval harness as part of pytest.

This makes every model-free router eval case a real pytest test, so the eval
board runs automatically during normal `pytest` (and in any pre-commit/CI hook)
without having to invoke `tools/eval/run_eval.py` separately. Engine cases (which
need a loaded model) are excluded here — run those with the standalone harness:
    python tools/eval/run_eval.py --target engine
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("ELI_HEADLESS", "1")

_REPO = Path(__file__).resolve().parents[1]
_CASES = _REPO / "tools" / "eval" / "cases.yaml"


def _router_cases():
    """Load the router (model-free) cases at collection time. Returns [] on any
    import/parse error so a harness problem can't break the whole test session."""
    try:
        import sys
        if str(_REPO) not in sys.path:
            sys.path.insert(0, str(_REPO))
        from tools.eval.run_eval import _load_cases
        cases = _load_cases(_CASES)
        return [c for c in cases if str(c.get("target", "router")).lower() == "router"]
    except Exception:
        return []


_ROUTER_CASES = _router_cases()


@pytest.mark.skipif(not _ROUTER_CASES, reason="no router eval cases discovered")
@pytest.mark.parametrize("case", _ROUTER_CASES, ids=lambda c: str(c.get("id", "?")))
def test_eval_router_case(case):
    from tools.eval.run_eval import _run_case
    from tools.eval import assertions as A

    res = _run_case(case)
    if res.get("skipped"):
        pytest.skip(str(res.get("reason") or "skipped by driver"))

    failures = []
    for a in (case.get("assert") or []):
        ok, detail = A.check(a, res)
        if not ok:
            failures.append(detail)
    assert not failures, "; ".join(failures)


def test_eval_harness_has_cases():
    """Guard: the harness must actually discover cases (catches a broken
    cases.yaml or import path that would otherwise silently skip everything)."""
    assert _ROUTER_CASES, "router eval cases failed to load — check tools/eval/cases.yaml"


# ── the eval never changes the network or the data for the rest of ELI ─────────
def _driver():
    import sys
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    from tools.eval import eli_driver
    return eli_driver


def test_a_cases_network_state_is_seen_only_by_the_case(monkeypatch):
    """Swapping config.network_allowed for the whole process opened the network against Net-off
    for every thread while a case ran (the proactive news fetch, the self-upgrade check)."""
    import threading
    import eli.core.config as C

    D = _driver()
    monkeypatch.setattr(C, "network_allowed", lambda: False)        # the user's setting: off
    seen = {}
    with D._network(True):
        from eli.core.config import network_allowed
        seen["case"] = network_allowed()
        t = threading.Thread(target=lambda: seen.setdefault("elsewhere", C.network_allowed()))
        t.start()
        t.join()
    assert seen == {"case": True, "elsewhere": False}
    assert C.network_allowed() is False


def test_a_case_that_needs_the_network_the_user_turned_off_is_skipped(monkeypatch):
    import eli.core.config as C

    D = _driver()
    monkeypatch.setattr(C, "network_allowed", lambda: False)
    ran = []
    monkeypatch.setattr("eli.execution.executor_enhanced.execute_action", lambda *a: ran.append(a) or {})
    res = D.run_executor("GET_TIME", {}, network=True)
    assert res.get("skipped") and "network" in res["reason"] and not ran


def test_engine_cases_run_only_in_a_throwaway_copy_of_elis_folders(monkeypatch):
    """Every engine prompt was stored as a turn the user typed in the real memory."""
    D = _driver()
    monkeypatch.setattr(D, "SANDBOX", None)
    monkeypatch.setattr(D, "get_engine", lambda: (_ for _ in ()).throw(AssertionError("engine built")))
    res = D.run_engine("hello there")
    assert res.get("skipped") and "throwaway" in res["reason"]


def test_cases_that_act_on_the_desktop_run_only_when_asked_for(monkeypatch):
    """'minimize firefox' minimised the user's browser, 'open github.com' opened a tab and
    'what's on my screen' read the real screen during an unattended run."""
    import sys
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    from tools.eval.run_eval import _load_cases, _run_case

    D = _driver()
    marked = {c["id"] for c in _load_cases(_CASES) if c.get("desktop")}
    assert {"minimize_no_fabricated_done", "open_domain_engine_is_url", "screen_question_uses_vision"} <= marked
    ran = []
    monkeypatch.setattr(D, "run_engine", lambda *a, **k: ran.append(a) or {"text": "ok"})
    case = {"id": "x", "target": "engine", "desktop": True, "prompt": "minimize firefox"}
    assert _run_case(case).get("skipped") and not ran
    assert _run_case(case, desktop=True) == {"text": "ok"} and ran


_SANDBOXED = """
import json, sys
sys.path.insert(0, sys.argv[1])
from tools.eval import eli_driver as D
box = D.enter_sandbox()
from eli.core import paths
print(json.dumps({"box": str(box), "real": str(D.REAL_DATA_DIR), "paths": [str(p) for p in (
    paths.data_dir(), paths.config_dir(), paths.user_db_path(), paths.agent_db_path(),
    paths.persona_auto_path(), paths.notebook_dir(), paths.learning_dir(), paths.cache_dir())]}))
"""


def test_the_sandbox_holds_every_place_eli_keeps_state():
    import json
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-c", _SANDBOXED, str(_REPO)], cwd=str(_REPO),
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-1500:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    box = Path(got["box"])
    assert not box.exists(), "the throwaway folder outlived the run"
    assert all(box in Path(p).parents for p in got["paths"]), got
    assert box not in Path(got["real"]).parents, "the history would be thrown away with it"
