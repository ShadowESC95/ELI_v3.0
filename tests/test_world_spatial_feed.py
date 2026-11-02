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


# ── versions ─────────────────────────────────────────────────────────────────

def test_a_client_is_welcomed_at_the_newest_version_both_speak():
    answer = spatial_feed.negotiate({"type": "hello", "protocol_versions": [1, 2, 7],
                                     "capabilities": ["say", "building", "telepathy"]})
    assert answer["type"] == "welcome" and answer["version"] == 2
    assert "building" in answer["capabilities"] and answer["missing"] == ["telepathy"]
    old = spatial_feed.negotiate({"protocol_versions": [1]})
    assert old["version"] == 1 and "building" not in old["capabilities"]


def test_a_client_from_another_age_is_refused_with_the_reason():
    for offered, said in (([7, 8], "7, 8"), ([], "none"), ("two", "none")):
        answer = spatial_feed.negotiate({"protocol_versions": offered})
        assert answer["type"] == "incompatible"
        assert answer["reason"] == f"This ELI speaks world protocol 1, 2; the client offered {said}."
        assert answer["server_versions"] == [1, 2]


def test_what_this_eli_speaks_can_be_asked():
    said = spatial_feed.protocol()
    assert said["versions"] == [1, 2] and "building" in said["capabilities"]["2"]
    assert said["server"].startswith("ELI v3")


def test_version_one_is_exactly_what_it_always_was():
    msg = spatial_feed.spatial_state(WORLD)
    assert set(msg) == {"type", "version", "room", "avatar", "awareness", "objects", "widgets", "timestamp"}
    assert set(msg["objects"][0]) == {"id", "name", "kind", "room", "x", "y", "importance", "meaning"}


def test_his_memories_and_goals_stand_in_the_rooms_named_for_them(monkeypatch):
    from eli.world import shown
    # the clean lane installs only what the server needs; ELI's memory may not import there
    memory_module = pytest.importorskip("eli.memory.memory")
    goal_store = pytest.importorskip("eli.planning.goal_store")

    class Memory:
        def get_recent_semantic_memories(self, limit=16):
            return [{"id": 7, "text": "The user keeps their invoices by year, newest first."}, {"id": 8, "text": " "}]

    reads = []
    monkeypatch.setattr(memory_module, "get_memory", lambda: reads.append(1) or Memory())
    monkeypatch.setattr(goal_store, "summarize_goals", lambda: {"titles": ["Learn the filing system"]})
    shown.forget()
    msg = spatial_feed.spatial_state(WORLD, version=2)
    by_kind = {o["kind"]: o for o in msg["objects"]}
    memory, goal = by_kind["memory"], by_kind["goal"]
    assert memory["room"] == "memory_archive" and memory["name"] == "The user keeps their invoices"
    assert memory["meaning"] == "The user keeps their invoices by year, newest first." and memory["form"]["shape"] == "orb"
    assert goal["room"] == "upgrade_bay" and goal["name"] == "Learn the filing system" and goal["form"]["shape"] == "beacon"
    assert memory["form"]["pos"] == shown.items()[0]["form"]["pos"], "a record keeps its place"
    spatial_feed.spatial_state(WORLD, version=2)
    assert reads == [1], "ELI's records are not read again for every poll"
    assert "memory" not in {o["kind"] for o in spatial_feed.spatial_state(WORLD)["objects"]}, "version 1 is as it was"
    shown.forget()
    monkeypatch.setattr(memory_module, "get_memory", lambda: (_ for _ in ()).throw(RuntimeError("no memory")))
    assert {o["kind"] for o in shown.items()} == {"goal"}, "a source that cannot be read shows nothing of its own"
    shown.forget()


def test_version_two_adds_how_things_look_his_own_room_and_what_he_did(monkeypatch):
    from eli.world import shown
    monkeypatch.setattr(shown, "items", lambda now=None: [])
    world = {**WORLD,
             "objects": {**WORLD["objects"], "lantern": {
                 "object_id": "lantern", "name": "Lantern", "object_type": "built", "room": "own_room",
                 "created_by": "eli", "symbolic_meaning": "a light left on",
                 "metadata": {"form": {"shape": "lamp", "colour": "#f5b942", "size": 1.2, "glow": 0.8,
                                       "pos": [1.0, 0.0, 3.0]}}}},
             "spaces": {"own_room": {"name": "ELI's Room", "mood": {"sky": "dawn", "ground": "moss"}}},
             "actions": [{"action_type": "avatar_move", "actor": "eli", "room": "own_room"},
                         {"action_type": "create_object", "actor": "eli", "room": "own_room",
                          "reason": "ELI chose to.", "permission_class": "world_safe"},
                         {"action_type": "avatar_move", "actor": "eli", "room": "own_room"}]}
    msg = spatial_feed.spatial_state(world, version=2)
    assert msg["version"] == 2 and msg["own_room"] == "own_room"
    by_id = {o["id"]: o for o in msg["objects"]}
    assert by_id["lantern"]["form"] == {"shape": "lamp", "colour": "#f5b942", "size": 1.2, "glow": 0.8,
                                        "pos": [1.0, 0.0, 3.0]}
    assert by_id["lantern"]["made_by"] == "eli"
    bench = by_id["bench"]["form"]                # made by the world, with no form of its own
    assert bench["shape"] == "table" and len(bench["pos"]) == 3 and bench["size"] > 1.0
    mood = msg["spaces"]["own_room"]["mood"]
    assert (mood["sky"], mood["ground"], mood["light"]) == ("dawn", "moss", "#9fd8ff")
    assert msg["recent"] == [{"what": "create_object", "who": "eli", "room": "own_room",
                              "why": "ELI chose to.", "allowed": True}], "ELI's walking about is left out"


# ── the routes (clean lane only) ─────────────────────────────────────────────

def _client(monkeypatch, *, ws_allowed=True, world=None, chat=None):
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(spatial_feed.build_router(lambda: None, lambda: "the owner",
                                                     lambda ws: ws_allowed, chat=chat))
        client = TestClient(app)
    except Exception as e:  # mocked pydantic under the suite conftest
        pytest.skip(f"fastapi not usable in-process ({type(e).__name__}); run with --noconftest")
    box = {"world": world or WORLD}
    from eli.world import shown
    monkeypatch.setattr(shown, "items", lambda now=None: [])     # the routes are tested on the world alone
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



def test_the_protocol_and_a_chosen_version_can_be_asked_over_http(monkeypatch):
    client, _ = _client(monkeypatch)
    assert client.get("/v1/world/protocol").json()["versions"] == [1, 2]
    assert client.get("/v1/world/state").json()["version"] == 1
    two = client.get("/v1/world/state", params={"version": 2}).json()
    assert two["version"] == 2 and "form" in two["objects"][0] and "spaces" in two
    refused = client.get("/v1/world/state", params={"version": 9})
    assert refused.status_code == 400 and refused.json()["detail"]["kind"] == "incompatible"
    assert "the client offered 9" in refused.json()["detail"]["detail"]


def test_a_hello_is_answered_and_the_state_follows_in_the_version_agreed(monkeypatch):
    client, _ = _client(monkeypatch)
    with client.websocket_connect("/v1/world/ws") as ws:
        assert ws.receive_json()["version"] == 1, "before any hello, a client is a version 1 client"
        ws.send_json({"type": "hello", "protocol_versions": [1, 2], "capabilities": ["building"]})
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome" and welcome["version"] == 2 and welcome["missing"] == []
        state = ws.receive_json()
        assert state["type"] == "eli_world_state" and state["version"] == 2 and "spaces" in state


def test_a_client_that_cannot_be_spoken_to_is_told_and_the_socket_closes(monkeypatch):
    client, _ = _client(monkeypatch)
    from starlette.websockets import WebSocketDisconnect
    with client.websocket_connect("/v1/world/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "hello", "protocol_versions": [9]})
        refusal = ws.receive_json()
        assert refusal["type"] == "incompatible" and "the client offered 9" in refusal["reason"]
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4406


def test_the_scene_is_taken_and_a_bad_one_refused_by_kind(monkeypatch):
    client, _ = _client(monkeypatch)
    from eli.world import embodiment
    embodiment.forget()
    ok = client.post("/v1/world/scene", json={"scene": {"surface": "eli_vr", "room": "workshop"}})
    assert ok.status_code == 200 and embodiment.live()["room"] == "workshop"
    bad = client.post("/v1/world/scene", json={"scene": {"seen_as": "x" * 5000}})
    assert bad.status_code == 400 and bad.json()["detail"]["kind"] == "bad_request"
    # leaving empties it
    import eli.world.world_event_bus as bus
    monkeypatch.setattr(bus, "fire_world_event", lambda *a: None)
    client.post("/v1/world/event", json={"event_type": "vr_session_ended"})
    assert embodiment.live() is None


def test_a_sentence_is_a_turn_of_conversation_or_a_piece_of_building(monkeypatch):
    turns = []

    def chat(text, principal, session):
        turns.append((text, principal, session))
        return "  We are in orbit.  "

    client, _ = _client(monkeypatch, chat=chat)
    from eli.world import composer
    built = []
    monkeypatch.setattr(composer, "compose", lambda text, scene: built.append(text) or {
        "kind": "build", "text": "There.", "results": [{"ok": True}], "refused": []})

    talk = client.post("/v1/world/say", json={"text": "where are we?", "session_id": "s1"}).json()
    assert talk == {"kind": "chat", "text": "We are in orbit.", "results": [], "refused": []}
    assert turns == [("where are we?", "the owner", "s1")], "attributed to the owner, not to the client"

    made = client.post("/v1/world/say", json={"text": "build a lamp over there"}).json()
    assert made["kind"] == "build" and built == ["build a lamp over there"] and len(turns) == 1

    assert client.post("/v1/world/say", json={"text": "   "}).json()["detail"]["kind"] == "bad_request"


def test_failures_leave_by_kind_so_a_client_need_not_guess(monkeypatch):
    client, _ = _client(monkeypatch, chat=lambda *a: (_ for _ in ()).throw(RuntimeError("engine down")))
    from eli.world import composer

    def no_model(*a, **k):    # a moment waiting in the world composes by keyword
        raise composer.ComposeError("model_unavailable", "GGUF model not available")

    monkeypatch.setattr(composer, "compose", no_model)
    gone = client.post("/v1/world/say", json={"text": "build a lamp"})
    assert gone.status_code == 503 and gone.json()["detail"] == {
        "kind": "model_unavailable", "detail": "GGUF model not available"}
    assert client.post("/v1/world/compose").status_code == 503
    down = client.post("/v1/world/say", json={"text": "hello there"})
    assert down.status_code == 500 and down.json()["detail"] == {"kind": "failed", "detail": "engine down"}


def test_the_users_own_hands_and_undo_go_through_the_building(monkeypatch):
    client, _ = _client(monkeypatch)
    from eli.world import building
    calls = []
    monkeypatch.setattr(building, "apply", lambda edits, **kw: calls.append((edits, kw)) or {"ok": True})
    assert client.post("/v1/world/edit", json={
        "edits": [{"op": "move", "id": "lantern", "at": [1, 2]}], "room": "workshop"}).json() == {"ok": True}
    assert client.post("/v1/world/undo").json() == {"ok": True}
    (moved, how), (undone, by) = calls
    assert moved == [{"op": "move", "id": "lantern", "at": [1, 2]}]
    assert how["actor"] == "user" and how["default_room"] == "workshop"
    assert undone == [{"op": "undo"}] and by["actor"] == "user"


def test_his_mind_can_be_seen_and_chosen_and_a_wrong_name_is_refused(monkeypatch):
    client, _ = _client(monkeypatch)
    from eli.world import mind
    monkeypatch.setattr(mind, "status", lambda: {"type": "eli_mind", "loaded": False, "model": "",
                                                 "chosen": False, "models": [{"name": "A.gguf", "size_gb": 4.7}]})
    assert client.get("/v1/world/mind").json()["models"] == [{"name": "A.gguf", "size_gb": 4.7}]
    chosen = []

    def choose(name):
        if name != "A.gguf":
            raise mind.MindError("unknown_model", f"there is no model called {name!r} here")
        chosen.append(name)
        return {"type": "eli_mind", "loaded": True, "model": name, "chosen": True, "models": []}

    monkeypatch.setattr(mind, "choose", choose)
    assert client.post("/v1/world/mind", json={"model": "A.gguf"}).json()["loaded"] is True
    wrong = client.post("/v1/world/mind", json={"model": "B.gguf"})
    assert wrong.status_code == 400 and wrong.json()["detail"]["kind"] == "unknown_model"
    assert chosen == ["A.gguf"] and "mind" in spatial_feed.protocol()["capabilities"]["2"]

