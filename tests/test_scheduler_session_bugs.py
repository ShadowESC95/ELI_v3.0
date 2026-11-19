"""Scheduler regressions from the 2026-08-01 session.

Three independent defects, all reproduced verbatim below:

  1. a joke ("Stop being so grumpy … you think i want to debug and test you at
     8:30 on a saturday? … go back to sleep haha") was routed to SCHEDULE_TASK and
     dispatched to the CODING AGENT, because a clock time anywhere in the sentence
     plus the everyday verb "Stop" satisfied the gate, and the question guard was
     anchored to ^ so the mid-sentence "?" was invisible;
  2. "8:45am today", typed at 08:44, was scheduled for 02:00 the NEXT day — the
     detector accepted `for|by|at` but the parser only understood `at`, and the
     mismatch fell through to a silent overnight default;
  3. chat had no list/cancel/reschedule, so the model confabulated "the system
     shows no active background tasks" while four were armed.

No GGUF and no model are used here — the clock is injected.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from eli.core.when import parse_when
from eli.execution.router_enhanced import _eli_schedule_prepass as prepass


# The session clock: Saturday 1 August 2026, 08:44:56.
NOW = datetime(2026, 8, 1, 8, 44, 56)

GRUMPY_JOKE = ("Stop being so grumpy, it is the weekend after all. You think i want "
               "to debug and test you at 8:30 on a saturday? If you don't want to be "
               "awke, go back to sleep haha")


# ── 1. The gate ──────────────────────────────────────────────────────────────

def test_the_joke_is_not_scheduled():
    assert prepass(GRUMPY_JOKE) is None


@pytest.mark.parametrize("text", [
    "you think i want to run the tests at 8am",          # rhetorical question
    "stop being so dramatic about the 9am standup",      # chatter + a time
    "haha you're up at 7am again",                       # banter
    "i want to sleep until 10am",                        # statement, not a command
])
def test_conversational_utterances_with_times_are_not_scheduled(text):
    assert prepass(text) is None, text


@pytest.mark.parametrize("text", [
    "research quantum computing overnight",
    "open spotify at 8pm",
    "schedule a morning report every morning",
    "Someone woke up on the wrong side of the bed this morning. Schedule a debug, "
    "and code calidation task for 8:45am today",
])
def test_genuine_scheduling_requests_still_work(text):
    r = prepass(text)
    assert r is not None and r["action"] == "SCHEDULE_TASK", text


def test_when_is_the_time_phrase_not_the_whole_utterance():
    """Passing the raw sentence is what fed the parser a paragraph it could not
    read, producing the silent 02:00 default."""
    r = prepass("Someone woke up on the wrong side of the bed this morning. "
                "Schedule a debug, and code calidation task for 8:45am today")
    when = r["args"]["when"]
    assert "8:45am" in when and "today" in when
    assert "woke up" not in when and "wrong side" not in when
    # A vague day-part from an unrelated clause must not outvote the explicit time.
    assert "this morning" not in when


# ── 2. Time parsing ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec,expected", [
    ("for 8:45am today",             "2026-08-01 08:45"),
    ("reschedule it for 8:45am today", "2026-08-01 08:45"),
    ("for 08:50 AM today",           "2026-08-01 08:50"),
    ("at 8:45am today",              "2026-08-01 08:45"),
    ("set it for 8:45 am",           "2026-08-01 08:45"),
    ("in 20 minutes",                "2026-08-01 09:04"),
    ("in 2 hours",                   "2026-08-01 10:44"),
    ("at 2am tomorrow",              "2026-08-02 02:00"),
    ("on sunday at 02:00",           "2026-08-02 02:00"),
    ("02:00 on Sunday, August 2nd",  "2026-08-02 02:00"),
    ("noon",                         "2026-08-01 12:00"),
    ("at 7",                         "2026-08-02 07:00"),   # already past → tomorrow
    ("overnight",                    "2026-08-02 02:00"),
    ("tonight",                      "2026-08-02 02:00"),   # the COMING night
])
def test_time_specs_resolve_correctly(spec, expected):
    ts = parse_when(spec, now=NOW)
    assert ts is not None, f"{spec!r} did not parse"
    assert datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") == expected, spec


def test_the_exact_regression_no_longer_defaults_to_overnight():
    """"for 8:45am today" at 08:44 previously produced +62104s → 02:00 next day."""
    ts = parse_when("reschedule it for 8:45am today", now=NOW)
    assert int(ts - NOW.timestamp()) == 4, "fell back to the overnight default again"


@pytest.mark.parametrize("spec", ["whenever", "sometime", "at 25", "next week sometime", ""])
def test_unparseable_specs_return_none_instead_of_inventing_a_time(spec):
    assert parse_when(spec, now=NOW) is None, spec


def test_named_day_in_the_past_is_reported_not_silently_moved():
    """"8:45am today" typed at 08:46 is impossible — say so rather than booking
    tomorrow, which is precisely how the original bug looked to the user."""
    assert parse_when("for 8:45am today", now=datetime(2026, 8, 1, 8, 46)) is None


def test_no_spec_ever_resolves_to_the_past():
    for spec in ("tonight", "overnight", "at 7", "in 20 minutes", "tomorrow"):
        for hour in (0, 3, 8, 13, 20, 23):
            now = NOW.replace(hour=hour, minute=30)
            ts = parse_when(spec, now=now)
            if ts is not None:
                assert ts > now.timestamp(), f"{spec!r} at {hour}:30 resolved to the past"


# ── 3. Task control from chat ────────────────────────────────────────────────

def test_task_control_utterances_route_to_the_new_actions():
    from eli.execution.router_enhanced import route
    cases = {
        # The verbatim utterance that produced the confabulated denial.
        "delete background task for 02:00 on Sunday, August 2nd": "CANCEL_TASK",
        "cancel task 3": "CANCEL_TASK",
        "what background tasks are scheduled": "LIST_TASKS",
        "list my scheduled tasks": "LIST_TASKS",
        "what have you got scheduled": "LIST_TASKS",
        "reschedule task 3 to 14:00": "RESCHEDULE_TASK",
    }
    for text, expected in cases.items():
        assert route(text)["action"] == expected, f"{text!r} -> {route(text)['action']}"


def test_task_actions_are_registered_and_supported():
    from eli.execution.effectors.registry import get as registry_get
    from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
    for action in ("LIST_TASKS", "CANCEL_TASK", "RESCHEDULE_TASK"):
        assert registry_get(action) is not None, f"{action} has no effector"
        assert action in SUPPORTED_ACTIONS, f"{action} missing from SUPPORTED_ACTIONS"


def test_describe_tasks_is_never_silently_empty():
    """The prompt block must say 'none' explicitly — an empty string is what let
    the model fill the gap with an invented answer."""
    from eli.runtime.task_admin import describe_tasks
    assert describe_tasks().strip() != ""


def test_the_kinds_the_tasks_tab_offers_are_the_kinds_the_scheduler_runs():
    """The list was written out a second time by hand beside the worker table."""
    from eli.runtime.scheduled_tasks import _WORKERS
    from eli.runtime.task_admin import KNOWN_KINDS
    assert KNOWN_KINDS == ("auto", *_WORKERS)
    assert KNOWN_KINDS[:3] == ("auto", "research", "code")
