"""The coding agent must honour a shared absolute wall-clock deadline.

Regression cover for the 2-hour GUI freeze: one self-repair request fanned out to
tens of sequential model calls because every loop carried its own budget, re-armed
per invocation and checked only AFTER a generation returned. On a local GGUF a
single call runs 30-500s, so an after-the-fact check can never enforce a budget.

No GGUF here — `generate` is a stub that burns wall-clock time.
"""
from __future__ import annotations

import time

from eli.coding.agent import CodeAgent
from eli.coding.budget import Deadline


def _slow_generate(seconds: float, counter: list):
    def _gen(prompt, *, system="", max_tokens=2000, temperature=0.2):
        counter.append(prompt)
        time.sleep(seconds)
        return "def f():\n    return 1\n"
    return _gen


def test_deadline_reports_expiry_and_remaining():
    d = Deadline(0.05, label="t")
    assert not d.expired()
    assert d.remaining() > 0
    time.sleep(0.08)
    assert d.expired()
    assert d.remaining() == 0.0
    assert d.check("stage") is True


def test_deadline_none_never_expires_on_time():
    d = Deadline(None)
    assert not d.expired()
    assert d.remaining() == float("inf")


def test_coerce_accepts_deadline_float_and_none():
    d = Deadline(5.0)
    assert Deadline.coerce(d) is d
    assert isinstance(Deadline.coerce(3.0), Deadline)
    assert Deadline.coerce(None).remaining() == float("inf")


def test_solve_stops_making_calls_once_deadline_passes():
    """The bound must hold end to end: solve() returns shortly after the deadline,
    not after burning every beam root, DAG node and critique pass."""
    calls: list = []
    agent = CodeAgent(generate=_slow_generate(0.05, calls))
    started = time.monotonic()
    cr = agent.solve("write a function that returns 1", use_tests=False,
                     use_dag=False, deadline=Deadline(0.12, label="test"))
    elapsed = time.monotonic() - started

    # Unbounded, beam=3 + 6 refinements + critique + refine would be ~11 calls.
    assert len(calls) < 8, f"deadline did not bound the call count: {len(calls)}"
    # Allow one in-flight call to finish past the deadline, plus verification.
    assert elapsed < 2.0, f"solve overran its deadline badly: {elapsed:.2f}s"
    assert cr is not None


def test_already_expired_deadline_makes_solve_nearly_free():
    calls: list = []
    agent = CodeAgent(generate=_slow_generate(0.05, calls))
    expired = Deadline(0.001, label="test")
    time.sleep(0.01)
    cr = agent.solve("write a function", use_tests=False, use_dag=False, deadline=expired)
    assert calls == [], "no model call may start on an expired deadline"
    assert cr.ok is False
    assert "halted" in (cr.message or "").lower()


def test_shared_deadline_is_not_rearmed_per_node():
    """One Deadline instance passed to two solves keeps ONE clock between them —
    the defect was a per-invocation budget that N failures multiplied."""
    calls: list = []
    agent = CodeAgent(generate=_slow_generate(0.05, calls))
    shared = Deadline(0.15, label="shared")
    agent.solve("task one", use_tests=False, use_dag=False, deadline=shared)
    first = len(calls)
    agent.solve("task two", use_tests=False, use_dag=False, deadline=shared)
    # The second solve inherits the already-spent clock and adds little or nothing.
    assert len(calls) - first <= 1, "second solve re-armed the budget"
