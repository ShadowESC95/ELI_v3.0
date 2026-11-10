"""
Regression tests for natural salience weighting + auto-capture.

ELI should weight statements by how memorable they are *from the content itself*
and promote genuinely important user statements into durable memory on its own —
without the user ever saying "remember this", and without chit-chat / questions
polluting the store. This is the general fix behind the "Biscuit (my dog)" failure.
"""
import pathlib
import sqlite3
import tempfile

import pytest

from eli.memory.memory import Memory


@pytest.fixture
def mem(monkeypatch):
    # Force sparse durable results so recall exercises the real durable path
    # rather than being masked by the global FAISS store.
    import eli.memory.vector_store as vs
    monkeypatch.setattr(vs, "get_vector_store", lambda *a, **k: None)
    return Memory(db_path=pathlib.Path(tempfile.mktemp(suffix=".sqlite3")))


# ── Importance is the evidence for it (eli/memory/salience.importance_of) ────

def test_a_stated_fact_outweighs_chitchat_and_a_question():
    from eli.memory.salience import importance_of
    fact = importance_of("i'm allergic to peanuts", source="conversation", kind="fact")
    chat = importance_of("yeah that makes sense", source="conversation", kind="memory")
    q = importance_of("what is my dog's name?", source="conversation", kind="fact")
    assert fact["stated"] and not chat["stated"] and not q["stated"]
    assert fact["importance"] > chat["importance"] == q["importance"] == 0.5, "nothing stated: nothing known"


def test_asking_for_it_to_be_kept_adds_the_users_word():
    from eli.memory.salience import importance_of
    passing = importance_of("my locker is number 42 at the gym", source="conversation")
    asked = importance_of("my locker is number 42 at the gym", source="chat")
    pinned = importance_of("my locker is number 42 at the gym", source="working_memory",
                           tags=["working_memory", "user_explicit", "session_pin"])
    assert asked["asked"] and pinned["asked"] and not passing["asked"]
    assert asked["importance"] == pinned["importance"] > passing["importance"] > 0.5


def test_a_fact_eli_already_holds_adds_only_what_is_new():
    """Novelty is one less the chance ELI gave the fact (the Rescorla-Wagner error): a fact held at
    0.8 said again adds 0.2 of the user's word; a changed fact adds the whole of it."""
    from eli.memory.salience import importance_of
    held = [{"relation": "lives_in", "value": "Riverton", "confidence": 0.8}]
    new = importance_of("I live in Riverton", source="conversation", held_claims=[])
    again = importance_of("I live in Riverton", source="conversation", held_claims=held)
    moved = importance_of("I live in Lakeside", source="conversation", held_claims=held)
    assert new["novelty"] == 1.0 and again["novelty"] == pytest.approx(0.2) and moved["novelty"] == 1.0
    assert new["importance"] == moved["importance"] > again["importance"] > 0.5
    unread = importance_of("I go sea swimming every Sunday", source="conversation")
    assert unread["novelty"] is None and 0.5 < unread["importance"] < new["importance"], \
        "a fact of a kind ELI reads is the user's word on it; another statement is said in passing"
    asked = importance_of("the spare shed key is under the green watering can", source="chat")
    assert asked["importance"] > new["importance"], "what the user asked to keep matters most"
    kept = importance_of("I really like green tea", source="conversation", graph_holds=lambda p, o: p == "prefers")
    assert kept["novelty"] == 0.0 and kept["importance"] == 0.5


def test_what_eli_or_a_tool_wrote_carries_no_users_word():
    from eli.memory.salience import importance_of
    assert importance_of("remember that my locker is 42", source="eli", kind="observation")["importance"] == 0.5
    assert importance_of("I live in Riverton", source="document_reader", kind="document")["importance"] == 0.5


@pytest.mark.parametrize("text,memorable", [
    ("my name is Alex", True),
    ("i'm allergic to peanuts", True),
    ("my daughter's birthday is on the 3rd of March", True),
    ("i just started a new job at a robotics lab", True),
    ("we adopted a rescue dog last week", True),
    ("my favourite band is Radiohead", True),
    ("Biscuit (my dog) is very clever", True),
    ("by the way i'm allergic to peanuts", True),
    ("I've started learning the cello on Thursday evenings", True),
    ("I mostly listen to podcasts while I bake", True),
    ("I've been baking rye loaves all week", True),
    ("I don't eat meat", True),
    ("I can't remember where I left my reading glasses", False),
    ("I didn't have time to try the new recipe yet", False),
    ("I don't know if I will make it", False),
    # non-facts / queries / requests / chit-chat:
    ("what is the weather today", False),
    ("what is my dog's name?", False),
    ("do you remember what I said about the trip yesterday?", False),
    ("I want you to play something calm", False),
    ("remind me to water the plants", False),
    ("maybe I'll move to the coast", False),
    ("ok cool thanks", False),
    ("yeah", False),
    ("haha nice one", False),
])
def test_states_about_user(text, memorable):
    from eli.memory.salience import states_about_user
    assert states_about_user(text) is memorable, text


# ── Auto-capture on the conversation-turn path ──────────────────────────────

def test_passing_fact_is_captured_and_recallable(mem):
    """A non-relational fact said in passing must become durable + recallable."""
    mem.add_conversation_turn("user", "by the way i'm allergic to peanuts")
    res = mem.recall_memory("what am i allergic to", limit=5)
    assert any("allergic to peanuts" in (r.get("text") or "") for r in res)


def test_chitchat_and_questions_not_promoted(mem):
    mem.add_conversation_turn("user", "ok cool thanks")
    mem.add_conversation_turn("user", "what is the weather like")
    conn = sqlite3.connect(mem.db_path)
    rows = [r[0] for r in conn.execute("SELECT text FROM memories").fetchall()]
    conn.close()
    assert rows == [], f"chit-chat/questions wrongly promoted: {rows}"


def test_assistant_turns_not_promoted(mem):
    mem.add_conversation_turn("assistant", "I think your dog Biscuit sounds lovely.")
    conn = sqlite3.connect(mem.db_path)
    rows = [r[0] for r in conn.execute("SELECT text FROM memories").fetchall()]
    conn.close()
    assert rows == [], f"assistant turn wrongly promoted: {rows}"


def test_capture_is_deduplicated(mem):
    fact = "i'm allergic to peanuts and shellfish"
    for _ in range(3):
        mem.add_conversation_turn("user", fact)
    conn = sqlite3.connect(mem.db_path)
    n = conn.execute("SELECT COUNT(*) FROM memories WHERE text = ?", (fact,)).fetchone()[0]
    conn.close()
    assert n <= 1, f"same fact promoted {n} times"


def test_captured_fact_carries_the_users_word_and_said_again_counts_the_sighting(mem):
    mem.add_conversation_turn("user", "my daughter's birthday is on the 3rd of March")
    conn = sqlite3.connect(mem.db_path)
    imp = conn.execute("SELECT importance FROM memories WHERE text LIKE '%birthday%'").fetchone()
    conn.close()
    from eli.cognition.belief import PROVENANCE_WEIGHT
    passing = PROVENANCE_WEIGHT["user_passing"]
    assert imp is not None and imp[0] == pytest.approx((1 + passing) / (2 + passing), abs=1e-3), \
        "a statement no reader reads is the user's word in passing"
    # said again in another conversation (one conversation's re-entrant double write is not a sighting)
    mem.add_conversation_turn("user", "my daughter's birthday is on the 3rd of March", session_id="later")
    conn = sqlite3.connect(mem.db_path)
    rows = conn.execute("SELECT seen_count FROM memories WHERE text = ?",
                        ("my daughter's birthday is on the 3rd of March",)).fetchall()
    conn.close()
    assert rows == [(2,)], "the same statement is one memory, seen twice"


def test_a_stated_fact_gets_its_claim_from_the_memory_it_was_stored_as(mem):
    mem.add_conversation_turn("user", "I live in Riverton these days")
    conn = sqlite3.connect(mem.db_path)
    mid = conn.execute("SELECT id FROM memories WHERE text LIKE '%Riverton%'").fetchone()[0]
    claim = conn.execute("SELECT source_memory_id, origin FROM memory_claims WHERE relation = 'lives_in'").fetchone()
    conn.close()
    assert claim == (mid, "user_said"), "the claim says where it came from"
