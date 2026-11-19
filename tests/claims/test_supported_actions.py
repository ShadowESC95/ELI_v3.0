"""CLAIM: every executor SUPPORTED_ACTION has a dispatch branch: nothing is advertised that routes
but cannot run."""
from __future__ import annotations

from . import _helpers as H


def test_every_supported_action_is_handled():
    unhandled = set(H.supported_actions()) - H.executor_action_branches()
    assert not unhandled, f"SUPPORTED_ACTIONS with no executor dispatch path: {sorted(unhandled)}"


def test_user_facing_routable_admin_actions_are_supported():
    expected = {"EXPLAIN_LAST_RESPONSE", "REFRESH_USER_INFO", "SELF_REPORT", "SELF_UPDATE",
                "USER_IDENTITY_SUMMARY", "OPEN_URL"}
    missing = expected - set(H.supported_actions())
    assert not missing, f"Missing from SUPPORTED_ACTIONS: {sorted(missing)}"
    assert "OPEN_URL" in H.executor_action_branches()
