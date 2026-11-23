"""
test_05_planning.py
===================
Tests for eli.planning — goals, habits, proposals, autonomy, proactive daemon.
"""


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
