"""SELF_IMPROVE routed through the coding agent (decompose→solve→verify, propose-only)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

import eli.runtime.self_improvement as SI


@pytest.fixture(autouse=True)
def _jobs(monkeypatch):
    """Background submissions are recorded, not run: a real job would reach the coding agent
    after the test's patches are gone."""
    from eli.runtime import background_tasks
    submitted = []

    class _Queue:
        def submit(self, name, fn, *args, **kwargs):
            submitted.append(name)
            return len(submitted)

    monkeypatch.setattr(background_tasks, "get_background_tasks", lambda: _Queue())
    return submitted


def _fake_cr(solved=True, score=0.97):
    return MagicMock(solved=solved, score=score, plan={"approach": "targeted patch"},
                     message="solved", code="def f():\n    return 1\n", patch=None)


@pytest.fixture
def contained(monkeypatch):
    """Self-repair only runs model-written code where it can be contained; most tests are about
    what happens when it can."""
    import eli.coding.sandbox as SB
    monkeypatch.setattr(SB, "containment", lambda: {"contained": True, "notes": []}, raising=False)


@pytest.fixture
def fresh_engine(tmp_path):
    """An engine on its own agent database: self-repair steps are checkpointed there, so a step one
    test finished would be resumed, not run, in the next."""
    from eli.memory import Memory
    return SI.SelfImprovementEngine(memory=Memory(db_path=str(tmp_path / "agent.sqlite3"), db_type="agent"))


def _tb(rel, line=3, exc="KeyError: 'k'"):
    from eli.core.paths import source_root
    return (f'Traceback (most recent call last):\n  File "{source_root()}/{rel}", line {line}, in f\n'
            f"    x = d['k']\n{exc}")


def test_propose_via_agent_orchestrates_failures(contained, fresh_engine):
    eng = fresh_engine
    fails = [{"error": _tb("eli/core/dag.py"), "user_input": "do x",
              "context": '{"action": "SOME_ACTION", "args": {"n": 1}}'},
             {"error": _tb("eli/coding/budget.py", exc="TypeError: bad"), "user_input": "do y"}]
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch("eli.coding.agent.CodeAgent.solve", return_value=_fake_cr()):
            r = eng.propose_via_agent(max_items=2)
    assert r["ok"] and r["count"] == 2
    assert all(p["verified"] for p in r["proposals"])
    # ran as a DAG layer (orchestrated, not a sequential loop)
    assert r["orchestration"]["layers"] == [["fix_0", "fix_1"]]
    assert {p["file"] for p in r["proposals"]} == {"eli/core/dag.py", "eli/coding/budget.py"}


def test_complaints_and_sentences_never_become_coding_tasks(contained, fresh_engine):
    """The proactive loop ran the coding agent on the person's own complaints: note_turn_anomalies
    logs each one with the sentence as user_input, and the agent was told to 'fix the bug' in it.
    Only a failure with a traceback into ELI's source is a coding task, and the task is built from
    the action, its arguments and the error — never the person's words."""
    eng = fresh_engine
    said = "you useless thing, that search was rubbish"
    fails = [{"error": "anomaly (rejected): the person rejected the reply", "user_input": said,
              "source": "anomaly", "context": '{"action": "WEB_SEARCH"}'},
             {"error": "Low confidence", "user_input": said, "context": '{"intent": {"action": "CHAT"}}'},
             {"error": "background job failed: boom", "user_input": said, "source": "anomaly"},
             {"error": _tb("eli/core/dag.py"), "user_input": said, "command": said,
              "context": '{"action": "SOME_ACTION", "args": {"n": 1}}'}]
    tasks = []

    def _solve(self, task, **kw):
        tasks.append(task)
        return _fake_cr()
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch("eli.coding.agent.CodeAgent.solve", _solve):
            r = eng.propose_via_agent(max_items=3)
    assert r["count"] == 1 and len(tasks) == 1
    assert said not in tasks[0] and "rubbish" not in tasks[0]
    assert "SOME_ACTION" in tasks[0] and "KeyError" in tasks[0]
    assert all(said not in str(p) for p in r["proposals"])


def test_nothing_runs_where_model_written_code_cannot_be_contained(monkeypatch):
    import eli.coding.sandbox as SB
    monkeypatch.setattr(SB, "containment", lambda: {"contained": False, "notes": ["no sandbox here"]},
                        raising=False)
    eng = SI.get_self_improvement()
    with patch.object(eng, "analyze_failures", return_value=[{"error": _tb("eli/core/dag.py")}]):
        with patch("eli.coding.agent.CodeAgent.solve", side_effect=AssertionError("must not run")):
            r = eng.propose_via_agent()
    assert r["ok"] and r["proposals"] == [] and "contained" in r["reason"]


def test_the_callers_thread_is_not_left_marked_background(contained, fresh_engine):
    """run_graph with one worker runs the task on the caller's thread, and the marker it set was
    never restored: the same turn's later model calls were capped, deferred or preempted."""
    from eli.cognition.gguf_inference import is_background_inference, set_background_inference
    eng = fresh_engine
    seen = []

    def _solve(self, task, **kw):
        seen.append(is_background_inference())
        return _fake_cr()
    set_background_inference(False)
    with patch.object(eng, "analyze_failures", return_value=[{"error": _tb("eli/core/dag.py")}]):
        with patch("eli.coding.agent.CodeAgent.solve", _solve):
            eng.propose_via_agent(max_items=1)
    assert seen == [True], "the agent's calls are background work"
    assert is_background_inference() is False


def test_a_whole_file_candidate_is_kept_for_approval_not_applied(contained, tmp_path):
    """The 'propose-only' loop wrote the agent's code into the failing file. Now the candidate is
    recorded, unchecked, for the owner; approving it checks it on a copy and applies it only if
    it fixes the failure."""
    import shutil
    import tempfile
    from pathlib import Path
    from eli.core.paths import source_root
    d = Path(tempfile.mkdtemp(prefix="selfrepair_", dir=str(Path(source_root()) / "artifacts")))
    try:
        target = d / "calc.py"
        target.write_text("def add(a, b):\n    d = {}\n    return d['k']\n", encoding="utf-8")
        rel = target.relative_to(Path(source_root())).as_posix()
        before = target.read_text()
        fixed = "def add(a, b):\n    return a + b\n"
        from eli.coding.patches import propose_file_patch
        cr = _fake_cr()
        tasks = {}

        def _solve(self, task, **kw):
            tasks["target_file"] = kw.get("target_file")
            cr.patch = propose_file_patch(kw["target_file"], fixed)
            return cr
        eng = SI.get_self_improvement()
        with patch.object(eng, "analyze_failures", return_value=[{"error": _tb(rel)}]):
            with patch("eli.coding.agent.CodeAgent.solve", _solve):
                r = eng.propose_via_agent(max_items=1)
        assert Path(tasks["target_file"]) == target
        cid = r["proposals"][0]["candidate_id"]
        assert target.read_text() == before and not list(d.glob("*.eli_bak*"))
        assert {c["id"]: c for c in eng.candidate_archive(limit=200)}.get(cid) is None, "no verdict until checked"

        # approval: the copy-of-the-tree check decides
        with patch.object(eng, "verify_in_workspace", return_value={"verdict": "unchanged", "cost_s": 0.0}):
            res = eng.approve_candidate(cid)
        assert res["applied"] is False and target.read_text() == before

        cid2 = eng.record_candidate({"file": rel, "old": before, "new": fixed, "description": "fix add"})
        with patch.object(eng, "verify_in_workspace", return_value={"verdict": "fixed", "cost_s": 0.0}), \
                patch.object(SI, "_run_targeted_tests", return_value=(True, True, "targeted tests passed")):
            res = eng.approve_candidate(cid2)
        assert res["applied"] is True and target.read_text() == fixed
        assert {c["id"]: c for c in eng.candidate_archive(limit=200)}[cid2]["status"] == "adopted"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_propose_via_agent_no_failures():
    eng = SI.get_self_improvement()
    with patch.object(eng, "analyze_failures", return_value=[]):
        r = eng.propose_via_agent()
    assert r["ok"] and r["proposals"] == [] and "no recent failure" in r.get("reason", "")


def test_build_fix_task_inlines_named_file():
    eng = SI.get_self_improvement()
    task = eng._build_fix_task({"error": 'File "eli/core/dag.py", line 1\nBoom', "user_input": "x"})
    assert "Fix the bug" in task and "eli/core/dag.py" in task
    assert "User input" not in task


def test_the_default_self_improve_never_starts_the_coding_agent():
    """'self improve' (mode analyze) ran analyze_and_improve(repair=True) inline: the coding agent,
    up to its 600 s budget, on the chat turn's thread."""
    from eli.execution.executor_enhanced import execute
    eng = SI.get_self_improvement()
    import uuid
    fails = [{"error": f"boom-{uuid.uuid4().hex[:8]}", "user_input": "do x"}]
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch.object(eng, "propose_via_agent", return_value={"ok": True, "proposals": []}) as spy:
            with patch("eli.cognition.gguf_inference.is_loaded", return_value=True):
                r = execute("SELF_IMPROVE", {})
    assert r["ok"], r
    assert not spy.called, "the coding agent must not run for analyze"


def test_self_improve_action_propose_mode_runs_inline_when_asked():
    from eli.execution.executor_enhanced import execute
    fails = [{"error": "boom", "user_input": "do x"}]
    eng = SI.get_self_improvement()
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch("eli.coding.agent.CodeAgent.solve", return_value=_fake_cr()):
            r = execute("SELF_IMPROVE", {"mode": "propose", "_no_background": True})
    assert r["ok"] and r.get("evidence_source") == "coding_agent"
    assert "fix proposals" in r["content"].lower()


def test_self_improve_action_propose_mode_is_backgrounded(_jobs):
    """propose mode returns at once with a job id, never runs inline: the coding agent fans out
    to tens of sequential 30-500 s model calls with nothing shown until it returns, and running
    it on the chat turn held the model for hours."""
    from eli.execution.executor_enhanced import execute
    r = execute("SELF_IMPROVE", {"mode": "propose"})
    assert r["ok"] and r.get("evidence_source") == "coding_agent"
    assert r.get("background") is True and r.get("job_id") == 1 and len(_jobs) == 1
    assert "background" in r["content"].lower()


def test_asking_for_it_now_keeps_it_in_the_turn(_jobs):
    from eli.execution.executor_enhanced import execute
    eng = SI.get_self_improvement()
    with patch.object(eng, "analyze_failures", return_value=[]):
        r = execute("SELF_IMPROVE", {"mode": "propose",
                                     "_raw_user_text": "propose fixes now, in the foreground"})
    assert not _jobs and r.get("background") is not True


def test_analyze_mode_never_invokes_the_coding_agent():
    """The regression guard for the 2-hour freeze.

    analyze_and_improve() used to call the coding agent whenever the model happened
    to be loaded, so a read-sounding 'analyze' detonated the full repair stack."""
    eng = SI.get_self_improvement()
    fails = [{"error": "boom", "user_input": "do x"}]
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch.object(eng, "propose_via_agent",
                          side_effect=AssertionError("coding agent must not run for analyze")):
            with patch("eli.cognition.gguf_inference.is_loaded", return_value=True):
                out = eng.analyze_and_improve()
    assert out["proposals_made"] == 0


def test_repair_opt_in_still_reaches_the_coding_agent():
    """repair=True must still reach the agent — the fix bounds the path, not removes it."""
    import uuid
    eng = SI.get_self_improvement()
    # A unique failure so it can't be suppressed as already-investigated.
    fails = [{"error": f"boom-{uuid.uuid4().hex[:8]}", "user_input": "do x"}]
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch.object(eng, "propose_via_agent",
                          return_value={"ok": True, "proposals": []}) as spy:
            with patch("eli.cognition.gguf_inference.is_loaded", return_value=True):
                eng.analyze_and_improve(repair=True)
    assert spy.called, "repair=True must invoke the coding-agent proposer"


def test_self_improve_detects_propose_intent_from_text():
    from eli.execution.executor_enhanced import execute
    eng = SI.get_self_improvement()
    with patch.object(eng, "analyze_failures", return_value=[]):
        # default mode=analyze, but the raw text asks to propose fixes → routes to propose
        r = execute("SELF_IMPROVE",
                    {"_raw_user_text": "improve your code: propose verified fixes for the failing tests"})
    assert r["ok"] and r.get("evidence_source") == "coding_agent"


def test_the_routes_hand_the_words_to_the_executor():
    """The executor reads "propose" / "self fix" / "in the foreground" from the user's words;
    the routes passed none, so "improve yourself and propose verified fixes" only analysed."""
    from eli.execution.router_enhanced import route
    for text in ("improve yourself and propose verified fixes", "generate patch"):
        r = route(text)
        assert r["action"] == "SELF_IMPROVE" and r["args"].get("_raw_user_text") == text


def test_engine_failures_go_to_the_one_failure_record(tmp_path, monkeypatch):
    """SelfImprovementEngine.log_failure kept a second store (error_tracking) that nothing but its
    own counter read; lessons looked there and found no action failures. One record, counted
    from it."""
    import sqlite3
    from eli.memory import Memory
    db = tmp_path / "agent.sqlite3"
    eng = SI.SelfImprovementEngine(memory=Memory(db_path=str(db), db_type="agent"))
    notices = []
    monkeypatch.setattr(SI, "_push_self_heal_notice", notices.append)
    monkeypatch.setattr(SI.SelfImprovementEngine, "_background_analyze", lambda self, *a, **k: None)
    for _ in range(5):
        eng.log_failure("REPORT_BUILDER build", error="KeyError: 'title'", context={"action": "REPORT_BUILD"})
    conn = sqlite3.connect(db)
    assert conn.execute("select occurrence_count from failures where error = ?", ("KeyError: 'title'",)).fetchone() == (5,)
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    if "error_tracking" in tables:
        assert conn.execute("select count(*) from error_tracking").fetchone() == (0,)
    assert notices and notices[-1]["count"] == 5


def test_an_interrupted_self_repair_job_resumes_after_its_finished_steps(contained, tmp_path):
    """Each failure is one step; a finished step is checkpointed, so running the job again (after a
    restart cut it off) redoes only what had not finished."""
    from eli.memory import Memory
    eng = SI.SelfImprovementEngine(memory=Memory(db_path=str(tmp_path / "agent.sqlite3"), db_type="agent"))
    fails = [{"id": 1, "occurrence_count": 2, "error": _tb("eli/core/dag.py")},
             {"id": 2, "occurrence_count": 1, "error": _tb("eli/coding/budget.py", exc="TypeError: bad")}]
    solved = []

    def _solve_then_die(self, task, **kw):
        if "budget.py" in task:
            raise RuntimeError("the job was cut off here")
        solved.append("dag")
        return _fake_cr()
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch("eli.coding.agent.CodeAgent.solve", _solve_then_die):
            first = eng.propose_via_agent(max_items=2)
    assert [p["file"] for p in first["proposals"]] == ["eli/core/dag.py"]

    def _solve(self, task, **kw):
        solved.append("budget" if "budget.py" in task else "dag")
        return _fake_cr()
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch("eli.coding.agent.CodeAgent.solve", _solve):
            second = eng.propose_via_agent(max_items=2)
    assert solved == ["dag", "budget"], "the finished step must not run again"
    assert {p["file"] for p in second["proposals"]} == {"eli/core/dag.py", "eli/coding/budget.py"}
    assert any(p.get("resumed") for p in second["proposals"])

    # the same failure recurring is a new step
    fails[0]["occurrence_count"] = 3
    with patch.object(eng, "analyze_failures", return_value=fails):
        with patch("eli.coding.agent.CodeAgent.solve", _solve):
            eng.propose_via_agent(max_items=2)
    assert solved[2:] == ["dag"]


def test_a_closed_self_repair_goal_is_not_reopened(contained):
    """Every pass upserted the selfrepair goal as active, reopening goals the work loop had closed."""
    import hashlib
    from eli.planning.goal_store import load_goals, upsert_goal
    eng = SI.get_self_improvement()
    closed_label, new_label = "SOME_ACTION: KeyError: 'closed'", "OTHER_ACTION: KeyError: 'new'"
    gid = "selfrepair_" + hashlib.sha1(closed_label.encode()).hexdigest()[:12]
    upsert_goal({"goal_id": gid, "title": "Self-repair: done already", "status": "done", "enabled": True,
                 "autonomy_mode": "proposal_only"})
    proposals = [{"failure": closed_label, "verified": False, "approach": "a"},
                 {"failure": new_label, "verified": False, "approach": "b"}]
    with patch.object(eng, "propose_via_agent", return_value={"ok": True, "proposals": proposals}):
        made = eng._generate_and_persist_fix_proposals(max_items=2)
    goals = {g.goal_id: g for g in load_goals()}
    assert goals[gid].status == "done" and goals[gid].title == "Self-repair: done already"
    new_gid = "selfrepair_" + hashlib.sha1(new_label.encode()).hexdigest()[:12]
    assert goals[new_gid].status == "active" and made == 1
