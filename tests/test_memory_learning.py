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
