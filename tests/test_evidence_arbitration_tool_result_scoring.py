"""A tool-result record with no "ok" field is UNVERIFIED, not a success.

`_score_tool_result()` used to default a missing "ok" field to True, so a
record whose outcome was never recorded scored the same 0.86 confidence as a
genuinely confirmed success -- that number then flows straight into the
rendered evidence bundle text as if something had actually been verified.
For a product whose pitch is trustworthy, grounded local intelligence, "I
don't know if this worked" must not score the same as "this worked".
"""
from __future__ import annotations

import pytest

from eli.runtime import evidence_arbitration as arb
from eli.runtime.evidence_arbitration import _score_tool_result


@pytest.fixture(autouse=True)
def _no_history(monkeypatch):
    """The fixed figure stands only while the action has no measured reliability here."""
    from eli.runtime import evidence_ledger as led
    monkeypatch.setattr(led, "predict_success", lambda action, **k: None)


def test_confirmed_success_scores_high():
    item = _score_tool_result({"ok": True, "status": "ok", "action": "OPEN_APP"})
    assert item.score == 0.86


def test_a_success_is_trusted_by_the_actions_measured_reliability(monkeypatch):
    """Once the ledger has runs of the action, a result that reports success weighs what such runs
    have proved worth: the chance the next one works, not a fixed figure."""
    from eli.runtime import evidence_ledger as led
    monkeypatch.setattr(led, "predict_success", lambda action, **k: 0.42 if action == "OPEN_APP" else None)
    assert _score_tool_result({"ok": True, "status": "ok", "action": "OPEN_APP"}).score == pytest.approx(0.42)
    assert _score_tool_result({"ok": True, "status": "ok", "action": "DATE"}).score == 0.86
    assert _score_tool_result({"ok": False, "status": "ok", "action": "OPEN_APP"}).score == 0.30, "a failure is a failure"


def test_confirmed_failure_scores_low():
    item = _score_tool_result({"ok": False, "status": "ok", "action": "OPEN_APP"})
    assert item.score == 0.30


def test_missing_ok_field_is_not_scored_as_success():
    item = _score_tool_result({"status": "ok", "action": "OPEN_APP"})
    assert item.score == 0.30, (
        "a record with no recorded outcome must not score the same as a "
        "confirmed success"
    )


def test_missing_ok_field_scores_the_same_as_a_confirmed_failure():
    unverified = _score_tool_result({"status": "ok", "action": "OPEN_APP"})
    failed = _score_tool_result({"ok": False, "status": "ok", "action": "OPEN_APP"})
    assert unverified.score == failed.score
