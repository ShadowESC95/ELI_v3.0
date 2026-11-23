"""Deterministic tests for the ELI coding agent (eli/coding).

No model required — the LLM is injected as a stub generator, so the full
plan → search → verify → repair → bug-memory loop is exercised against real
sandbox execution.
"""

import sys
import tempfile
from pathlib import Path

import pytest

from eli.coding.sandbox import run_code
from eli.coding.bug_memory import classify_bug, BugClass, BugMemory
from eli.coding.verification import Candidate, verify_candidate, score_candidate
from eli.coding.agent import CodeAgent


# ── sandbox ───────────────────────────────────────────────────────────────────

def test_sandbox_clean_run():
    r = run_code("print(sum(range(10)))", "python", timeout=8)
    assert r.clean and not r.crashed and r.returncode == 0


def test_sandbox_real_crash_detected():
    r = run_code("x = [1, 2]\nprint(x[9])", "python", timeout=8)
    assert r.crashed and "IndexError" in r.traceback_tail


def test_sandbox_timeout_is_inconclusive_not_clean():
    """A run that never finished shows nothing about the code: not a crash, and not a pass."""
    r = run_code("import time\nwhile True:\n    time.sleep(0.2)", "python", timeout=2)
    assert r.timed_out and not r.crashed and not r.clean and r.verdict == "inconclusive"


def test_sandbox_a_module_the_candidate_imports_that_is_not_here_is_a_failure():
    """A hallucinated import scored as a clean run, so the candidate could be reported 'solved'."""
    r = run_code("import a_module_that_is_not_installed_xyz", "python", timeout=8)
    assert r.crashed and not r.clean and r.verdict == "failed"
    assert "a_module_that_is_not_installed_xyz" in r.note


def test_sandbox_cannot_import_name_from_its_own_module_is_a_failure():
    r = run_code("import candidate\nprint(candidate.f())", "python", timeout=8,
                 extra_files={"candidate.py": "from json import no_such_function_xyz\n"},
                 entry_name="run_tests")
    assert r.crashed and r.verdict == "failed"


def test_a_hallucinated_import_with_no_tests_is_never_solved():
    c = verify_candidate(Candidate(code="import quantum_widgets_xyz\n\ndef main():\n    return 1\n\nmain()\n"))
    assert c.runs_clean is False and c.gate_failed == "execution"
    assert c.score < 0.95


def test_an_inconclusive_run_earns_no_execution_credit():
    c = verify_candidate(Candidate(code="import time\n\ndef main():\n    while True:\n        time.sleep(0.2)\n\nmain()\n"),
                         run_timeout=2)
    assert c.runs_clean is None and c.score < 0.95
    clean = verify_candidate(Candidate(code="def main():\n    return 1\n\nmain()\n"))
    assert clean.score > c.score


# An uncaught sys.exit(n)/os._exit(n) has no traceback but a positive returncode; it must not
# be tolerated like a signal kill.
def test_sandbox_sys_exit_nonzero_is_a_crash_not_tolerated():
    r = run_code("import sys\nsys.exit(1)", "python", timeout=8)
    assert r.crashed and not r.clean and r.returncode == 1


def test_sandbox_os_exit_nonzero_is_a_crash_not_tolerated():
    r = run_code("import os\nos._exit(7)", "python", timeout=8)
    assert r.crashed and not r.clean and r.returncode == 7


def test_sandbox_sys_exit_zero_is_still_clean():
    """sys.exit(0) / sys.exit() / sys.exit(None) all mean success -- must not
    be swept into the new "positive nonzero exit" failure path."""
    r = run_code("import sys\nsys.exit(0)", "python", timeout=8)
    assert r.clean and not r.crashed and r.returncode == 0


# A kill for the run's budget (SIGKILL: the OOM killer or a hard limit) says nothing about the code.
def test_sandbox_a_budget_kill_is_inconclusive():
    r = run_code("import os, signal\nos.kill(os.getpid(), signal.SIGKILL)", "python", timeout=8)
    assert not r.crashed and not r.clean and r.verdict == "inconclusive"
    assert "SIGKILL" in r.note


# A crash signal is a crash: it counted as a clean run.
def test_sandbox_a_crash_signal_is_a_failure():
    r = run_code("import os, signal\nos.kill(os.getpid(), signal.SIGSEGV)", "python", timeout=8)
    assert r.crashed and not r.clean and r.verdict == "failed" and "SIGSEGV" in r.note


def test_a_candidate_runs_contained_with_no_session_secrets_and_no_stdin(monkeypatch):
    """Model-written code ran with ELI's stdin, the ssh agent, the session bus and every credential
    whose name did not contain KEY/TOKEN/SECRET, and with no OS isolation unless an opt-in was set."""
    import subprocess as sp
    from eli.coding import sandbox as SB
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/user/0/ssh-agent.sock")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/0/bus")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/home/someone/creds.json")
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = list(argv), kw
        return sp.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(SB.subprocess, "run", fake_run)
    r = SB.run_code("print(1)", "python", timeout=8)
    env = seen["kw"]["env"]
    assert seen["kw"].get("stdin") is sp.DEVNULL
    for name in ("SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS", "GOOGLE_APPLICATION_CREDENTIALS"):
        assert name not in env
    assert env["HOME"] == seen["kw"]["cwd"]
    if SB.containment()["contained"]:
        assert r.contained and seen["argv"][0] != sys.executable
        assert "--unshare-net" in seen["argv"] or "-n" in seen["argv"]
    else:
        assert not r.contained and "uncontained" in r.note


# ── bug classification ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("tb,expected", [
    ("Traceback (most recent call last):\n  File \"x\", line 1, in f\n    o.v\n"
     "AttributeError: 'NoneType' object has no attribute 'v'", BugClass.NULL_DEREF),
    ("Traceback (most recent call last):\n  File \"x\", line 1\n    a[9]\nIndexError: list index out of range",
     BugClass.INDEX_OOB),
    ("Traceback (most recent call last):\n  File \"x\", line 1\n    d['k']\nKeyError: 'k'", BugClass.KEY_MISSING),
])
def test_bug_classification(tb, expected):
    dg = classify_bug(traceback_text=tb)
    assert dg.bug_class == expected and dg.confidence >= 0.8 and dg.signature


def test_bug_memory_record_and_recall():
    with tempfile.TemporaryDirectory() as td:
        bm = BugMemory(db_path=Path(td) / "bm.sqlite3")
        dg = classify_bug(traceback_text="AttributeError: 'NoneType' object has no attribute 'v'")
        bm.record_fix(dg, "guard None before attribute access", fix_diff="if o is None: return")
        recalled = bm.recall(dg)
        assert recalled and recalled[0].fix_summary.startswith("guard None")
        assert bm.stats()["total_fixes"] == 1


# ── verification + scoring ──────────────────────────────────────────────────────

def test_verify_syntax_gate():
    c = verify_candidate(Candidate(code="def f(:\n  pass"))
    assert c.syntax_ok is False and c.gate_failed == "syntax"


def test_verify_clean_scores_higher_than_crash():
    good = verify_candidate(Candidate(code="def main():\n    return 1\nmain()\n"))
    bad = verify_candidate(Candidate(code="raise ValueError('boom')\n"))
    assert good.score > bad.score and good.runs_clean and bad.runs_clean is False


# A failing candidate and test must not verify as "1/1 tests passed"; the sandbox
# classification and _parse_test_result fixes are exercised independently.
def test_a_candidate_that_sys_exits_is_not_scored_as_passing():
    """The candidate itself dies via sys.exit(1) -- must fail at Gate 2
    (execution), never even reaching the tests gate as a false pass."""
    bad_code = "import sys\ndef add(a, b):\n    return a + b\nsys.exit(1)\n"
    tests = (
        "import candidate, sys\n"
        "p = 0; t = 1\n"
        "try:\n"
        "    assert candidate.add(2, 3) == 5\n"
        "    p += 1\n"
        "except Exception:\n"
        "    pass\n"
        "print(f'ELI_TESTS: {p}/{t}')\n"
        "sys.exit(0 if p == t else 1)\n"
    )
    cand = verify_candidate(Candidate(code=bad_code), tests=tests)
    assert cand.gate_failed == "execution"
    assert cand.runs_clean is False
    assert not (cand.tests_total and cand.tests_passed == cand.tests_total), (
        "a candidate that never ran must not be scored as if its tests passed")


def test_a_test_harness_that_sys_exits_before_the_marker_is_not_a_pass():
    """The CANDIDATE is innocent; the test HARNESS itself dies via sys.exit()
    before ever printing ELI_TESTS: -- this must fail Gate 3, not silently
    credit a pass just because the harness process "didn't crash"."""
    good_code = "def add(a, b):\n    return a + b\n"
    tests = "import candidate, sys\nsys.exit(1)\n"  # dies before any print
    cand = verify_candidate(Candidate(code=good_code), tests=tests)
    assert cand.runs_clean is True, "the candidate alone is fine"
    assert cand.gate_failed == "tests"
    assert cand.tests_passed < cand.tests_total


# ── full agent loop (stub generator) ────────────────────────────────────────────

def _stub(prompt, system="", max_tokens=1000, temperature=0.2, **k):
    p = prompt.lower()
    if "only json" in p:
        return '{"approach":"sum two ints","steps":["def add(a,b)","return a+b"]}'
    if "test harness" in p:
        return ("import candidate, sys\np=0;t=2\n"
                "try:\n    assert candidate.add(2,3)==5; p+=1\nexcept Exception: pass\n"
                "try:\n    assert candidate.add(-1,1)==0; p+=1\nexcept Exception: pass\n"
                "print(f'ELI_TESTS: {p}/{t}')\nsys.exit(0 if p==t else 1)\n")
    if "rejected" in p or "previous attempt feedback" in p:
        return ("def add(a, b):\n    return a + b\n\ndef main():\n    print(add(2, 3))\n\n"
                "if __name__ == '__main__':\n    main()\n")
    return ("def add(a, b):\n    return a - b\n\ndef main():\n    print(add(2, 3))\n\n"
            "if __name__ == '__main__':\n    main()\n")


def test_search_early_exits_on_plateau():
    """Tree search must stop refining when scores stop improving, so an ill-posed
    task can't burn the whole iteration budget."""
    from eli.coding.search import tree_search
    from eli.coding.planner import Plan
    calls = {"n": 0}

    def gen(prompt, system="", **k):
        calls["n"] += 1
        if "test harness" in prompt.lower():
            return "import candidate, sys\nprint('ELI_TESTS: 0/1')\nsys.exit(1)\n"
        return "def f():\n    return 1 / 0\n"  # always crashes → never improves

    r = tree_search("do x", gen, plan=Plan(approach="x", steps=["x"]), language="python",
                    tests=None, beam=2, max_iterations=8, no_improve_patience=2, run_timeout=6)
    assert r.iterations < 8
    assert any(s.get("step") == "early_exit" for s in r.trace)


def test_agent_repairs_via_search_and_records_fix():
    with tempfile.TemporaryDirectory() as td:
        bm = BugMemory(db_path=Path(td) / "bm.sqlite3")
        r = CodeAgent(generate=_stub, bug_memory=bm).solve(
            "Write add(a, b) returning the sum.", beam=2, max_iterations=4)
        assert r.solved, f"expected solved, got score {r.score}"
        assert "a + b" in r.code and "a - b" not in r.code
        assert r.score >= 0.95
        assert bm.stats()["total_fixes"] >= 1   # the repair was remembered


def test_search_prefers_the_node_whose_refinements_improve():
    """UCT: a node's value is what trying from it yielded, not the score it gave itself."""
    from eli.coding.search import _select_ucb
    a = Candidate(code="a", language="python")
    b = Candidate(code="b", language="python")
    a.score = b.score = 0.6
    a.feedback = b.feedback = "fails"
    rewards = {a.id: [0.6, 0.2, 0.1], b.id: [0.6, 0.7, 0.8]}     # from a it got worse; from b, better
    visits = {a.id: 3, b.id: 3}
    assert _select_ucb([a, b], visits, 6, 0.95, 0.0, rewards) is b
    assert _select_ucb([a, b], visits, 6, 0.95, 0.0) is a, "with nothing tried yet the scores alone decide"


# ── solving never writes ELI's source ────────────────────────────────────────────

@pytest.fixture
def in_tree_file(workspace):
    """A small module inside ELI's source tree, as a self-repair task would name one."""
    f = workspace / "adder.py"
    f.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    return f


def _fixing_stub(prompt, system="", max_tokens=1000, temperature=0.2, **k):
    p = prompt.lower()
    if "only json" in p:
        return '{"approach":"correct the sign","steps":["return a + b"]}'
    if "test harness" in p:
        return ("import candidate, sys\np=0;t=1\n"
                "try:\n    assert candidate.add(2,3)==5; p+=1\nexcept Exception: pass\n"
                "print(f'ELI_TESTS: {p}/{t}')\nsys.exit(0 if p==t else 1)\n")
    return "def add(a, b):\n    return a + b\n"


def test_solving_a_fix_task_never_writes_the_named_file(in_tree_file):
    """Every solve ended with a hidden repo-write pass: a task with a fix verb that named an
    in-project file had the best candidate diffed into ELI's live source, with no approval, no
    protected-path check and no tests — including the 'propose-only' self-repair loop."""
    from eli.core.paths import source_root
    rel = in_tree_file.relative_to(Path(source_root())).as_posix()
    before = in_tree_file.read_text()
    with tempfile.TemporaryDirectory() as td:
        r = CodeAgent(generate=_fixing_stub, bug_memory=BugMemory(db_path=Path(td) / "bm.sqlite3")).solve(
            f"Fix the bug in {rel}: add subtracts.", beam=1, max_iterations=1, use_dag=False)
    assert "a + b" in r.code
    assert in_tree_file.read_text() == before
    assert not list(in_tree_file.parent.glob("*.eli_bak*"))
    assert r.patch is None


def test_a_target_file_gets_a_proposal_not_a_write(in_tree_file):
    before = in_tree_file.read_text()
    with tempfile.TemporaryDirectory() as td:
        r = CodeAgent(generate=_fixing_stub, bug_memory=BugMemory(db_path=Path(td) / "bm.sqlite3")).solve(
            "Correct this file and return the complete fixed version.", beam=1, max_iterations=1,
            use_dag=False, target_file=in_tree_file)
    assert r.patch and r.patch["ok"] and r.patch["applied"] is False
    assert r.patch["old"] == before and "a + b" in r.patch["new"]
    assert in_tree_file.read_text() == before


# ── a harness that does not report its own count ─────────────────────────────────

def test_a_harness_without_a_count_is_run_not_credited():
    """A model harness that defined test_* functions but never ran them got a constant
    'ELI_TESTS: 1/1' appended, so every candidate that imported scored as passing its tests."""
    from eli.coding.verification import synthesize_tests
    harness = synthesize_tests("f returns 1", "def f():\n    return 2\n",
                               lambda p, **k: "import candidate\n\ndef test_x():\n    assert candidate.f() == 1\n")
    cand = verify_candidate(Candidate(code="def f():\n    return 2\n"), tests=harness)
    assert cand.tests_total == 1 and cand.tests_passed == 0 and cand.gate_failed == "tests"
    good = verify_candidate(Candidate(code="def f():\n    return 1\n"), tests=harness)
    assert good.tests_total == 1 and good.tests_passed == 1


def test_a_harness_with_nothing_to_run_falls_back_to_the_smoke_harness():
    from eli.coding.verification import synthesize_tests, _deterministic_smoke_harness
    harness = synthesize_tests("x", "def f():\n    return 1\n",
                               lambda p, **k: "import candidate\nprint('looks fine')\n")
    assert harness == _deterministic_smoke_harness()
