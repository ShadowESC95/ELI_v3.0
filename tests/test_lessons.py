"""Lessons expire, are checked against what happens next, and are retired when they do not help."""
import json
import sqlite3
import time

import pytest

from eli.runtime import lessons

DAY = 86400.0


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(lessons._SCHEMA)
    return c


def _make(conn, now=1000.0, **kw):
    return lessons.propose("SCREENSHOT", "SCREENSHOT failed with: no display", ["3 occurrences"],
                           "Check a display is available before SCREENSHOT", now=now, conn=conn, **kw)


def test_the_same_trigger_adds_evidence_not_a_second_lesson(conn):
    a = _make(conn)
    b = lessons.propose("SCREENSHOT", "SCREENSHOT failed with: no display", ["seen again"], "x", now=1001.0, conn=conn)
    assert a == b
    assert len(json.loads(conn.execute("select evidence from lessons").fetchone()[0])) == 2


def test_only_active_unexpired_lessons_for_the_action_apply(conn):
    _make(conn, now=1000.0, ttl_days=1)
    assert lessons.applicable("screenshot", now=1500.0, conn=conn)
    assert not lessons.applicable("OPEN_APP", now=1500.0, conn=conn)
    assert not lessons.applicable("SCREENSHOT", now=1000.0 + 2 * DAY, conn=conn)


def test_a_lesson_that_keeps_failing_is_retired(conn):
    """Kept or retired by the one test against how the action did before the lesson, not by a
    hand-set share of checks."""
    lid = _make(conn, baseline=(0.6, 20))
    status = [lessons.record_outcome(lid, ok, now=2000.0 + i, conn=conn) for i, ok in enumerate([False] * 6)]
    assert status[-1] == "retired"
    assert "shown worse than before the lesson" in conn.execute("select retired_reason from lessons").fetchone()[0]


def test_a_lesson_that_helps_survives_and_is_renewed(conn):
    lid = _make(conn, now=0.0, ttl_days=1, baseline=(0.3, 10))
    for i in range(6):
        lessons.record_outcome(lid, True, now=10.0 + i, conn=conn)
    assert lessons.review(now=2 * DAY, conn=conn) == {"retired": 0, "renewed": 1}
    assert lessons.applicable("SCREENSHOT", now=2 * DAY + 1, conn=conn)


def test_a_lesson_on_an_action_that_nearly_always_works_can_still_be_kept(conn):
    """It was retired at three checks unless its rate beat the baseline by 0.05, so with a baseline of
    0.95 or more no lesson could ever survive; review() used a different margin again."""
    lid = _make(conn, now=0.0, ttl_days=1, baseline=(0.96, 50))
    for i in range(30):
        assert lessons.record_outcome(lid, True, now=10.0 + i, conn=conn) == "active"
    assert lessons.review(now=2 * DAY, conn=conn)["renewed"] == 1


def test_an_unconfirmed_lesson_expires(conn):
    _make(conn, now=0.0, ttl_days=1)
    assert lessons.review(now=2 * DAY, conn=conn) == {"retired": 1, "renewed": 0}
    assert conn.execute("select retired_reason from lessons").fetchone()[0] == "expired unconfirmed"


def test_failure_clusters_become_lessons(tmp_path, monkeypatch):
    """Read from the failure record every runtime failure path writes (Memory.log_failure into the
    failures table), not error_tracking, which no action failure ever reached."""
    from eli.memory import Memory
    db = tmp_path / "agent.sqlite3"
    mem = Memory(db_path=str(db), db_type="agent")
    for n in range(3):    # the executor's shape: one row per argument set, the action in the context
        mem.log_failure(f'SCREENSHOT {{"n": {n}}}', error="no display", context={"action": "SCREENSHOT", "args": {"n": n}},
                        source="executor_post_dispatch")
    mem.log_failure("OPEN_APP {}", error="rare", context={"action": "OPEN_APP"}, source="executor_post_dispatch")
    for _ in range(3):    # a complaint is not a failure of the action its turn routed to
        mem.log_failure("a complaint, in the person's words", error="anomaly (rejected): the reply was rejected",
                        context={"action": "CHAT", "outcome": "ok"}, source="anomaly")
    monkeypatch.setattr("eli.core.paths.agent_db_path", lambda: db)
    made = lessons.from_failure_clusters()
    assert len(made) == 1
    rows = sqlite3.connect(db).execute("select applies_to, proposed_change from lessons").fetchall()
    assert len(rows) == 1 and rows[0][0] == "SCREENSHOT" and "no display" in rows[0][1]


def test_a_lesson_is_checked_only_on_runs_it_was_shown_for(tmp_path, monkeypatch):
    """brief() had no caller, so no lesson ever reached a prompt, while every run of the action
    counted as a check of it: lessons were kept or retired on runs that never saw them."""
    db = tmp_path / "agent.sqlite3"
    monkeypatch.setattr("eli.core.paths.agent_db_path", lambda: db)
    lid = lessons.propose("SCREENSHOT", "t", ["e"], "check a display is there first")
    lessons.observe_action("screenshot", True)              # nobody saw the lesson
    assert sqlite3.connect(db).execute("select checks from lessons where id = ?", (lid,)).fetchone() == (0,)

    told = lessons.brief("SCREENSHOT")
    assert "check a display is there first" in told
    lessons.observe_action("OPEN_APP", False)               # another action: not this lesson's check
    lessons.observe_action("screenshot", True)
    lessons.observe_action("screenshot", False)             # shown once, checked once
    row = sqlite3.connect(db).execute("select checks, helped from lessons where id = ?", (lid,)).fetchone()
    assert row == (1, 1)


def test_a_lesson_worse_than_the_baseline_is_retired_and_one_not_shown_worse_is_not(conn):
    lid = lessons.propose("SCREENSHOT", "t2", ["e"], "check", now=1000.0, conn=conn, baseline=(0.9, 20))
    for i, ok in enumerate([True, True, False, True]):        # 3 of 4 against 18 of 20: nothing shown
        status = lessons.record_outcome(lid, ok, now=2000.0 + i, conn=conn)
    assert status == "active"
    for i, ok in enumerate([False, False, False, True, False]):
        status = lessons.record_outcome(lid, ok, now=3000.0 + i, conn=conn)
    assert status == "retired" and "shown worse than before" in conn.execute("select retired_reason from lessons").fetchone()[0]


def test_a_lesson_that_beats_its_baseline_survives(conn):
    lid = lessons.propose("SCREENSHOT", "t3", ["e"], "check", now=1000.0, conn=conn, baseline=(0.3, 12))
    for i in range(4):
        status = lessons.record_outcome(lid, True, now=2000.0 + i, conn=conn)
    assert status == "active"


def test_a_scoped_lesson_applies_only_where_it_was_learned(conn):
    lessons.propose("OPEN_APP", "t4", ["e"], "check the path exists", now=1000.0, conn=conn, scope=["path"])
    assert lessons.applicable("OPEN_APP", args={"path": "/x"}, now=1500.0, conn=conn)
    assert not lessons.applicable("OPEN_APP", args={"name": "code"}, now=1500.0, conn=conn)


def test_the_scope_is_the_argument_only_the_failures_share(tmp_path, monkeypatch):
    from eli.runtime import evidence_ledger as led
    db = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: db)
    for i in range(5):
        led.record_event("executor_action", action="OPEN_APP", subject=f"f{i}", payload={"args": {"path": f"/mnt/x{i}"}}, outcome="failed", db_path=db, timestamp=1000.0 + i * 20)
        led.record_event("executor_action", action="OPEN_APP", subject=f"o{i}", payload={"args": {"name": f"app{i}"}}, outcome="ok", db_path=db, timestamp=1500.0 + i * 20)
    assert lessons.scope_from_history("OPEN_APP", days=10 ** 6) == ["path"]


def test_a_lesson_is_checked_on_sibling_actions_too(conn):
    lid = lessons.propose("SCREENSHOT", "t5", ["e"], "check display", now=1000.0, conn=conn, siblings=["SCREEN_READ_ANALYZE"],
                          baseline=(0.5, 10))
    shown = lessons.applicable("SCREEN_READ_ANALYZE", now=1500.0, conn=conn, siblings=True)
    assert [r["id"] for r in shown] == [lid] and shown[0]["transfer"] is True
    for ok in (True, True, True):
        lessons._record_transfer([lid], ok, conn=conn)
    assert lessons.transfers(lid, conn=conn) is None, "three runs show nothing yet"
    for ok in (True, True, True):
        lessons._record_transfer([lid], ok, conn=conn)
    assert lessons.transfers(lid, conn=conn) is True


def test_a_sibling_lesson_shown_is_a_transfer_check(tmp_path, monkeypatch):
    db = tmp_path / "agent.sqlite3"
    monkeypatch.setattr("eli.core.paths.agent_db_path", lambda: db)
    lid = lessons.propose("SCREENSHOT", "t6", ["e"], "check display", siblings=["SCREEN_READ_ANALYZE"])
    assert "check display" in lessons.brief("SCREEN_READ_ANALYZE")
    lessons.observe_action("SCREEN_READ_ANALYZE", True)
    row = sqlite3.connect(db).execute("select checks, transfer_checks, transfer_helped from lessons where id = ?",
                                      (lid,)).fetchone()
    assert row == (0, 1, 1)


def test_a_run_is_told_its_lessons_before_it_runs_and_they_are_checked_by_it(tmp_path, monkeypatch):
    """Nothing asked brief() before an action ran, so no lesson was ever shown to a run and none was
    ever checked. The executor asks before every run, keeps what it was told on the turn and the
    result, and the run's outcome checks exactly those lessons."""
    from eli.execution.executor_enhanced import execute
    from eli.kernel import request_context as rc
    db = tmp_path / "agent.sqlite3"
    monkeypatch.setattr("eli.core.paths.agent_db_path", lambda: db)
    lid = lessons.propose("GET_TIME", "t", ["e"], "read the clock of this machine")
    facts = {"request_id": "r-lessons"}
    token = rc.turn_facts_var.set(facts)
    try:
        out = execute("GET_TIME", {})
    finally:
        rc.turn_facts_var.reset(token)
    assert "read the clock of this machine" in out.get("lessons_applied", "")
    assert "read the clock of this machine" in facts["lessons_shown"]["GET_TIME"]
    assert sqlite3.connect(db).execute("select checks from lessons where id = ?", (lid,)).fetchone() == (1,)


# ── lever lessons: a part of ELI flagged by its outcomes is changed, and the change judged ────────

@pytest.fixture()
def stores(tmp_path, monkeypatch):
    from eli.cognition import epistemics as ep
    from eli.cognition import thoughts as th
    from eli.runtime import action_outcomes as ao
    from eli.runtime import evidence_ledger as led
    ledger = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: ledger)
    monkeypatch.setattr(th, "_default_db_path", lambda: ledger)
    monkeypatch.setattr("eli.core.paths.agent_db_path", lambda: tmp_path / "agent.sqlite3")
    monkeypatch.setenv("ELI_GOAL_STORE", str(tmp_path / "goals.json"))
    ep.forget_fits()
    ao.forget()
    lessons._forget_levers()
    yield tmp_path
    ep.forget_fits()
    lessons._forget_levers()


def _routes(prefix, n, right, _class="rule", **features):
    from eli.cognition import epistemics as ep
    for i in range(n):
        ep.assess(ep.ROUTE, 0.9, request_id=f"route:{prefix}{i}", class_=_class, features=dict(features))
        ep.settle(f"route:{prefix}{i}", i < right, kind=ep.ROUTE)


def test_a_rule_measured_worse_than_the_parser_is_flagged_and_handed_to_it(stores):
    """The router never handed a rule that measured worse than the resolver over to it (no worse_than
    existed), and the lessons it kept were advice nobody applied."""
    from eli.cognition import epistemics as ep
    from eli.kernel import engine
    _routes("bad", 20, 4, via="media.play_guess", parser_agrees=0.0)
    _routes("good", 40, 36, via="app.wake_prefix_contract", parser_agrees=1.0)
    _routes("parser", 20, 18, via="parser.frame", _class="parser")
    assert ep.worse_than(ep.ROUTE, {"via": "media.play_guess"}, {"class": "parser"}) is True
    assert ep.worse_than(ep.ROUTE, {"via": "app.wake_prefix_contract"}, {"class": "parser"}) is not True
    flagged = ep.rank(ep.ROUTE)
    assert [f["unit"] for f in flagged] == ["media.play_guess"] and flagged[0]["p_value"] < 0.1
    out = lessons.review()
    assert out.get("opened") == 1 and lessons.lever("route", "media.play_guess") == lessons.PARSER_FIRST
    assert lessons.review().get("opened") is None, "one open lesson per part, and no second part is flagged"
    intent = {"action": "PLAY_MEDIA", "args": {"query": "the harbour song"}, "confidence": 0.9,
              "meta": {"matched_by": "media.play_guess"}}
    handed = engine._handed_to_parser("open firefox", intent, "media.play_guess")
    assert handed is not intent and handed["action"] == "OPEN_APP" and handed["meta"]["lever_of"] == "media.play_guess"
    assert engine._handed_to_parser("open firefox", dict(intent, action="OPEN_APP"), "media.play_guess")["action"] == "OPEN_APP"
    assert engine._handed_to_parser("open firefox", intent, "app.wake_prefix_contract") is intent, "no lever there"
    [lesson] = lessons.report()
    assert lesson["state"] == "open" and lesson["evidence"]["n"] == 20
    from eli.cognition import thoughts as th
    assert th.recent(5, kinds=["lesson"])[0]["decision"] == "taken"


def test_a_lever_is_kept_once_its_part_does_as_well_as_its_peers_and_reverted_once_worse(stores):
    from eli.cognition import epistemics as ep
    _routes("bad", 20, 4, via="media.play_guess", parser_agrees=0.0)
    _routes("good", 40, 36, via="app.wake_prefix_contract", parser_agrees=1.0)
    lessons.review()
    [opened] = lessons.report()
    _routes("handed", 40, 36, via="parser.lever", lever_of="media.play_guess")
    assert lessons.review().get(lessons.KEPT) == 1
    assert lessons.lever("route", "media.play_guess") == lessons.PARSER_FIRST, "a kept lever stays in force"
    assert ep.outcomes(ep.LESSON) and ep.outcomes(ep.LESSON)[0][1] == 1.0, "the lesson's figure settled kept"
    # a second part, whose lever makes things worse, is reverted
    lid = lessons.open_lever("route", "news.guess", lessons.PARSER_FIRST,
                             evidence={"right": 30, "n": 40, "peer_right": 70, "peer_n": 80, "z": 2.0})
    _routes("worse", 20, 2, via="parser.lever", lever_of="news.guess")
    assert lessons.review().get(lessons.REVERTED) == 1
    assert lessons.lever("route", "news.guess") == "" and lid


def test_a_held_path_is_held_whatever_its_figure_and_released_after_a_half_life(stores, monkeypatch):
    from eli.cognition import epistemics as ep
    from eli.kernel.engine import _FAST_PATH_LINE, _fast_path_stands
    assert _fast_path_stands(_FAST_PATH_LINE + 0.1)
    lid = lessons.open_lever("answer", "bus_fast", lessons.HOLD, evidence={"right": 5, "n": 20, "peer_right": 90,
                                                                           "peer_n": 100, "z": 3.0})
    assert lid and lessons.held("answer", "bus_fast") and not _fast_path_stands(_FAST_PATH_LINE + 0.1)
    later = time.time() + ep.HALF_LIFE_DAYS * DAY + 1
    assert lessons.review(now=later).get(lessons.RELEASED) == 1
    assert not lessons.held("answer", "bus_fast") and _fast_path_stands(_FAST_PATH_LINE + 0.1)


def test_a_part_with_no_lever_of_its_own_gets_one_repair_goal_closed_when_it_recovers(stores):
    from eli.planning.goal_store import load_goals
    lid = lessons.open_lever("claim", "inferred", lessons.REPAIR,
                             evidence={"right": 2, "n": 20, "peer_right": 60, "peer_n": 80, "z": 3.1})
    [goal] = [g for g in load_goals() if "repair" in g.tags]
    assert goal.status == "proposed" and not goal.enabled and "2 of 20" in goal.objective
    assert goal.metadata["lesson_id"] == lid
    assert lessons.open_lever("claim", "inferred", lessons.REPAIR, evidence={}) is None, "one per part"


def test_an_action_with_a_proven_alternative_runs_it_first_under_its_lever(stores):
    from eli.runtime import action_outcomes as ao
    from eli.runtime import evidence_ledger as led
    now = time.time()
    for k in (3, 2, 1):
        t = now - 3600 * k
        led.record_event("executor_action", action="OPEN_APP", subject="spotify",
                         payload={"args": {"name": "spotify"}, "result": {"ok": False}}, outcome="failed",
                         session_id="s", timestamp=t)
        led.record_event("executor_action", action="OPEN_URL", subject="https://open.spotify.com",
                         payload={"args": {"url": "https://open.spotify.com"}, "result": {"ok": True}}, outcome="ok",
                         session_id="s", timestamp=t + 20)
    ao.forget()
    assert ao.first_choice("OPEN_APP", {"name": "spotify"}) is None, "no lever on it"
    lessons.open_lever(lessons.ACTION, "OPEN_APP", lessons.PROVEN_FIRST,
                       evidence={"right": 0, "n": 3, "peer_right": 3, "peer_n": 3, "z": 2.4})
    first = ao.first_choice("OPEN_APP", {"name": "spotify"})
    assert first == {"action": "OPEN_URL", "args": {"url": "https://open.spotify.com", "_proven_first": "OPEN_APP"}}
    assert ao.first_choice("OPEN_APP", {"name": "spotify", "_proven_first": "OPEN_APP"}) is None


def test_a_gate_with_nothing_shown_explores_by_thompson_sampling_and_stops_once_shown():
    """A gate judged only by its own path could never open: it never took the path. Thompson
    sampling over the two records takes it as often as the uncertainty deserves."""
    import random
    from eli.cognition import epistemics as ep
    rng = random.Random(7)
    opened = [ep.thompson([(0, 0), (0, 0)], rng=rng) for _ in range(400)]
    assert 0.35 < sum(opened) / len(opened) < 0.65
    assert ep.thompson([(1, 30), (29, 30)], rng=rng) == 1
