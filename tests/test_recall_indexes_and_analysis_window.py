"""Two memory faults found by measuring a live 2.3.10 database.

**The indexes never existed.** `_ensure_memory_schema` carried four CREATE INDEX
statements, but they sat in the middle of schema creation with
`CREATE INDEX ... ON conversation_turns(...)` running ~20 lines BEFORE
`CREATE TABLE ... conversation_turns`. All four shared one `try` ending in
`except Exception: pass`, so the "no such table" abandoned the rest and reported
nothing. Verified on the operator's live DB: `memories` (441 rows),
`conversation_turns` (617) and `observations` (114) had NO indexes, while
kg_entities/runtime_events/emotion_events — indexed elsewhere — had theirs. Every
recall was `SCAN` + `USE TEMP B-TREE FOR ORDER BY`.

**SELF_ANALYZE hid its own window.** It looks back 7 days and said "No recent
failures found" — while SELF_IMPROVE, using a different lookback, reported
"failures_inspected: 3" seconds later. Both were correct; the seven stored failures
were 9.8-15.1 days old. Two self-reports contradicting each other because neither
said what it had looked at.
"""
import sqlite3
import time

import pytest


# ── indexes ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "user.sqlite3"
    conn = sqlite3.connect(str(path))
    from eli.memory.memory import _ensure_full_memory_schema, _ensure_profile_schema
    _ensure_full_memory_schema(conn)
    _ensure_profile_schema(conn)
    conn.commit()
    yield conn
    conn.close()


def _indexes(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")}


@pytest.mark.parametrize("name", [
    "idx_memories_ts",
    "idx_memories_timestamp",
    "idx_memories_importance",
    "idx_conversations_user_session",
    "idx_conversation_turns_session",
    "idx_conversation_turns_ts",
    "idx_observations_timestamp",
])
def test_every_recall_index_exists_on_a_fresh_database(db, name):
    assert name in _indexes(db)


@pytest.mark.parametrize("query", [
    "SELECT * FROM memories ORDER BY ts DESC LIMIT 10",
    "SELECT * FROM conversation_turns ORDER BY timestamp DESC LIMIT 20",
    "SELECT * FROM observations ORDER BY timestamp DESC LIMIT 8",
])
def test_recall_queries_no_longer_sort_in_a_temp_btree(db, query):
    """The temp B-tree WAS the cost — an unindexed ORDER BY sorts the whole table
    in memory on every recall, several times per turn."""
    plan = " ".join(r[3] for r in db.execute("EXPLAIN QUERY PLAN " + query))
    assert "USE TEMP B-TREE" not in plan, f"still sorting in memory: {plan}"
    assert "USING INDEX" in plan, f"not using an index: {plan}"


def test_index_creation_runs_after_every_table_exists(db):
    """The original ordering bug: an index created before its table.

    A fresh database is the exact case that failed — if ordering regresses, the
    conversation_turns indexes are the ones that vanish."""
    assert "idx_conversation_turns_session" in _indexes(db)
    assert "idx_conversation_turns_ts" in _indexes(db)


def test_one_failing_index_cannot_prevent_the_others(tmp_path, caplog):
    """Four statements shared one try, so the first failure took the rest with it."""
    from eli.memory.memory import _ensure_memory_indexes
    conn = sqlite3.connect(str(tmp_path / "partial.sqlite3"))
    # Only ONE of the indexed tables exists; the rest must still be attempted.
    conn.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY, timestamp REAL)")
    _ensure_memory_indexes(conn)
    assert "idx_observations_timestamp" in _indexes(conn)
    conn.close()


def test_index_failures_are_logged_not_swallowed(tmp_path, caplog):
    from eli.memory.memory import _ensure_memory_indexes
    conn = sqlite3.connect(str(tmp_path / "empty.sqlite3"))
    with caplog.at_level("DEBUG"):
        _ensure_memory_indexes(conn)   # no tables at all — every statement fails
    conn.close()
    assert any("index" in r.message.lower() for r in caplog.records), \
        "a database where every index failed produced no log line"


# ── the analysis window ───────────────────────────────────────────────────────

@pytest.fixture()
def engine_with_old_failures(tmp_path, monkeypatch):
    """Seven open failures, all older than the 7-day window — the live shape."""
    monkeypatch.setenv("ELI_DATA_DIR", str(tmp_path))
    from eli.core import paths
    for fn in ("data_dir", "config_dir", "cache_dir"):
        f = getattr(paths, fn, None)
        if hasattr(f, "cache_clear"):
            f.cache_clear()

    # The engine is a module singleton, and by this point in a suite run it is
    # already bound to whatever database imported first. Reset it, or the fixture
    # silently tests the dev tree's real failures instead of the ones seeded here.
    import eli.runtime.self_improvement as si
    monkeypatch.setattr(si, "_self_engine", None, raising=False)

    from eli.memory.memory import get_agent_memory
    engine = si.SelfImprovementEngine(memory=get_agent_memory(db_path=str(tmp_path / "agent.sqlite3")))
    monkeypatch.setattr(si, "_self_engine", engine, raising=False)

    # Setting ELI_DATA_DIR fires vector_store's legacy-index migration, which copies
    # the dev tree's index into the tmp dir AND leaves the module singleton bound to
    # it — poisoning every later test that touches the vector store. Reset it on the
    # way in and the way out.
    try:
        from eli.memory.vector_store import reset_vector_store
        reset_vector_store()
    except Exception:
        pass

    conn = engine.memory._get_connection()
    conn.execute("DELETE FROM failures")
    old = time.time() - (12 * 86400)
    for i in range(7):
        conn.execute(
            "INSERT INTO failures (timestamp, user_input, command, error, status, occurrence_count) "
            "VALUES (?,?,?,?,?,?)",
            (old, f"input {i}", f"CMD_{i}", f"error {i}", "open", 1))
    conn.commit()
    conn.close()
    yield engine

    # Teardown matters as much as setup here. paths.data_dir() is lru_cached, so
    # clearing it only on the way IN leaves it pinned to this tmp_path for every
    # later test — artifacts_dir() is an alias of it, and the vector store resolves
    # through that. Live symptom: test_vector_store_isolation and
    # test_recent_memory_processing_no_gguf failed only when run after this file.
    try:
        from eli.memory.vector_store import reset_vector_store
        reset_vector_store()
    except Exception:
        pass
    for fn in ("data_dir", "config_dir", "cache_dir"):
        f = getattr(paths, fn, None)
        if hasattr(f, "cache_clear"):
            f.cache_clear()


def test_older_open_failures_are_counted(engine_with_old_failures):
    older = engine_with_old_failures.failures_outside_window(days=7)
    assert older["count"] == 7
    assert older["oldest_days"] >= 11


def test_the_seven_day_window_genuinely_finds_none(engine_with_old_failures):
    """The report was not lying — it just was not saying what it had looked at."""
    assert engine_with_old_failures.analyze_failures(days=7, min_cluster_size=1) == []


def test_a_wider_window_finds_them(engine_with_old_failures):
    found = engine_with_old_failures.analyze_failures(days=30, min_cluster_size=1)
    assert len(found) == 7


def test_the_report_states_its_window_and_the_backlog(engine_with_old_failures):
    from eli.execution.executor_enhanced import execute
    text = execute("SELF_ANALYZE", {}).get("response") or ""
    assert "last 14 day" in text, "the unified 14-day window should be stated"
    assert "7 open issue" in text, "the older backlog is still hidden"
    assert "No recent failures found." not in text, "the misleading phrasing survives"


# ── the live schema path, not only the test one ─────────────────────────────────────────────────

def _live(tmp_path, monkeypatch):
    from eli.memory.memory import Memory
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: None)
    return Memory(db_path=tmp_path / "live.sqlite3")


def test_the_live_schema_creates_the_recall_indexes_and_the_reads_use_them(tmp_path, monkeypatch):
    """Only _ensure_memory_schema (init_db, tests) created the indexes: a live database opened through
    Memory carried none of them, and every recency read was SCAN + TEMP B-TREE."""
    mem = _live(tmp_path, monkeypatch)
    conn = mem._get_connection()
    try:
        assert {"idx_conversation_turns_time", "idx_conversation_turns_session_time", "idx_memories_time"} <= _indexes(conn)
        for sql in ("SELECT content FROM conversation_turns ORDER BY COALESCE(timestamp, ts, 0) DESC LIMIT 16",
                    "SELECT content FROM conversation_turns WHERE session_id = 's' ORDER BY COALESCE(timestamp, ts, 0) DESC LIMIT 8",
                    "SELECT text FROM memories ORDER BY COALESCE(timestamp, ts, 0) DESC LIMIT 10"):
            plan = " ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
            assert "TEMP B-TREE" not in plan, (sql, plan)
    finally:
        conn.close()


def test_conversation_search_uses_the_turns_full_text_index(tmp_path, monkeypatch):
    mem = _live(tmp_path, monkeypatch)
    mem.add_conversation_turn("user", "we were driving to the coast on saturday", session_id="a")
    mem.add_conversation_turn("user", "the drive home took forever", session_id="a")
    mem.add_conversation_turn("user", "something else entirely", session_id="a")
    hits = [h["content"] for h in mem.search_conversations("drive coast", limit=10)]
    assert hits == ["we were driving to the coast on saturday", "the drive home took forever"]
    both = [h["content"] for h in mem.search_conversations("drive home", limit=10, match_all=True)]
    assert both == ["the drive home took forever"]
    assert [h["content"] for h in mem.search_conversations("satur", limit=10)] == [
        "we were driving to the coast on saturday"], "a keyword finds the words it starts"
    conn = mem._get_connection()
    try:
        conn.execute("UPDATE conversation_turns SET content = 'gone' WHERE content LIKE '%coast%'")
        conn.commit()
    finally:
        conn.close()
    assert [h["content"] for h in mem.search_conversations("coast", limit=10)] == [], "the index follows the turn"


def test_a_connection_does_not_migrate_a_database_already_brought_up(tmp_path, monkeypatch):
    """Every connection re-ran the whole migration (about 190 PRAGMA table_info calls)."""
    import eli.memory.memory as mm
    mem = _live(tmp_path, monkeypatch)
    mem._get_connection().close()
    ran = []
    real = mm._ensure_full_memory_schema
    monkeypatch.setattr(mm, "_ensure_full_memory_schema", lambda conn: ran.append(1) or real(conn))
    for _ in range(5):
        mem._get_connection().close()
    assert ran == []
    (tmp_path / "live.sqlite3").unlink()
    for extra in ("-wal", "-shm"):
        (tmp_path / f"live.sqlite3{extra}").unlink(missing_ok=True)
    conn = mem._get_connection()
    try:
        assert ran == [1] and conn.execute("SELECT COUNT(*) FROM memories").fetchone() == (0,), \
            "a database replaced underneath is brought up again"
    finally:
        conn.close()


def test_with_no_user_named_a_conversation_read_is_the_turns_own_users(tmp_path, monkeypatch):
    """An API member's turns reached the owner's per-turn readers (anti-repeat, output governor)."""
    from eli.kernel import request_context as rc
    from eli.memory import policy
    mem = _live(tmp_path, monkeypatch)
    mem.add_conversation_turn("user", "the owner's own words here", user_id="default-user")
    mem.add_conversation_turn("user", "a member's private words here", user_id="member-7")
    monkeypatch.setattr(policy, "rbac_members", lambda: frozenset({"member-7"}))
    owner = [t["content"] for t in mem.get_recent_conversation(limit=10)]
    assert owner == ["the owner's own words here"]
    token = rc.user_id_var.set("member-7")
    try:
        assert [t["content"] for t in mem.get_recent_conversation(limit=10)] == ["a member's private words here"]
        assert [h["content"] for h in mem.search_conversations("words", limit=10)] == ["a member's private words here"]
    finally:
        rc.user_id_var.reset(token)


def test_what_was_just_said_is_read_from_this_conversation_and_its_last_reply():
    """The readers that mean "this conversation" (the last reply a pronoun points at, the turns a
    search points back at) read the user's turns of every conversation; and the last-reply reader took
    the oldest of the last six turns, oldest first as they come, for the last reply."""
    from eli.execution.effectors import memory_ops
    from eli.kernel import request_context as rc
    asked = []

    class Mem:
        def get_recent_conversation(self, limit=20, session_id=None, **k):
            asked.append(session_id)
            return [{"role": "assistant", "content": "Playing — spotify (Harbour Lights — Old Pier)"},
                    {"role": "user", "content": "nice"},
                    {"role": "assistant", "content": "Now playing Marigold Avenue by Quiet Rivers."}]
    assert rc.turn_session() is None, "outside a turn there is none"
    token = rc.turn_facts_var.set({"session_id": "desk-1"})
    try:
        assert rc.turn_session() == "desk-1", "a desktop turn's conversation is the engine's own session"
        named = memory_ops._named_in_last_reply(Mem())
    finally:
        rc.turn_facts_var.reset(token)
    assert asked == ["desk-1"]
    assert "Marigold" in named and not {"Harbour", "Lights", "Pier"} & set(named), "the last reply, not the oldest"
    token = rc.session_id_var.set("api-7")
    try:
        assert rc.turn_session() == "api-7", "the caller's own session first"
    finally:
        rc.session_id_var.reset(token)


def test_the_users_own_name_in_the_last_reply_is_not_who_him_means(monkeypatch):
    """"You remember him?" points at someone ELI just named, never at the user being greeted by name;
    the user's name comes from their profile, not from a name written into the code."""
    from eli.execution.effectors import memory_ops
    from eli.kernel import state

    class Mem:
        def get_recent_conversation(self, limit=20, session_id=None, **k):
            return [{"role": "assistant", "content": "Good to hear from you Tomas. Now playing Marigold Avenue by Quiet Rivers."}]
    monkeypatch.setattr(state, "get_user_name", lambda *a, **k: "Tomas")
    named = memory_ops._named_in_last_reply(Mem())
    assert "Tomas" not in named and "Marigold" in " ".join(named), named
    monkeypatch.setattr(state, "get_user_name", lambda *a, **k: "")
    assert "Tomas" in memory_ops._named_in_last_reply(Mem()), "someone else's name is still offered"


def test_the_last_resort_memory_write_says_when_nothing_was_stored(monkeypatch):
    """When the store's own write raised, the handler fell back to a memory adapter that no longer
    exists and then to Memory.store_memory, and said "memory stored" whatever that returned."""
    from eli.execution.effectors import memory_ops

    def broken(text, tags=None):
        raise OSError("the store could not be opened")

    class Refusing:
        def store_memory(self, text, tags=""):
            return {"ok": False, "skipped": True, "reason": "persistence_gate"}

    monkeypatch.setattr(memory_ops, "memory_store", broken)
    monkeypatch.setattr("eli.memory.memory.get_memory", lambda *a, **k: Refusing())
    out = memory_ops._h_memory_store("MEMORY_STORE", {"text": "where did I put the kettle?"})
    assert out["ok"] is False and out["skipped"] is True and "persistence_gate" in out["content"]
