"""Tests for eli.runtime sub-modules — ~80 tests."""
from __future__ import annotations


# ── Security ──────────────────────────────────────────────────────────────


# ── Response Contracts ────────────────────────────────────────────────────


def test_status_reports_answer_from_the_fast_path_and_a_chat_turn_does_not():
    """It asserted only that a contract came back, which contract_for_action always does."""
    from eli.runtime.response_contracts import contract_for_action
    for action in ("MEMORY_STATUS", "COGNITION_STATUS"):
        contract = contract_for_action(action)
        assert contract.quick and contract.provider == "fastpath" and contract.grounded, action
    chat = contract_for_action("CHAT")
    assert not chat.quick and chat.provider != "fastpath"
    assert contract_for_action(None).action == "CHAT"


# ── Reflection ────────────────────────────────────────────────────────────


# ── Self Improvement ──────────────────────────────────────────────────────


# ── Live Introspection ────────────────────────────────────────────────────
