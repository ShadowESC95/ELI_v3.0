"""What became of ELI's actions, as something it learns from (eli/runtime/action_outcomes.py): the
kind of target a run was about, a run checked against the world, the cause of a failure, the chance a
run like this works here, what worked instead, and what ELI does about a failure."""
import os
import time

import pytest

from eli.runtime import action_outcomes as ao
from eli.runtime import evidence_ledger as led

NOW = 1_800_000_000.0


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: path)
    ao.forget()
    yield path
    ao.forget()


def _ran(action, args, ok, t, *, session="s1", error="", cause="", extra=None):
    result = {"ok": ok, **({"error": error} if error else {}), **(extra or {})}
    led.record_event("executor_action", source="executor.post_dispatch", action=action,
                     subject=ao.subject_of(action, args), content=error or "done",
                     payload={"args": args, "result": result, **({"cause": cause} if cause else {})},
                     outcome="ok" if ok else "failed", session_id=session, timestamp=t)


def test_the_kind_of_target_a_run_was_about():
    assert ao.target_class("OPEN_APP", {"name": "Spotify", "target": "Spotify"}) == "app:spotify"
    assert ao.target_class("OPEN_URL", {"url": "https://www.example.org/path"}) == "site:example.org"
    assert ao.target_class("OPEN_APP", {"name": "github.com"}) == "site:github.com"
    assert ao.target_class("ANALYZE_PDF", {"path": "/tmp/a/report.PDF"}) == "path:.pdf"
    assert ao.target_class("OPEN_FILE_SYSTEM", {"path": "/tmp/a"}) == "path:folder"
    assert ao.target_class("WEB_SEARCH", {"query": "tide times"}) == "query"
    assert ao.target_class("DATE", {}) == ""
    # an item asked of a service is the item, judged with the service's other items: every song asked
    # of Spotify is not "the same request" as every other
    assert ao.subject_of("PLAY_MEDIA", {"query": "jazz", "target": "spotify"}) == "jazz"
    assert ao.target_class("PLAY_MEDIA", {"query": "jazz", "target": "spotify"}) == "service:spotify"
    assert ao.subject_of("STOP_MEDIA", {"target": "spotify"}) == "spotify"


def test_a_written_file_is_checked_and_a_search_that_brought_nothing_had_no_effect(tmp_path):
    written = tmp_path / "out.txt"
    assert ao.verify("CREATE_DOCUMENT", {"path": str(written)}, {"ok": True}, started=time.time()) == (
        False, "the file out.txt is not there")
    written.write_text("x")
    verified, how = ao.verify("CREATE_DOCUMENT", {"path": str(written)}, {"ok": True}, started=time.time() - 5)
    assert verified is True and "out.txt" in how
    os.utime(written, (time.time() - 3600, time.time() - 3600))
    assert ao.verify("CREATE_DOCUMENT", {}, {"ok": True, "path": str(written)}, started=time.time())[0] is False
    assert ao.verify("WEB_SEARCH", {"query": "x"}, {"ok": True, "results": []}) == (False, "nothing came back")
    assert ao.verify("WEB_SEARCH", {"query": "x"}, {"ok": True, "results": [{"title": "t"}]})[0] is True
    assert ao.verify("OPEN_URL", {"url": "https://a.b"}, {"ok": True}) == (None, ""), "what cannot be seen is not judged"
    assert ao.verify("CREATE_DOCUMENT", {"path": "/nowhere"}, {"ok": False}) == (None, ""), "a failure is not re-checked"
    # a generated script names its place in script_path; its bare file name is not a place
    script = tmp_path / "plot.py"
    script.write_text("print(1)")
    assert ao.verify("GENERATE_SCRIPT", {}, {"ok": True, "filename": "plot.py", "script_path": str(script)},
                     started=time.time() - 5) == (True, "the file plot.py is there")


def test_why_a_run_failed_by_the_one_taxonomy():
    assert ao.cause_of({"ok": False, "error": "No module named vlc"}, "PLAY_MEDIA") == "dependency"
    assert ao.cause_of({"ok": False, "error": "Permission denied: /etc/x"}) == "permission"
    assert ao.cause_of({"ok": True, "effect": False}) == ao.NOTHING_FOUND
    assert ao.cause_of({"ok": True, "verified": False, "verified_how": "the file x is not there"}) == ao.UNVERIFIED
    assert ao.cause_of({"ok": True}) == ""
    assert ao.cause_words("dependency") == "something it needs is not installed"


def test_the_cause_is_read_from_what_the_run_says_before_its_text_and_is_never_invented():
    # another song played: the result says so, whatever its words are
    played_other = {"ok": False, "played": False, "mismatch": True, "target": "spotify",
                    "response": "Spotify is playing a different track, not the one asked for."}
    assert ao.cause_of(played_other, "PLAY_MEDIA") == ao.MISMATCH
    assert ao.cause_words(ao.MISMATCH) == "what came up was not what was asked for"
    # the cause an effector names is taken as it is
    assert ao.cause_of({"ok": False, "cause": "missing_resource", "response": "no player"}, "MEDIA_CONTROL") == "missing_resource"
    # a program's action handed a site
    assert ao.cause_of({"ok": False, "response": "nothing matches"}, "CLOSE_APP", {"name": "example.org"}) == "not_a_program"
    # nothing explains it: the cause is not known, not "an error inside ELI"
    unknown = ao.cause_of({"ok": False, "response": "the light stayed on"}, "SMART_HOME")
    assert unknown == ao.UNKNOWN_CAUSE and "error inside ELI" not in ao.cause_words(unknown)
    assert "error inside ELI" not in ao.cause_words("no such category")


def test_the_chance_rests_on_the_target_then_the_action_then_every_action(ledger):
    for i in reversed(range(6)):
        _ran("OPEN_APP", {"name": "spotify"}, i % 3 != 0, NOW - 3600 * i, error="not found")
    for i in reversed(range(4)):
        _ran("OPEN_APP", {"name": "firefox"}, True, NOW - 3600 * i)
    spotify = ao.chance("OPEN_APP", {"name": "spotify"}, now=NOW)
    firefox = ao.chance("OPEN_APP", {"name": "firefox"}, now=NOW)
    unseen = ao.chance("OPEN_APP", {"name": "gimp"}, now=NOW)
    nothing = ao.chance("SET_TIMER", {"duration": 60}, now=NOW)
    assert spotify["by"] == "target" and spotify["n"] == 6 and spotify["cause"] == "missing_resource"
    assert firefox["by"] == "target" and firefox["p"] > spotify["p"]
    assert unseen["by"] == "action" and spotify["p"] < unseen["p"] < firefox["p"], "shrunk toward the action's rate"
    assert nothing["by"] == "all" and nothing["n"] == 0 and 0.4 < nothing["p"] < 0.9


def test_what_worked_instead_is_learned_from_the_same_request_tried_another_way(ledger):
    # twice, opening spotify failed and opening its site worked within the same minutes
    # another session's success a day apart is not the same request
    _ran("OPEN_APP", {"name": "spotify", "target": "spotify"}, False, NOW - 3 * 86400, session="s2", error="not found")
    _ran("OPEN_URL", {"url": "https://example.org"}, True, NOW - 3 * 86400 + 20, session="s2")
    for k in (1, 0):
        t = NOW - 86400 * k
        _ran("OPEN_APP", {"name": "spotify", "target": "spotify"}, False, t, error="not found", cause="missing_resource")
        _ran("OPEN_URL", {"url": "https://open.spotify.com"}, True, t + 20)
    others = ao.alternatives("OPEN_APP", {"name": "spotify"}, now=NOW)
    assert [o["action"] for o in others] == ["OPEN_URL"]
    assert others[0]["n"] == 2 and others[0]["p"] > 0.7 and others[0]["args"] == {"url": "https://open.spotify.com"}
    assert ao.alternatives("OPEN_APP", {"name": "gimp"}, now=NOW) == [], "learned for the target it was seen on"


def test_a_proven_safe_likelier_alternative_is_tried_and_the_rest_is_advised(ledger):
    for k in (2, 1, 0):
        t = NOW - 86400 * k
        _ran("OPEN_APP", {"name": "spotify", "target": "spotify"}, False, t, error="not found", cause="missing_resource")
        _ran("OPEN_URL", {"url": "https://open.spotify.com"}, True, t + 20)
    advice = ao.advice("OPEN_APP", {"name": "spotify", "target": "spotify"}, {"ok": False, "error": "not found"}, now=NOW)
    assert advice["cause"] == "missing_resource" and advice["failed_lately"] == 3
    assert advice["attempt"] == {"action": "OPEN_URL", "args": {"url": "https://open.spotify.com", "_alternative_of": "OPEN_APP"}}
    told = ao.said(advice)
    assert "open app for spotify has failed every time (3)" in told and "open url worked instead before" in told
    # the alternative itself is never retried as an alternative
    again = ao.advice("OPEN_URL", {"url": "https://open.spotify.com", "_alternative_of": "OPEN_APP"}, {"ok": False}, now=NOW)
    assert again["attempt"] is None
    # an action that destroys, writes or undoes is not the same request tried another way: it is
    # neither run unasked nor offered
    for k in (1, 0):
        t = NOW - 3600 * (k + 1)
        _ran("READ_FILE", {"path": "/tmp/x.txt"}, False, t, error="No such file", cause="missing_resource")
        _ran("DELETE_FILE", {"path": "/tmp/x.txt"}, True, t + 10)
        _ran("CREATE_FILE", {"path": "/tmp/x.txt", "content": "old words"}, True, t + 20)
        _ran("OPEN_APP", {"name": "firefox"}, False, t + 30, error="not found")
        _ran("CLOSE_APP", {"name": "firefox"}, True, t + 40)
    ao.forget()
    risky = ao.advice("READ_FILE", {"path": "/tmp/x.txt"}, {"ok": False, "error": "No such file"}, now=NOW)
    assert risky["alternatives"] == [] and risky["attempt"] is None
    assert "instead" not in ao.said(risky)
    undone = ao.advice("OPEN_APP", {"name": "firefox"}, {"ok": False, "error": "not found"}, now=NOW)
    assert undone["alternatives"] == [] and undone["attempt"] is None


def test_every_deed_group_writes_sends_destroys_and_undoes_nothing():
    from eli.cognition.llm_intent import _EXPLICIT_ONLY_ACTIONS
    from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
    for group in ao.SAME_DEED:
        assert len(group) > 1
        for name in group:
            assert name in SUPPORTED_ACTIONS and ao._safe_on_its_own(name) and name not in _EXPLICIT_ONLY_ACTIONS
    assert not ao.same_deed("PLAY_MEDIA", "STOP_MEDIA") and not ao.same_deed("PLAY_MEDIA", "PAUSE_MEDIA")
    assert not ao.same_deed("OPEN_APP", "CLOSE_APP") and not ao.same_deed("READ_FILE", "CREATE_FILE")
    assert not ao.same_deed("PLAY_MEDIA", "MEDIA_CONTROL") and ao.same_deed("OPEN_APP", "OPEN_URL")


def test_a_pause_after_a_failed_play_is_never_what_worked_instead(ledger):
    # the live pattern: a song asked of Spotify does not play, the music is paused, again and again
    for k in range(4):
        t = NOW - 3600 + 60 * k
        _ran("PLAY_MEDIA", {"target": "spotify", "query": f"song {k}"}, False, t,
             extra={"played": False, "mismatch": True, "target": "spotify"})
        _ran("STOP_MEDIA", {"target": "spotify"}, True, t + 20)
        _ran("MEDIA_CONTROL", {"command": "pause", "target": "spotify"}, True, t + 30)
    asked = {"target": "spotify", "query": "a song never asked before"}
    advice = ao.advice("PLAY_MEDIA", asked, {"ok": False, "played": False, "mismatch": True}, now=NOW)
    assert advice["attempt"] is None and advice["alternatives"] == []
    told = ao.said(advice)
    # the rate is over every song asked of Spotify, and says so; it is not this request's record
    assert told.startswith("play media on spotify has failed every time (4) here lately")
    assert "a song never asked before" not in told and "stop media" not in told
    assert "what came up was not what was asked for" in told
    # a play of an earlier item is no alternative for a later one either
    _ran("MEDIA_CONTROL", {"command": "play", "target": "youtube"}, False, NOW - 120, session="s3", error="not found")
    _ran("PLAY_MEDIA", {"target": "youtube", "query": "an earlier video"}, True, NOW - 100, session="s3")
    ao.forget()
    again = ao.advice("MEDIA_CONTROL", {"command": "play", "target": "youtube"}, {"ok": False, "error": "not found"}, now=NOW)
    assert again["attempt"] is None and again["alternatives"] == []


def test_elis_own_tries_are_not_evidence_and_each_later_run_counts_once(ledger):
    t = NOW - 3000
    for k in range(3):     # three failures in a row, then one success that answered them all
        _ran("OPEN_APP", {"name": "spotify"}, False, t + 10 * k, error=f"not found ({k})")
    _ran("OPEN_URL", {"url": "https://open.spotify.com"}, True, t + 40)
    # ELI's own try of the alternative, after a later failure, recorded as such
    _ran("OPEN_APP", {"name": "spotify"}, False, t + 100, error="not found")
    _ran("OPEN_URL", {"url": "https://open.spotify.com", "_alternative_of": "OPEN_APP"}, True, t + 101)
    (only,) = ao.alternatives("OPEN_APP", {"name": "spotify"}, now=NOW)
    assert only["action"] == "OPEN_URL" and only["n"] == 1
    assert ao.chance("OPEN_APP", {"name": "spotify"}, now=NOW)["n"] == 4
    assert ao.advice("OPEN_APP", {"name": "spotify"}, {"ok": False}, now=NOW)["runs_lately"] == 4


def test_an_alternative_is_tried_with_this_requests_item_never_an_old_runs():
    proven = {"url": "https://example.org/search", "query": "an old search", "text": "old words"}
    assert ao._attempt_args(proven, {"query": "what was asked now"}, "WEB_SEARCH") == {
        "url": "https://example.org/search", "query": "what was asked now"}
    assert ao._attempt_args({"url": "https://open.spotify.com"}, {"name": "spotify"}, "OPEN_APP") == {
        "url": "https://open.spotify.com"}


def test_the_record_told_what_worked_and_what_did_not(ledger):
    for i in reversed(range(5)):
        _ran("WEB_SEARCH", {"query": f"q{i}"}, i != 4, NOW - 60 * i, error="timed out" if i == 4 else "")
    for k in (1, 0):
        _ran("OPEN_APP", {"name": "spotify"}, False, NOW - 600 * (k + 1), error="not found", cause="missing_resource")
        _ran("OPEN_URL", {"url": "https://open.spotify.com"}, True, NOW - 600 * (k + 1) + 15)
    _ran("DATE", {}, True, NOW - 30)
    rep = ao.report(days=30, now=NOW)
    assert [r["action"] for r in rep["actions"]][:2] == ["OPEN_APP", "WEB_SEARCH"], "actions with failures first"
    first = rep["lines"][0]
    assert first.startswith("open app failed every time (2)") and "what it was pointed at was not there" in first
    assert "open url worked instead 2 times" in first
    assert "web search worked 4 of 5 times" in rep["lines"][1] and "took too long" in rep["lines"][1]
    assert rep["words"].startswith("What has worked here in the last 30 days")
    assert ao.report(days=30, action="DATE", now=NOW)["lines"] == ["date worked the one time it ran."]
    assert ao.words(days=30, limit=1).count("\n") == 0


def test_the_report_says_how_each_action_went_week_by_week_and_can_keep_every_action(ledger):
    from collections import Counter
    from eli.cognition.epistemics import week_start
    day = 86400.0
    runs = ((NOW - 20 * day, False), (NOW - 20 * day + 60, True), (NOW - 6 * day, True), (NOW - day, False))
    for t, ok in runs:
        _ran("OPEN_APP", {"name": "Notes"}, ok, t, error="" if ok else "Notes is not installed")
    _ran("WEB_SEARCH", {"query": "tide times"}, True, NOW - 2 * day)
    rep = ao.report(30.0, now=NOW, limit=None)
    row = next(r for r in rep["actions"] if r["action"] == "OPEN_APP")
    weeks = row["weeks"]
    assert [w["from"] for w in weeks] == sorted({w["from"] for w in weeks}), "every week once, oldest first"
    assert weeks[0]["from"] == week_start(NOW - 30 * day) and weeks[-1]["from"] == week_start(NOW), "the whole window"
    ran = Counter(week_start(t) for t, _ok in runs)
    worked = Counter(week_start(t) for t, ok in runs if ok)
    assert {w["from"]: (w["n"], w["ok"]) for w in weeks if w["n"]} == {k: (n, worked[k]) for k, n in ran.items()}
    assert any(w["n"] == 0 for w in weeks), "a week nothing ran in is there, empty"
    assert len(rep["actions"]) == 2 and len(ao.report(30.0, now=NOW, limit=1)["actions"]) == 1
