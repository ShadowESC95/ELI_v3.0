"""The world feed for spatial clients (eli/world/spatial_feed.py).

Two halves. The conversion from world state to the small spatial message is a
pure function and runs in the normal suite. The routes need real fastapi and
pydantic, which the suite conftest mocks, so those tests skip there and run on
the clean lane, the same way tests/test_api_server.py does:

    .venv/bin/python -m pytest tests/test_world_spatial_feed.py --noconftest

The routes are mounted on a bare app with the world stubbed, so nothing here
reads or writes the real world state.
"""
from __future__ import annotations

import pytest

from eli.world import spatial_feed

WORLD = {
    "avatar": {
        "name": "ELI", "room": "workshop", "x": 220.0, "y": 0.0, "posture": "leaning",
        "expression": "focused", "activity": "repairing", "attention_target": None,
        "movement_intent": "none", "persona_tint": {"precision": 0.85}, "timestamp": 1.0,
    },
    "awareness": {"focus": 0.9, "uncertainty": 0.1, "evidence_confidence": 0.8,
                  "tool_activity": 0.7, "timestamp": 1.0},
    "objects": {
        "bench": {"object_id": "bench", "name": "Workbench", "object_type": "workbench",
                  "room": "workshop", "x": 10.0, "y": 5.0, "importance": 0.9,
                  "symbolic_meaning": "where things get fixed", "retired": False},
        "old": {"object_id": "old", "name": "Old thing", "object_type": "relic",
                "room": "core_room", "retired": True},
    },
    "events": [{"event_type": "tool_result", "source": "executor", "summary": "ran the tests"}],
    "actions": [{"action_type": "journal_entry"}] * 500,
    "timestamp": 123.0,
}


# ── the conversion ───────────────────────────────────────────────────────────

def test_message_carries_room_avatar_and_awareness():
    msg = spatial_feed.spatial_state(WORLD)
    assert msg["type"] == "eli_world_state"
    assert msg["version"] == spatial_feed.FEED_VERSION
    assert msg["room"] == "workshop"
    assert msg["avatar"]["activity"] == "repairing"
    assert msg["avatar"]["persona_tint"] == {"precision": 0.85}
    assert msg["awareness"]["tool_activity"] == 0.7
    assert "timestamp" not in msg["awareness"]      # numbers a client draws, nothing else


def test_retired_objects_and_the_logs_stay_behind():
    msg = spatial_feed.spatial_state(WORLD)
    assert [o["id"] for o in msg["objects"]] == ["bench"]
    assert msg["objects"][0]["kind"] == "workbench"
    assert "actions" not in msg and "events" not in msg
    assert msg["widgets"]["last_event"] == "ran the tests"


def test_an_empty_world_still_gives_a_usable_message():
    msg = spatial_feed.spatial_state({})
    assert msg["room"] == "core_room"
    assert msg["avatar"] == {} and msg["awareness"] == {} and msg["objects"] == []


def test_digest_ignores_time_but_not_content():
    a = spatial_feed.spatial_state(WORLD)
    b = spatial_feed.spatial_state({**WORLD, "timestamp": 999.0})
    moved = spatial_feed.spatial_state({**WORLD, "avatar": {**WORLD["avatar"], "room": "core_room"}})
    assert spatial_feed._digest(a) == spatial_feed._digest(b)
    assert spatial_feed._digest(a) != spatial_feed._digest(moved)


def test_only_listed_events_reach_the_world(monkeypatch):
    fired = []
    import eli.world.world_event_bus as bus
    monkeypatch.setattr(bus, "fire_world_event", lambda *a: fired.append(a))

    spatial_feed.record_client_event("vr_room_entered", "entered the workshop", {"room": "workshop"})
    assert fired == [("vr_room_entered", "spatial_client", "entered the workshop", {"room": "workshop"})]

    with pytest.raises(ValueError):
        spatial_feed.record_client_event("create_object", "make me a door")
    with pytest.raises(ValueError):
        spatial_feed.record_client_event("vr_user_spoke", "x", {"blob": "y" * 5000})
    assert len(fired) == 1


def test_a_long_summary_is_cut_to_a_status_line(monkeypatch):
    fired = []
    import eli.world.world_event_bus as bus
    monkeypatch.setattr(bus, "fire_world_event", lambda *a: fired.append(a))
    spatial_feed.record_client_event("vr_user_spoke", "word " * 200)
    assert len(fired[0][2]) == 200


# ── the routes (clean lane only) ─────────────────────────────────────────────

def _client(monkeypatch, *, ws_allowed=True, world=None):
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(spatial_feed.build_router(lambda: None, lambda: None,
                                                     lambda ws: ws_allowed))
        client = TestClient(app)
    except Exception as e:  # mocked pydantic under the suite conftest
        pytest.skip(f"fastapi not usable in-process ({type(e).__name__}); run with --noconftest")
    box = {"world": world or WORLD}
    monkeypatch.setattr(spatial_feed, "_load_world", lambda: box["world"])
    monkeypatch.setattr(spatial_feed, "POLL_SECONDS", 0.05)
    return client, box


def test_state_route_returns_the_message(monkeypatch):
    client, _ = _client(monkeypatch)
    r = client.get("/v1/world/state")
    assert r.status_code == 200
    assert r.json()["room"] == "workshop"


def test_event_route_accepts_listed_and_refuses_the_rest(monkeypatch):
    client, _ = _client(monkeypatch)
    fired = []
    import eli.world.world_event_bus as bus
    monkeypatch.setattr(bus, "fire_world_event", lambda *a: fired.append(a))

    ok = client.post("/v1/world/event", json={"event_type": "vr_session_started", "summary": "hi"})
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    bad = client.post("/v1/world/event", json={"event_type": "rollback"})
    assert bad.status_code == 400
    assert [f[0] for f in fired] == ["vr_session_started"]


def test_socket_sends_on_connect_then_only_on_change(monkeypatch):
    client, box = _client(monkeypatch)
    fired = []
    import eli.world.world_event_bus as bus
    monkeypatch.setattr(bus, "fire_world_event", lambda *a: fired.append(a))

    with client.websocket_connect("/v1/world/ws") as ws:
        first = ws.receive_json()
        assert first["room"] == "workshop"

        # a report from the client is taken, and with nothing changed nothing is re-sent
        ws.send_json({"type": "vr_event", "event_type": "vr_room_entered",
                      "summary": "entered", "payload": {"room": "core_room"}})
        ws.send_json({"type": "vr_event", "event_type": "rollback"})     # not listed: dropped
        ws.send_text("not json")                                        # malformed: dropped

        box["world"] = {**WORLD, "avatar": {**WORLD["avatar"], "room": "debug_basement"}}
        second = ws.receive_json()
        assert second["room"] == "debug_basement"

    assert [f[0] for f in fired] == ["vr_room_entered"]


def test_socket_is_refused_when_the_gate_says_no(monkeypatch):
    client, _ = _client(monkeypatch, ws_allowed=False)
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/v1/world/ws"):
            pass
    assert closed.value.code == 4401
