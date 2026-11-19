"""Approval engine — the emitter/action-class gate that decides what an autonomous
emitter may even propose. Pure policy logic; the guardrail behind ELI's autonomy.
"""
from __future__ import annotations

from eli.runtime import approval_engine as ae


def test_normalize_action_class():
    assert ae.normalize_action_class("shell-exec") == "shell-exec"
    assert ae.normalize_action_class("SHELL-EXEC") == "shell-exec"
    assert ae.normalize_action_class("nonsense") == "observe-only"   # safe default
    assert ae.normalize_action_class("") == "observe-only"


def test_normalize_emitter():
    assert ae.normalize_emitter("proactive") == "proactive"
    assert ae.normalize_emitter("who-dis") == "unknown"


def test_safe_class_allowed():
    assert ae.can_emitter_propose("autonomy_controller", "memory-write")[0] is True


def test_autonomy_cannot_run_shell():
    # The guardrail: the autonomy controller may only observe / write memory.
    ok, reason = ae.can_emitter_propose("autonomy_controller", "shell-exec")
    assert ok is False and "may not" in reason


def test_unknown_emitter_locked_to_observe():
    assert ae.can_emitter_propose("mystery", "shell-exec")[0] is False
    assert ae.can_emitter_propose("mystery", "observe-only")[0] is True


def test_remediation_may_run_shell():
    assert ae.can_emitter_propose("grounded_remediation", "shell-exec")[0] is True


def test_every_class_is_auto_or_needs_the_users_yes(monkeypatch):
    """evaluate_record has no third path: a class is auto-approved or waits for confirmation."""
    from eli.planning.proposal_models import ProposalRecord

    monkeypatch.setattr("eli.core.full_control.is_full_control", lambda: False)
    assert ae.AUTO_APPROVE | ae.MANUAL_APPROVE == ae.ACTION_CLASSES
    assert not (ae.AUTO_APPROVE & ae.MANUAL_APPROVE)
    for cls in sorted(ae.MANUAL_APPROVE):
        emitter = next(e for e, allowed in ae.EMITTER_POLICY.items() if cls in allowed)
        base = {"emitter": emitter, "action_class": cls}
        waiting = ae.evaluate_record(ProposalRecord.from_dict(dict(base)))
        assert (waiting.approval_state, waiting.requires_confirmation) == ("pending_confirmation", True)
        agreed = ae.evaluate_record(ProposalRecord.from_dict(dict(base)), user_confirmed=True)
        assert (agreed.approval_state, agreed.requires_confirmation) == ("approved", False)
