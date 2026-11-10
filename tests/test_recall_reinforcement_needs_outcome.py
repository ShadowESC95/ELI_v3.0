"""Retrieving a memory must not strengthen it; an answer nobody corrected does, and a corrected one weakens it."""
import sqlite3

import pytest

from eli.memory.memory import Memory


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_TEST_MODE", "1")
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: None)
    return Memory(db_path=tmp_path / "user.sqlite3")


def _row(mem, rid):
    c = sqlite3.connect(mem.db_path)
    try:
        return c.execute("select weight, recall_count, exposure_count, corrected_count from memories where id = ?", (rid,)).fetchone()
    finally:
        c.close()


def _weaken(mem, rid):
    c = sqlite3.connect(mem.db_path)
    c.execute("update memories set weight = 0.3 where id = ?", (rid,))
    c.commit()
    c.close()


def test_helped_reinforces_and_counts_a_use(mem):
    rid = mem.store_memory("I keep my running shoes by the back door of the house")["id"]
    _weaken(mem, rid)
    assert mem.record_recall_outcome([rid], helped=True) == 1
    weight, recalls, _exposed, corrected = _row(mem, rid)
    assert weight == 1.0 and recalls == 1 and corrected == 0


def test_corrected_weakens_and_never_counts_as_a_use(mem):
    rid = mem.store_memory("I keep my running shoes by the back door of the house")["id"]
    assert mem.record_recall_outcome([rid], helped=False) == 1
    weight, recalls, _exposed, corrected = _row(mem, rid)
    assert weight <= 0.5 and recalls == 0 and corrected == 1


def test_a_corrected_memory_cannot_be_rescued_by_retrieval_alone(mem):
    rid = mem.store_memory("I keep my running shoes by the back door of the house")["id"]
    mem.record_recall_outcome([rid], helped=False)
    before = _row(mem, rid)
    for _ in range(5):
        mem.recall_memory("running shoes back door", limit=5)
    import time
    time.sleep(0.3)
    after = _row(mem, rid)
    assert after[0] == before[0] and after[1] == before[1]


def test_the_engine_settles_the_previous_answer_from_the_next_message():
    from eli.kernel.engine import CognitiveEngine

    calls = []

    class Mem:
        def record_recall_outcome(self, ids, helped):
            calls.append((list(ids), helped))

    class E:
        memory = Mem()
        _pending_recall_ids = [4, 5]
    e = E()
    CognitiveEngine._settle_recall_outcome(e, "no, that's wrong, I said Tuesday", "user")
    assert calls == [([4, 5], False)] and e._pending_recall_ids is None
    e._pending_recall_ids = [7]
    CognitiveEngine._settle_recall_outcome(e, "thanks, spot on", "user")
    assert calls[-1] == ([7], True), "an endorsement (correction_patterns.reply_verdict) settles them helped"
    e._pending_recall_ids = [9]
    CognitiveEngine._settle_recall_outcome(e, "run the nightly job", "habit")
    assert e._pending_recall_ids == [9]


def test_moving_on_is_no_verdict_on_the_memories_shown():
    """A new request or a change of subject says nothing of whether the memories helped: they are not
    counted as used (one silent use lifted importance to 0.67, five past the pin line)."""
    from eli.kernel.engine import CognitiveEngine

    calls = []

    class Mem:
        def record_recall_outcome(self, ids, helped):
            calls.append((list(ids), helped))

    class E:
        memory = Mem()
        _pending_recall_ids = [4, 5]
    e = E()
    for said in ("what's the weather like in town", "play something calm", "ok"):
        e._pending_recall_ids = [4, 5]
        CognitiveEngine._settle_recall_outcome(e, said, "user")
        assert e._pending_recall_ids is None, "taken either way: the next reply has its own"
    assert calls == []


def test_a_corrected_claim_counts_against_the_memory_it_was_read_from(monkeypatch):
    from eli.cognition import epistemics, thoughts
    from eli.kernel.engine import CognitiveEngine
    from eli.memory import salience

    monkeypatch.setattr(epistemics, "settle", lambda *a, **k: True)
    monkeypatch.setattr(thoughts, "settle", lambda *a, **k: True)
    noted = []
    monkeypatch.setattr(salience, "note_surprise", lambda kind, magnitude, **k: noted.append((kind, k)) or {"touched": []})
    calls = []

    class Mem:
        def record_recall_outcome(self, ids, helped):
            calls.append((list(ids), helped))

    class E:
        memory = Mem()
        _pending_claims = [{"request_id": "claim:3:r1", "relation": "lives_in", "value": "Riverton", "p": 0.7,
                            "memory_id": 21}]
    CognitiveEngine._settle_recall_outcome(E(), "no, I don't live in Riverton", "user")
    assert calls == [([21], False)], "the memory that carried the refuted value is counted against"
    assert noted and noted[0][0] == "claim" and not noted[0][1].get("memory_ids"), "nothing is raised for it"


def test_a_corrected_claims_memory_is_counted_against_once_when_the_model_reads_it_again(monkeypatch):
    """The word cues settle at once and ELI's model may read the same message again later: a claim
    both readings correct counts against its memory once, and one first read as accepted and then as
    disputed counts against it then."""
    from eli.cognition import epistemics, thoughts
    from eli.cognition.correction_patterns import Judged
    from eli.kernel import engine
    from eli.memory import salience

    monkeypatch.setattr(epistemics, "settle", lambda *a, **k: True)
    monkeypatch.setattr(epistemics, "settle_open", lambda *a, **k: 0)
    monkeypatch.setattr(thoughts, "settle", lambda *a, **k: True)
    monkeypatch.setattr(thoughts, "settle_prefix", lambda *a, **k: 0)
    noted = []
    monkeypatch.setattr(salience, "note_surprise", lambda kind, magnitude, **k: noted.append(kind) or {"touched": []})
    calls = []

    class Mem:
        def record_recall_outcome(self, ids, helped):
            calls.append((list(ids), helped))

    pending = {"claims": [{"request_id": "claim:3:r1", "relation": "lives_in", "value": "Riverton", "p": 0.7,
                           "memory_id": 21}]}
    said = "no, I don't live in Riverton"
    words = Judged({"label": "neither", "by": "words"})
    model = Judged({"label": "disputes", "confidence": 0.9, "by": "model"})
    engine._settle_pending(Mem(), "", pending, said, words)
    engine._settle_pending(Mem(), "", pending, said, model, revising=words)
    assert calls == [([21], False)] and noted == ["claim"], "the first reading counted it; the second does not"
    calls.clear()
    noted.clear()
    accepted = Judged({"label": "accepts", "by": "words"})
    engine._settle_pending(Mem(), "", pending, "lovely, Riverton it is", accepted)
    engine._settle_pending(Mem(), "", pending, "lovely, Riverton it is", model, revising=accepted)
    assert calls == [([21], False)], "read as accepted first, the model's dispute counts against it"
