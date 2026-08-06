"""Ordered intent stages carved out of ``router_enhanced.route``.

Each stage takes (text, raw, low, text_l) and returns a route dict, or None to
let the next stage try. The two tuples are the legacy evaluation order and must
not be reordered — an earlier guard deliberately shadows later ones.

They are two tuples rather than one because the code-generation block between
them is still inline in route(): it computes context (_GENERATION_COMPLAINT and
friends) that later guards read. When that block moves out, these merge.
"""
from __future__ import annotations

from eli.execution.routing.intents._sentinel import ROUTE_NONE
from eli.execution.routing.intents.self_report import route_self_report
from eli.execution.routing.intents.runtime_voice import route_runtime_voice
from eli.execution.routing.intents.input_agents import route_input_agents
from eli.execution.routing.intents.documents import route_documents
from eli.execution.routing.intents.system_settings import route_system_settings
from eli.execution.routing.intents.app_launch import route_app_launch
from eli.execution.routing.intents.shell_ops import route_shell_ops
from eli.execution.routing.intents.capture_input import route_capture_input
from eli.execution.routing.intents.session_ops import route_session_ops

# Run before the inline code-generation block.
INTENT_STAGES_EARLY = (
    ("self_report", route_self_report),
    ("runtime_voice", route_runtime_voice),
    ("input_agents", route_input_agents),
)

# Run after it, immediately before route()'s CHAT fallback.
INTENT_STAGES_TAIL = (
    ("documents", route_documents),
    ("system_settings", route_system_settings),
    ("app_launch", route_app_launch),
    ("shell_ops", route_shell_ops),
    ("capture_input", route_capture_input),
    ("session_ops", route_session_ops),
)

INTENT_STAGES = INTENT_STAGES_EARLY + INTENT_STAGES_TAIL

__all__ = ["ROUTE_NONE", "INTENT_STAGES", "INTENT_STAGES_EARLY",
           "INTENT_STAGES_TAIL"] + [
    n for n, _ in INTENT_STAGES
]
