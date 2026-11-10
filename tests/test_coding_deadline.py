"""The coding agent must honour a shared absolute wall-clock deadline.

Regression cover for the 2-hour GUI freeze: one self-repair request fanned out to
tens of sequential model calls because every loop carried its own budget, re-armed
per invocation and checked only AFTER a generation returned. On a local GGUF a
single call runs 30-500s, so an after-the-fact check can never enforce a budget.

No GGUF here — `generate` is a stub that spends time on the deadline's clock. The clock
moves only by what the stub model spends (and what a test moves it by): the agent's own
work between calls takes no time on it, so a busy machine cannot change what these tests
see. On the real clock they failed under load: a 50 ms deadline had passed before it was
first read, and a solve's checks between calls took longer than the 2 s allowed.
"""
from __future__ import annotations

import math

import pytest

from eli.coding.agent import CodeAgent
from eli.coding.budget import Deadline


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    from eli.coding import budget
    c = _Clock()
    monkeypatch.setattr(budget, "time", c)      # the deadline reads time.monotonic() alone
    return c


def _slow_generate(seconds: float, counter: list, clock: _Clock):
    def _gen(prompt, *, system="", max_tokens=2000, temperature=0.2):
        counter.append(prompt)
        clock.now += seconds
        return "def f():\n    return 1\n"
    return _gen


def test_deadline_reports_expiry_and_remaining(clock):
    d = Deadline(0.05, label="t")
    assert not d.expired()
    assert d.remaining() == pytest.approx(0.05)
    clock.now += 0.08
    assert d.expired()
    assert d.remaining() == 0.0
    assert d.check("stage") is True


def test_deadline_none_never_expires_on_time(clock):
    d = Deadline(None)
    clock.now += 10_000
    assert not d.expired()
    assert d.remaining() == float("inf")


def test_coerce_accepts_deadline_float_and_none():
    d = Deadline(5.0)
    assert Deadline.coerce(d) is d
    assert isinstance(Deadline.coerce(3.0), Deadline)
    assert Deadline.coerce(None).remaining() == float("inf")


def test_solve_stops_making_calls_once_deadline_passes(clock):
    """The bound must hold end to end: solve() returns shortly after the deadline,
    not after burning every beam root, DAG node and critique pass."""
    per_call, budget = 0.05, 0.12
    unbounded: list = []
    CodeAgent(generate=_slow_generate(per_call, unbounded, clock)).solve(
        "write a function that returns 1", use_tests=False, use_dag=False, deadline=Deadline(None))

    calls: list = []
    agent = CodeAgent(generate=_slow_generate(per_call, calls, clock))
    started = clock.now
    cr = agent.solve("write a function that returns 1", use_tests=False,
                     use_dag=False, deadline=Deadline(budget, label="test"))

    # No call starts once the deadline has passed: at most the calls that fit, the last of them
    # allowed to finish past it.
    assert len(calls) <= math.ceil(budget / per_call) < len(unbounded), (len(calls), len(unbounded))
    assert clock.now - started <= budget + per_call
    assert cr is not None


def test_already_expired_deadline_makes_solve_nearly_free(clock):
    calls: list = []
    agent = CodeAgent(generate=_slow_generate(0.05, calls, clock))
    expired = Deadline(0.001, label="test")
    clock.now += 0.01
    cr = agent.solve("write a function", use_tests=False, use_dag=False, deadline=expired)
    assert calls == [], "no model call may start on an expired deadline"
    assert cr.ok is False
    assert "halted" in (cr.message or "").lower()


def test_shared_deadline_is_not_rearmed_per_node(clock):
    """One Deadline instance passed to two solves keeps ONE clock between them —
    the defect was a per-invocation budget that N failures multiplied."""
    calls: list = []
    agent = CodeAgent(generate=_slow_generate(0.05, calls, clock))
    shared = Deadline(0.15, label="shared")
    agent.solve("task one", use_tests=False, use_dag=False, deadline=shared)
    first = len(calls)
    agent.solve("task two", use_tests=False, use_dag=False, deadline=shared)
    # The second solve inherits the already-spent clock and adds little or nothing.
    assert len(calls) - first <= 1, "second solve re-armed the budget"
