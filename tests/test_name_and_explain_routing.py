"""Behaviour lock: two bugs seen live in a real session.

#2  A command typed early ("pause spotify") was captured as the user's NAME, so
    every self-report greeted them as "You're pause spotify."
#3  "Can you explain what you meant by 'the context window was full of noise'?"
    routed to the raw EXPLAIN_COGNITION_RUNTIME data dump (paths, line numbers,
    every DB table) — the user's "that is not what i asked". The trigger fired on
    a bare 'context window' match inside quoted prose.
"""

import pytest

from eli.runtime.identity_validation import normalize_identity_candidate as _norm
from eli.execution.router_enhanced import route


# --------------------------------------------------------------------------
# #2 — a command is not a name
# --------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "pause spotify", "open chrome", "play music", "turn off the lights",
    "set a timer", "mute the volume", "skip track", "search the web",
])
def test_command_phrases_are_not_valid_names(command):
    assert _norm(command) == "", f"{command!r} was accepted as a name"


@pytest.mark.parametrize("name", [
    "Alex", "Alex Bridgeman", "Mark", "Mary Jane", "Bill", "O'Brien",
])
def test_real_names_still_accepted(name):
    assert _norm(name) == name


def test_single_word_overlap_with_a_verb_is_left_alone():
    """The command guard only fires on the verb+object SHAPE, not lone words."""
    # "Set" as a standalone one-word candidate is not treated as a command here
    # (the guard requires >1 word), so ordinary short names are unaffected.
    assert _norm("Sky") == "Sky"


def test_set_user_name_rejects_a_command(tmp_path, monkeypatch):
    """End to end: the persistence chokepoint refuses to store a command."""
    from eli.kernel import state
    monkeypatch.setattr(state, "load_user_profile", lambda uid=None: {})
    saved = {}
    monkeypatch.setattr(state, "save_user_profile", lambda p, uid=None: saved.update(p))
    monkeypatch.setattr(state, "sync_identity_to_world_model", lambda **k: None)
    # set_user_name once wrote a copy of the name into config/settings.json by hand, and the suite
    # blanked user_name in the repo's own file on every run; paths stay this test's own.
    from types import SimpleNamespace
    monkeypatch.setattr("eli.core.paths.get_paths", lambda: SimpleNamespace(config_dir=tmp_path))
    returned = state.set_user_name("pause spotify")
    assert returned == ""
    assert not saved.get("name")


@pytest.fixture()
def executor_name_store(tmp_path, monkeypatch):
    """The executor's SET_USER_NAME with every place it writes kept in memory."""
    from eli.execution import executor_enhanced as ex
    from eli.kernel import state
    stored = {}
    monkeypatch.setattr(ex, "_load_state", lambda: {})
    monkeypatch.setattr(ex, "_save_state", lambda st: stored.update(st))
    monkeypatch.setattr(ex, "_purge_conflicting_identity_patterns", lambda new_name: 0)
    monkeypatch.setattr(state, "set_user_name", lambda n, **k: stored.__setitem__("profile", n) or n)
    return ex, stored


@pytest.mark.parametrize("name", ["Will", "May", "Can Ozturk"])
def test_a_name_that_is_also_a_word_is_still_a_name(executor_name_store, name):
    ex, stored = executor_name_store
    out = ex.set_user_name(name)
    assert out["ok"] is True and stored.get("profile") == name


def test_the_executor_leaves_a_names_letters_to_the_shared_validator(executor_name_store, monkeypatch):
    # the executor refused any letter outside A-Z on its own, whatever the shared validator said
    import eli.runtime.identity_validation as iv
    ex, stored = executor_name_store
    monkeypatch.setattr(iv, "normalize_identity_candidate", lambda value, **k: str(value).strip())
    assert ex.set_user_name("Zoë")["ok"] is True and stored.get("profile") == "Zoë"
    monkeypatch.setattr(iv, "normalize_identity_candidate", lambda value, **k: "")
    assert ex.set_user_name("pause spotify")["ok"] is False


def test_a_name_from_one_word_asks_before_replacing_the_stored_one(executor_name_store, monkeypatch):
    """"I'm Sam" read as a name overwrote the stored one and cleared every name row unasked: one word
    after "I'm" is no declaration. ELI asks; the yes says "my name is Sam", which sets it."""
    from eli.execution.effectors.memory_ops import _h_set_user_name
    from eli.kernel import state
    import eli.runtime.pending_proposal as pp
    ex, stored = executor_name_store
    purged, armed = [], []
    monkeypatch.setattr(ex, "_purge_conflicting_identity_patterns", lambda new_name: purged.append(new_name) or 0)
    monkeypatch.setattr(pp, "set_pending_proposal", lambda command, summary="", **k: armed.append(command))
    monkeypatch.setattr(state, "get_user_name", lambda default="", user_id=None: "Robin")
    out = _h_set_user_name("SET_USER_NAME", {"name": "Sam", "declared_by": "i_am_word"})
    assert out["changed"] is False and out["user_name"] == "Robin" and out["explain_in_words"] is True
    assert "profile" not in stored and purged == [] and armed == ["my name is Sam"]
    out = _h_set_user_name("SET_USER_NAME", {"name": "Robin", "declared_by": "i_am_word"})   # the same name
    assert out.get("changed") is not False and stored.get("profile") == "Robin"
    out = _h_set_user_name("SET_USER_NAME", {"name": "Sam", "declared_by": "my_name_is"})
    assert stored.get("profile") == "Sam" and purged == ["Robin", "Sam"]


def test_the_name_route_tells_the_executor_how_it_was_said():
    from eli.execution.router_enhanced import route
    got = route("I'm Alex")
    assert got["args"] == {"name": "Alex", "declared_by": "i_am_word"}


def test_asking_who_i_am_never_stores_a_name(tmp_path, monkeypatch):
    # "who am I" with no name stored: the answer used to mine the install-wide learned patterns for
    # "called X" (another user's name, or a pet's) and store it as this user's name
    import sqlite3
    from eli.core import paths
    from eli.execution import executor_enhanced as ex
    from eli.kernel import state
    db = tmp_path / "user.sqlite3"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE user_patterns (id INTEGER PRIMARY KEY, pattern_type TEXT, pattern_data TEXT, ts REAL, timestamp REAL)")
    con.execute("INSERT INTO user_patterns (pattern_type, pattern_data, ts) VALUES ('identity.name', 'Their dog is called Rex.', 1.0)")
    con.commit()
    con.close()
    monkeypatch.setattr(paths, "user_db_path", lambda *a, **k: db)
    monkeypatch.setattr(state, "get_user_name", lambda default="", **k: "")
    wrote = []
    monkeypatch.setattr(state, "set_user_name", lambda n, **k: wrote.append(n) or n)
    text = ex._eli_identity_only_response({"identity_scope": "name_only"})
    assert wrote == [] and "Rex" not in text
    assert "No confirmed name is stored for the active user." in text


# --------------------------------------------------------------------------
# #3 — explaining a prior claim is conversation, not a runtime dump
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    'can you explain "the context window was full of noise"?',
    "what do you mean the context window was full of noise?",
    "you said the context window was noisy — explain that",
    "So what improved, calibration wise? Can you explain what you meant?",
])
def test_explaining_a_prior_claim_does_not_dump_runtime(text):
    action = str(route(text).get("action"))
    assert action != "EXPLAIN_COGNITION_RUNTIME", f"{text!r} dumped the runtime map"


# Real diagnostics must still reach a GROUNDED report (not a confabulated CHAT).
# Which grounded action varies — a GPU question goes to GPU_STATUS, an inference
# question to EXPLAIN_COGNITION_RUNTIME — but never to unguarded chat.
_GROUNDED_DIAGNOSTICS = {"EXPLAIN_COGNITION_RUNTIME", "GPU_STATUS", "RUNTIME_STATUS"}


@pytest.mark.parametrize("text", [
    "what is my context window size?",
    "show me the current inference runtime",
    "what are my gpu layers set to?",
])
def test_real_diagnostics_still_route_to_a_grounded_report(text):
    assert str(route(text).get("action")) in _GROUNDED_DIAGNOSTICS


def test_a_slow_reply_is_answered_from_the_turn_record():
    """Now "why did you take 20 minutes?" is answered from the turn record of that reply
    (the engine's last-turn explainer, reached through chat), not by a runtime report."""
    from eli.cognition.self_claims import asks_about_own_behaviour
    text = "why did you take 20 minutes to respond?"
    assert str(route(text).get("action")) == "CHAT"
    assert asks_about_own_behaviour(text)


@pytest.mark.parametrize("asked", ["where is the file named report.pdf located", "which file has the username in it",
                                   "how do you know the name of that song"])
def test_a_name_that_is_not_the_users_is_not_the_name_source_audit(asked):
    """"named", "username" and "the name of that song" got the audit of where ELI keeps the user's name."""
    assert route(asked)["action"] != "NAME_SOURCE_AUDIT"


@pytest.mark.parametrize("asked", ["how do you know my name", "where is my name stored", "which file is my name in"])
def test_where_the_users_name_comes_from_is_still_the_audit(asked):
    assert route(asked)["action"] == "NAME_SOURCE_AUDIT"


def test_a_members_new_name_clears_only_their_own_name_rows(tmp_path, monkeypatch):
    """The purge of name rows that no longer match ran over every user's rows, so an API member
    naming themself deleted the owner's name."""
    import sqlite3
    from types import SimpleNamespace
    from eli.execution import executor_enhanced as ex
    db = tmp_path / "user.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE user_patterns (id INTEGER PRIMARY KEY, pattern_type TEXT, pattern_data TEXT, "
                     "timestamp REAL, ts REAL, user_id TEXT)")
        conn.executemany("INSERT INTO user_patterns (pattern_type, pattern_data, user_id) VALUES (?, ?, ?)", [
            ("identity.name", "Rowan", None), ("identity.name", "Kit", "member-b")])
    monkeypatch.setattr("eli.memory.memory.get_memory", lambda *a, **k: SimpleNamespace(db_path=db))
    monkeypatch.setattr("eli.runtime.profile_extractor.pattern_owner", lambda user_id=None: "member-b")
    assert ex._purge_conflicting_identity_patterns("Jules") == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT pattern_data FROM user_patterns").fetchall() == [("Rowan",)]


def test_a_name_is_kept_once_in_the_callers_profile_and_status_reads_it_there(executor_name_store, monkeypatch):
    """The executor's state file kept a copy of the owner's name, which status read for every caller
    (a member asking was told the owner's). A name is written to the caller's own profile alone, and
    status reads that."""
    from eli.kernel import state
    ex, stored = executor_name_store
    for whose, name in (("member-b", "Jules"), ("", "Rowan")):
        monkeypatch.setattr("eli.runtime.profile_extractor.pattern_owner", lambda user_id=None, _w=whose: _w)
        assert ex.set_user_name(name)["ok"] is True and stored.get("profile") == name
        assert "user_name" not in stored, "no copy beside the profile"
    monkeypatch.setattr(state, "get_user_name", lambda default="", user_id=None: "Jules")
    monkeypatch.setattr(ex, "_ollama_ps", lambda: {})
    assert ex.get_status()["user_name"] == "Jules"
