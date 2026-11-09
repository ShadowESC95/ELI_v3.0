"""Tests for the proactive habit-offer flow: ELI proposes a detected habit
(disabled) and only activates it on the user's yes. Plus the 00:00 timestamp fix.

Deterministic — no model, no daemon; uses a temp artifacts dir + temp DB.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import eli.planning.habits as H
from eli.planning.habits import _extract_hour_minute
from eli.memory.memory import Memory


@pytest.fixture
def tmp_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(H, "_artifacts_dir", lambda: tmp_path)
    H.clear_pending_habit()
    yield tmp_path
    H.clear_pending_habit()


@pytest.fixture
def tmp_memory(monkeypatch):
    db = tempfile.mktemp(suffix=".sqlite3")
    m = Memory(db_path=db)
    import eli.memory as mem_pkg
    monkeypatch.setattr(mem_pkg, "get_memory", lambda: m)
    yield m
    Path(db).unlink(missing_ok=True)


# ── 00:00 fix ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ts,expected", [
    (None, None), ("", None), (0, None), (0.0, None), ("0", None),
    (1000, (10, 0)), (1305, (13, 5)),
])
def test_extract_hour_minute_skips_degenerate(ts, expected):
    assert _extract_hour_minute(ts) == expected


def test_real_unix_timestamp_parses():
    hm = _extract_hour_minute(1780759925)
    assert hm is not None and 0 <= hm[0] <= 23 and 0 <= hm[1] <= 59


# ── pending-offer state ──────────────────────────────────────────────────────
def test_pending_habit_state_roundtrip(tmp_artifacts):
    assert H.get_pending_habit() is None
    H.set_pending_habit(7, "Open spotify at 09:00", 9, 0, "spotify")
    p = H.get_pending_habit()
    assert p and p["rule_id"] == 7 and p["hour"] == 9
    H.clear_pending_habit()
    assert H.get_pending_habit() is None


def test_offered_tracking(tmp_artifacts):
    assert not H.was_offered(42)
    H.mark_offered(42)
    assert H.was_offered(42)


# ── router intercept ─────────────────────────────────────────────────────────
def test_router_intercepts_yes_no_only_when_pending(tmp_artifacts):
    from eli.execution.router_enhanced import route
    # Nothing pending → 'yes' is ordinary chat, not a habit confirm.
    assert route("yes")["action"] != "CONFIRM_HABIT"
    H.set_pending_habit(1, "Open spotify at 09:00", 9, 0, "spotify")
    assert route("yes")["action"] == "CONFIRM_HABIT"
    assert route("sure, add it")["action"] == "CONFIRM_HABIT"
    assert route("no thanks")["action"] == "DECLINE_HABIT"
    # A real sentence that merely contains a confirm/decline word must NOT be
    # hijacked while an offer is pending — only short, unambiguous replies are.
    for sentence in (
        "you do not know my name?",
        "what do you do with the data you collect?",
        "sure but first tell me what habits you have detected so far",
    ):
        assert route(sentence)["action"] not in ("CONFIRM_HABIT", "DECLINE_HABIT")


# ── confirm / decline executor ───────────────────────────────────────────────
def test_confirm_habit_enables_rule(tmp_artifacts, tmp_memory):
    from eli.execution import executor_enhanced as EX
    rid = tmp_memory.add_habit_rule("Open spotify at 09:00", "spotify", 9, 0, None, enabled=False)
    H.set_pending_habit(rid, "Open spotify at 09:00", 9, 0, "spotify")
    res = EX.execute("CONFIRM_HABIT", {"message": "yes"})
    assert res["ok"]
    rule = {r["id"]: r for r in tmp_memory.get_habit_rules(enabled_only=False)}[rid]
    assert rule["enabled"]
    assert H.get_pending_habit() is None


def test_a_declined_habit_is_kept_disabled_and_not_offered_again(tmp_artifacts, tmp_memory):
    """Live: "no" deleted the suggestion, the next detection made the same rule under a new id that had
    never been offered, and ELI asked again the next session."""
    from eli.execution import executor_enhanced as EX
    rid = tmp_memory.add_habit_rule("Open spotify at 09:00", "spotify", 9, 0, None, enabled=False)
    H.set_pending_habit(rid, "Open spotify at 09:00", 9, 0, "spotify")
    H.mark_offered(rid)
    res = EX.execute("DECLINE_HABIT", {"message": "no"})
    assert res["ok"]
    rules = {r["id"]: r for r in tmp_memory.get_habit_rules(enabled_only=False)}
    assert rid in rules and not rules[rid]["enabled"] and H.was_offered(rid)
    assert H.get_pending_habit() is None


def test_confirm_with_no_pending_is_graceful(tmp_artifacts, tmp_memory):
    from eli.execution import executor_enhanced as EX
    res = EX.execute("CONFIRM_HABIT", {"message": "yes"})
    assert res["ok"]
    assert "no habit offer" in res["content"].lower()


# ── purge of legacy un-schedulable rows (the 00:00 leak source) ──────────────
def test_purge_invalid_habit_rules_drops_legacy_rows(tmp_memory):
    # Legacy corruption: NULL time + command == bare name (e.g. 'firefox').
    conn = tmp_memory._get_connection()
    conn.execute(
        "INSERT INTO habit_rules (name, command, hour, minute, enabled) "
        "VALUES (?, ?, NULL, NULL, 0)", ("firefox", "firefox"))
    conn.execute(
        "INSERT INTO habit_rules (name, command, hour, minute, enabled) "
        "VALUES (?, ?, NULL, NULL, 0)", ("spotify", "spotify"))
    conn.commit(); conn.close()
    # A legitimate detected rule must survive (distinct command + real time).
    tmp_memory.add_habit_rule("Open firefox at 13:05", "firefox", 13, 5, None, enabled=False)

    deleted = tmp_memory.purge_invalid_habit_rules()
    assert deleted == 2
    names = {r["name"] for r in tmp_memory.get_habit_rules(enabled_only=False)}
    assert names == {"Open firefox at 13:05"}


def test_purge_keeps_user_authored_rule_with_real_time(tmp_memory):
    # command == name but a real time → user-authored, must NOT be purged.
    tmp_memory.add_habit_rule("firefox", "firefox", 9, 30, None, enabled=True)
    assert tmp_memory.purge_invalid_habit_rules() == 0
    assert len(tmp_memory.get_habit_rules(enabled_only=False)) == 1


# ── frequent behaviour: what worked, lately ──────────────────────────────────

def _uses(mem, action, ok, n, days_ago=0.0):
    import sqlite3
    import time
    for _ in range(n):
        mem.log_habit_event("command_result", {"action": action, "args": {}, "ok": ok, "source": "cognitive_engine"})
    if days_ago:
        when = time.time() - days_ago * 86400.0
        c = sqlite3.connect(mem.db_path)
        c.execute("UPDATE habit_events SET timestamp = ?, ts = ? WHERE id IN (SELECT id FROM habit_events "
                  "ORDER BY id DESC LIMIT ?)", (when, when, n))
        c.commit()
        c.close()


def test_a_frequent_behaviour_counts_the_uses_that_worked_lately(tmp_path):
    """The all-time tally of every request, failed or not, grew "play media x31" to x57 during a run
    of failed plays, and ELI offered to streamline it."""
    mem = Memory(db_path=tmp_path / "user.sqlite3")
    _uses(mem, "PLAY_MEDIA", False, 20)
    _uses(mem, "PLAY_MEDIA", True, 2)
    _uses(mem, "TAKE_SCREENSHOT", True, 9)
    _uses(mem, "OPEN_NEWS", True, 30, days_ago=10 * mem.adaptive_half_life())
    found = {h["name"]: h for h in mem.get_detected_habits(min_count=3, limit=10)}
    assert set(found) == {"TAKE_SCREENSHOT"}, found
    assert found["TAKE_SCREENSHOT"]["count"] == 9 and found["TAKE_SCREENSHOT"]["failed"] == 0
    loose = {h["name"]: h for h in mem.get_detected_habits(min_count=1, limit=10)}
    assert loose["PLAY_MEDIA"]["count"] == 2 and loose["PLAY_MEDIA"]["failed"] == 20
    assert "OPEN_NEWS" not in loose, "uses long past the user's own window still counted"


# ── the offer bar comes from his answers; a broken action goes to repair ─────────────────────────────
class _Rules:
    """A memory whose habit rules were offered and answered: `answers` maps rule id -> enabled."""

    def __init__(self, answers, habits=()):
        self.answers, self.habits = dict(answers), list(habits)

    def get_habit_rules(self, enabled_only=False):
        return [{"id": rid, "enabled": on} for rid, on in self.answers.items()]

    def get_detected_habits(self, min_count=3, limit=10):
        return [h for h in self.habits if h["count"] >= min_count][:limit]


@pytest.mark.parametrize("answers,bar", [({}, 8), ({1: False, 2: False, 3: False}, 32),
                                         ({1: True, 2: True, 3: True}, 2), ({1: True, 2: False}, 8)])
def test_the_offer_bar_follows_how_he_answered_earlier_offers(tmp_artifacts, answers, bar):
    """It was a fixed 8 uses whatever he had said to ELI's offers before."""
    for rid in answers:
        H.mark_offered(rid)
    assert H.offer_threshold(_Rules(answers)) == bar


def test_the_offer_still_waiting_is_not_counted_as_an_answer(tmp_artifacts):
    for rid in (1, 2):
        H.mark_offered(rid)
    H.set_pending_habit(2, "Open the news at 08:00", 8, 0, "open the news")
    assert H.offer_answers(_Rules({1: True, 2: False})) == (1, 0)


def test_an_action_that_fails_more_than_it_works_goes_to_repair_not_to_an_offer(tmp_artifacts):
    """A run of failed plays was offered to be 'streamlined'."""
    from eli.planning.goal_autogenesis import _candidates_from_patterns
    from eli.planning.proactive_daemon import ProactiveDaemon
    for rid in (1, 2, 3):
        H.mark_offered(rid)
    daemon = ProactiveDaemon.__new__(ProactiveDaemon)
    daemon.user_mem = _Rules({1: True, 2: True, 3: True}, habits=[
        {"name": "PLAY_MEDIA", "count": 9, "failed": 20}, {"name": "SCREENSHOT", "count": 9, "failed": 1},
        {"name": "OPEN_NEWS", "count": 3, "failed": 0}, {"name": "TIME", "count": 1, "failed": 0}])
    found = daemon._frequent_behaviour_patterns()
    assert [(p["type"], p.get("behavior") or p.get("error")) for p in found] == [
        ("recurring_error", "PLAY_MEDIA fails more often than it works"),
        ("frequent_behavior", "SCREENSHOT"), ("frequent_behavior", "OPEN_NEWS")]
    goals = _candidates_from_patterns(found + [{"type": "recurring_error", "error": "x", "count": 1}])
    kinds = sorted((g["kind"], g["key"]) for g in goals)
    assert ("frequent_behavior", "OPEN_NEWS") in kinds, "the bar he set reaches the goal too, not a second 8"
    assert ("recurring_error", "PLAY_MEDIA fails more often than it works") in kinds
    assert ("frequent_behavior", "PLAY_MEDIA") not in kinds
