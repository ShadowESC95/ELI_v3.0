"""Forgetting a memory removes it and what was made from it: index entries, claims, summaries, profile items, stances, graph, turns."""
import json
import sqlite3
import time

import pytest

from eli.memory.memory import Memory


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_TEST_MODE", "1")
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: None)
    return Memory(db_path=tmp_path / "user.sqlite3")


def _q(mem, sql, *a):
    c = sqlite3.connect(mem.db_path)
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def _seed(mem):
    text = "I live in Lisbon and the spare key code is ORCHID-7319 for my sister Priya"
    rid = mem.store_memory(text, source="user")["id"]
    other = mem.store_memory("I prefer green tea with a slice of ginger every afternoon", source="user")["id"]
    child = mem.store_memory("Summary: the user has a spare key code ORCHID-7319", source="assistant", kind="summary")["id"]
    mem.link_derivation(child, [rid], "summary")
    from eli.cognition.stance_store import ensure_tables
    from eli.runtime.profile_extractor import ensure_profile_tables
    from eli.runtime.user_model import ensure_user_model_row
    ensure_profile_tables(mem.db_path)
    ensure_user_model_row("u", mem.db_path)
    mem.save_session_summary("s", "u", summary="They shared the key code ORCHID-7319 and Priya", turns_count=2)
    mem.save_session_summary("s2", "u", summary="They talked about tea and gardening", turns_count=2)
    c = sqlite3.connect(mem.db_path)
    ensure_tables(c.cursor())
    now = time.time()
    c.execute("insert into semantic (user_id, fact, evidence, evidence_count, created_at) values ('u', 'Has a spare key', ?, 1, ?)",
              (json.dumps([text]), now))
    c.execute("insert into semantic (user_id, fact, evidence, evidence_count, created_at) values ('u', 'Likes green tea', ?, 1, ?)",
              (json.dumps(["I prefer green tea with a slice of ginger every afternoon"]), now))
    c.execute("update user_model set brief = 'Key code ORCHID-7319 kept for Priya', dossier = 'Likes tea' where user_id = 'u'")
    c.execute("insert into eli_stances (topic, position) values ('key code', 'ORCHID-7319 is not to be shared')")
    c.execute("insert into conversation_turns (session_id, user_id, role, content, ts, timestamp) values ('s','u','user',?,?,?)", (text, now, now))
    c.commit()
    c.close()
    return rid, other, child, text


def test_forget_removes_the_memory_and_everything_made_from_it(mem):
    rid, other, child, text = _seed(mem)
    report = mem.forget([rid])
    assert report["memories"] == 2 and report["derived"] == 1
    assert report["claims"] >= 1 and report["semantic"] == 1 and report["summaries"] == 1
    assert report["profile"] == 1 and report["stances"] == 1 and report["turns"] == 1
    assert not _q(mem, "select id from memories where id in (?, ?)", rid, child)
    assert not _q(mem, "select child_id from memory_lineage")
    assert [r[0] for r in _q(mem, "select fact from semantic")] == ["Likes green tea"]
    assert [r[0] for r in _q(mem, "select summary from session_summaries")] == ["They talked about tea and gardening"]
    brief, dossier = _q(mem, "select brief, dossier from user_model")[0]
    assert not brief and dossier == "Likes tea"
    assert mem.claims_current() == []
    assert _q(mem, "select id from memories where id = ?", other)


def test_forget_leaves_unrelated_memories_and_can_be_repeated(mem):
    rid, other, _child, _text = _seed(mem)
    mem.forget([rid])
    again = mem.forget([rid])
    assert again["memories"] == 0
    assert mem.recall_memory("green tea ginger", limit=3)


def test_a_summary_of_a_fact_is_not_independent_evidence(mem):
    a = mem.store_memory("The lease starts in March according to the landlord letter", source="user")["id"]
    b = mem.store_memory("Summary of the letter: the lease starts in March", source="assistant", kind="summary")["id"]
    c = mem.store_memory("A second separate note says the lease starts in March", source="user")["id"]
    mem.link_derivation(b, [a], "summary")
    assert mem.lineage_root(b) == a
    assert mem.independent_sources([a, b]) == 1
    assert mem.independent_sources([a, b, c]) == 2


def test_forget_candidates_lists_what_would_go(mem):
    rid, *_ = _seed(mem)
    assert rid in [c["id"] for c in mem.forget_candidates("spare key code ORCHID-7319")]


def _derived_rows(mem):
    """A semantic fact, a profile pattern, a stance and a profile item about a friend, each from its
    own statement that mentions the friend too."""
    from eli.cognition.stance_store import ensure_tables
    from eli.runtime.profile_extractor import ensure_profile_tables
    from eli.runtime.user_model import ensure_user_model_row
    ensure_profile_tables(mem.db_path)
    ensure_user_model_row("u", mem.db_path)
    mem.store_memory("Sam is coming over for dinner on Friday", source="user")
    mem.store_memory("Tomorrow is bin day on our street", source="user")
    c = sqlite3.connect(mem.db_path)
    ensure_tables(c.cursor())
    now = time.time()
    c.execute("insert into semantic (user_id, fact, evidence, evidence_count, created_at) values ('u', 'Sam likes tennis', ?, 1, ?)",
              (json.dumps(["Sam is coming over for dinner on Friday"]), now))
    c.execute("insert into user_patterns (pattern_type, pattern_data, ts) values ('friend', 'Sam plays tennis on Sundays', ?)", (now,))
    c.execute("insert into user_patterns (pattern_type, pattern_data, ts) values ('routine', 'Tomorrow is bin day', ?)", (now,))
    c.execute("insert into eli_stances (topic, position) values ('Sam and tennis', 'Sam should rest his knee')")
    c.execute("update user_model set interests = ? where user_id = 'u'", (json.dumps(["tennis with Sam"]),))
    c.commit()
    c.close()


def test_forgetting_one_memory_keeps_what_merely_shares_a_word_with_it(mem):
    """Every capitalised word was a mark: forgetting a memory that named a friend, or began with
    "Tomorrow", deleted every unrelated fact, stance and pattern that mentioned the word."""
    _derived_rows(mem)
    rid = mem.store_memory("Remember that Sam's locker code is 4411", source="user")["id"]
    other = mem.store_memory("Tomorrow I have the dentist at 3pm", source="user")["id"]
    report = mem.forget([rid, other])
    assert report["memories"] == 2
    assert report["semantic"] == 0 and report["patterns"] == 0 and report["stances"] == 0 and report["profile"] == 0
    assert [r[0] for r in _q(mem, "select fact from semantic")] == ["Sam likes tennis"]
    assert sorted(r[0] for r in _q(mem, "select pattern_data from user_patterns")) == [
        "Sam plays tennis on Sundays", "Tomorrow is bin day"]
    assert _q(mem, "select count(*) from eli_stances") == [(1,)]
    assert _q(mem, "select interests from user_model") == [(json.dumps(["tennis with Sam"]),)]


def test_forget_takes_the_words_out_of_every_store_that_copied_them(mem, monkeypatch):
    """'Forgotten' left the words in the learning replay, the latest-turn copy, the claim's source text,
    ELI's thoughts and the vector index's saved text."""
    removed = []

    class VS:
        def add(self, text, metadata=None):
            return True

        def mark_memories_deleted(self, ids):
            return len(list(ids))

        def remove_memories(self, ids):
            removed.append(sorted(ids))
            return len(removed[-1])

    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: VS())
    said = "I work nights at the ZETA-4242 depot these days"
    mem.add_conversation_turn("user", said, session_id="s1")
    rid = mem.store_memory(said, source="user")["id"]
    mem.record_claims_from(said, rid)
    c = sqlite3.connect(mem.db_path)
    from eli.cognition.thoughts import _SCHEMA
    c.executescript(_SCHEMA)
    c.execute("insert into thoughts (ts, kind, what, decision) values (?, 'memory', ?, 'shown')", (time.time(), said[:140]))
    c.commit()
    c.close()
    report = mem.forget([rid])
    assert removed == [[rid]] and report["vectors"] == 1
    assert report["replay"] >= 1 and report["conversations"] == 1 and report["thoughts"] == 1
    c = sqlite3.connect(mem.db_path)
    try:
        holding = []
        for (table,) in c.execute("select name from sqlite_master where type='table' and name not like '%fts%'"):
            cols = [r[1] for r in c.execute(f"pragma table_info({table})") if r[2].upper() in ("TEXT", "")]
            for col in cols:
                if c.execute(f'select 1 from "{table}" where lower("{col}") like ? limit 1', ("%zeta-4242%",)).fetchone():
                    holding.append(f"{table}.{col}")
    finally:
        c.close()
    # the audit chain commits to each row's content: it keeps them and says so
    assert holding == ["runtime_events.content"] and report["audit_log_kept"] >= 1, holding


def test_the_forget_reply_says_what_was_kept_apart_from_what_went(monkeypatch):
    """The audit log's rows that cannot be edited were listed under "Forgotten"."""
    from eli.execution.effectors import memory_ops

    class _Mem:
        def forget(self, ids):
            return {"memories": 2, "claims": 1, "vectors": 0, "audit_log_kept": 3}

    monkeypatch.setattr("eli.memory.get_memory", lambda *a, **k: _Mem())
    out = memory_ops._h_memory_forget("MEMORY_FORGET", {"confirm": True, "ids": [11, 12]})
    assert out["ok"] is True
    assert out["content"] == "Forgotten: 2 memories, 1 claims. Kept, as it cannot be removed: 3 audit log."
