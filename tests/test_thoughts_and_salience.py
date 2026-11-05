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


def test_a_surprise_raises_the_memories_about_its_subject_and_is_a_thought(store, monkeypatch):
    mem = _Memory()
    import eli.memory.memory as memory_module
    monkeypatch.setattr(memory_module, "get_memory", lambda *a, **k: mem)
    out = salience.note_surprise("action", 0.9, subjects=["spotify"], why="open app for spotify was expected to work (90%) and failed")
    assert out["touched"] == [11, 12] and out["gain"] == pytest.approx(0.9 * salience.GAIN)
    assert mem.raised == [([11, 12], pytest.approx(0.225), "surprise: action")]
    kept = th.recent(5)[0]
    assert kept["kind"] == "surprise" and kept["room"] == "anomaly_room" and kept["decision"] == "raised"
    assert "2 memories about spotify matter 22% more" in kept["reason"] and kept["chance"] == pytest.approx(0.9)
    nothing = salience.note_surprise("claim", 0.5, subjects=["unknown thing"], why="a claim was corrected")
    assert nothing["touched"] == [] and th.recent(5)[0]["decision"] == "noted"
    assert salience.note_surprise("x", 0.0, subjects=["spotify"], why="no surprise") == {"touched": [], "gain": 0.0}
    assert salience.importance_gain(2.0) == salience.GAIN and salience.importance_gain(-1) == 0.0
