"""Live introspection — the action→agents map and the snapshot/trace readers.

agents_for_action declares which agents a grounded-status action draws on;
runtime_snapshot / last_trace / stored_user_name read live state safely (structured
result, never a crash). Runs in the normal suite.
"""
from __future__ import annotations

import pytest

from eli.runtime import live_introspection as li


# --------------------------------------------------------------------------- #
# agents_for_action (pure map)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("action,expected", [
    ("RUNTIME_STATUS", ["introspection", "file_code"]),
    ("self_report", ["introspection", "file_code"]),          # normalised (case)
    ("USER_IDENTITY_SUMMARY", ["introspection", "memory", "file_code"]),
    ("EXPLAIN_COGNITION_RUNTIME", ["introspection", "file_code", "reflection"]),
    ("LAST_TRACE_REPORT", ["introspection"]),
])
def test_agents_for_action(action, expected):
    assert li.agents_for_action(action) == expected


def test_agents_for_action_default():
    assert li.agents_for_action("SOMETHING_ELSE") == ["introspection"]
    assert li.agents_for_action("") == ["introspection"]
    assert li.agents_for_action(None) == ["introspection"]
