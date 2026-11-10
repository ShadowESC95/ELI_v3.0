"""Recall was vector-only in practice, and the keyword index was never read.

`recall_memory` is documented as FAISS-first with FTS5 "as a supplement", gated on:

    _need_keyword = keyword_only or (not populated) or (len(vector) < max(1, limit//2))

FAISS `IndexFlat` applies no similarity threshold — it returns top_k nearest for any
query at all. With 442 vectors and limit=10 that count was always 10, `10 < 5` was
never true, and the branch was unreachable. Meanwhile `memories_fts` held 441 rows,
one per memory, maintained by triggers on every write, and was never queried.

Measured on the live index, the scores that gate had no hope of separating:

    "what did I say about fallout"   -> 10 hits, lowest score 0.559
    "asdfghjkl qwerty nonsense"      -> 10 hits, lowest score 0.524

0.035 of discrimination between a real query and gibberish.

Both channels now always run and are fused by Reciprocal Rank Fusion, which combines
by POSITION rather than score — necessary precisely because FAISS similarity
(1/(1+L2)) and FTS5 BM25 share no scale.
"""
import pytest

from eli.cognition.reranker import RRF_K, fuse_ranked_lists, rerank_candidates


def _c(text, **kw):
    return dict(text=text, **kw)


# ── rank fusion ───────────────────────────────────────────────────────────────

def test_agreement_between_channels_outranks_either_alone():
    """The property a hybrid retriever exists for."""
    fused = fuse_ranked_lists({
        "vector":  [_c("both"), _c("vector only"), _c("v3")],
        "keyword": [_c("keyword only"), _c("both"), _c("k3")],
    })
    assert fused[0]["text"] == "both"
    assert fused[0]["_channels"] == ["keyword", "vector"]


def test_fusion_ignores_incomparable_scores():
    """FAISS 0.55 and BM25 -8.2 must not be summed. Only rank is used."""
    fused = fuse_ranked_lists({
        "vector":  [_c("a", score=0.559), _c("b", score=0.558)],
        "keyword": [_c("b", score=-8.2), _c("a", score=-9.9)],
    })
    # Both appear in both lists at ranks (0,1) and (1,0) — RRF ties them.
    assert {r["text"] for r in fused} == {"a", "b"}
    assert abs(fused[0]["rrf_score"] - fused[1]["rrf_score"]) < 1e-9


def test_rank_position_decides_within_a_channel():
    fused = fuse_ranked_lists({"vector": [_c("first"), _c("second")]})
    assert fused[0]["text"] == "first"
    assert fused[0]["rrf_score"] == pytest.approx(1.0 / (RRF_K + 1))


def test_channels_are_recorded_so_agreement_is_inspectable():
    fused = fuse_ranked_lists({"vector": [_c("x")], "keyword": [_c("x")]})
    assert fused[0]["_channels"] == ["keyword", "vector"]
    assert fused[0]["_channel_ranks"] == {"vector": 0, "keyword": 0}


def test_the_richer_record_survives_a_merge():
    """A vector hit often carries less metadata than the SQL row for the same memory."""
    fused = fuse_ranked_lists({
        "vector":  [{"text": "same memory"}],
        "keyword": [{"text": "same memory", "id": 42, "tags": "preference"}],
    })
    assert fused[0]["id"] == 42 and fused[0]["tags"] == "preference"


def test_empty_and_malformed_input_is_survivable():
    assert fuse_ranked_lists({}) == []
    assert fuse_ranked_lists({"a": [None, 3, {"text": ""}]}) == []


# ── the reranker's use of it ──────────────────────────────────────────────────

def test_reranker_rewards_multi_channel_agreement():
    both = _c("shared result", rrf_score=0.03, _channels=["keyword", "vector"])
    one = _c("shared result other", rrf_score=0.03, _channels=["vector"])
    ranked = rerank_candidates("shared result", [one, both], limit=2)
    assert ranked[0]["text"] == "shared result"


def test_content_overlap_still_outranks_fusion_bonus():
    """Fusion tips ties; it must not override literal relevance — the whole point
    was to stop a retriever's internal confidence beating what the user asked."""
    literal = _c("fallout 4 has AI")
    unrelated = _c("completely different subject", rrf_score=0.05,
                   _channels=["keyword", "vector"])
    ranked = rerank_candidates("fallout", [unrelated, literal], limit=2)
    assert ranked[0]["text"] == "fallout 4 has AI"


def test_the_recall_fit_is_applied_on_its_own_scale(monkeypatch):
    """The fit standardises its features over the outcomes it was fitted on; the reranker applied its
    weights to the raw features (the scale left out), so a fitted ranking was not the fit's."""
    from eli.cognition import epistemics as ep
    rows = [(0.3 + 0.05 * i, float(i % 3 != 0), {"accessibility": 2.0 + i, "importance": 0.2 + 0.07 * i,
                                                 "overlap": 0.1 * i, "agreement": float(i % 2)}) for i in range(10)]
    fit = dict(ep.fit_model(rows, ep.features_of(ep.RECALL)), n=len(rows))
    assert fit.get("scale"), "the fit carries the scale its features were read on"
    monkeypatch.setattr(ep, "weights_for", lambda kind, **k: fit)
    [row] = rerank_candidates("harbour lights", [_c("the harbour lights at dusk", accessibility=3.5, importance=0.6)], limit=1)
    score = min(1.0 - 1e-3, max(1e-3, row["rerank_score"]))
    want = ep.apply_fit(fit, score, {"accessibility": 3.5, "importance": 0.6, "overlap": row["_overlap"],
                                     "agreement": row["_agreement"]})
    assert row["recall_chance"] == round(want, 6)


# ── the keyword query itself ──────────────────────────────────────────────────

def test_fts_query_drops_stopwords():
    """Every token was OR'd, so "what did I say about fallout" matched any memory
    containing "what" or "say". Unreachable code, so never exercised — making the
    channels co-equal surfaced it as noise ranked first."""
    import re

    from eli.runtime.reflection import TOPIC_STOPWORDS
    q = "what did I say about fallout"
    terms = [t for t in re.split(r"[^a-zA-Z0-9_]+", q)
             if len(t) > 1 and t.lower() not in TOPIC_STOPWORDS]
    assert terms == ["fallout"], f"stopwords survived: {terms}"


def test_an_all_stopword_query_still_searches_something():
    """"what about that?" must not become an empty FTS5 query."""
    import re

    from eli.runtime.reflection import TOPIC_STOPWORDS
    q = "what about that"
    terms = [t for t in re.split(r"[^a-zA-Z0-9_]+", q)
             if len(t) > 1 and t.lower() not in TOPIC_STOPWORDS]
    if not terms:
        terms = [t for t in re.split(r"[^a-zA-Z0-9_]+", q) if len(t) > 1]
    assert terms, "fallback produced no search terms at all"


def test_the_keyword_channel_reads_what_was_said_not_the_tags(tmp_path):
    """Tags are split at "_" by the index, so "memory" matched every row tagged memory_recall or
    working_memory: a recall of "memory status" brought back eighteen unrelated messages."""
    from eli.memory.memory import Memory
    mem = Memory(db_path=str(tmp_path / "user.sqlite3"))
    mem.store_memory("Both setups are in Bristol", tags=["working_memory", "memory_recall"])
    mem.store_memory("my memory of the trip is hazy", tags=["conversation"])
    by_keyword = [h["text"] for h in mem.search_memory("memory", limit=10) if "keyword" in h.get("_channels", ())]
    assert by_keyword == ["my memory of the trip is hazy"]


def test_an_index_entry_whose_id_now_belongs_to_another_memory_is_not_recalled(tmp_path, monkeypatch):
    """After a reset the ids start again, and the index's own copy of an old memory was shown under
    the new memory #1: a fresh store recalled "test content" when asked about what it held."""
    from types import SimpleNamespace
    from eli.memory.memory import Memory
    mem = Memory(db_path=str(tmp_path / "user.sqlite3"))
    rowid = mem.store_memory("the kettle in the back kitchen is new", tags=["conversation"])["id"]
    index = SimpleNamespace(_index=SimpleNamespace(ntotal=2), search=lambda q, top_k=5: [
        {"memory_id": rowid, "text": "the shed roof leaks when it rains", "score": 0.9},
        {"memory_id": rowid + 1, "text": "a memory that was deleted", "score": 0.8}])
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: index)
    vector = [h["text"] for h in mem.search_memory("does the shed roof leak", limit=5) if "vector" in h.get("_channels", ())]
    assert vector == []


# ── one ranking: relevance first, priors among the equally relevant ─────────────────────────────

def _quiet_memory(tmp_path, monkeypatch):
    from eli.memory.memory import Memory
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: None)
    return Memory(db_path=str(tmp_path / "user.sqlite3"))


def test_the_memory_holding_every_word_asked_comes_before_important_one_word_matches(tmp_path, monkeypatch):
    """recall_memory fused the channels by rank, then sorted by importance and recency and cut to the
    limit: the limit went to the most important rows, not the most relevant."""
    mem = _quiet_memory(tmp_path, monkeypatch)
    for i in range(6):
        mem.store_memory(f"the red door on the {i}th floor", source="eli", importance=0.95)
    mem.store_memory("my red bicycle needs a repair before the race", source="eli", importance=0.3)
    got = mem.recall_memory("red bicycle repair", limit=3)
    assert got[0]["text"] == "my red bicycle needs a repair before the race", [h["text"] for h in got]


def test_the_keyword_channel_ranks_by_match_not_by_importance(tmp_path, monkeypatch):
    """The FTS5 channel ordered matches by importance*0.6 + time and cut to the limit: the most
    important row holding any one word came first, never the best match (BM25)."""
    mem = _quiet_memory(tmp_path, monkeypatch)
    for i in range(12):
        mem.store_memory(f"red paint sample number {i}", source="eli", importance=0.9)
    best = mem.store_memory("the red bicycle repair kit is in the shed", source="eli", importance=0.2)["id"]
    keyword = [h for h in mem.recall_memory("red bicycle repair", limit=3) if "keyword" in h.get("_channels", ())]
    assert keyword and keyword[0]["id"] == best


def test_a_vector_hit_carries_its_own_importance_not_its_similarity(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from eli.memory.memory import Memory
    mem = Memory(db_path=str(tmp_path / "user.sqlite3"))
    rowid = mem.store_memory("the canal path floods in spring", source="eli", importance=0.2)["id"]
    index = SimpleNamespace(_index=SimpleNamespace(ntotal=1), search=lambda q, top_k=5: [
        {"memory_id": rowid, "text": "the canal path floods in spring", "score": 0.93}])
    monkeypatch.setattr("eli.memory.vector_store.get_vector_store", lambda: index)
    hit = next(h for h in mem.recall_memory("does the towpath flood", limit=3) if h.get("id") == rowid)
    assert hit["importance"] == pytest.approx(0.2) and hit["_similarity"] == pytest.approx(0.93)
    assert "vector" in hit["_channels"] and hit["_channels_ran"] == ["keyword", "vector"]


def test_a_question_with_name_in_it_is_not_flooded_with_every_semantic_fact(tmp_path, monkeypatch):
    """Any query with "name" or "user" in it put every semantic row first, lowest confidence first."""
    mem = _quiet_memory(tmp_path, monkeypatch)
    conn = mem._get_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS semantic (id INTEGER PRIMARY KEY, fact TEXT, tags TEXT, "
                     "confidence REAL, created_at REAL, user_id TEXT DEFAULT '')")
        for i in range(15):
            conn.execute("INSERT INTO semantic (fact, tags, confidence, created_at) VALUES (?, '', ?, 0)",
                         (f"user fact number {i} about gardening", 0.5 + i / 100))
        conn.commit()
    finally:
        conn.close()
    film = mem.store_memory("I loved the film Arrival", source="conversation")["id"]
    got = mem.recall_memory("what was the name of the film I loved", limit=5)
    assert got and got[0]["id"] == film and not any(str(h.get("id", "")).startswith("sem:") for h in got)
    about = mem.recall_memory("what do you know about me", limit=20)
    assert sum(str(h.get("id", "")).startswith("sem:") for h in about) == 15, "a question about the user still reads them"


def test_a_restated_memory_keeps_the_date_it_was_first_said(tmp_path, monkeypatch):
    import sqlite3
    import time as _time
    mem = _quiet_memory(tmp_path, monkeypatch)
    first = mem.store_memory("I adopted a cat called Pebble", source="conversation")["id"]
    said = _time.time() - 20 * 86400
    conn = sqlite3.connect(mem.db_path)
    conn.execute("UPDATE memories SET ts = ?, timestamp = ?, event_ts = ?, last_seen = ? WHERE id = ?",
                 (said, said, said, said, first))
    conn.commit()
    conn.close()
    again = mem.store_memory("I adopted a cat called Pebble", source="conversation")
    assert again["id"] == first and again["deduplicated"]
    hit = next(h for h in mem.recall_memory("cat Pebble", limit=3) if h.get("id") == first)
    from eli.cognition.evidence_format import row_time
    assert abs(row_time(hit) - said) < 1.0, "dated when it was first said, not when it was said again"


def test_a_memory_whose_fact_was_superseded_comes_after_the_one_that_replaced_it(tmp_path, monkeypatch):
    mem = _quiet_memory(tmp_path, monkeypatch)
    old = mem.store_memory("I live in Fenwick Bay near the harbour", source="conversation")["id"]
    new = mem.store_memory("we moved, I live in Ashby Green now", source="conversation")["id"]
    for _ in range(3):
        mem.record_recall_outcome([old], helped=True)        # the old one was used more
    got = [h["id"] for h in mem.recall_memory("where do I live", limit=3)]
    assert got.index(new) < got.index(old), got
    old_hit = next(h for h in mem.recall_memory("where do I live", limit=3) if h["id"] == old)
    assert 0.0 < old_hit["chance"] < 0.5
