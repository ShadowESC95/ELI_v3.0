"""Ordered intent stages carved out of ``router_enhanced.route``.

Each stage takes (raw, low, text_l) and returns a route dict or None.
``INTENT_STAGES`` is the legacy evaluation order and must not be
reordered: earlier guards intentionally shadow later ones.
"""
from __future__ import annotations

from eli.execution.routing.intents.documents import route_documents
from eli.execution.routing.intents.system_settings import route_system_settings
from eli.execution.routing.intents.app_launch import route_app_launch
from eli.execution.routing.intents.shell_ops import route_shell_ops
from eli.execution.routing.intents.capture_input import route_capture_input
from eli.execution.routing.intents.session_ops import route_session_ops

INTENT_STAGES = (
    ("documents", route_documents),
    ("system_settings", route_system_settings),
    ("app_launch", route_app_launch),
    ("shell_ops", route_shell_ops),
    ("capture_input", route_capture_input),
    ("session_ops", route_session_ops),
)

__all__ = ["INTENT_STAGES"] + [n for n, _ in INTENT_STAGES]
