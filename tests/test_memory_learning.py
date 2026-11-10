"""What the outcomes do to a memory (eli/memory/memory.py): a surprise raises its importance, use
promotes it and corrections demote it in the daily upkeep, and a memory let go is a thought with its
reason."""
import pytest

from eli.cognition import thoughts as th
from eli.runtime import evidence_ledger as led


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from eli.memory import interest
    from eli.memory.memory import Memory
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: path)
    monkeypatch.setattr(th, "_default_db_path", lambda: path)
    monkeypatch.setattr(interest, "share", lambda text, user_id="", **k: 0.0)
    return Memory(db_path=tmp_path / "user.sqlite3")


def _importance(mem, mid):
    conn = mem._get_connection()
    try:
        return float(conn.execute("SELECT COALESCE(importance, 0.5) FROM memories WHERE id = ?", (int(mid),)).fetchone()[0])
    finally:
        conn.close()


def test_a_surprise_raises_importance_part_of_the_way_to_one(mem):
    a = mem.store_memory("the canal path floods in spring", source="user")["id"]
    before = _importance(mem, a)
    assert mem.raise_importance([a], 0.5, reason="test") == 1
    assert _importance(mem, a) == pytest.approx(before + (1.0 - before) * 0.5)
    assert mem.raise_importance([a], 1.0) == 1 and _importance(mem, a) == pytest.approx(1.0), "never past one"
    assert mem.raise_importance([a], 0.0) == 0 and mem.raise_importance([], 0.5) == 0


def test_use_promotes_a_memory_and_corrections_demote_it_in_the_upkeep(mem):
    a = mem.store_memory("a kayak needs a licence on the canal", source="user")["id"]
    b = mem.store_memory("the lock keeper is there on weekdays", source="user")["id"]
    for _ in range(3):
        mem.record_recall_outcome([a], helped=True)
    mem.record_recall_outcome([b], helped=True)
    for _ in range(3):
        mem.record_recall_outcome([b], helped=False)
    assert mem.promote_used(dry_run=True)["promoted"] == 1, "a dry run changes nothing"
    out = mem.promote_used()
    assert out == {"promoted": 1, "demoted": 1, "dry_run": False}
    assert _importance(mem, a) == pytest.approx((3 + 1) / (3 + 0 + 2)), "at least its share of helpful uses"
    assert _importance(mem, b) == pytest.approx((1 + 1) / (1 + 3 + 2)), "no higher than its share"
    assert mem.promote_used() == {"promoted": 0, "demoted": 0, "dry_run": False}, "pure arithmetic on the counts"
    kept = th.recent(5, kinds=["memory"])[0]
    assert kept["what"].startswith("1 memories that helped answers matter more, 1 that were corrected matter less")


def test_a_memory_let_go_is_a_thought_with_its_reason(mem):
    mem.store_memory("the user said the index rebuilt", source="user")
    c = mem.store_memory("the index rebuilt overnight", source="eli", kind="observation")["id"]
    conn = mem._get_connection()
    try:
        conn.execute("UPDATE memories SET weight = 0.05, origin = 'eli_said' WHERE id = ?", (int(c),))
        conn.commit()
    finally:
        conn.close()
    assert mem.archive_faded(dry_run=True)["archived"] == 1
    assert mem.archive_faded()["archived"] == 1
    kept = th.recent(5, kinds=["memory"])
    assert len(kept) == 1 and kept[0]["decision"] == "dropped" and kept[0]["ref"] == f"memory:{c}"
    assert kept[0]["chance"] == pytest.approx(0.05) and "it never went into an answer" in kept[0]["why"]
    assert "archive" in kept[0]["reason"] and mem.search_archive("index rebuilt")


# ── association: what went together into endorsed answers comes to mind together ──────────────

_HOUSE = [
    "the spare key is under the blue flowerpot at the front door",
    "the alarm panel is behind the coat rack, the code starts with seven",
    "the front door paint is peeling near the letterbox",
    "the house next door has a new dog",
    "the front door hinges need oil",
    "the back door sticks in winter",
    "the front garden gate is broken",
]
# (question, what answers it), asked after the first two went together into endorsed answers
_LATER = [("locked out at the front door again", {0, 1}),
          ("where is the spare key for the front door", {0, 1})]


def _recall_at(mem, k):
    found = total = 0
    for question, wanted in _LATER:
        got = [h.get("text") for h in mem.recall_memory(question, limit=k)]
        found += sum(1 for i in wanted if _HOUSE[i] in got)
        total += len(wanted)
    return found / total


def _household(tmp_path, monkeypatch, name):
    from eli.memory.memory import Memory
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: None)
    m = Memory(db_path=tmp_path / f"{name}.sqlite3")
    ids = [m.store_memory(t, source="eli")["id"] for t in _HOUSE]
    for _ in range(2):
        m.record_recall_outcome([ids[0], ids[1]], helped=True)
    return m, ids


def test_endorsed_answers_link_their_memories_and_the_link_fades_without_use(mem):
    a = mem.store_memory("the canal path floods in spring", source="user")["id"]
    b = mem.store_memory("wellingtons are by the back door", source="user")["id"]
    c = mem.store_memory("the lock keeper is there on weekdays", source="user")["id"]
    assert mem.strengthen_links([a]) == 0, "a link needs two"
    mem.record_recall_outcome([a, b], helped=True)
    mem.record_recall_outcome([a, c], helped=False)
    import time as _time
    conn = mem._get_connection()
    try:
        now = _time.time()
        fresh = mem._linked(conn, [a], now)
        later = mem._linked(conn, [a], now + 90 * 86400)
    finally:
        conn.close()
    assert set(fresh) == {b}, "only what went into an endorsed answer is linked"
    assert later[b][a] < fresh[b][a] / 5, "unused, a link fades as a memory does"


def test_association_improves_recall_or_it_is_switched_off(tmp_path, monkeypatch):
    """The measured condition for keeping association: recall@k with it beats recall@k without it on
    questions whose answer went before with another memory into endorsed answers. If it ever stops
    doing so, this fails and eli/memory/memory.ASSOCIATION must be switched off."""
    import eli.memory.memory as mm
    if not mm.ASSOCIATION:
        pytest.skip("association is switched off")
    on, _ = _household(tmp_path, monkeypatch, "on")
    with_links = _recall_at(on, 3)
    monkeypatch.setattr(mm, "ASSOCIATION", False)
    without_links = _recall_at(on, 3)
    assert with_links > without_links, (with_links, without_links)
