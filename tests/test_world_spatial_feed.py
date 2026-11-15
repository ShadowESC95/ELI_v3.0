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

import json
from pathlib import Path

import pytest

from eli.world import shown, spatial_feed
from tests._world_records import DAY, T0, records  # noqa: F401 - the fixture

_BOOKS, _EVIDENCE = shown.books, shown.evidence
CONTRACT = Path(__file__).parent / "fixtures" / "world_feed_v2_contract.json"
EXAMPLE = Path(__file__).parent / "fixtures" / "world_feed_v2_example.json"


@pytest.fixture(autouse=True)
def _books_and_evidence_only_where_asked(monkeypatch):
    """ELI's books and the Evidence Wall's figures read ELI's stores; a test that wants them puts every
    store in its own folder first (tests/_world_records.py) and asks for them, so no test here reads the
    machine's records on the --noconftest lane."""
    monkeypatch.setattr(shown, "books", lambda room, now=None: [])
    monkeypatch.setattr(shown, "evidence", lambda now=None: {})

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
    vr = spatial_feed.negotiate({"protocol_versions": [2], "capabilities": ["books", "evidence"], "max_message": 65536})
    assert vr["missing"] == [] and {"books", "evidence"} <= set(spatial_feed.CAPABILITIES[2])
    assert vr["max_message"] == 65536, "a body says how much one message to it may take"
    assert answer["max_message"] == spatial_feed.MESSAGE_BYTES == 1 << 20, "ELI VR's world link takes 1 MiB"


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
            return [{"id": 6, "timestamp": 6, "text": "A memory kept before memories carried a time."},
                    {"id": 7, "timestamp": 1_780_000_000.0, "text": "The user keeps their invoices by year, newest first."},
                    {"id": 8, "text": " "}]

    reads = []
    monkeypatch.setattr(memory_module, "get_memory", lambda: reads.append(1) or Memory())
    monkeypatch.setattr(goal_store, "summarize_goals", lambda: {"titles": ["Learn the filing system"]})
    monkeypatch.setattr("eli.runtime.evidence_ledger.recent_events", lambda **k: [])   # nothing done for the user
    monkeypatch.setattr(shown, "_thought_notes", lambda: [])                            # and no thought sheets
    shown.forget()
    msg = spatial_feed.spatial_state(WORLD, version=2)
    by_kind = {o["kind"]: o for o in msg["objects"]}
    memory, goal = by_kind["memory"], by_kind["goal"]
    assert memory["room"] == "memory_archive" and memory["name"] == "The user keeps their invoices"
    assert memory["meaning"] == "The user keeps their invoices by year, newest first." and memory["form"]["shape"] == "orb"
    assert memory["when"] == 1_780_000_000.0, "a memory says when it was kept, for its cover"
    assert "when" not in next(o for o in msg["objects"] if o["id"] == "memory_6"), "a row's id is not its time"
    assert goal["room"] == "upgrade_bay" and goal["name"] == "Learn the filing system" and goal["form"]["shape"] == "beacon"
    assert memory["form"]["pos"] == shown.items()[1]["form"]["pos"], "a record keeps its place"
    read_once = len(reads)
    spatial_feed.spatial_state(WORLD, version=2)
    assert len(reads) == read_once, "ELI's records are not read again for every poll"
    assert "memory" not in {o["kind"] for o in spatial_feed.spatial_state(WORLD)["objects"]}, "version 1 is as it was"
    shown.forget()
    monkeypatch.setattr(memory_module, "get_memory", lambda: (_ for _ in ()).throw(RuntimeError("no memory")))
    assert {o["kind"] for o in shown.items()} == {"goal"}, "a source that cannot be read shows nothing of its own"
    shown.forget()


def test_what_eli_believes_is_on_the_evidence_wall_and_what_disagrees_is_in_the_anomaly_room(monkeypatch):
    import sqlite3
    from eli.memory import claims
    from eli.world import building, shown
    memory_module = pytest.importorskip("eli.memory.memory")
    conn = sqlite3.connect(":memory:")
    claims.record(conn, "works_as", "a ferry pilot", origin="user_said", now=1_780_000_000.0)
    claims.record(conn, "lives_in", "Harbour Town", origin="user_said", now=1_780_100_000.0, source_memory_id=11)
    claims.record(conn, "lives_in", "Upper Ridge", origin="eli_said", now=1_780_200_000.0)

    class Memory:
        def get_recent_semantic_memories(self, limit=16):
            return []

        def memory_facts(self, memory_id):
            return {11: {"id": 11, "text": "I moved to Harbour Town last spring.", "when": 1_780_050_000.0}}.get(memory_id, {})

        def claims_current(self):
            return claims.current(conn)

        def claim_conflicts(self):
            return claims.open_conflicts(conn)

    monkeypatch.setattr(memory_module, "get_memory", lambda: Memory())
    shown.forget()
    shown_now = shown.items()
    beliefs = [o for o in shown_now if o["kind"] == "claim"]
    assert [o["name"] for o in beliefs] == ["lives in: Harbour Town", "works as: a ferry pilot"], "newest first"
    assert all(o["room"] == "evidence_wall" and o["form"]["shape"] == "board" for o in beliefs)
    assert beliefs[0]["meaning"].startswith("lives in: Harbour Town (since ")
    assert beliefs[0]["form"]["colour"] == "#f5b942", "the belief something else disputes shows as contested"
    assert beliefs[1]["form"]["colour"] == "#d9e4f2"
    held = beliefs[0]["claim"]
    row = next(r for r in claims.current(conn) if r["value"] == "Harbour Town")
    assert held == {"relation": "lives_in", "value": "Harbour Town", "chance": round(beliefs[0]["importance"], 4),
                    "sure": row["confidence"], "since": 1_780_100_000.0, "until": None, "learned": 1_780_100_000.0,
                    "origin": "user_said", "basis": claims.basis(row), "confirmations": 1, "contested": True,
                    "status": "current",
                    "sources": [{"id": "memory_11", "kind": "memory", "text": "I moved to Harbour Town last spring.",
                                 "when": 1_780_050_000.0, "origin": "you said so"}],
                    "also": []}, "a belief with what it rests on"
    assert beliefs[1]["claim"]["sources"] == [] and beliefs[1]["claim"]["contested"] is False, "a claim with no source rests on none"
    [conflict] = [o for o in shown_now if o["kind"] == "conflict"]
    assert conflict["room"] == "anomaly_room" and conflict["name"] == "Lives in?" and conflict["relation"] == "lives_in"
    assert "Harbour Town" in conflict["meaning"] and "Upper Ridge" in conflict["meaning"] and conflict["meaning"].endswith("?")
    for n in range(building.MAX_PER_ROOM + 5):
        claims.record(conn, f"likes_{n}", f"thing {n}", single_valued=False, origin="user_said", now=1_780_300_000.0 + n)
    shown.forget()
    assert sum(o["kind"] == "claim" for o in shown.items()) == building.MAX_PER_ROOM, "as many as the room holds"
    shown.forget()


def test_what_is_on_eli_s_mind_comes_from_its_records_and_rises_in_the_feed(monkeypatch):
    from eli.world import shown, urges
    insight_module = pytest.importorskip("eli.planning.insight_synthesis")
    goal_store = pytest.importorskip("eli.planning.goal_store")
    from eli.cognition import tone_adaptor
    monkeypatch.setattr(insight_module, "get_cached_insight", lambda: "  Quiet work   goes further than loud work. ")
    monkeypatch.setattr(urges, "waiting", lambda: [{"kind": "trouble", "text": "WEB_SEARCH failed: the network is off"}])
    monkeypatch.setattr(goal_store, "summarize_goals", lambda: {"titles": ["Learn the filing system", "Tidy the index"]})
    monkeypatch.setattr(tone_adaptor, "current_tone", lambda: {"tone": "calm"})
    monkeypatch.setattr(shown, "items", lambda now=None: [])
    shown.forget()
    assert shown.thoughts() == [
        {"kind": "insight", "text": "Quiet work goes further than loud work."},
        {"kind": "trouble", "text": "WEB_SEARCH failed: the network is off"},
        {"kind": "goal", "text": "Learn the filing system"},
        {"kind": "tone", "text": "calm"},
    ]
    assert spatial_feed.spatial_state(WORLD, version=2)["mind"] == shown.thoughts()
    assert "mind" not in spatial_feed.spatial_state(WORLD), "version 1 is as it was"
    world = {**WORLD, "spaces": {"own_room": {"urges": [{"kind": "insight", "text": "x" * 20}, "junk"]}}}
    monkeypatch.setattr(urges, "free", lambda: True)
    shown.forget()
    msg = spatial_feed.spatial_state(world, version=2)
    assert msg["waiting"] == 1 and msg["free"] is True
    monkeypatch.setattr(urges, "free", lambda: False)
    assert spatial_feed.spatial_state(world, version=2)["free"] is True, "read as often as the records, not every poll"
    shown.forget()
    assert spatial_feed.spatial_state(world, version=2)["free"] is False
    monkeypatch.setattr(insight_module, "get_cached_insight", lambda: "")
    monkeypatch.setattr(tone_adaptor, "current_tone", lambda: {"tone": "neutral"})
    shown.forget()
    assert [t["kind"] for t in shown.thoughts()] == ["trouble", "goal"], "nothing made up to fill a gap"
    shown.forget()


def test_version_two_carries_a_screenful_of_eli_s_own_log_newest_last(monkeypatch):
    from eli.world import shown
    monkeypatch.setattr(shown, "items", lambda now=None: [])
    events = [{"event_type": "reasoning_stage", "source": "reasoning_engine", "summary": f"Stage {n}", "timestamp": float(n)}
              for n in range(40)]
    events.append({"event_type": "error_detected", "source": "executor", "summary": "WEB_SEARCH   failed:\nthe network is off",
                   "timestamp": 41.0})
    events.append({"event_type": "tool_activity", "source": "executor", "summary": "  ", "timestamp": 42.0})
    events.append({"event_type": "user_presence", "source": "spatial_client", "summary": "The user opened ELI VR",
                   "timestamp": 43.0})
    msg = spatial_feed.spatial_state({**WORLD, "events": events}, version=2)
    log = msg["log"]
    assert log[-1] == {"type": "error_detected", "source": "executor", "text": "WEB_SEARCH failed: the network is off", "ts": 41.0}
    assert len(log) <= spatial_feed.LOG_LINES and log[0]["text"] == f"Stage {41 - spatial_feed.LOG_LINES}", "the newest screenful"
    assert all(entry["source"] != "spatial_client" for entry in log), "the room reporting the user is not ELI's log"
    assert "log" not in spatial_feed.spatial_state({**WORLD, "events": events}), "version 1 is as it was"


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
             "rooms": {"workshop": {"name": "Workshop", "purpose": "Tools and repair.",
                                    "mood": {"light": "#ff8800", "mist": 0.3, "sky": "dawn"}},
                       "core_room": {"name": "Core Room", "purpose": "The centre."}},
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
    assert msg["spaces"]["workshop"] == {"name": "Workshop", "purpose": "Tools and repair.",
                                         "mood": {"light": "#ff8800"}}, \
        "a room of the house ELI relit, with only what a room of the house can take"
    assert "core_room" not in msg["spaces"], "a room still as it was built is not sent"
    assert msg["recent"] == [{"what": "create_object", "who": "eli", "room": "own_room",
                              "why": "ELI chose to.", "allowed": True}], "ELI's walking about is left out"


# ── the routes (clean lane only) ─────────────────────────────────────────────

def _client(monkeypatch, *, ws_allowed=True, world=None, chat=None, remember=None):
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(spatial_feed.build_router(lambda: None, lambda: "the owner",
                                                     lambda ws: ws_allowed, chat=chat, remember=remember))
        client = TestClient(app)
    except Exception as e:  # mocked pydantic under the suite conftest
        pytest.skip(f"fastapi not usable in-process ({type(e).__name__}); run with --noconftest")
    box = {"world": world or WORLD}
    from eli.world import shown
    monkeypatch.setattr(shown, "items", lambda now=None: [])     # the routes are tested on the world alone
    monkeypatch.setattr(spatial_feed, "_load_world", lambda: box["world"])
    monkeypatch.setattr(spatial_feed, "POLL_SECONDS", 0.05)
    return client, box


def test_the_thoughts_route_gives_each_caller_their_own_thoughts(monkeypatch, tmp_path):
    """GET /v1/world/thoughts read as whatever user the request's context held, none for an API call, so
    a member asking read the owner's recalled memories and claims; and one cache served every caller."""
    try:
        from fastapi import FastAPI, Header
        from fastapi.testclient import TestClient
    except Exception as e:  # mocked pydantic under the suite conftest
        pytest.skip(f"fastapi not usable in-process ({type(e).__name__}); run with --noconftest")
    from eli.cognition import thoughts as th
    from eli.memory import policy
    from eli.world import shown
    monkeypatch.setattr(th, "_default_db_path", lambda: tmp_path / "thoughts.sqlite3")
    monkeypatch.setattr(policy, "rbac_members", lambda: frozenset({"member-b"}))
    monkeypatch.setattr(policy, "_active_user_id", lambda: "")
    th.record("recall", "the owner's walk by the canal", decision="shown", ref="recall:r-a:1")
    th.record("claim", "works at: the harbour office", decision="shown", ref="claim:1:r-b", user_id="member-b")
    th.record("proposal", "a fix for the router", decision="proposed", ref="proposal:1")
    shown.forget()

    def who(x_who: str = Header(default="")):
        return x_who

    try:
        from pydantic import BaseModel

        class _Probe(BaseModel):
            text: str = ""
        _Probe(text="x")
    except Exception as e:  # noqa: BLE001 - mocked pydantic under the suite conftest
        pytest.skip(f"pydantic not usable in-process ({type(e).__name__}); run with --noconftest")
    app = FastAPI()
    app.include_router(spatial_feed.build_router(who, who, lambda ws: True, reader=lambda p: p or None))
    client = TestClient(app)
    seen = {name: {t["what"] for t in client.get("/v1/world/thoughts", headers={"x-who": name}).json()["thoughts"]}
            for name in ("", "member-b")}
    assert seen[""] == {"the owner's walk by the canal", "a fix for the router"}
    assert seen["member-b"] == {"works at: the harbour office", "a fix for the router"}
    kinds = client.get("/v1/world/thoughts", params={"kinds": "recall,claim"}, headers={"x-who": "member-b"}).json()
    assert [t["what"] for t in kinds["thoughts"]] == ["works at: the harbour office"]


def test_the_world_message_gives_each_caller_their_own_thoughts(monkeypatch, tmp_path):
    """The world message itself (/v1/world/state and the WebSocket feed a body lives on) carried the
    owner's thoughts to every caller, though /v1/world/thoughts gave each their own: a member's VR body
    was handed the owner's recalled memories and claims. Each caller now gets their own and ELI's
    shared ones, over HTTP and over the socket."""
    try:
        from fastapi import FastAPI, Header
        from fastapi.testclient import TestClient
        from pydantic import BaseModel

        class _Probe(BaseModel):
            text: str = ""
        _Probe(text="x")
    except Exception as e:  # noqa: BLE001 - mocked pydantic under the suite conftest
        pytest.skip(f"fastapi/pydantic not usable in-process ({type(e).__name__}); run with --noconftest")
    from types import SimpleNamespace
    from eli.cognition import thoughts as th
    from eli.memory import policy
    from eli.world import models, shown
    monkeypatch.setattr(th, "_default_db_path", lambda: tmp_path / "thoughts.sqlite3")
    monkeypatch.setattr(policy, "rbac_members", lambda: frozenset({"member-b"}))
    monkeypatch.setattr(policy, "_active_user_id", lambda: "")
    th.record("recall", "the owner's walk by the canal", decision="shown", ref="recall:r-a:1")
    th.record("claim", "works at: the harbour office", decision="shown", ref="claim:1:r-b", user_id="member-b")
    th.record("proposal", "a fix for the router", decision="proposed", ref="proposal:1")
    for name, stand_in in (("items", lambda now=None: []), ("thoughts", lambda now=None: []),
                           ("coming_up", lambda now=None: []), ("free", lambda now=None: False)):
        monkeypatch.setattr(shown, name, stand_in)       # the rest of the message reads no store here
    monkeypatch.setattr(models, "listed", lambda: [])
    monkeypatch.setattr(spatial_feed, "_load_world", lambda: WORLD)
    monkeypatch.setattr(spatial_feed, "POLL_SECONDS", 0.05)
    shown.forget()

    def who(x_who: str = Header(default="")):
        return SimpleNamespace(user=x_who)

    app = FastAPI()
    app.include_router(spatial_feed.build_router(who, who, lambda ws: SimpleNamespace(user=ws.headers.get("x-who", "")),
                                                 reader=lambda p: p.user or None))
    client = TestClient(app)

    def over_http(name):
        message = client.get("/v1/world/state", params={"version": 2}, headers={"x-who": name}).json()
        return {t["what"] for t in message["thoughts"]}

    def over_the_socket(name):
        with client.websocket_connect("/v1/world/ws", headers={"x-who": name}) as ws:
            ws.receive_json()
            ws.send_json({"type": "hello", "protocol_versions": [1, 2]})
            assert ws.receive_json()["type"] == "welcome"
            return {t["what"] for t in ws.receive_json()["thoughts"]}

    for read in (over_http, over_the_socket):
        assert read("") == {"the owner's walk by the canal", "a fix for the router"}, read.__name__
        assert read("member-b") == {"works at: the harbour office", "a fix for the router"}, read.__name__


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


def test_played_route_hands_the_result_to_the_games(monkeypatch):
    client, _ = _client(monkeypatch)
    from eli.world import games
    got = []
    monkeypatch.setattr(games, "played", lambda game, result: got.append((game, result)) or {
        "ok": True, "text": "", "result": result, "best": False})
    r = client.post("/v1/world/played", json={"game": "game_x", "result": {"score": 3}})
    assert r.status_code == 200 and r.json()["ok"] and got == [("game_x", {"score": 3})]

    def none(game, result):
        raise ValueError("there is no game 'nope'")

    monkeypatch.setattr(games, "played", none)
    gone = client.post("/v1/world/played", json={"game": "nope"})
    assert gone.status_code == 400 and gone.json()["detail"]["kind"] == "bad_request"
    assert "games" in spatial_feed.CAPABILITIES[2]


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



def _done(row_id, action, result, args=None, user="owner", ts=0.0):
    return {"id": row_id, "action": action, "user_id": user, "timestamp": ts,
            "payload": {"args": args or {}, "result": dict({"ok": True}, **result)}}


def test_what_eli_did_for_the_user_stands_in_its_house_with_what_it_says(monkeypatch):
    from eli.runtime import evidence_ledger
    from eli.world import shown
    monkeypatch.setattr("eli.kernel.state.get_active_user_id", lambda *a, **k: "owner")
    rows = [  # newest first, as the ledger gives them
        _done(9, "PAUSE_MEDIA", {}),
        _done(8, "PLAY_MEDIA", {"media": {"title": "Harbour Lights", "artist": "The Quiet Ferries", "album": "Low Tide"}}),
        _done(7, "WEB_SEARCH", {"query": "when do the tides turn", "results": [
            {"title": "**Tide** times explained", "url": "https://www.example.org/tides"},
            {"title": "Why there are two tides a day", "url": "https://science.example.net/a/b"}]}),
        _done(6, "CREATE_FILE", {"path": "/tmp/notes/garden_plan.md", "readback": "# Garden plan\n- Basil by the window\n- Tomatoes in May"}),
        _done(5, "WEB_SEARCH", {"ok": False, "error": "offline"}, {"query": "never shown"}),
        _done(4, "WEB_SEARCH", {"query": "someone else's search", "results": []}, user="guest"),
        _done(3, "SET_VOLUME", {}),
    ]
    monkeypatch.setattr(evidence_ledger, "recent_events", lambda **k: rows)
    found = {o["id"]: o for o in shown._results()}
    assert set(found) == {"playing", "found_7", "written_6"}, "only what worked, for this user, of the kinds shown"
    search = found["found_7"]
    assert search["room"] == "evidence_wall" and search["name"] == "Searched: when do the tides turn"
    assert search["lines"] == ["Tide times explained", "    example.org", "Why there are two tides a day", "    science.example.net"]
    written = found["written_6"]
    assert written["room"] == "workshop" and written["name"] == "garden_plan.md"
    assert written["lines"] == ["Garden plan", "Basil by the window", "Tomatoes in May"]
    radio = found["playing"]
    assert radio["room"] == "core_room" and radio["name"] == "Harbour Lights"
    assert radio["lines"] == ["The Quiet Ferries", "Low Tide", "paused"], "stopped after it started"
    assert all(o["meaning"] for o in found.values()), "a body that reads only the meaning still has words"


def test_what_is_playing_is_the_newest_and_says_where_when_that_is_all_it_knows(monkeypatch):
    from eli.runtime import evidence_ledger
    from eli.world import shown
    monkeypatch.setattr("eli.kernel.state.get_active_user_id", lambda *a, **k: "owner")
    monkeypatch.setattr(evidence_ledger, "recent_events", lambda **k: [
        _done(2, "NOW_PLAYING", {"content": "▶ Playing: Night Ferry — firefox"}),
        _done(1, "PAUSE_MEDIA", {}),
        _done(0, "PLAY_MEDIA", {"media": {"title": "Older Song"}}),
    ])
    (radio,) = [o for o in shown._results() if o["kind"] == "playing"]
    assert radio["name"] == "Night Ferry" and radio["lines"] == ["on firefox"], "a pause before it does not count"


def test_a_body_can_tell_eli_the_real_things_it_draws():
    from eli.world import embodiment
    assert "props" in spatial_feed.CAPABILITIES[2]
    embodiment.forget_body()
    embodiment.report_body({"props": [{"id": "boombox", "name": "boombox", "size": [0.72, 0.47, 0.19]}]})
    assert embodiment.props() == [{"id": "boombox", "name": "boombox", "size": [0.72, 0.47, 0.19]}]
    embodiment.forget_body()


def test_what_is_coming_up_on_the_agenda_is_in_the_feed(monkeypatch):
    import time as clock
    from eli.runtime import agenda
    from eli.world import shown
    soon = clock.time() + 3600
    monkeypatch.setattr("eli.kernel.state.get_active_user_id", lambda *a, **k: "owner")
    seen = {}
    def between(since, until, user_id="", **k):
        seen["user"], seen["span"] = user_id, until - since
        return [{"start_ts": soon, "title": "**Ferry** to the island", "kind": "event", "has_time": 1}]
    monkeypatch.setattr(agenda, "between", between)
    shown.forget()
    (item,) = shown.coming_up()
    # an hour from now is today, or tomorrow when the test runs in the last hour of the day
    day = "tomorrow" if clock.localtime(soon).tm_mday != clock.localtime().tm_mday else "today"
    assert item["what"] == "Ferry to the island" and item["said"].startswith(day) and item["when"] == soon
    assert seen == {"user": "owner", "span": shown.AHEAD_HOURS * 3600}
    assert spatial_feed.spatial_state(WORLD, version=2)["coming_up"] == [item]
    monkeypatch.setattr(agenda, "between", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no agenda")))
    shown.forget()
    assert shown.coming_up() == [], "an agenda that cannot be read shows nothing"
    shown.forget()


def test_a_picture_eli_took_is_shown_only_as_the_ledger_records_it(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from eli.runtime import evidence_ledger
    from eli.world import shown
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    fake = tmp_path / "notes.png"
    fake.write_text("not a picture at all")
    secret = tmp_path / "keys.txt"
    secret.write_text("never served")
    monkeypatch.setattr("eli.kernel.state.get_active_user_id", lambda *a, **k: "owner")
    rows = [
        {"id": 31, "action": "SCREENSHOT", "user_id": "owner", "timestamp": 3.0,
         "payload": {"args": {}, "result": {"ok": True, "path": str(shot), "content": "The desktop, two windows open"}}},
        {"id": 30, "action": "ANALYZE_IMAGE", "user_id": "owner", "timestamp": 2.0,
         "payload": {"args": {"path": str(fake)}, "result": {"ok": True, "content": "Text"}}},
        {"id": 29, "action": "SCREENSHOT", "user_id": "guest", "timestamp": 1.0,
         "payload": {"args": {}, "result": {"ok": True, "path": str(shot)}}},
        {"id": 28, "action": "WEB_SEARCH", "user_id": "owner", "timestamp": 0.5,
         "payload": {"args": {"path": str(secret)}, "result": {"ok": True, "query": "x", "results": []}}},
    ]
    monkeypatch.setattr(evidence_ledger, "recent_events", lambda **k: rows)
    seen = [o for o in shown._results() if o["kind"] == "seen"]
    assert [(o["id"], o["picture"], o["room"]) for o in seen] == [("seen_31", "/v1/world/picture/31", "evidence_wall")], \
        "a file that is not a picture is not shown"
    assert seen[0]["name"] == "A screenshot" and seen[0]["lines"] == ["The desktop, two windows open"]
    assert shown.picture(31) == str(shot.resolve()) and shown.picture(29) == "" and shown.picture(28) == ""
    app = FastAPI()
    app.include_router(spatial_feed.build_router(lambda: None, lambda: None, lambda ws: True))
    client = TestClient(app)
    got = client.get("/v1/world/picture/31")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.content.startswith(b"\x89PNG")
    for other in (30, 29, 28, 999):
        assert client.get(f"/v1/world/picture/{other}").status_code == 404, other
    assert "pictures" in spatial_feed.CAPABILITIES[2]


def test_elis_thoughts_hang_as_sheets_in_the_rooms_they_belong_to(monkeypatch, tmp_path):
    from eli.cognition import thoughts as th
    from eli.world import shown
    monkeypatch.setattr(th, "_default_db_path", lambda: tmp_path / "thoughts.sqlite3")
    for source in ("_memories", "_goals", "_claims", "_conflicts", "_results"):
        monkeypatch.setattr(shown, source, lambda: [])
    for n in range(8):
        th.record("route", f"read sentence {n} as a command", chance=0.5 + n * 0.05, decision="held", now=1_790_000_000.0 + n)
    cid = th.record("claim", "you live in Harbour Town", chance=0.9, decision="held", reason="confirmed twice", why=["said twice"],
                    ref="claim:1", now=1_790_001_000.0)
    th.settle("claim:1", "right", how="it stood")
    th.record("note", "Look before saying where things are.", decision="pinned", reason="ELI wrote it of its own accord",
              now=1_790_002_000.0)
    shown.forget()
    sheets = [o for o in shown.items() if o["kind"] == "thought"]
    core = [o for o in sheets if o["room"] == "core_room"]
    assert len(core) == shown.NOTES_PER_ROOM and core[0]["name"] == "route: read sentence 7 as a command", "the newest six"
    wall = {o["id"]: o for o in sheets if o["room"] == "evidence_wall"}
    sheet = wall[f"thought_{cid}"]
    assert sheet["lines"][:4] == ["you live in Harbour Town", "held: confirmed twice", "chance 90% · proved right (it stood)", "because said twice"]
    assert sheet["lines"][4].endswith(":36") or sheet["lines"][4], "and when it was had"
    assert sheet["thought"] == {"kind": "claim", "chance": 0.9, "decision": "held", "status": "settled", "outcome": "right"}
    assert sheet["form"]["colour"] == shown.THOUGHT_COLOURS["claim"] and sheet["form"]["shape"] == "board" and sheet["importance"] == 0.9
    assert sheet["meaning"].startswith("You live in Harbour Town. Held because confirmed twice.")
    note = [o for o in wall.values() if o["thought"]["kind"] == "note"][0]
    assert note["name"] == "note: Look before saying where things are." and note["form"]["colour"] == shown.THOUGHT_COLOURS["note"]
    assert spatial_feed.spatial_state(WORLD, version=2)["objects"][-1]["kind"] == "thought"
    shown.forget()


def test_a_sentence_the_world_answered_is_kept_with_its_vr_session(monkeypatch):
    """Built or set as a game, a sentence said in VR left no turn and no action row: recall and the
    period log could not show it. Only sentences that fell through to chat were recorded."""
    remembered, ledger = [], []
    client, _ = _client(monkeypatch, chat=lambda *a: "chat", remember=lambda *a: remembered.append(a))
    from eli.world import composer
    import eli.runtime.evidence_ledger as evidence_ledger
    monkeypatch.setattr(evidence_ledger, "record_event", lambda kind, **kw: ledger.append((kind, kw)) or 1)
    monkeypatch.setattr(composer, "compose", lambda text, scene: {
        "kind": "build", "text": "There, by the window.", "room": "workshop",
        "results": [{"op": "create", "name": "Lamp", "ok": True},
                    {"op": "remove", "name": "Throne", "ok": False, "reason": "there is no 'Throne' here"}],
        "refused": []})
    out = client.post("/v1/world/say", json={"text": "build a lamp and remove the throne",
                                              "session_id": "eli-vr-42"}).json()
    assert out["text"] == "There, by the window."
    assert remembered == [("build a lamp and remove the throne", "There, by the window.", "the owner", "eli-vr-42")]
    (kind, row), = ledger
    assert kind == "executor_action" and row["action"] == "WORLD_EDIT" and row["session_id"] == "eli-vr-42"
    assert row["outcome"] == "ok" and "remove Throne (not done: there is no 'Throne' here)" in row["content"]


def test_the_world_link_closing_ends_the_bodys_session(monkeypatch):
    """The world WebSocket's disconnect was ignored: no VR session since one day had an end."""
    import time as _time
    client, _ = _client(monkeypatch)
    from eli.world import embodiment
    monkeypatch.setattr(embodiment, "_remember", lambda title, text: True)
    monkeypatch.setattr(embodiment, "_measure_headset", lambda: None)
    embodiment.forget()
    with client.websocket_connect("/v1/world/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "hello", "surface": "eli_vr", "session": "eli-vr-link", "protocol_versions": [1, 2]})
        assert ws.receive_json()["type"] == "welcome"
        assert client.post("/v1/world/scene", json={"scene": {"surface": "eli_vr", "session": "eli-vr-link",
                                                              "room": "workshop"}}).status_code == 200
        assert [b["session"] for b in embodiment.present()] == ["eli-vr-link"]
    for _ in range(100):                       # the server side finishes the closed socket on its own thread
        if not embodiment.present():
            break
        _time.sleep(0.02)
    assert embodiment.present() == []
    ended = [s for s in embodiment.sessions_between(0, _time.time() + 1) if s["session"] == "eli-vr-link"]
    assert ended and ended[-1]["end"] == "its world link closed"


# ── what ELI VR's knowledge rooms are sent: books, beliefs with their sources, the Evidence Wall ─────

def _contract_errors(value, kind, shapes, where="message"):
    """Every way `value` differs from the contract's `kind` (tests/fixtures/world_feed_v2_contract.json)."""
    errors = []
    for one in kind.split("|"):
        if one.startswith("array<"):
            inner = one[len("array<"):-1]
            if isinstance(value, list):
                found = [e for i, item in enumerate(value) for e in _contract_errors(item, inner, shapes, f"{where}[{i}]")]
                if not found:
                    return []
                errors.extend(found)
            continue
        if one in shapes:
            if not isinstance(value, dict):
                errors.append(f"{where}: not an object ({one})")
                continue
            shape = shapes[one]
            found = [f"{where}: {key} is not in the contract for {one}" for key in value
                     if key not in shape and f"?{key}" not in shape]
            for key, sub in shape.items():
                name = key.lstrip("?")
                if name not in value:
                    if not key.startswith("?"):
                        found.append(f"{where}: {name} is missing ({one})")
                    continue
                found.extend(_contract_errors(value[name], sub, shapes, f"{where}.{name}"))
            if not found:
                return []
            errors.extend(found)
            continue
        ok = {"str": isinstance(value, str), "bool": isinstance(value, bool), "null": value is None,
              "object": isinstance(value, dict),
              "int": isinstance(value, int) and not isinstance(value, bool),
              "number": isinstance(value, (int, float)) and not isinstance(value, bool)}.get(one)
        if ok is None:
            errors.append(f"{where}: the contract names no type {one}")
        elif ok:
            return []
        else:
            errors.append(f"{where}: {value!r} is not {one}")
    return errors


def _seed_the_knowledge_rooms(records):
    """One of each record the knowledge rooms show, in the test's own stores."""
    from eli.cognition import epistemics, thoughts
    from eli.memory import claims
    from eli.planning import goal_store
    from eli.runtime import lessons
    from eli.world import stories
    from eli.world.persistence.journal import append_journal_entry
    now = __import__("time").time()
    mid = records.memory_row("I moved to Harbour Town last spring.", now - 20 * DAY)
    conn = records.memory._claims_conn()
    claims.record(conn, "lives_in", "Harbour Town", origin="user_said", now=now - 20 * DAY, source_memory_id=mid,
                  subject=records.memory._claim_subject())
    claims.record(conn, "lives_in", "Upper Ridge", origin="eli_said", now=now - 2 * DAY, subject=records.memory._claim_subject())
    conn.commit()
    conn.close()
    records.turn("s1", "user", "Where did I leave the blue kettle?", now - 3 * DAY)
    records.turn("s1", "assistant", "On the windowsill, you said.", now - 3 * DAY + 60)
    records.summary("s1", "We found the kettle. It was on the sill.", now - 3 * DAY, now - 3 * DAY + 60, 2)
    records.ran("OPEN_APP", False, now - 3 * DAY + 10, session="s1", said="Notes is not installed", args={"name": "Notes"})
    records.ran("OPEN_URL", True, now - 3 * DAY + 20, session="s1", said="Opened notes.example.org",
                args={"url": "https://notes.example.org", "_alternative_of": "OPEN_APP"})
    records.ran("OPEN_APP", True, now - 10 * DAY, session="s0", said="Opened Notes", args={"name": "Notes"})
    for i in range(4):
        records.figure(epistemics.ANSWER, f"answer:w{i}", 0.8, i < 3, now - (2 + 7 * i) * DAY)
    records.stance("rain sounds", "I am fond of the sound of rain on a roof.", now - 5 * DAY, valence=1)
    thoughts.record("act", "made a lamp for the reading corner", chance=0.6, decision="taken", subject="a lamp",
                    ref="act:1", now=now - DAY)
    append_journal_entry("A note from the rooms", "The reading corner is warmer now.")
    goal_store.upsert_goal({"title": "Tidy the index", "objective": "Fewer duplicate rows."})
    lessons.propose("OPEN_APP", "the app is not installed", ["Notes failed"], "open the site instead", now=now - 3 * DAY)
    folder = stories.folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "abc123def456.json").write_text(json.dumps({
        "id": "abc123def456", "title": "The Blue Kettle", "text": "The kettle waited on the sill.", "fiction": True,
        "drawn_from": [{"ref": "conversation:x", "words": "the conversation of three days ago"}],
        "about_from": now - 3 * DAY, "about_to": now - 3 * DAY + 60, "written_at": now - DAY, "model": "small-model"}))


def _knowledge_message(monkeypatch, records, budget=None):
    monkeypatch.setattr(shown, "books", _BOOKS)
    monkeypatch.setattr(shown, "evidence", _EVIDENCE)
    # what the knowledge rooms are not about is not read from the machine (its world state, agenda, models)
    monkeypatch.setattr(shown, "thoughts", lambda now=None: [])
    monkeypatch.setattr(shown, "coming_up", lambda now=None: [])
    monkeypatch.setattr(shown, "free", lambda now=None: False)
    monkeypatch.setattr("eli.world.models.listed", lambda: [])
    shown.forget()
    return spatial_feed.spatial_state(WORLD, version=2, budget=budget)


def test_the_knowledge_rooms_are_sent_in_the_shapes_the_contract_says(monkeypatch, records):
    import os
    contract = json.loads(CONTRACT.read_text())
    _seed_the_knowledge_rooms(records)
    msg = _knowledge_message(monkeypatch, records)
    assert set(contract["capabilities"]) <= set(spatial_feed.CAPABILITIES[2])
    errors = []
    for key, kind in contract["message"].items():
        errors += _contract_errors(msg[key], kind, contract["shapes"], key)
    for i, obj in enumerate(msg["objects"]):
        if obj["kind"] in contract["object_shapes"]:
            errors += _contract_errors(obj, contract["object_shapes"][obj["kind"]], contract["shapes"], f"objects[{i}]")
    assert errors == [], "\n".join(errors)
    kinds = {o["kind"] for o in msg["objects"]}
    assert {"book", "claim", "conflict", "memory"} <= kinds, "one of each was sent"
    of = {o["book"]["of"] for o in msg["objects"] if o["kind"] == "book"}
    assert {"conversation", "session", "log", "period", "story", "likes", "thoughts", "goals", "diary", "lesson"} <= of
    assert msg["evidence"]["actions"]["rows"] and msg["evidence"]["accuracy"], "the scoreboard and the charts have rows"
    assert any(t["subject"] == "a lamp" and t["ref"] == "act:1" for t in msg["thoughts"]), "a thought says what it is about"
    if os.environ.get("ELI_WRITE_WORLD_FIXTURES"):
        EXAMPLE.write_text(json.dumps({k: msg[k] for k in ("objects", "thoughts", "evidence")}, indent=1) + "\n")
    example = json.loads(EXAMPLE.read_text())
    errors = []
    for key, kind in contract["message"].items():
        errors += _contract_errors(example[key], kind, contract["shapes"], f"example.{key}")
    for i, obj in enumerate(example["objects"]):
        if obj["kind"] in contract["object_shapes"]:
            errors += _contract_errors(obj, contract["object_shapes"][obj["kind"]], contract["shapes"], f"example.objects[{i}]")
    assert errors == [], "the example a body tests against is a payload of today's shape:\n" + "\n".join(errors)


def test_the_evidence_wall_is_told_how_each_action_and_each_kind_of_figure_went_week_by_week(monkeypatch, records):
    from eli.cognition.epistemics import week_start
    _seed_the_knowledge_rooms(records)
    evidence = _knowledge_message(monkeypatch, records)["evidence"]
    rows = {r["action"]: r for r in evidence["actions"]["rows"]}
    opened = rows["OPEN_APP"]
    assert (opened["name"], opened["n"], opened["ok"]) == ("open app", 2, 1)
    assert [c["n"] for c in opened["causes"]] == [1] and opened["causes"][0]["words"], "why it failed, in words"
    assert sum(w["n"] for w in opened["weeks"]) == 2 and sum(w["ok"] for w in opened["weeks"]) == 1
    assert [w["from"] for w in opened["weeks"]] == sorted(w["from"] for w in opened["weeks"]), "oldest first"
    assert opened["weeks"][-1]["from"] == week_start(__import__("time").time()), "up to this week"
    assert list(rows)[0] == "OPEN_APP", "the actions with failures first"
    [answers] = [a for a in evidence["accuracy"] if a["kind"] == "answer"]
    assert answers["words"] == "replies to you" and answers["n"] == 4 and answers["right"] == 0.75
    filled = [w for w in answers["weeks"] if w["n"]]
    assert sum(w["n"] for w in filled) == 4 and all(w["right"] in (0.0, 1.0) for w in filled)
    assert all(w["right"] is None for w in answers["weeks"] if not w["n"]), "a week with nothing settled says nothing"


def _seed_a_wall_that_repeats_itself(records):
    """Records shaped like a real wall's: one fact kept in several wordings and under several relations,
    the same belief thought of turn after turn, a routine reading in every turn, and actions run once."""
    from eli.cognition import epistemics, thoughts
    from eli.memory import claims
    from eli.runtime import lessons
    now = __import__("time").time()
    conn = records.memory._claims_conn()
    who = records.memory._claim_subject()
    told = [("identity.name", "Name is Rowan."), ("identity.fact", "You're Rowan"), ("identity.fact", "User is named Rowan."),
            ("preference.commands", 'User prefers written steps for repairs. Said: "give me the steps"'),
            ("preference.style", 'User prefers written steps for repairs. Said: "write it out for me"'),
            ("identity.fact", "Watches season 2 of Tide Lines."), ("identity.fact", "Watches season 3 of Tide Lines."),
            ("identity.fact", "Keeps bees."), ("identity.fact", "Does not keep bees any more."),
            ("identity.fact", "User keeps bees. They also grow pears on the south wall.")]
    ids = {}
    for n, (relation, value) in enumerate(told):
        ids[value] = claims.record(conn, relation, value, origin="user_passing", single_valued=False, subject=who,
                                   now=now - (20 - n) * DAY)
    claims.record(conn, "lives_in", "Harbour Town", origin="user_said", now=now - 9 * DAY, subject=who)
    claims.record(conn, "lives_in", "Upper Ridge", origin="eli_said", now=now - 2 * DAY, subject=who)
    conn.commit()
    conn.close()
    for turn in range(3):
        rid = f"req-{turn}"
        thoughts.record("claim", "identity.fact: Keeps bees.", chance=0.6, decision="shown", subject="Keeps bees.",
                        request_id=rid, ref=epistemics.claim_request_id(ids["Keeps bees."], rid), now=now - turn * 60)
        thoughts.record("route", "read as chat", chance=0.9, decision="taken", subject=f"sentence {turn}",
                        request_id=rid, ref=f"route:{rid}", now=now - turn * 60)
        thoughts.record("recall", "the pears were early this year", chance=0.5, decision="shown", subject=f"sentence {turn}",
                        request_id=rid, ref=f"recall:{rid}:31", now=now - turn * 60)
    for i in range(20):
        records.ran("PLAY_TUNE", i < 8, now - (1 + i % 5) * DAY - i * 60, said=f"tune {i}", args={"query": f"tune {i}"})
        records.ran("SET_VOLUME", True, now - (1 + i % 5) * DAY - i * 60, said=f"volume {i}", args={"level": i})
        records.figure(epistemics.ROUTE, f"route:r{i}", 0.9, i != 0, now - (1 + i % 5) * DAY)
    records.ran("TELL_TIME", True, now - DAY)
    records.ran("OPEN_APP", False, now - DAY, said="Notes is not installed", args={"name": "Notes"})
    lesson = lessons.propose("OPEN_APP", "the app is not installed", ["Notes failed"], "open the site instead", now=now - DAY)
    thoughts.record("lesson", "open the site instead", decision="taken", subject="action:OPEN_APP", ref=f"lesson:{int(lesson)}",
                    now=now - DAY)
    return ids, int(lesson)


def test_the_evidence_wall_is_sent_one_record_for_one_fact_what_matters_first_and_nothing_that_settles_nothing(monkeypatch, records):
    ids, lesson = _seed_a_wall_that_repeats_itself(records)
    msg = _knowledge_message(monkeypatch, records)
    beliefs = {o["id"]: o["claim"] for o in msg["objects"] if o["kind"] == "claim"}
    values = sorted(c["value"] for c in beliefs.values())
    # one sheet for one fact, however often and under whatever relation the store keeps it
    assert sum("Rowan" in v for v in values) == 1, values
    assert sum("written steps" in v for v in values) == 1, values
    named = next(c for c in beliefs.values() if "Rowan" in c["value"])
    assert len(named["also"]) == 2 and all("Rowan" in a["value"] and a["id"].startswith("claim_") for a in named["also"])
    # the fuller record of a fact is the one kept, and carries the one it says again
    kept = next(c for c in beliefs.values() if "pears" in c["value"])
    assert [a["value"] for a in kept["also"]] == ["Keeps bees."]
    # what differs by a figure, or denies, is a fact of its own
    assert sum("Tide Lines" in v for v in values) == 2 and "Does not keep bees any more." in values
    wall = msg["evidence"]["wall"]
    refs = [e["ref"] for e in wall]
    assert len(refs) == len(set(refs)), "no record is pinned up twice"
    rows = {r["id"]: r for r in msg["evidence"]["actions"]["rows"]}
    figures = {r["id"]: r for r in msg["evidence"]["accuracy"]}
    objects = {o["id"] for o in msg["objects"]}
    for e in wall:
        assert e["ref"] in objects or e["ref"] in rows or e["ref"] in figures, f"{e} is a record of this message"
    assert {e["kind"] for e in wall} == {"conflict", "claim", "action", "lesson", "accuracy"}, "no thought is a sheet of its own"
    # what is in dispute first: the question, then the beliefs it is about
    assert wall[0]["kind"] == "conflict" and beliefs[wall[1]["ref"]]["relation"] == "lives_in"
    # then each kind of evidence in turn, so the nearest sheets are not all of one kind
    after = [e["kind"] for e in wall[2:6]]
    assert after == ["claim", "action", "lesson", "accuracy"], after
    # an action whose record says something is on the wall; one run once that worked is on the scoreboard alone
    on_wall = [e["ref"] for e in wall if e["kind"] == "action"]
    assert on_wall[0] == "action_PLAY_TUNE", "the one shown worse than the rest first"
    assert "action_OPEN_APP" in on_wall, "an action a lesson was learned about"
    assert "action_TELL_TIME" not in on_wall and "action_TELL_TIME" in rows
    # a thought is evidence of the record it is of, folded into it
    folded = {e["ref"]: e["also"] for e in wall}
    bees = next(i for i, c in beliefs.items() if "pears" in c["value"])
    assert f"claim_{ids['Keeps bees.']}" in folded[bees]
    assert sum(a.startswith("thought_") for a in folded[bees]) == 3, "the three turns a reply rested on it"
    assert sum(a.startswith("thought_") for a in folded["accuracy_route"]) == 3, "each reading is one of the figures measured"
    assert sum(a.startswith("thought_") for a in folded[f"book_lesson_{lesson}"]) == 1
    recalled = {f"thought_{t['id']}" for t in msg["thoughts"] if t["kind"] == "recall"}
    assert len(recalled) == 3 and not recalled & {a for e in wall for a in e["also"]}, \
        "a memory shown to a reply is no evidence of anything on the wall while none is settled"
    # and the same record thought of in three turns hangs as one sheet in its room
    notes = [o for o in msg["objects"] if o["kind"] == "thought"]
    assert sum(o["room"] == "evidence_wall" for o in notes) == 1
    assert sum(o["room"] == "memory_archive" for o in notes) == 1, "one memory recalled in three turns is one sheet"


def test_a_name_too_long_for_its_label_ends_at_a_word_and_says_it_goes_on(monkeypatch, records):
    from eli.memory import claims
    conn = records.memory._claims_conn()
    value = "Prefers every reply to begin with the conclusion and to leave the reasoning for afterwards."
    claims.record(conn, "preference.session", value, origin="user_said", subject=records.memory._claim_subject())
    conn.commit()
    conn.close()
    [belief] = [o for o in _knowledge_message(monkeypatch, records)["objects"] if o["kind"] == "claim"]
    assert belief["claim"]["value"] == value, "the record itself is whole"
    assert belief["name"].endswith("…") and (belief["name"][:-1] + " ") in f"preference.session: {value} "[:60] + " " or \
        f"preference.session: {value}".startswith(belief["name"][:-1])
    cut = belief["name"][:-1]
    assert f"preference.session: {value}"[len(cut)] == " ", "cut between words, never inside one"


def test_elis_books_fill_what_the_rest_of_the_message_leaves_and_the_rest_are_read_whole_by_id(monkeypatch, records):
    from eli.world import books
    for n in range(40):
        records.turn(f"s{n}", "user", f"Question number {n} " + "about the garden " * 20, T0 + n * DAY)
        records.turn(f"s{n}", "assistant", f"Answer number {n} " + "with the roses " * 20, T0 + n * DAY + 60)
    everything = _knowledge_message(monkeypatch, records)
    talks = [o for o in everything["objects"] if o["kind"] == "book" and o["book"]["of"] == "conversation"]
    assert len(talks) == 40 and all("sections" in o["book"] for o in talks), "with room, every book is sent whole"
    bare = json.loads(json.dumps(everything))
    for obj in bare["objects"]:
        obj.get("book", {}).pop("sections", None)
    budget = len(json.dumps(bare)) + 6000                  # room for every book, and the text of a few
    every = len([o for o in everything["objects"] if o["kind"] == "book"])       # the conversations and their weeks
    msg = _knowledge_message(monkeypatch, records, budget=budget)
    assert len(json.dumps(msg, default=str)) <= budget, "a message is never larger than the body takes"
    sent = [o for o in msg["objects"] if o["kind"] == "book"]
    whole = [o for o in sent if "sections" in o["book"] and o["book"]["of"] == "conversation"]
    assert len(sent) == every and 0 < len(whole) < 40, "every book, the text of as many as fit"
    talks = [o for o in sent if o["book"]["of"] == "conversation"]
    assert [o["when"] for o in whole] == sorted((o["when"] for o in talks), reverse=True)[:len(whole)], "the newest first"
    rest = next(o for o in talks if "sections" not in o["book"])
    assert rest["book"]["length"] > 0, "a book sent without its text says how long it is"
    assert books.read(rest["id"])["sections"][0]["text"].startswith("Question number"), "and is read whole by its id"
    others = dict(bare, objects=[o for o in bare["objects"] if o["kind"] != "book"])
    one = max(len(json.dumps(o)) + 2 for o in bare["objects"] if o["kind"] == "book")
    tight = _knowledge_message(monkeypatch, records, budget=len(json.dumps(others)) + 10 * one)
    assert 10 <= len([o for o in tight["objects"] if o["kind"] == "book"]) < every, "with less room, fewer books"
    assert not [o for o in _knowledge_message(monkeypatch, records, budget=len(json.dumps(others)))["objects"]
                if o["kind"] == "book"], "with none, none: the rest of the message is never cut for them"


def test_a_book_is_read_whole_over_http_and_anything_else_is_not_found(monkeypatch, records):
    client, _ = _client(monkeypatch)
    records.turn("s1", "user", "Where did I leave the blue kettle?", T0)
    records.turn("s1", "assistant", "On the windowsill, you said.", T0 + 60)
    from eli.world import books
    [talk] = [b for b in books.listed() if b["of"] == "conversation"]
    got = client.get(f"/v1/world/book/{talk['id']}")
    assert got.status_code == 200
    body = got.json()
    assert body["id"] == talk["id"] and body["book"]["of"] == "conversation" and "session" not in body["book"]
    assert [s["text"] for s in body["sections"]] == ["Where did I leave the blue kettle?", "On the windowsill, you said."]
    for other in ("book_conversation_0000", "book_nothing_1", "memory_1"):
        assert client.get(f"/v1/world/book/{other}").status_code == 404, other


# ── every store in this module's own folder, with or without the suite conftest ─────────────────
# On the --noconftest lane (CI's API lane) nothing set the stores aside, so these tests read and
# wrote this machine's own records: the thoughts, the agenda, the world state and the model list,
# on a developer's machine the owner's live data, and brought the owner's ~/.eli_user_id along. The
# module puts every store in a folder of its own, the way the conftest does for the suite
# (tests/_state_env.py), and the last test checks nothing was opened anywhere else.
from tests import _state_env


@pytest.fixture(scope="module", autouse=True)
def _every_store_in_this_modules_own_folder(tmp_path_factory):
    with _state_env.own_state(tmp_path_factory.mktemp("eli_state")) as held:
        yield held


def test_zz_no_test_here_reads_or_writes_this_machines_own_records(_every_store_in_this_modules_own_folder):
    """Runs last in the module (by name and place): every store resolves inside the module's own
    folder, and no test above opened a file or a database under this machine's own state."""
    held = _every_store_in_this_modules_own_folder
    assert not _state_env.outside(held.root)
    assert not held.opened_elsewhere, sorted(set(held.opened_elsewhere))[:20]
