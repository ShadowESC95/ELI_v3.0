"""Durable user facts are captured from the session summary and accumulate.

The extraction funnel was the narrowest point in the whole memory system. On a
live machine 619 conversation turns and 441 memories produced **10**
user_patterns, 6 semantic facts and 27 KG entities — and everything downstream
inherits that, because promotion fans out from user_patterns.

Two causes, and neither was the promotion machinery:

  * `extract_patterns_from_text` matches about twenty fixed phrasings, and most
    emit a CANNED sentence — the same string whatever the user actually said — so
    hundreds of turns dedupe down to a handful of rows.
  * The LLM summariser already read the whole transcript, but the only things
    routed out of it were `project.current` and `preference.session`, both
    SINGLE-SLOT: deleted and rewritten every session, so they could never widen
    anything.

Facts now go to accumulating pattern types whose prefixes `_promote_to_semantic`
accepts, so a fact captured tonight reaches the semantic tier and the knowledge
graph without anything else changing.
"""
from __future__ import annotations

import sqlite3

import pytest

from eli.runtime import profile_extractor as PE


@pytest.fixture()
def cur(tmp_path):
    db = tmp_path / "user.sqlite3"
    PE.ensure_profile_tables(db)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    yield con.cursor()
    con.commit()
    con.close()


def _patterns(cur):
    return [(r[0], r[1]) for r in cur.execute(
        "SELECT pattern_type, pattern_data FROM user_patterns").fetchall()]


# ── classification ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    ("User is called Alex by close colleagues", "identity.name_mention"),
    ("User works as an independent physicist", "identity.work"),
    # "is called" / "is a" of something else, or of the user, is not their name or job
    ("Their cat is called Biscuit", "identity.fact"),
    ("The user is a keen cyclist", "identity.fact"),
    ("User lives in Dublin", "identity.location"),
    ("User researches entropy and coherence fields", "research.field"),
    ("User is building a solar-to-hydrogen prototype", "project.named"),
    ("User prefers assumptions challenged directly", "preference.stated"),
    ("User is interested in pulsed power electronics", "interest.explicit"),
])
def test_facts_route_to_promotable_types(line, expected):
    assert PE._fact_pattern_type(line) == expected


def test_unclassified_facts_are_still_kept():
    """Dropping a durable fact because no verb matched is worse than filing it
    under identity — which is a prefix the semantic promoter accepts."""
    t = PE._fact_pattern_type("User has two cats and a workshop in the garage")
    assert t == "identity.fact"
    assert t.startswith(PE._SEMANTIC_PATTERN_PREFIXES)


def test_every_route_target_reaches_the_semantic_tier():
    """If a route emitted a prefix _promote_to_semantic ignores, the fact would be
    captured and then silently stop at user_patterns."""
    for _pattern, ptype in PE._FACT_ROUTES:
        assert ptype.startswith(PE._SEMANTIC_PATTERN_PREFIXES), ptype
        assert not ptype.startswith(PE._SEMANTIC_PATTERN_EXCLUDE), ptype


def test_fact_types_are_not_single_valued():
    """Single-slot types are superseded on write. Facts must accumulate, or this
    change reproduces the exact bug it exists to fix."""
    for _p, t in PE._FACT_ROUTES:
        assert t not in PE._SINGLE_VALUED_PATTERNS, t
    assert "identity.fact" not in PE._SINGLE_VALUED_PATTERNS


def test_a_summary_line_never_replaces_the_name_or_role_the_user_gave(cur):
    """A summary line is the model's account of the conversation. 'their cat is called X' was typed
    as the user's name and 'is a keen cyclist' as their role, and each deleted the stored one."""
    PE._insert_user_pattern(cur, "identity.name", "User's name is Robin.", provenance="user_explicit")
    PE._insert_user_pattern(cur, "identity.role", "User is a surveyor.", provenance="user_explicit")
    PE._route_facts_to_patterns(cur, "- Their cat is called Biscuit\n- The user is a keen cyclist\n"
                                     "- The user's name is Biscuit\n")
    rows = dict(cur.execute("SELECT pattern_type, pattern_data FROM user_patterns "
                            "WHERE pattern_type IN ('identity.name', 'identity.role')").fetchall())
    assert rows == {"identity.name": "User's name is Robin.", "identity.role": "User is a surveyor."}
    kept = [d for t, d in _patterns(cur) if t not in PE._SINGLE_VALUED_PATTERNS]
    assert any("Biscuit" in d for d in kept) and any("cyclist" in d for d in kept)


# ── routing ──────────────────────────────────────────────────────────────────

def test_facts_are_written_as_separate_patterns(cur):
    n = PE._route_facts_to_patterns(cur, """
- User researches entropy and coherence fields
- User is building a solar-to-hydrogen electrolyser
- User is interested in pulsed power electronics
""")
    assert n == 3
    types = {t for t, _ in _patterns(cur)}
    assert {"research.field", "project.named", "interest.explicit"} <= types


def test_eli_talking_about_itself_is_not_a_user_fact(cur):
    """The live store was 38% ELI's own reflection telemetry. None of it is a
    fact about the user, and it must not reach the semantic tier."""
    n = PE._route_facts_to_patterns(cur, """
- ELI generated 4 artifacts this session
- User works on high-voltage pulsed power
- Reflection recorded a conversation volume of 12 messages
- The model failed to load with 33 gpu layers
""")
    assert n == 1
    assert len(_patterns(cur)) == 1
    assert "pulsed power" in _patterns(cur)[0][1]


def test_repeating_a_fact_does_not_duplicate_it(cur):
    text = "- User researches entropy and coherence fields"
    PE._route_facts_to_patterns(cur, text)
    PE._route_facts_to_patterns(cur, text)
    PE._route_facts_to_patterns(cur, text)
    assert len(_patterns(cur)) == 1


def test_bullet_markers_and_numbering_are_stripped(cur):
    PE._route_facts_to_patterns(cur, "1. User lives in Dublin\n* User studies physics\n")
    data = [d for _t, d in _patterns(cur)]
    assert all(not d[0].isdigit() and d[0] not in "-*•" for d in data), data


def test_empty_and_none_sections_write_nothing(cur):
    for text in ("", "none", "  ", "N/A", "not specified"):
        assert PE._route_facts_to_patterns(cur, text) == 0
    assert _patterns(cur) == []


def test_fragments_too_short_to_be_facts_are_skipped(cur):
    assert PE._route_facts_to_patterns(cur, "- ok\n- yes\n- 42\n") == 0


# ── the section is actually asked for and parsed ─────────────────────────────

def test_the_summary_parser_recognises_the_section():
    summary = (
        "SUMMARY: worked on the electrolyser.\n"
        "USER FACTS:\n- User is building a solar-to-hydrogen prototype\n"
        "CURRENT WORK: stack assembly\n"
    )
    sections = {m.group(1).upper().replace(" ", "_"): m.group(2).strip()
                for m in PE._SUMMARY_SECTION_RE.finditer(summary)}
    assert "USER_FACTS" in sections
    assert "solar-to-hydrogen" in sections["USER_FACTS"]
    assert sections["CURRENT_WORK"].startswith("stack assembly")


def test_route_summary_writes_facts_end_to_end(cur):
    PE._route_summary_to_profile(cur, (
        "SUMMARY: a session.\n"
        "CURRENT WORK: building the electrolyser stack.\n"
        "USER FACTS:\n"
        "- User researches entropy and coherence fields\n"
        "- User is interested in pulsed power electronics\n"
    ))
    types = {t for t, _ in _patterns(cur)}
    assert "project.current" in types, "existing behaviour must be preserved"
    assert {"research.field", "interest.explicit"} <= types, "facts must be routed too"


# ── the canned-sentence problem ──────────────────────────────────────────────

def test_the_same_rule_on_different_statements_yields_different_rows():
    """Every preference rule emits a FIXED label. That is why hundreds of turns
    collapsed into ten rows: the string was byte-identical whatever the user
    said, so dedupe folded them all together. The label is kept (persona and
    proactive surfaces read it) but the user's own words are appended."""
    a = PE.extract_patterns_from_text("please be thorough")
    b = PE.extract_patterns_from_text(
        "I need you to be thorough about the electrolyser stack, the anode keeps failing")
    assert a and b
    assert a != b, "two different statements must not produce identical patterns"
    assert all("Said:" in d for _t, d in a + b)


def test_the_canned_label_survives_for_downstream_readers():
    got = dict(PE.extract_patterns_from_text("be meticulous please"))
    assert got["preference.detail"].startswith(
        "User prefers in-depth, meticulous, thorough responses.")


def test_evidence_is_absent_when_the_quote_is_too_short():
    assert PE._evidence("hi", None) == ""
    assert PE._evidence("", None) == ""


def test_evidence_does_not_start_or_end_mid_word():
    text = "I would really appreciate a thorough and complete answer to this question"
    import re as _re
    m = _re.search("thorough", text.lower())
    q = PE._evidence(text, m)
    assert q.startswith(' Said: "') and q.endswith('"')
    inner = q[len(' Said: "'):-1]
    assert inner in text, "the quote must be a verbatim span of what was said"


# ── ELI's readings weighed against what the user said ────────────────────────────────────────────

def _claims_now(cur):
    return sorted((r[0], r[1]) for r in cur.execute(
        "SELECT relation, value FROM memory_claims WHERE subject = 'user' AND status = 'current'").fetchall())


def _user_says(cur, text, when=None):
    from eli.memory import claims
    claims.record_from_text(cur, text, when=when)


def test_a_fact_line_of_several_sentences_is_each_of_its_facts(cur):
    """The summariser put a whole session's facts on one line; stored as one fact, a part that stopped
    holding stood on the parts that still did."""
    PE._route_facts_to_patterns(cur, "Has a sister named Ines who drives trams. Works days at the Quay Street "
                                     "garage, mostly early mornings. Keeps bees on the roof. Sells their honey "
                                     "at the market. Likes podcasts about the sea. Listens to them while baking.")
    data = sorted(d for _t, d in _patterns(cur))
    assert data == ["Has a sister named Ines who drives trams.", "Keeps bees on the roof.",
                    "Likes podcasts about the sea. Listens to them while baking.",
                    "Sells their honey at the market.",
                    "Works days at the Quay Street garage, mostly early mornings."], \
        "each sentence its own fact; one that points back is part of the fact it points to"


def test_a_reading_that_contradicts_what_the_user_said_is_no_fact_about_them(cur):
    _user_says(cur, "I live in Ashby Green")
    PE._route_facts_to_patterns(cur, "- Lives in Fenwick Bay\n- Keeps bees on the roof\n")
    assert [d for _t, d in _patterns(cur)] == ["Keeps bees on the roof"]
    assert ("lives_in", "Ashby Green") in _claims_now(cur)
    assert not any("Fenwick" in v for _r, v in _claims_now(cur))
    assert cur.execute("SELECT COUNT(*) FROM semantic WHERE fact LIKE '%Fenwick%'").fetchone()[0] == 0


def test_a_reading_that_bears_the_users_word_out_is_that_claim_not_one_of_its_own(cur):
    _user_says(cur, "I work nights at the depot")
    for _session in range(3):
        PE._route_facts_to_patterns(cur, "- Works nights at the depot\n")
    assert _claims_now(cur) == [("work_schedule", "nights")]
    row = cur.execute("SELECT confirmations, origin FROM memory_claims WHERE status = 'current'").fetchone()
    assert tuple(row) == (2, "user_said"), "ELI's reading is one more source, counted once"
    linked = cur.execute("SELECT c.relation FROM user_patterns p JOIN memory_claims c ON c.id = p.claim_id").fetchall()
    assert [r[0] for r in linked] == ["work_schedule"]


def test_readings_resting_on_a_value_the_user_changed_retire_with_it(cur, tmp_path):
    """'Lives in Fenwick Bay' stood in the profile, the semantic tier and every prompt beside the user's
    word that they had moved. It goes with the claim it rests on, kept as history."""
    import time as _t
    db = tmp_path / "user.sqlite3"
    t0 = _t.time() - 30 * 86400
    _user_says(cur, "I live in Fenwick Bay", when=t0)
    PE._route_facts_to_patterns(cur, "Lives in Fenwick Bay, near the harbour. Keeps bees on the roof.")
    from eli.runtime import user_model as um
    cur.execute("INSERT INTO user_model (user_id, dossier, brief) VALUES (?, "
                "'A beekeeper who lives in Fenwick Bay. Keeps bees on the roof.', 'stale')", (um._resolve_user_id(),))
    cur.connection.commit()
    _user_says(cur, "We moved, I live in Ashby Green now", when=t0 + 20 * 86400)
    cur.connection.commit()
    PE.ensure_profile_tables(db)          # every profile write passes here
    assert [d for _t2, d in _patterns(cur)] == ["Keeps bees on the roof."]
    assert cur.execute("SELECT COUNT(*) FROM semantic WHERE fact LIKE '%Fenwick%'").fetchone()[0] == 0
    revision = cur.execute("SELECT old_value, new_value FROM belief_revisions WHERE kind = 'user_pattern'").fetchall()
    assert [tuple(r) for r in revision] == [("Lives in Fenwick Bay, near the harbour.", "Ashby Green")]
    old = cur.execute("SELECT status, source_roots FROM memory_claims WHERE relation = 'lives_in' "
                      "AND value = 'Fenwick Bay'").fetchone()
    assert old[0] == "superseded" and '"inferred"' in old[1], "history keeps what the reading bore out"
    model = um.read_user_model(db_path=db)
    assert "Fenwick" not in model["dossier"] and "Keeps bees" in model["dossier"]
    assert "Fenwick" not in um.get_user_brief(db_path=db), "the per-turn brief no longer says it"


def test_readings_kept_before_they_were_weighed_are_weighed_at_the_next_summary(cur):
    """An install's profile already holds readings stored as facts of their own: the summariser's pass
    weighs them as it weighs new ones."""
    for line in ("Lives in Fenwick Bay", "Works nights at the depot", "Keeps bees on the roof"):
        cur.execute("INSERT INTO user_patterns (pattern_type, pattern_data, timestamp, ts, corroboration, provenance) "
                    "VALUES ('identity.fact', ?, 1, 1, 1, 'inferred')", (line,))
        PE._link_pattern_to_claim(cur, "identity.fact", line, "inferred", 1.0)
    _user_says(cur, "I live in Ashby Green")
    _user_says(cur, "I work nights at the depot")
    PE._route_summary_to_profile(cur, "SUMMARY: a quiet evening.\nUSER FACTS: none\n")
    assert sorted(d for _t, d in _patterns(cur)) == ["Keeps bees on the roof", "Works nights at the depot"]
    assert _claims_now(cur) == [("identity.fact", "Keeps bees on the roof"), ("lives_in", "Ashby Green"),
                                ("work_schedule", "nights")]


def test_the_sessions_preference_replaced_closes_its_claim(cur):
    """Rewritten every session in the profile, each session's preference went on standing as a claim."""
    for prefs in ("Likes short answers.", "Wants the steps written out."):
        PE._route_summary_to_profile(cur, f"SUMMARY: a session.\nUSER PREFERENCES: {prefs}\n")
    assert _claims_now(cur) == [("preference.session", "Wants the steps written out.")]


def test_a_summary_fact_the_users_own_words_do_not_carry_is_not_kept(cur):
    """The summary reads ELI's replies too: what ELI said of the user is no fact about them unless they
    said it. One the claims can weigh is weighed against what they said instead, whatever its words."""
    _user_says(cur, "My dog is called Pepper")
    PE._route_summary_to_profile(cur, "SUMMARY: a session.\nUSER FACTS: Keeps bees on the roof. "
                                      "Maintains a sourdough starter. Owns a dog named Pepper. "
                                      "Learns to play the cello.\n",
                                 user_text="I keep bees on the roof\nMy dog is called Pepper\nWhat should I "
                                           "call a sourdough starter?\nI've started learning the cello to play duets")
    data = sorted(d for _t, d in _patterns(cur))
    assert data == ["Keeps bees on the roof.", "Learns to play the cello.", "Owns a dog named Pepper."]
