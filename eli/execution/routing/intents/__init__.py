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
from eli.execution.routing.intents.world_models import route_world_models
from eli.execution.routing.intents.world_games import route_world_games
from eli.execution.routing.intents.world_held import route_world_held
from eli.execution.routing.command_parser import route_command_strict, route_command_tail

# Run before the inline code-generation block.
INTENT_STAGES_EARLY = (
    # The command language (routing/command_parser): a sentence read by its form. Its strict pass
    # takes only commands whose object is an atom (an address, a path, a duration, a time of day,
    # what the last command was about); everything else waits for the rules.
    ("command_parser", route_command_strict),
    ("world_held", route_world_held),       # a question about the sheet in the user's hand: conversation, from its record
    ("self_report", route_self_report),
    ("runtime_voice", route_runtime_voice),
    ("input_agents", route_input_agents),
    ("world_games", route_world_games),     # a game on, or asked for: before the chat guards take "I give up"
)

# Run after it, immediately before route()'s CHAT fallback.
INTENT_STAGES_TAIL = (
    ("documents", route_documents),
    ("system_settings", route_system_settings),
    ("app_launch", route_app_launch),
    ("shell_ops", route_shell_ops),
    ("world_models", route_world_models),
    ("capture_input", route_capture_input),
    ("session_ops", route_session_ops),
    # The command language again, for what no rule took: a verb said another way ("fire up",
    # "jot down", "skip this track"), a duration in words, a literal of the catalogue.
    ("command_parser_tail", route_command_tail),
)

INTENT_STAGES = INTENT_STAGES_EARLY + INTENT_STAGES_TAIL

__all__ = ["ROUTE_NONE", "INTENT_STAGES", "INTENT_STAGES_EARLY",
           "INTENT_STAGES_TAIL"] + [
    n for n, _ in INTENT_STAGES
]
