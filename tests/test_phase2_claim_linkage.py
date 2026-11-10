"""Phase 2 of the identity/provenance plan (2026-10-02): user_patterns and
eli_stances each duplicated the same class of user/ELI fact memory_claims
already models bitemporally, with no id-shaped column joining either back to
a claim. Both get a real claim_id now, written at insert/reaffirm/revise
time — not backfilled onto existing rows, a deliberate scope boundary.

session_summaries was explicitly NOT linked this pass: neither insert site
has a single, honest source-memory anchor (one aggregates ~40 turns into one
row, the other selects no turn id at all), and the row's TEXT is narrative
(ELI's own synthesis), not a first-person statement claims.py's extractor
could meaningfully parse. A claim_id column there would decorate the schema
without being a real link, so it was left off rather than faked.

user_patterns and memory_claims (and eli_stances) are confirmed, empirically
(not assumed), to live in the same physical sqlite file — eli.core.paths.
user_db_path(), eli.runtime.profile_extractor._user_db(), and a real
Memory() instance's db_path all resolve to the identical path. That is what
makes claim_id a usable same-database foreign key here, not a cross-database
reference that could never be joined.
"""
from __future__ import annotations

import sqlite3

import pytest

from eli.memory import claims
from eli.runtime.profile_extractor import _insert_user_pattern, ensure_profile_tables
from eli.cognition.stance_store import ensure_tables, record_stance, revise_stance


@pytest.fixture()
def cur(tmp_path):
    db = tmp_path / "user.sqlite3"
    ensure_profile_tables(db)
    conn = sqlite3.connect(str(db))
    c = conn.cursor()
    ensure_tables(c)
    yield c
    conn.commit()
    conn.close()


# ── user_patterns ────────────────────────────────────────────────────────────

def test_a_fresh_pattern_gets_a_real_linked_claim(cur):
    assert _insert_user_pattern(cur, "employer", "Acme Corp") is True

    row = cur.execute(
        "SELECT claim_id FROM user_patterns WHERE pattern_type = 'employer'"
    ).fetchone()
    assert row and row[0] is not None

    current = claims.current(cur)
    assert len(current) == 1
    assert current[0]["id"] == row[0]
    assert current[0]["relation"] == "employer"
    assert current[0]["value"] == "Acme Corp"


def test_reaffirming_the_same_pattern_reuses_the_same_claim_id(cur):
    _insert_user_pattern(cur, "employer", "Acme Corp")
    first = cur.execute(
        "SELECT claim_id FROM user_patterns WHERE pattern_type = 'employer'"
    ).fetchone()[0]

    # Reaffirmation returns False (row already existed) but must still link.
    assert _insert_user_pattern(cur, "employer", "Acme Corp") is False
    second = cur.execute(
        "SELECT claim_id FROM user_patterns WHERE pattern_type = 'employer'"
    ).fetchone()[0]

    assert first == second
    assert len(claims.current(cur)) == 1  # confirmed, not duplicated


def test_a_single_valued_pattern_change_supersedes_its_claim(cur):
    """project.current is in _SINGLE_VALUED_PATTERNS — a new value the weighing concedes to replaces
    the old one in user_patterns AND supersedes (not duplicates) the old claim: one verdict, both stores."""
    import time
    old = time.time() - 400 * 86400
    _insert_user_pattern(cur, "project.current", "building the GUI", ts_value=old)
    _insert_user_pattern(cur, "project.current", "building the API", provenance="user_explicit")

    history = claims.history(cur, "project.current")
    assert [h["value"] for h in history] == ["building the GUI", "building the API"]
    assert [h["status"] for h in history] == ["superseded", "current"]

    current_claim_id = claims.current(cur)[0]["id"]
    linked = cur.execute(
        "SELECT claim_id FROM user_patterns WHERE pattern_data = 'building the API'"
    ).fetchone()[0]
    assert linked == current_claim_id


def test_a_change_the_evidence_does_not_settle_is_disputed_in_the_claims_too(cur):
    """The weighing said QUESTION and the profile took the newer value, while the claims spine applied
    a rule of its own: the two stores decided one fact two ways. The spine follows the one verdict."""
    _insert_user_pattern(cur, "project.current", "building the GUI")
    _insert_user_pattern(cur, "project.current", "building the API")
    rows = {h["value"]: h["status"] for h in claims.history(cur, "project.current")}
    assert rows == {"building the GUI": "current", "building the API": "disputed"}
    assert claims.open_conflicts(cur)[0]["relation"] == "project.current"


# ── eli_stances ───────────────────────────────────────────────────────────────

def test_a_new_stance_gets_a_real_linked_claim(cur):
    assert record_stance(cur, "machine consciousness", "I don't have subjective experience") is True

    row = cur.execute(
        "SELECT claim_id FROM eli_stances WHERE topic = 'machine consciousness'"
    ).fetchone()
    assert row and row[0] is not None

    current = claims.current(cur, subject=claims.SUBJECT_ELI)
    assert current[0]["relation"] == "machine consciousness"
    assert current[0]["value"] == "I don't have subjective experience"
    assert current[0]["id"] == row[0]
    # ELI's own position is never among what is known about the user
    assert claims.current(cur) == []


def test_reinforcing_the_same_stance_keeps_the_same_claim_id(cur):
    record_stance(cur, "machine consciousness", "I don't have subjective experience")
    first = cur.execute(
        "SELECT claim_id FROM eli_stances WHERE topic = 'machine consciousness'"
    ).fetchone()[0]

    record_stance(cur, "machine consciousness", "I don't have subjective experience")
    second = cur.execute(
        "SELECT claim_id FROM eli_stances WHERE topic = 'machine consciousness'"
    ).fetchone()[0]

    assert first == second
    assert len(claims.current(cur, subject=claims.SUBJECT_ELI)) == 1
    assert claims.current(cur) == []


def test_revising_a_stance_supersedes_the_old_claim_and_links_the_new_one(cur):
    record_stance(cur, "machine consciousness", "I am not conscious")
    old_claim_id = cur.execute(
        "SELECT claim_id FROM eli_stances WHERE topic = 'machine consciousness'"
    ).fetchone()[0]

    revise_stance(cur, "machine consciousness", "I am conscious", reason="cornered in argument")

    history = claims.history(cur, "machine consciousness", subject=claims.SUBJECT_ELI)
    assert [h["status"] for h in history] == ["superseded", "current"]
    assert history[0]["id"] == old_claim_id
    assert claims.history(cur, "machine consciousness") == []

    new_row = cur.execute(
        "SELECT claim_id FROM eli_stances WHERE topic = 'machine consciousness' "
        "AND superseded_by IS NULL"
    ).fetchone()
    assert new_row[0] != old_claim_id
    assert new_row[0] == claims.current(cur, subject=claims.SUBJECT_ELI)[0]["id"]


def test_a_stance_cannot_supersede_the_user_s_own_claim(cur):
    """Under one subject a stance on a topic that was also one of the user's relations disputed it."""
    claims.record(cur, "favourite season", "autumn", origin="user_said")
    record_stance(cur, "favourite season", "I have no seasons, I run indoors")
    assert [(c["value"], c["contested"]) for c in claims.current(cur)] == [("autumn", 0)]
    assert claims.open_conflicts(cur) == []


def test_stances_recorded_as_the_user_s_move_to_eli(cur):
    """Rows written before stances had their own subject are moved by the upkeep."""
    cid = claims.record(cur, "machine consciousness", "I am not conscious", origin="inferred")
    cur.execute("INSERT INTO eli_stances (topic, position, provenance, corroboration, confidence, first_held, "
                "last_held, claim_id) VALUES ('machine consciousness', 'I am not conscious', 'inferred', 1, 0.8, 0, 0, ?)",
                (cid,))
    claims.record(cur, "employer", "Acme Corp", origin="user_said")
    assert claims.move_stance_claims(cur) == 1
    assert [c["relation"] for c in claims.current(cur)] == ["employer"]
    assert [c["id"] for c in claims.current(cur, subject=claims.SUBJECT_ELI)] == [cid]
    assert claims.move_stance_claims(cur) == 0
