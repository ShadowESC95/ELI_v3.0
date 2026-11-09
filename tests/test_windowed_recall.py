"""A question about a period is answered from that period, with dates, not from the best keyword match."""
import sqlite3
import time
from types import SimpleNamespace

import pytest

from eli.cognition.evidence_format import when_label
from eli.cognition.query_planner import parse_window
from eli.memory.memory import Memory
from eli.memory.unified_retrieval import format_period_log, orchestrator_retrieve

DAY = 86400.0


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_TEST_MODE", "1")
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: None)
    mem = Memory(db_path=tmp_path / "user.sqlite3")
    now = time.time()
    c = sqlite3.connect(mem.db_path)
    for days, text in [(3, "i am watching the lighthouse keeper again, the second one"),
                       (9, "just started the new marlow film"),
                       (33, "i am watching puffin cove, not much of a deal")]:
        ts = now - days * DAY
        c.execute("insert into conversation_turns (session_id, user_id, role, content, ts, timestamp) "
                  "values ('s', 'old-id', 'user', ?, ?, ?)", (text, ts, ts))
    c.commit()
    c.close()
    return SimpleNamespace(memory=mem, session_id="s", user_id="a-different-id")


def _plan(q):
    return {"need_keyword": True, "need_semantic": True, "window": parse_window(q)}


def test_only_the_period_asked_about_is_returned(engine):
    """The period's own turns reach the prompt through the period log, whole and in order
    (mixed into the semantic hits they were cut, and quick mode dropped them entirely)."""
    q = "What movies or series was i watching the past week or two?"
    kw, sem, tr = orchestrator_retrieve(engine, q, q, _plan(q))
    said = format_period_log(tr, q) + " ".join(h["text"] for h in kw + sem)
    assert "lighthouse keeper" in said and "marlow" in said
    assert "puffin cove" not in said


def test_without_a_period_nothing_is_filtered_by_time(engine):
    q = "what do you know about puffin cove"
    kw, sem, tr = orchestrator_retrieve(engine, q, q, {"need_keyword": True, "need_semantic": True})
    assert "puffin cove" in " ".join(h["text"] for h in kw + sem)


def test_every_hit_exposes_its_date_to_ranking_and_display(engine):
    q = "what was i watching in the past week"
    kw, sem, tr = orchestrator_retrieve(engine, q, q, _plan(q))
    turns = tr.conv_hits
    assert turns and all(h.get("timestamp") for h in turns)
    assert all(when_label(h["timestamp"]).count(",") == 1 for h in turns)
    log_lines = format_period_log(tr, q).splitlines()[1:]
    assert log_lines and all(line.startswith("[") for line in log_lines)


def test_turns_from_earlier_user_ids_are_still_found(engine):
    q = "what was i watching the past week"
    _, sem, _ = orchestrator_retrieve(engine, q, q, _plan(q))
    assert any("lighthouse keeper" in h["text"] for h in sem)


def test_the_date_filter_finds_memories_the_topic_search_cannot(engine):
    mem = engine.memory
    now = time.time()
    mem.store_memory("Booked the flat viewing for Thursday afternoon", kind="note", tags="", importance=0.7)
    c = sqlite3.connect(mem.db_path)
    c.execute("update memories set event_ts=?, timestamp=?, ts=? where text like 'Booked the flat%'",
              (now - 4 * DAY, now - 4 * DAY, now - 4 * DAY))
    c.commit()
    c.close()
    q = "past week?"
    assert not mem.recall_memory(q, limit=12), "the topic search alone must not reach it"
    kw, sem, tr = orchestrator_retrieve(engine, q, q, _plan(q))
    assert "flat viewing" in " ".join(h["text"] for h in kw + sem)
    stats = tr.window_stats
    assert stats["added_by_time"] >= 1 and stats["in_window"] >= 1


def test_the_search_reports_what_the_window_did(engine):
    from eli.cognition import memory_diag
    q = "what was going on with me over the past week?"
    kw, sem, tr = orchestrator_retrieve(engine, q, q, _plan(q))
    diag = memory_diag.retrieval_record(len(kw), len(sem), 0, len(kw) + len(sem), None, window=tr.window_stats)
    assert "time-bounded to" in memory_diag.block(diag)
    assert "time-bounded to" in memory_diag.explanation(diag)


def test_a_deeper_pass_is_not_served_the_shallower_cached_result(engine):
    """The deepening loop asks the same question with larger limits; a cache keyed on the text alone replayed the first answer."""
    from eli.memory.retrieval import invalidate_turn_cache, retrieve_for_turn
    invalidate_turn_cache()

    class _Mem:
        def recall_memory(self, q, limit=10, **kw):
            return [{"id": str(i), "text": f"row {i}"} for i in range(limit)]
        def search_conversations(self, *a, **k): return []
        def get_recent_conversation(self, *a, **k): return []
        def get_session_summaries(self, *a, **k): return []

    shallow = retrieve_for_turn(_Mem(), "same question", session_id="s", semantic_limit=4, rerank=False, enable_hop2=False)
    deep = retrieve_for_turn(_Mem(), "same question", session_id="s", semantic_limit=12, rerank=False, enable_hop2=False)
    assert len(shallow.semantic_hits) == 4 and len(deep.semantic_hits) == 12


@pytest.mark.parametrize("asked, topic", [
    ("Are you serious? When did i drive to the coast, because it was weeks ago!", "the coast"),
    ("give me the exact day that i went to the coast..", "the coast"),
    ("when did we talk about the garden", "the garden"),
    ("give me the timestamp then", ""),
    ("I am looking for timestamps of that interaction Eli!", ""),
])
def test_when_did_i_mention_x_goes_to_the_conversation_log(asked, topic):
    """Asked when they told ELI about a trip, the user got a time made up from memories whose dates had
    moved, then "the timestamps aren't recorded"; "give me the timestamp then" went to GET_TIME."""
    from eli.execution.router_enhanced import route
    r = route(asked)
    assert r["action"] == "MESSAGE_TIME_QUERY" and r["args"]["topic"] == topic


@pytest.mark.parametrize("asked", ["what time is it", "when did I last restart", "add timestamps to my log",
                                   "Hey Eli, what is the story?"])
def test_other_time_questions_are_not_taken_for_it(asked):
    from eli.execution.router_enhanced import route
    assert route(asked)["action"] != "MESSAGE_TIME_QUERY"


def test_the_answer_lists_every_message_by_day_and_follows_up_on_the_last_topic(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_TEST_MODE", "1")
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: None)
    mem = Memory(db_path=tmp_path / "user.sqlite3")
    monkeypatch.setattr("eli.memory.get_memory", lambda *a, **k: mem)
    first = time.mktime((2026, 9, 10, 10, 5, 0, 0, 0, -1))
    rows = [(first, "we're heading to the coast this weekend. leaving early"),
            (first + 46800, "back from the coast tomorrow"),
            (first + DAY + 300, "i am driving to the shop"),
            (first + 40 * DAY, "When did i drive to the coast?"),
            (first + 40 * DAY + 60, "give me the timestamp then")]
    c = sqlite3.connect(mem.db_path)
    for ts, text in rows:
        c.execute("insert into conversation_turns (session_id, user_id, role, content, ts, timestamp) "
                  "values ('s', 'u', 'user', ?, ?, ?)", (text, ts, ts))
    c.commit()
    c.close()
    from eli.execution.executor_enhanced import execute
    # every word must appear: "driving to the shop" is not about the trip
    assert "can't find" in execute("MESSAGE_TIME_QUERY", {"topic": "drive to the coast"})["content"]
    for topic in ("the coast", ""):         # "" is "give me the timestamp then": the last topic asked about
        out = execute("MESSAGE_TIME_QUERY", {"topic": topic})["content"]
        assert "coast” in 2 messages" in out and "Thu 10 Sep 2026 at 10:05" in out and "10:05, 23:05" in out, out
        assert "shop" not in out and "When did" not in out


# ── ELI VR in the period's record ────────────────────────────────────────────────────────────────

def test_the_period_log_holds_who_came_into_elis_world_and_what_was_said_there(monkeypatch):
    """Asked "were we in VR yesterday?", the log held only what was typed and run."""
    import eli.world.embodiment as embodiment
    start = time.time() - DAY
    asked = []

    def sessions_between(since, until):
        asked.append((since, until))
        return [{"surface": "spatial_client", "session": "eli-vr-1a2b", "headset": True, "started": start,
                 "ended": start + 42 * 60, "rooms": ["the range"], "end": "the link closed"}]

    monkeypatch.setattr(embodiment, "sessions_between", sessions_between, raising=False)
    tr = SimpleNamespace(window_stats={"since": start - 3600, "until": start + 7200}, actions=[],
                         conv_hits=[{"content": "set up the targets again", "timestamp": start + 60, "session_id": "eli-vr-1a2b"},
                                    {"content": "back at the desk now", "timestamp": start + 3000, "session_id": "desk"}])
    out = format_period_log(tr, "were we in vr yesterday?")
    assert asked == [(start - 3600, start + 7200)]
    assert "you came in with the headset, in the range" in out and "you left after 42 min (the link closed)" in out
    assert "You (in VR): set up the targets again" in out and "You: back at the desk now" in out
    assert out.index("came in") < out.index("set up the targets") < out.index("left after")


def test_without_a_presence_record_the_period_log_is_as_it_was(monkeypatch):
    import eli.world.embodiment as embodiment
    monkeypatch.setattr(embodiment, "sessions_between", lambda since, until: [], raising=False)
    start = time.time() - DAY
    tr = SimpleNamespace(window_stats={"since": start, "until": start + 3600}, actions=[],
                         conv_hits=[{"content": "hello", "timestamp": start + 60, "session_id": "desk"}])
    out = format_period_log(tr, "what did i say?")
    assert "ELI's world" not in out and "You: hello" in out


def test_a_period_says_where_each_turn_was_said_and_who_came_into_the_rooms(engine):
    """A VR evening read as plain "You:" lines, and the arrival in ELI's rooms was not in the period
    log at all: ranked among the day's memories, the presence record was cut."""
    mem = engine.memory
    now = time.time()
    arrived = now - 2 * DAY
    c = sqlite3.connect(mem.db_path)
    c.execute("insert into conversation_turns (session_id, user_id, role, content, ts, timestamp) "
              "values ('eli-vr-1786000000', 'default', 'user', 'can you light the fire in here', ?, ?)",
              (arrived + 60, arrived + 60))
    c.commit()
    c.close()
    for i in range(40):                      # a busy day of stronger memories
        mem.store_memory(f"the greenhouse tomato row number {i} was watered", importance=0.95,
                         metadata={"event_ts": arrived + i})
    mem.store_memory("A body arrived: ELI VR (headset) joined ELI in the Reading Room.", tags=["embodiment", "body", "vr"],
                     source="embodiment", kind="event", importance=0.1, metadata={"event_ts": arrived})
    q = "what did we do the past week"
    _kw, _sem, tr = orchestrator_retrieve(engine, q, q, _plan(q))
    log_text = format_period_log(tr, q)
    assert "You (in VR): can you light the fire in here" in log_text
    assert "Presence: A body arrived: ELI VR (headset) joined ELI in the Reading Room." in log_text
    lines = log_text.splitlines()
    assert lines.index(next(l for l in lines if "Presence:" in l)) < lines.index(next(l for l in lines if "in VR" in l))
