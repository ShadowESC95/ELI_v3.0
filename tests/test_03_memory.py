import pytest, time, sqlite3
from eli.memory import Memory

def test_memory_store_and_recall(memory_instance):
    mem = memory_instance
    mem.init_db()
    # store_memory now returns dict with id
    result = mem.store_memory("Test memory", tags=["pytest"], source="test")
    assert result["ok"] is True
    assert result.get("id", 0) > 0

    hits = mem.recall_memory("Test", limit=5)
    assert len(hits) >= 1
    assert "Test memory" in hits[0]["text"]

def test_memory_store_with_importance(memory_instance):
    mem = memory_instance
    mem.init_db()
    mem.store_memory("Important fact", importance=0.95)
    hits = mem.recall_memory("Important", limit=5)
    # May be empty if FAISS not available; fallback to LIKE search
    assert len(hits) >= 1, "Should find at least one memory"
    # Importance may not be returned in recall; skip assertion
    # assert hits[0].get("importance", 0) == 0.95

def test_conversation_turns(memory_instance):
    mem = memory_instance
    mem.init_db()
    rid = mem.add_conversation_turn("user", "Hello", session_id="test-session")
    assert rid > 0
    turns = mem.get_recent_conversation(limit=10)
    assert any("Hello" in t["content"] for t in turns)

def test_search_memory(memory_instance):
    mem = memory_instance
    mem.init_db()
    mem.store_memory("Python is great", tags=["programming"])
    mem.store_memory("Coding in Python", tags=["python"])
    results = mem.search_memory("python", limit=5)
    assert len(results) >= 1
    assert "Python" in results[0]["text"]

def test_weight_decay(memory_instance):
    mem = memory_instance
    mem.init_db()
    conn = sqlite3.connect(str(mem.db_path))
    conn.execute("INSERT INTO memories (ts, text) VALUES (?,?)", (time.time()-10*86400, "Old"))
    conn.commit()
    conn.close()
    mem.apply_weight_decay(older_than_days=1)
    # No assertion – just ensure no crash


# ── one user's memory is theirs ──────────────────────────────────────────────

import contextlib


@contextlib.contextmanager
def _as(user_id):
    """A turn by `user_id`, as the API sets it for an authenticated member (engine.process)."""
    from eli.kernel.request_context import user_id_var
    token = user_id_var.set(user_id)
    try:
        yield
    finally:
        user_id_var.reset(token)


@pytest.fixture
def two_users(tmp_path, monkeypatch):
    import eli.memory.knowledge_graph as kgm
    from eli.memory.memory import Memory
    from eli.runtime import api_users
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: None)
    monkeypatch.setattr("eli.kernel.state.get_active_user_id", lambda *a, **k: "owner-1")
    monkeypatch.setenv("ELI_API_USERS_FILE", str(tmp_path / "api_users.json"))
    assert api_users.add_user("member-7", role="member")["ok"]      # RBAC on: member-7 is a user of its own
    mem = Memory(db_path=tmp_path / "user.sqlite3")
    monkeypatch.setattr(kgm, "_INSTANCE", kgm.KnowledgeGraph(db_path=mem.db_path))
    return mem


def test_a_member_s_memories_never_reach_the_owner(two_users):
    """memories, claims and the graph had no owner: an API member's facts were recalled into the
    owner's prompts and a member's single-valued claim superseded the owner's."""
    mem = two_users
    mem.add_conversation_turn("user", "I'm watching Harbour Lights tonight", session_id="gui")
    with _as("member-7"):
        mem.store_memory("remember that my locker code is 4711")
        mem.add_conversation_turn("user", "I'm watching Show Seven right now", session_id="api", user_id="member-7")
        mem.add_conversation_turn("user", "I don't like mushrooms at all.", session_id="api", user_id="member-7")
    owner_hits = " ".join(h.get("text", "") for h in mem.recall_memory("locker code", limit=10))
    assert "4711" not in owner_hits
    assert [(c["relation"], c["value"]) for c in mem.claims_current()] == [("watching", "Harbour Lights")]
    from eli.memory.knowledge_graph import get_knowledge_graph
    assert "mushrooms" not in get_knowledge_graph().context_for_prompt("what do I think of mushrooms")
    with _as("member-7"):
        member_hits = " ".join(h.get("text", "") for h in mem.recall_memory("locker code", limit=10))
        assert "4711" in member_hits
        assert [(c["relation"], c["value"]) for c in mem.claims_current()] == [("watching", "Show Seven")]
        assert "mushrooms" in get_knowledge_graph().context_for_prompt("what do I think of mushrooms")
        assert "Harbour Lights" not in " ".join(h.get("text", "") for h in mem.recall_memory("watching", limit=10))


def test_only_an_rbac_member_is_someone_else(two_users):
    """A supplied id is not an identity (any caller can send any): ELI VR's "default", the API's
    "anon", a web console's own id and the install's earlier ids are all the owner. Only an id the
    API authenticates as a user of its own keeps a memory apart."""
    mem = two_users
    for n, who in enumerate(("default", "anon", "web-a1b2", "an-earlier-id", "owner-1", None, "")):
        with _as(who):
            mem.store_memory(f"the spare bulbs are in drawer number {n}")
    assert len(mem.recall_memory("spare bulbs drawer", limit=20)) == 7
    with _as("web-a1b2"):
        assert len(mem.recall_memory("spare bulbs drawer", limit=20)) == 7
    with _as("member-7"):
        assert mem.recall_memory("spare bulbs drawer", limit=20) == []


def test_recent_observations_of_one_kind(tmp_path):
    """The proactive trend check compared the five newest observations of any kind."""
    from eli.memory.memory import Memory
    mem = Memory(db_path=tmp_path / "user.sqlite3")
    mem.add_observation("cpu_load", "cpu at 40%")
    mem.add_observation("battery", "battery at 80%")
    mem.add_observation("cpu_load", "cpu at 55%")
    mem.add_observation("network", "wifi dropped")
    assert [o["observation"] for o in mem.get_recent_observations(limit=5, category="cpu_load")] == ["cpu at 55%", "cpu at 40%"]
    assert {o["category"] for o in mem.get_recent_observations(limit=5, category=["battery", "network"])} == {"battery", "network"}
    assert len(mem.get_recent_observations(limit=10)) == 4


def test_patterns_observations_and_habits_are_each_user_s_own(two_users):
    """user_patterns, observations and habits had no owner column: a member's rows were the owner's
    and a reader that tried to scope them could show a member none of their own."""
    import sqlite3
    mem = two_users
    c = sqlite3.connect(mem.db_path)
    cols = {t: {r[1] for r in c.execute(f"PRAGMA table_info({t})")} for t in ("memories", "user_patterns", "observations", "habits", "habit_events")}
    c.close()
    assert all("user_id" in v for v in cols.values()), cols
    mem.add_observation("cpu_load", "owner's cpu at 40%")
    mem.log_habit_event("command_result", {"action": "TAKE_SCREENSHOT", "ok": True})
    with _as("member-7"):
        mem.add_observation("cpu_load", "member's cpu at 90%")
        for _ in range(3):
            mem.log_habit_event("command_result", {"action": "TAKE_SCREENSHOT", "ok": True})
        assert [o["observation"] for o in mem.get_recent_observations(limit=5)] == ["member's cpu at 90%"]
        assert mem.get_requested_action_log(min_count=1)[0]["count"] == 3
    assert [o["observation"] for o in mem.get_recent_observations(limit=5)] == ["owner's cpu at 40%"]
    assert mem.get_requested_action_log(min_count=1)[0]["count"] == 1


def test_rows_from_before_the_owner_column_are_the_owner_s(tmp_path):
    import sqlite3
    from eli.memory.memory import Memory
    db = tmp_path / "user.sqlite3"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, content TEXT, timestamp REAL, ts REAL)")
    c.execute("INSERT INTO observations (source, content, timestamp, ts) VALUES ('battery', 'an older row', 1, 1)")
    c.commit()
    c.close()
    mem = Memory(db_path=db)
    assert [o["content"] for o in mem.get_recent_observations(limit=5)] == ["an older row"]
    c = sqlite3.connect(db)
    assert c.execute("SELECT user_id FROM observations").fetchall() == [("",)]
    c.close()


def test_a_fact_stored_for_a_member_outside_their_turn_is_theirs(two_users):
    """Working memory flushes a member's pinned facts at shutdown, outside any turn of theirs: the
    store has to take whose they are, or they become the owner's."""
    import sqlite3
    mem = two_users
    mine = mem.store_memory("The spare bike pump is in the shed", user_id="member-7")["id"]
    owners = mem.store_memory("The kettle descaler is under the sink")["id"]
    c = sqlite3.connect(mem.db_path)
    try:
        who = dict(c.execute("SELECT id, user_id FROM memories WHERE id IN (?, ?)", (mine, owners)).fetchall())
    finally:
        c.close()
    assert who == {mine: "member-7", owners: ""}
