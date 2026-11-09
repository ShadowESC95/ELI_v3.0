"""A prune keeps the FAISS index and its metadata in step, never embeds again, and runs once.

`search()` maps a FAISS position straight to `self._meta[idx]`, so a prune moves each vector with
its own metadata row. The old `_prune()` re-embedded the newest 50,000 texts in the caller's thread
on every add past the cap: it kept exactly the cap, so the next add was over it again and every
memory write after that took minutes to hours. Now the kept vectors are copied out of the index,
deleted memories go first, the index goes PRUNE_HEADROOM under the cap so the next prune is that
many adds away, and the cap is what fits in INDEX_RAM_SHARE of the machine's RAM.
"""
import numpy as np
import pytest

from eli.memory import vector_store as vs


class _FakeIndex:
    """Minimal stand-in for faiss.IndexFlatL2 that can also give its vectors back."""

    def __init__(self, dim):
        self.dim = dim
        self.rows = []

    @property
    def ntotal(self):
        return len(self.rows)

    def add(self, arr):
        for row in np.asarray(arr, dtype="float32").reshape(-1, self.dim):
            self.rows.append(row.copy())

    def reconstruct_n(self, i0, n):
        return np.array(self.rows[i0:i0 + n], dtype="float32").reshape(n, self.dim)

    def search(self, vec, k):
        q = np.asarray(vec, dtype="float32").reshape(-1)
        if not self.rows:
            return np.zeros((1, 0), dtype="float32"), np.zeros((1, 0), dtype="int64")
        d = np.array([[float(((r - q) ** 2).sum()) for r in self.rows]], dtype="float32")
        idx = np.argsort(d, axis=1)[:, :k]
        return np.take_along_axis(d, idx, axis=1), idx.astype("int64")


def _vec(text):
    h = (sum(map(ord, text)) * 7919) % 997
    return np.full((1, vs.EMBED_DIM), h / 997.0, dtype="float32")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A store with a deterministic fake embedder and a cap of 6."""
    import threading

    monkeypatch.setattr(vs.faiss, "IndexFlatL2", _FakeIndex, raising=False)

    s = vs.VectorStore.__new__(vs.VectorStore)
    s._lock = threading.RLock()
    s._embed_lock = threading.RLock()
    s._closed = False
    s._index = _FakeIndex(vs.EMBED_DIM)
    s._meta = []
    s._adds_since_save = 0
    s._save_generation = 0
    s._tombstone_ids = set()
    s._entry_cap = 6
    s._index_path = str(tmp_path / "idx.faiss")
    s._meta_path = str(tmp_path / "meta.json")
    s._embedder = object()          # non-None so _embed is attempted
    embedded = []

    def fake_embed(text):
        embedded.append(text)
        return _vec(text)

    monkeypatch.setattr(s, "_embed", fake_embed, raising=False)
    monkeypatch.setattr(s, "_save_async", lambda: None, raising=False)
    monkeypatch.setattr(s, "flush", lambda **k: None, raising=False)
    return s, embedded


def _fill(store, n):
    for i in range(n):
        store._index.add(_vec(f"memory {i}"))
        store._meta.append({"text": f"memory {i}", "memory_id": i})


def test_prune_keeps_index_and_metadata_aligned(store):
    s, _ = store
    _fill(s, 10)
    assert s._prune(6) is True
    assert s._index.ntotal == len(s._meta) == int(6 * (1 - vs.PRUNE_HEADROOM))
    assert [e["text"] for e in s._meta] == [f"memory {i}" for i in range(5, 10)]
    for i, entry in enumerate(s._meta):
        assert np.allclose(s._index.rows[i], _vec(entry["text"])), "a vector moved away from its text"


def test_prune_never_embeds_again(store):
    s, embedded = store
    _fill(s, 10)
    s._prune(6)
    assert embedded == [], f"the prune re-embedded {len(embedded)} texts"


def test_adds_past_the_cap_prune_once_not_on_every_add(store, monkeypatch):
    s, embedded = store
    s._entry_cap = 20
    _fill(s, 20)
    prunes = []
    real = s._prune
    monkeypatch.setattr(s, "_prune", lambda cap: prunes.append(cap) or real(cap))
    for i in range(20, 23):
        assert s.add(f"memory {i}", metadata={"memory_id": i}) is True
    assert len(prunes) == 1, f"pruned {len(prunes)} times for three adds"
    assert embedded == [f"memory {i}" for i in range(20, 23)]     # each new memory once, nothing else
    assert s._index.ntotal == len(s._meta) == 20


def test_deleted_memories_go_before_live_ones(store):
    s, _ = store
    _fill(s, 8)
    s._tombstone_ids = {6, 7}
    s._prune(6)
    kept = [e["memory_id"] for e in s._meta]
    assert 6 not in kept and 7 not in kept
    assert kept == [1, 2, 3, 4, 5]


def test_search_returns_the_text_that_matches_the_vector(store):
    s, _ = store
    _fill(s, 10)
    s._prune(6)
    target = s._meta[-1]["text"]
    hits = s.search(target, top_k=1, min_ratio=0)
    assert hits and hits[0]["text"] == target


def test_rebuild_takes_only_what_fits(store, monkeypatch):
    s, embedded = store
    monkeypatch.setattr(vs, "entry_cap", lambda meta=None: 4)
    entries = [{"text": f"memory {i}", "memory_id": i} for i in range(10)]
    assert s.rebuild_full(entries) is True
    assert [e["memory_id"] for e in s._meta] == [6, 7, 8, 9]
    assert len(embedded) == 4


def test_the_cap_follows_the_machine(monkeypatch):
    import eli.core.hardware_profile as hp
    meta = [{"text": "x" * 100}] * 10
    monkeypatch.setattr(hp, "system_ram_gib", lambda: (32.0, 16.0))
    big = vs.entry_cap(meta)
    monkeypatch.setattr(hp, "system_ram_gib", lambda: (8.0, 4.0))
    small = vs.entry_cap(meta)
    assert big == pytest.approx(4 * small, rel=0.01)
    assert big == int(32 * (1 << 30) * vs.INDEX_RAM_SHARE / (vs.EMBED_DIM * 4 + 100))
    monkeypatch.setattr(hp, "system_ram_gib", lambda: (0.0, 0.0))
    assert vs.entry_cap(meta) == 0, "RAM unknown: nothing is pruned rather than a made-up cap"


def _user_db(tmp_path, monkeypatch, texts):
    import sqlite3
    db = tmp_path / "user.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE memories(id INTEGER PRIMARY KEY, text TEXT, content TEXT, source TEXT, "
                "tags TEXT, kind TEXT, origin TEXT)")
    con.executemany("INSERT INTO memories(id, text, origin) VALUES (?,?,?)",
                    [(i, t, o) for i, (t, o) in enumerate(texts, 1)])
    con.commit()
    con.close()
    monkeypatch.setattr("eli.core.paths.user_db_path", lambda: db)


def test_startup_adds_only_the_memories_the_saved_index_is_missing(store, tmp_path, monkeypatch):
    """Saves are batched, so an unclean exit can leave the newest memories out of the saved index."""
    s, _ = store
    saves = []
    monkeypatch.setattr(s, "flush", lambda **k: saves.append(k), raising=False)
    s._needs_rebuild = False
    _user_db(tmp_path, monkeypatch, [("my cat is called Biscuit", "user_said"),
                                     ("I drive to York on Saturdays", "user_said"),
                                     ("Top topics: a, b, c, d", "telemetry"),
                                     ("my sister lives in Bristol", "user_said")])
    for mid in (1, 2):
        s._index.add(s._embed(f"row {mid}"))
        s._meta.append({"text": f"row {mid}", "memory_id": mid})
    s._auto_rebuild()
    assert [m.get("memory_id") for m in s._meta] == [1, 2, 4]
    assert s._index.ntotal == len(s._meta)
    assert saves == [{"pending_only": True}]


def test_storing_a_memory_does_not_rewrite_the_whole_index(monkeypatch, tmp_path):
    """store_memory saved the index after every row: 18 MB per memory at 2,900 memories."""
    from eli.memory.memory import Memory
    calls = []

    class VS:
        def add(self, text, metadata=None):
            calls.append("add")
            return True

        def flush(self, **k):
            calls.append("flush")

    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: VS())
    mem = Memory(db_path=str(tmp_path / "m.sqlite3"))
    for i in range(3):
        mem.store_memory(f"I visited place number {i} last spring")
    assert calls == ["add", "add", "add"]
