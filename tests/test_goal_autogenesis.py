"""Goal autogenesis: ELI turns its own signals into governed goals.

Closes the autonomy gap where the goal store was always empty (create_goal was
operator-only) so the scheduler/goal-tick ticked nothing. Goals are proposal_only,
deduped by a stable id, capped, and thresholded.
"""
import importlib


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("ELI_GOAL_STORE", str(tmp_path / "goals.json"))
    # Reimport so the store path picks up the env each time.
    import eli.planning.goal_store as gs
    importlib.reload(gs)
    import eli.planning.goal_autogenesis as ga
    importlib.reload(ga)
    return ga, gs


def _errors(*names, count=30):
    return [{"type": "recurring_error", "error": n, "count": count} for n in names]


def test_autogenesis_creates_governed_goals_from_signals(monkeypatch, tmp_path):
    ga, gs = _fresh(monkeypatch, tmp_path)
    patterns = [
        {"type": "recurring_error", "error": "LoRA build_job failed", "count": 30},
        {"type": "recurring_error", "error": "index rebuild timed out", "count": 6},
        {"type": "recurring_error", "error": "blip", "count": 2},          # < 5 → filtered
        {"type": "time_habit", "error": "x"},                              # not error → filtered
    ]
    created = ga.propose_goals_from_signals(patterns)
    assert len(created) == 2, created

    goals = gs.load_goals()
    assert len(goals) == 2
    # All governed (proposal_only) — never silent execution.
    assert all(g.autonomy_mode == "proposal_only" for g in goals)
    assert all(ga.AUTO_TAG in g.tags for g in goals)
    # Highest-priority signal (the 30x failure) is the top goal.
    assert "LoRA build_job failed" in max(goals, key=lambda g: g.priority).objective


def test_a_world_model_reading_makes_no_goal_and_one_it_made_is_retired(monkeypatch, tmp_path):
    """Live: "[GOAL_AUTOGENESIS] new goal auto_world_… — Address: self improve", made from the world
    model's autonomy pressure and reflection depth, which name nothing to address."""
    ga, gs = _fresh(monkeypatch, tmp_path)
    assert not hasattr(ga, "_candidates_from_world")
    from eli.planning.goal_models import GoalSpec
    gs.upsert_goal(GoalSpec.from_any({"goal_id": "auto_world_0123456789ab", "title": "Address: self improve",
                                      "objective": "autonomy_pressure=0.67 reflection_depth=0.99", "priority": 0.83,
                                      "cadence_sec": 3600, "autonomy_mode": "proposal_only",
                                      "tags": [ga.AUTO_TAG, "world_driven"], "enabled": True, "status": "active"}))
    assert ga.propose_goals_from_signals([]) == []
    old = {g.goal_id: g for g in gs.load_goals()}["auto_world_0123456789ab"]
    assert old.status == "retired" and "nothing observed" in old.metadata["retired_because"]


def test_autogenesis_is_idempotent(monkeypatch, tmp_path):
    ga, gs = _fresh(monkeypatch, tmp_path)
    first = ga.propose_goals_from_signals(_errors("LoRA build_job failed"))
    second = ga.propose_goals_from_signals(_errors("LoRA build_job failed"))
    assert len(first) == 1
    assert len(second) == 0          # same signal must not spawn a duplicate goal
    assert len(gs.load_goals()) == 1


def test_autogenesis_generates_self_improvement_goals(monkeypatch, tmp_path):
    # Generative (not just failure-reactive): code-health signals about ELI's own
    # code become self-betterment goals, thresholded so noise (small fns, TODOs) is out.
    ga, gs = _fresh(monkeypatch, tmp_path)
    improvements = [
        {"file": "engine.py", "type": "long_function", "function": "process",
         "lines": 450, "suggestion": "split it"},
        {"file": "executor.py", "type": "duplicate_code", "count": 12, "suggestion": "dedup"},
        {"file": "r.py", "type": "long_function", "function": "tiny", "lines": 80},  # < 150 → out
        {"file": "x.py", "type": "todos", "count": 9},                               # noise → out
    ]
    created = ga.propose_goals_from_signals(improvements=improvements)
    assert len(created) == 2
    goals = gs.load_goals()
    assert all("self_improve" in g.tags for g in goals)
    assert all(g.autonomy_mode == "proposal_only" for g in goals)


def test_autogenesis_respects_cap(monkeypatch, tmp_path):
    ga, gs = _fresh(monkeypatch, tmp_path)
    # Many distinct high-priority signals, but the cap bounds active auto goals.
    patterns = _errors(*[f"failure {i}" for i in range(ga.MAX_AUTO_GOALS + 4)])
    total = 0
    for _ in range(10):  # repeated ticks
        total += len(ga.propose_goals_from_signals(patterns))
    assert total <= ga.MAX_AUTO_GOALS
    assert len(gs.load_goals()) <= ga.MAX_AUTO_GOALS


def test_a_goal_whose_signal_has_gone_is_retired_and_frees_its_place(monkeypatch, tmp_path):
    """Nothing ever closed a self-set goal: once six existed no other could come, and finished or
    long-quiet goals were proposed again every cadence."""
    ga, gs = _fresh(monkeypatch, tmp_path)
    old = _errors(*[f"old failure {i}" for i in range(ga.MAX_AUTO_GOALS)])
    assert len(ga.propose_goals_from_signals(old, max_new=ga.MAX_AUTO_GOALS)) == ga.MAX_AUTO_GOALS
    new = _errors("new failure", count=40)
    assert ga.propose_goals_from_signals(new) == [], "full: the old signals are still within their cadence"
    import time as _time
    later = _time.time() + 7201                            # past the failure goals' own cadence
    monkeypatch.setattr(_time, "time", lambda: later)
    created = ga.propose_goals_from_signals(new)
    assert len(created) == 1
    by_id = {g.goal_id: g for g in gs.load_goals()}
    retired = [g for g in by_id.values() if g.status == "retired"]
    assert len(retired) == ga.MAX_AUTO_GOALS and all(g.metadata.get("retired_because") for g in retired)
    assert by_id[created[0]].status == "active"


def test_a_goal_whose_signal_keeps_coming_stays(monkeypatch, tmp_path):
    ga, gs = _fresh(monkeypatch, tmp_path)
    patterns = _errors("LoRA build_job failed")
    (gid,) = ga.propose_goals_from_signals(patterns)
    import time as _time
    later = _time.time() + 7201
    monkeypatch.setattr(_time, "time", lambda: later)
    ga.propose_goals_from_signals(patterns)
    assert {g.goal_id: g.status for g in gs.load_goals()}[gid] == "active"


def test_a_goal_the_work_finished_no_longer_holds_a_place(monkeypatch, tmp_path):
    ga, gs = _fresh(monkeypatch, tmp_path)
    patterns = _errors(*[f"failure {i}" for i in range(ga.MAX_AUTO_GOALS)])
    ga.propose_goals_from_signals(patterns, max_new=ga.MAX_AUTO_GOALS)
    goal = gs.load_goals()[0]
    from eli.planning import work_loop
    work_loop._persist(goal, {"status": "done", "steps": [], "attempts": 1, "no_progress": 0})
    assert {g.goal_id: g.status for g in gs.load_goals()}[goal.goal_id] == "done"
    assert len(ga.propose_goals_from_signals(patterns + _errors("a new failure", count=40))) == 1
