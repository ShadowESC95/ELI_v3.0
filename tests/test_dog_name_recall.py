"""
Regression tests for the "what is my dog's name" recall failure.

Root cause (a dog's name): a fact stated in passing ("Biscuit (my
dog)") was written to the raw conversation_turns log but never promoted into any
durable store (memories / kg_entities), and:
  * the KG extractor had no pattern for possessive relationships ("my dog X"),
  * the raw-log recall fallback matched the whole question verbatim (so
    "%what is my dog's name%" never matched "...my dog Biscuit"),
  * the KG context block truncated at 5 relations without query-awareness, and
  * demoted/contradicted name relations still leaked into context.

These tests lock in the fixes.
"""
import time
import pathlib

import pytest

from eli.memory.knowledge_graph import KnowledgeGraph, reset_knowledge_graph
from eli.memory.memory import Memory


@pytest.fixture
def kg(tmp_path):
    return KnowledgeGraph(db_path=tmp_path / "kg_test.sqlite3")


# ── KG possessive-relationship extraction ──────────────────────────────────

@pytest.mark.parametrize("text,predicate,name", [
    ("haha, i am going to explain the rules to Biscuit (my dog), next", "has_dog", "Biscuit"),
    ("my dog Biscuit is very smart", "has_dog", "Biscuit"),
    ("my dog's name is Rex", "has_dog", "Rex"),
    ("my dog is named Bella", "has_dog", "Bella"),
    ("Rex, my dog, ate the couch", "has_dog", "Rex"),
    ("my wife Sarah cooked dinner", "has_wife", "Sarah"),
    ("my son is called Tom", "has_son", "Tom"),
    ("my puppy Biscuit chewed a shoe", "has_dog", "Biscuit"),   # puppy -> dog
])
def test_kg_extracts_possessive_relationships(kg, text, predicate, name):
    kg.extract_from_memory(text, source="user")
    rels = {(r["predicate"], r["object"]) for r in (kg.query_entity("User") or {}).get("outbound", [])}
    assert (predicate, name) in rels, f"{text!r} -> missing {(predicate, name)}; got {rels}"


@pytest.mark.parametrize("text", [
    "my dog is barking",        # no capitalised name
    "i walked my dog today",    # no name at all
    "i really love my dog",
])
def test_kg_rejects_relationship_noise(kg, text):
    kg.extract_from_memory(text, source="user")
    rels = {(r["predicate"], r["object"]) for r in (kg.query_entity("User") or {}).get("outbound", [])}
    assert not any(p == "has_dog" for p, _ in rels), f"{text!r} wrongly extracted {rels}"


def test_kg_relationship_gated_to_trusted_source(kg):
    """Assistant/synthesised text must not poison the graph with relationships."""
    kg.extract_from_memory("my dog Biscuit is a good boy", source="assistant")
    ent = kg.query_entity("User")
    rels = {(r["predicate"], r["object"]) for r in (ent or {}).get("outbound", [])}
    assert ("has_dog", "Biscuit") not in rels


# ── KG context rendering: query-aware + retired-relation suppression ────────

def test_kg_context_is_query_aware(kg):
    # Bury the dog fact under many higher-volume "prefers" relations.
    for i in range(8):
        kg.extract_from_memory(f"user prefers thing{i}.", source="user")
    kg.extract_from_memory("my dog Biscuit is my dog", source="user")
    ctx = kg.context_for_prompt("what is my dog's name")
    assert "Biscuit" in ctx, f"dog fact crowded out of KG context:\n{ctx}"


def test_kg_context_suppresses_demoted_name(kg):
    # Two conflicting names: the older one is demoted and must not render.
    kg.extract_from_memory("my name is Alex", source="user")
    kg.extract_from_memory("my name is Sam", source="user")   # newer wins, Alex demoted
    ctx = kg.context_for_prompt("who am i, what is my name")
    assert "Sam" in ctx
    assert "Alex" not in ctx, f"retired name leaked into context:\n{ctx}"


# ── KG statements: holder, polarity, clause ends, moods ──────────────────────

def _user_rels(kg):
    return {(r["predicate"], r["object"]) for r in (kg.query_entity("User") or {}).get("outbound", [])}


@pytest.mark.parametrize("text,expected", [
    ("I don't like mushrooms.", {("dislikes", "mushrooms")}),
    ("I do not like rain", {("dislikes", "rain")}),
    ("I dislike loud music", {("dislikes", "loud music")}),
    ("I really love hiking, mostly in spring.", {("prefers", "hiking")}),
    ("I like tea and I hate coffee.", {("prefers", "tea"), ("dislikes", "coffee")}),
    ("user prefers dark mode.", {("prefers", "dark mode")}),
])
def test_kg_reads_who_likes_what(kg, text, expected):
    kg.extract_from_memory(text, source="user")
    assert _user_rels(kg) == expected


@pytest.mark.parametrize("text", [
    "My brother likes football.",          # someone else's taste
    "I'm a bit tired.",                    # a mood, not a kind
    "I'm a little worried about it.",
    "I would prefer not to talk about it.",  # a wish for now
    "I'd like to book a table.",
    "Do I like olives?",                   # a question states nothing
    "I don't hate jazz",                   # neither a liking nor a dislike
    "I really love my dog",                # a possessive names no thing liked
])
def test_kg_stores_nothing_about_the_user_for(kg, text):
    kg.extract_from_memory(text, source="user")
    assert _user_rels(kg) == set(), f"{text!r} became {_user_rels(kg)}"


def test_kg_objects_keep_no_trailing_punctuation(kg):
    kg.extract_from_memory("I like Node.js a lot.", source="user")
    assert _user_rels(kg) == {("prefers", "Node.js")}


def test_kg_negated_and_quantified_subjects_are_not_entities(kg):
    kg.extract_from_memory("Nobody uses cash anymore. Alex never uses Emacs. Sam uses Vim.", source="user")
    assert kg.get_entity_id("Nobody") is None
    assert (kg.query_entity("Alex") or {}).get("outbound", []) == []
    assert {(r["predicate"], r["object"]) for r in kg.query_entity("Sam")["outbound"]} == {("uses", "Vim")}


def test_multi_hop_shows_only_what_the_one_hop_block_would(kg):
    """related() fed 'Connected facts (multi-hop)' every relation: the retired name, moods stored as kinds."""
    import sqlite3
    kg.extract_from_memory("my name is Alex", source="user")
    kg.extract_from_memory("my name is Sam", source="user")          # Alex retired
    kg.add_relation("User", "is_a", "bit tired", source="user")      # an earlier extractor's junk
    kg.add_relation("User", "prefers", "tea", source="user")
    kg.add_relation("tea", "related_to", "Assam", source="user")
    rows = kg.related("tea", hops=2)
    objects = {r["object"] for r in rows}
    assert "Sam" in objects and "Assam" in objects
    assert "Alex" not in objects and "bit tired" not in objects
    assert all(r.get("ts") for r in rows), "multi-hop rows must carry when they were learned"
    line = kg.relation_line(rows[0]["subject"], rows[0]["predicate"], rows[0]["object"], rows[0]["ts"])
    assert "(as of " in line
    with sqlite3.connect(kg.db_path) as c:
        assert c.execute("SELECT COUNT(*) FROM kg_relations").fetchone()[0] == 5


# ── Raw-log recall fallback keys on the distinctive noun ────────────────────

def test_conversation_fallback_surfaces_passing_fact(tmp_path, monkeypatch):
    # Force sparse durable results so the conversation_turns fallback fires.
    import eli.memory.vector_store as vs
    monkeypatch.setattr(vs, "get_vector_store", lambda *a, **k: None)

    db = tmp_path / "user.sqlite3"
    mem = Memory(db_path=db)
    now = time.time()
    mem.add_conversation_turn("user", "haha explaining the rules to Biscuit (my dog), next")
    time.sleep(0.01)
    mem.add_conversation_turn("user", "what is the weather today")        # recent noise
    mem.add_conversation_turn("user", "remind me about my meeting tomorrow")

    res = mem.recall_memory("what is my dog's name", limit=5)
    texts = " || ".join((r.get("text") or "") for r in res)
    assert "Biscuit" in texts, f"fallback failed to surface the dog turn: {texts}"


def test_conversation_fallback_prefers_distinctive_noun_over_recency(tmp_path, monkeypatch):
    import eli.memory.vector_store as vs
    monkeypatch.setattr(vs, "get_vector_store", lambda *a, **k: None)
    db = tmp_path / "user.sqlite3"
    mem = Memory(db_path=db)
    mem.add_conversation_turn("user", "my dog Biscuit loves the park")
    # A more recent turn that only matches the generic word 'name'.
    mem.add_conversation_turn("user", "what was that restaurant name again")
    res = mem.recall_memory("what is my dog's name", limit=3)
    assert res, "no fallback results"
    assert "Biscuit" in (res[0].get("text") or ""), \
        f"distinctive-noun turn should rank first, got {[r.get('text') for r in res]}"


# ── add_conversation_turn promotes passing facts into the KG ─────────────────

def test_add_conversation_turn_promotes_fact_to_kg(tmp_path):
    reset_knowledge_graph()   # isolated (points at ELI_ARTIFACTS_DIR=_pytest store)
    from eli.memory.knowledge_graph import get_knowledge_graph
    db = tmp_path / "user.sqlite3"
    mem = Memory(db_path=db)
    mem.add_conversation_turn("user", "my dog Rexington is a rescue")
    rels = {(r["predicate"], r["object"]) for r in (get_knowledge_graph().query_entity("User") or {}).get("outbound", [])}
    assert ("has_dog", "Rexington") in rels, f"promotion did not reach the KG; got {rels}"
    reset_knowledge_graph()
