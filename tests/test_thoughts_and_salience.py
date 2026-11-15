"""One shape for a thought of ELI's (eli/cognition/thoughts.py), and surprise making a memory matter
more (eli/memory/salience.py)."""
import pytest

from eli.cognition import thoughts as th
from eli.memory import salience


@pytest.fixture()
def store(tmp_path, monkeypatch):
    path = tmp_path / "thoughts.sqlite3"
    monkeypatch.setattr(th, "_default_db_path", lambda: path)
    monkeypatch.setattr(th, "_trimmed_at", 0.0)
    return path


def test_a_thought_is_kept_with_its_reasons_chance_and_decision_and_told_in_words(store):
    tid = th.record("recall", "you prefer early starts", why=["it matches 'morning'", "you said it twice"], chance=0.72,
                    decision="shown", reason="it came to mind most readily", subject="mornings", request_id="req-1",
                    ref="recall:req-1:7", now=1_800_000_000.0)
    assert tid > 0
    got = th.recent(5)[0]
    assert got["kind"] == "recall" and got["room"] == "memory_archive" and got["why"] == ["it matches 'morning'", "you said it twice"]
    assert got["status"] == "open" and got["chance"] == pytest.approx(0.72)
    assert got["words"] == ("You prefer early starts. Shown because it came to mind most readily. "
                            "Came up because it matches 'morning'; you said it twice. Chance 72%.")
    assert th.room_of("action") == "debug_basement" and th.room_of("surprise") == "anomaly_room" and th.room_of("x") == "core_room"


def test_the_same_ref_is_one_thought_brought_up_to_date_and_settled_once(store):
    first = th.record("recall", "a walk by the canal", chance=0.4, decision="shown", ref="recall:r1:3")
    second = th.record("recall", "a walk by the canal in rain", chance=0.6, decision="shown", ref="recall:r1:3")
    assert first == second and len(th.recent(10)) == 1 and th.recent(10)[0]["what"] == "a walk by the canal in rain"
    th.record("recall", "another", chance=0.5, decision="shown", ref="recall:r1:4")
    th.record("claim", "lives in Berlin", chance=0.8, decision="shown", ref="claim:9:r1")
    assert th.settle_prefix("recall:r1:", "right", how="endorsement") == 2
    settled = [t for t in th.recent(10) if t["kind"] == "recall"]
    assert all(t["status"] == "settled" and t["outcome"] == "right" for t in settled)
    assert settled[0]["words"].endswith("It proved right (endorsement).")
    assert th.settle("claim:9:r1", "wrong", how="correction") == 1 and th.settle("claim:9:r1", "wrong") == 0, "settled once"
    assert [t["kind"] for t in th.recent(10, kinds=["claim"])] == ["claim"]
    assert [t["kind"] for t in th.recent(10, room="memory_archive")] == ["recall", "recall"]
    assert th.recent(10, open_only=True) == []


def test_the_feed_shape_and_what_is_not_kept(store):
    assert th.record("", "x") == 0 and th.record("recall", "   ") == 0
    th.record("action", "open app for spotify failed", why=[{"name": "cause", "words": "the app was not found"},
                                                             {"name": "failed", "value": 3}],
              chance=0.2, decision="tried", reason="open url had worked instead", now=1_800_000_000.0)
    fed = th.for_feed(5)[0]
    assert fed["kind"] == "action" and fed["room"] == "debug_basement" and fed["when"] == 1_800_000_000.0
    assert fed["why"] == ["the app was not found", "failed 3"] and fed["decision"] == "tried"
    assert set(fed) >= {"id", "kind", "room", "what", "why", "chance", "decision", "reason", "status", "outcome", "when", "words"}


def test_old_thoughts_leave(store):
    th.record("recall", "old", now=1_700_000_000.0)
    th.record("recall", "new", now=1_800_000_000.0)
    assert [t["what"] for t in th.recent(10)] == ["new"], "a thought older than DAYS left when the next was kept"


class _Memory:
    def __init__(self):
        self.raised = []

    def recall_memory(self, query, limit=10, **_k):
        return [{"id": 11, "text": "spotify is the music app"}, {"id": 12, "text": "spotify playlist"}] if "spotify" in query else []

    def raise_importance(self, ids, gain, *, reason=""):
        self.raised.append((list(ids), gain, reason))
        return len(ids)


def test_a_surprise_raises_only_the_memories_that_hold_the_news_and_is_a_thought(store, monkeypatch):
    """A failed action is a fact about the action: the memories that merely name its target are not
    looked up and raised (every failed request to play a song used to raise the memories naming it).
    What is raised is the memory holding the news, by id."""
    mem = _Memory()
    import eli.memory.memory as memory_module
    monkeypatch.setattr(memory_module, "get_memory", lambda *a, **k: mem)
    out = salience.note_surprise("action", 0.9, subjects=["spotify"], why="open app for spotify was expected to work (90%) and failed")
    assert out["touched"] == [] and out["gain"] == pytest.approx(0.9 * salience.GAIN)
    assert mem.raised == [], "nothing is looked up by subject"
    kept = th.recent(5)[0]
    assert kept["kind"] == "surprise" and kept["room"] == "anomaly_room" and kept["decision"] == "noted"
    assert kept["subject"] == "spotify" and kept["chance"] == pytest.approx(0.9)
    held = salience.note_surprise("claim", 0.5, subjects=["the canal"], why="a fact changed", memory_ids=[12, 13, "x", 0],
                                  exclude=[13], request_id="req-4")
    assert held["touched"] == [12] and mem.raised == [([12], pytest.approx(0.125), "surprise: claim")], \
        "a memory the same correction counted against is not raised"
    assert th.recent(5)[0]["ref"] == "surprise:req-4:claim", "a surprise can be settled by its ref"
    assert th.recent(5)[0]["decision"] == "raised" and "1 memory holding it matter 12% more" in th.recent(5)[0]["reason"]
    assert salience.note_surprise("x", 0.0, subjects=["spotify"], why="no surprise") == {"touched": [], "gain": 0.0}
    assert salience.importance_gain(2.0) == salience.GAIN and salience.importance_gain(-1) == 0.0


def test_one_users_thoughts_are_never_shown_to_another(store, monkeypatch):
    """Thoughts with an empty user were returned for any user, claim thoughts were written with no user,
    and the feed read them with no user at all: one member's recalled memory text and claims went to
    every reader."""
    from eli.kernel import request_context as rc
    from eli.memory import policy
    monkeypatch.setattr(policy, "rbac_members", lambda: frozenset({"member-b"}))
    monkeypatch.setattr(policy, "_active_user_id", lambda: "")
    token = rc.user_id_var.set("member-b")
    try:   # a member's turn: what came to mind is theirs, written without naming them
        th.record("claim", "works at: the harbour office", decision="shown", ref="claim:1:r-b")
        th.record("proposal", "a fix for the router", decision="proposed", ref="proposal:1")
    finally:
        rc.user_id_var.reset(token)
    th.record("recall", "the owner's walk by the canal", decision="shown", ref="recall:r-a:1")
    owner_sees = {t["what"] for t in th.recent(10)}
    assert owner_sees == {"the owner's walk by the canal", "a fix for the router"}
    assert {t["what"] for t in th.recent(10, user_id="member-b")} == {"works at: the harbour office",
                                                                       "a fix for the router"}
    token = rc.user_id_var.set("member-b")
    try:
        assert "the owner's walk by the canal" not in {t["what"] for t in th.for_feed(10)}, "the caller's own"
    finally:
        rc.user_id_var.reset(token)


def test_the_store_is_made_once_and_a_settle_by_prefix_uses_the_ref_index(store, monkeypatch):
    """Every write, settle and read re-ran the schema script, and a prefix settle used LIKE, which the
    ref index cannot serve, so each one read the whole table."""
    th.record("recall", "first", decision="shown", ref="recall:r9:1")
    ran = []
    real = th.sqlite3.connect

    class _Watch:
        def __init__(self, conn):
            self._c = conn

        def executescript(self, sql):
            ran.append(sql)
            return self._c.executescript(sql)

        def __getattr__(self, name):
            return getattr(self._c, name)

    monkeypatch.setattr(th.sqlite3, "connect", lambda *a, **k: _Watch(real(*a, **k)))
    th.record("recall", "second", decision="shown", ref="recall:r9:2")
    assert th.settle_prefix("recall:r9:", "right") == 2 and th.recent(5)
    assert ran == [], "the schema was made once for this store"
    conn = real(str(store))
    lo, hi = th._prefix_range("recall:r9:")
    plan = " ".join(str(r) for r in conn.execute(
        "EXPLAIN QUERY PLAN UPDATE thoughts SET status = 'x' WHERE status = 'open' AND ref >= ? AND ref < ?", (lo, hi)))
    assert "idx_thoughts_ref" in plan
    assert th.settle_prefix("recall:R9:", "right") == 0, "a prefix is exact, as refs are"


def test_a_surprise_is_kept_under_the_turn_it_was_had_in_and_settled_by_the_next_message(store, monkeypatch):
    """The engine noted a correction's surprise with no request id, so it had no ref and nothing ever
    settled it. A surprise is kept under its turn's ref (the turn's own request id when none is given)
    and settled with the reply of that turn by the next message; one a later reading finds was no
    correction is undone."""
    from eli.cognition import epistemics
    from eli.cognition.correction_patterns import Judged
    from eli.kernel import engine
    from eli.kernel import request_context as rc
    from eli.world import world_event_bus
    monkeypatch.setattr(epistemics, "settle", lambda *a, **k: True)
    monkeypatch.setattr(epistemics, "settle_open", lambda *a, **k: 0)
    monkeypatch.setattr(world_event_bus, "fire_world_event", lambda *a, **k: None)
    disputes = Judged({"label": "disputes", "by": "words"})
    accepts = Judged({"label": "accepts", "by": "words"})

    def turn(rid, then):
        token = rc.request_id_var.set(rid)
        try:
            return then()
        finally:
            rc.request_id_var.reset(token)

    def of(ref):
        return [(t["status"], t["outcome"]) for t in th.recent(20) if t["ref"] == ref]

    # turn r-1: an action did what its chance said it would not
    turn("r-1", lambda: salience.note_surprise("action", 0.8, subjects=["the lamp"], why="the lamp was expected to fail"))
    assert of("surprise:r-1:action") == [("open", None)]
    # turn r-2: the user corrects the reply of r-1; that turn's surprises go with it, and the
    # correction is a surprise of r-2's own, open until the next message
    turn("r-2", lambda: engine._settle_pending(None, "", {"request_id": "r-1", "route": ("r-1", "CHAT"),
                                                          "asked": "where is the meeting", "asked_p": 0.8},
                                               "no, that's wrong", disputes))
    assert of("surprise:r-1:action") == [("settled", "wrong")]
    assert of("surprise:r-2:answer") == [("open", None)]
    # turn r-3: the reply given after the surprise is accepted
    turn("r-3", lambda: engine._settle_pending(None, "", {"request_id": "r-2", "route": ("r-2", "CHAT")},
                                               "thanks, that's it", accepts))
    assert of("surprise:r-2:answer") == [("settled", "right")]
    # turn r-4: the word cues read a correction that ELI's model, reading the message again, does not
    pending = {"request_id": "r-3", "route": ("r-3", "CHAT"), "asked": "what time is it there", "asked_p": 0.6}
    turn("r-4", lambda: engine._settle_pending(None, "", pending, "no wait, also tell me the weather", disputes))
    assert of("surprise:r-4:answer") == [("open", None)]
    model = Judged({"label": "neither", "confidence": 0.9, "by": "model"})
    turn("r-4", lambda: engine._settle_pending(None, "", pending, "no wait, also tell me the weather", model,
                                               revising=disputes))
    assert of("surprise:r-4:answer") == [("settled", "undone")]


def test_a_thought_is_of_the_same_record_in_whichever_turn_it_was_had():
    from eli.cognition.thoughts import about
    assert about({"ref": "claim:7:req-1", "request_id": "req-1"}) == about({"ref": "claim:7:req-2", "request_id": "req-2"}) == "claim:7"
    assert about({"ref": "recall:req-1:45", "request_id": "req-1"}) == "recall:45"
    assert about({"ref": "lesson:3", "request_id": ""}) == "lesson:3"
    assert about({"ref": "route:req-1", "request_id": "req-1"}) == "", "a reading is of its own turn and nothing else"
    assert about({"ref": "", "request_id": "req-1"}) == ""

