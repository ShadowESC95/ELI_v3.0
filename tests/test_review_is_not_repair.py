"""A review cycle eases pressure; only a verified repair counts as a repair."""
from eli.world import world_event_bus as bus


def _capture(monkeypatch):
    fired = []
    monkeypatch.setattr(bus, "fire_world_event", lambda t, s, m, p=None: fired.append(t))
    return fired


def test_an_empty_review_is_review_completed_only(monkeypatch):
    fired = _capture(monkeypatch)
    bus.fire_improvement_event(0, 0)
    assert fired == ["review_completed"]


def test_proposals_are_not_repairs(monkeypatch):
    fired = _capture(monkeypatch)
    bus.fire_improvement_event(2, 5)
    assert fired == ["improvement_proposal", "review_completed"]


def test_a_verified_repair_fires_repair_completed(monkeypatch):
    fired = _capture(monkeypatch)
    bus.fire_improvement_event(1, 3, repaired=1)
    assert fired[-1] == "repair_completed" and "review_completed" in fired


def _pressure_after(event_type):
    from eli.world.agency.autonomy_engine import EliWorldAutonomyEngine
    from eli.world.core.schemas import EliWorldState, WorldEvent
    engine = EliWorldAutonomyEngine.__new__(EliWorldAutonomyEngine)
    state = EliWorldState()
    state.awareness.repair_pressure = 0.8
    engine._update_awareness_from_event(state, WorldEvent(event_type, "test", "x"))
    return state.awareness.repair_pressure


def test_a_review_eases_repair_pressure_less_than_a_repair():
    review, repair = _pressure_after("review_completed"), _pressure_after("repair_completed")
    assert repair < review < 0.8


def test_a_tool_that_worked_is_work_done_and_one_that_failed_says_what_went_wrong(monkeypatch):
    said = []
    monkeypatch.setattr(bus, "fire_world_event", lambda t, s, m, p=None: said.append((t, m)))
    bus.fire_tool_result_event("web_search", True)
    bus.fire_tool_result_event("web_search", False, detail="the  network\nis off")
    bus.fire_tool_result_event("open_app", False)
    assert said == [("task_completed", "WEB_SEARCH worked."), ("error_detected", "WEB_SEARCH failed: the network is off"),
                    ("error_detected", "OPEN_APP failed.")]


def test_every_action_reports_to_the_world_but_talking_is_not_a_tool(monkeypatch):
    from eli.execution import executor_enhanced as executor
    import eli.memory as memory
    said = []
    monkeypatch.setattr(bus, "fire_world_event", lambda t, s, m, p=None: said.append((t, m)))
    monkeypatch.setattr(memory, "get_agent_memory", lambda: type("M", (), {"log_failure": lambda *a, **k: None})())
    executor._action_post_dispatch("NOTE_SAVE", {"text": "milk"}, {"ok": True, "response": "Saved."})
    executor._action_post_dispatch("CHAT", {}, {"ok": True, "response": "Hello."})
    executor._action_post_dispatch("OPEN_APP", {"app": "Calculator"}, {"ok": False, "error": "no such app"})
    executor._action_post_dispatch("WEB_SEARCH", {}, {"ok": False, "error": "empty_query"})
    assert said == [("task_completed", "NOTE_SAVE worked."), ("error_detected", "OPEN_APP failed: no such app")], \
        "an empty search is not a fault, and a chat reply is not a tool"
