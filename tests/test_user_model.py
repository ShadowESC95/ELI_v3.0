"""Continuous User Model + light onboarding — fully offline (temp DB + fake broker)."""
import sqlite3
import pytest

from eli.runtime.profile_extractor import ensure_profile_tables, _insert_user_pattern
from eli.runtime import user_model as um
from eli.onboarding import interview as iv


@pytest.fixture(autouse=True)
def _clean_onboarding_state():
    # The onboarding state file is global — never let it leak into other tests.
    iv.clear_onboarding_state()
    yield
    iv.clear_onboarding_state()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(iv, "_state_file", lambda: tmp_path / "onboarding_state.json")
    p = tmp_path / "user.sqlite3"
    ensure_profile_tables(p)
    # Force a true blank slate: no stored name.
    monkeypatch.setattr("eli.kernel.state.get_user_name", lambda default="", user_id=None: "")
    return p


def _seed(db, ptype, data, user_id=None):
    con = sqlite3.connect(str(db)); cur = con.cursor()
    _insert_user_pattern(cur, ptype, data, **({"user_id": user_id} if user_id else {})); con.commit(); con.close()


def test_blank_slate_brief_is_onboarding_nudge(db):
    assert um.get_user_brief(turn_count=0, db_path=db).startswith("ONBOARDING")
    assert um.get_user_brief(turn_count=9, db_path=db) == ""   # stops nudging later
    assert um.read_user_model(db_path=db)["is_seeded"] is False


def test_seeded_patterns_render_a_brief(db):
    _seed(db, "identity.role", "User is a physicist.")
    _seed(db, "preference.style", "User prefers terse, direct answers.")
    brief = um.get_user_brief(turn_count=0, db_path=db)
    assert "USER MODEL" in brief and "physicist" in brief and "terse" in brief
    assert um.read_user_model(db_path=db)["is_seeded"] is True


def test_synthesize_consolidates_with_fake_broker(db):
    _seed(db, "project.current", "Tuning a scalar-field simulation.")

    class FakeBroker:
        gguf_ready = True
        def infer(self, prompt, system="", max_tokens=0, temperature=0, top_p=0,
                  background=False, retry=True):
            return "A physicist focused on scalar-field simulation tuning; prefers terse answers."

    assert um.synthesize_user_model(db_path=db, broker=FakeBroker()) is True
    m = um.read_user_model(db_path=db)
    assert m["is_seeded"] and "scalar-field" in m["dossier"]
    assert "scalar-field" in m["current_focus"][0]
    # the per-turn read is now the stored brief (single SELECT)
    assert "USER MODEL" in um.get_user_brief(db_path=db)


def test_synthesize_degrades_without_broker(db):
    _seed(db, "identity.role", "User is an engineer.")
    assert um.synthesize_user_model(db_path=db, broker=None) is True   # heuristic fallback
    assert um.read_user_model(db_path=db)["dossier"]  # non-empty heuristic dossier


def test_onboarding_flow_seeds_and_finishes(db, monkeypatch):
    captured = {}
    monkeypatch.setattr("eli.kernel.state.set_user_name", lambda n: captured.__setitem__("name", n))
    iv.clear_onboarding_state()
    # substantive task passes straight through
    assert iv.onboarding_intercept("fix this failing import in my module", db_path=db) is None
    assert not iv.is_onboarding_active()
    # light opener begins it
    assert "call you" in iv.onboarding_intercept("hey", db_path=db).lower()
    assert iv.is_onboarding_active()
    assert "work on" in iv.onboarding_intercept("Alex", db_path=db).lower()
    assert "1)" in iv.onboarding_intercept("physics", db_path=db).lower()
    assert "last one" in iv.onboarding_intercept("2", db_path=db).lower()
    done = iv.onboarding_intercept("d", db_path=db)
    # closing summary echoes the name + at least one captured answer, and ends onboarding
    assert "Alex" in done and "picked up" in done.lower() and not iv.is_onboarding_active()
    assert captured.get("name") == "Alex"
    rows = dict(sqlite3.connect(str(db)).execute(
        "select pattern_type, pattern_data from user_patterns").fetchall())
    assert "identity.role" in rows and "preference.style" in rows and "goal.primary" in rows
    iv.clear_onboarding_state()


def test_onboarding_meta_question_starts_flow(db):
    iv.clear_onboarding_state()
    msg = iv.onboarding_intercept(
        "I thought you had an initial question set to build a report on me?",
        db_path=db,
    )
    assert msg and "call you" in msg.lower()
    assert iv.is_onboarding_active()
    iv.clear_onboarding_state()


def test_onboarding_skip_clears(db):
    iv.clear_onboarding_state()
    iv.onboarding_intercept("hi", db_path=db)
    assert iv.is_onboarding_active()
    msg = iv.onboarding_intercept("skip", db_path=db)
    assert "pick it up" in msg.lower() and not iv.is_onboarding_active()
    iv.clear_onboarding_state()


@pytest.fixture
def members(monkeypatch):
    """API members, as the memory policy knows them (eli/memory/policy.rbac_members)."""
    monkeypatch.setattr("eli.memory.policy.rbac_members", lambda: frozenset({"alice", "member-b"}), raising=False)


def test_user_model_row_is_user_scoped(db, members):
    _seed(db, "identity.role", "User is a physicist.", user_id="alice")
    um.synthesize_user_model(user_id="alice", db_path=db, broker=None)
    # the synthesized ROW for alice exists; bob has no row of his own
    assert um._read_row("alice", db_path=db) is not None
    assert um._read_row("bob", db_path=db) is None


def test_each_user_model_is_built_from_that_users_own_facts(db, members):
    """user_patterns was one pooled table: an API member's facts went into the owner's model and the
    owner's into the member's brief."""
    _seed(db, "identity.role", "User is a surveyor.")                       # the owner's
    _seed(db, "identity.role", "User is a beekeeper.", user_id="member-b")  # a member's
    assert "surveyor" in um.get_user_brief(db_path=db) and "beekeeper" not in um.get_user_brief(db_path=db)
    member = um.get_user_brief(user_id="member-b", db_path=db)
    assert "beekeeper" in member and "surveyor" not in member
    # a member's single-valued fact did not retire the owner's
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM user_patterns WHERE pattern_type='identity.role'").fetchone()[0] == 2
    con.close()


def test_the_brief_inside_a_members_turn_is_the_members(db, members):
    from eli.kernel import request_context as rc
    _seed(db, "identity.role", "User is a surveyor.")
    _seed(db, "identity.role", "User is a beekeeper.", user_id="member-b")
    token = rc.user_id_var.set("member-b")
    try:
        assert "beekeeper" in um.get_user_brief(db_path=db) and "surveyor" not in um.get_user_brief(db_path=db)
    finally:
        rc.user_id_var.reset(token)
    # an API caller that named no user is the owner
    token = rc.user_id_var.set("default")
    try:
        assert "surveyor" in um.get_user_brief(db_path=db)
    finally:
        rc.user_id_var.reset(token)


def test_a_brief_rebuilt_after_a_fact_stopped_holding_does_not_say_it(db):
    """The narrative written at the last session's end went on naming a home the user had moved from,
    in every turn's brief, until the next synthesis."""
    _seed(db, "identity.fact", "Keeps bees on the roof.")
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO user_model (user_id, dossier, brief) VALUES (?, ?, 'old')",
                (um._resolve_user_id(), "A beekeeper in Fenwick Bay. Keeps bees on the roof and sells the honey."))
    con.commit(); con.close()
    brief = um.refresh_user_model_brief(db_path=db, retired=["Fenwick Bay"])
    assert "Fenwick" not in brief and "sells the honey" in brief
    assert um.read_user_model(db_path=db)["dossier"] == "Keeps bees on the roof and sells the honey."
