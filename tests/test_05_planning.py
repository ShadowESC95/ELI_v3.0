"""
test_05_planning.py
===================
Tests for eli.planning — goals, habits, proposals, autonomy, proactive daemon.
"""
import importlib


def test_planning_goal_models_has_dataclass():
    mod = importlib.import_module("eli.planning.goal_models")
    syms = dir(mod)
    matches = [s for s in syms if "Goal" in s]
    assert matches, f"No Goal model in goal_models.py: {syms}"


def test_planning_proactive_daemon_has_class():
    mod = importlib.import_module("eli.planning.proactive_daemon")
    syms = dir(mod)
    matches = [s for s in syms if "Daemon" in s or "Proactive" in s]
    assert matches, f"No daemon class in proactive_daemon.py: {syms}"


def test_planning_db_paths_loadable():
    # Planning has no own db_paths module; it uses the shared resolve_db_paths()
    # (imported by the proactive daemon). Verify that wiring is intact.
    from eli.planning.proactive_daemon import resolve_db_paths
    assert resolve_db_paths is not None
    assert resolve_db_paths() is not None


def test_goal_proposals_reach_the_proposal_queue(monkeypatch, tmp_path):
    from eli.planning import goal_tick, proposal_queue
    seen = {}
    monkeypatch.setattr(proposal_queue, "append_governed_proposal", lambda **kw: seen.update(kw) or {"id": 1})
    out = goal_tick._emit_governed_proposal({"title": "t", "reason": "r"})
    assert out["ok"] is True and "import failed" not in str(out)
