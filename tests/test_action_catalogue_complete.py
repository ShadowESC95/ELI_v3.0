"""Every working capability must appear in the catalogue the resolver can reach.

v2 shipped 12 actions with real handlers that were absent from SUPPORTED_ACTIONS —
DOC_GENERATE, PLUGIN_STATUS, LISTEN_FOR_COMMAND and friends. They dispatched fine if
something named them, but llm_intent._catalogue() is built from SUPPORTED_ACTIONS, so
the LLM fallback could never resolve to them. Grammar-constrained decoding turned that
latent gap into a hard one: what is not in the catalogue is not in the grammar, and
therefore unreachable.
"""
from __future__ import annotations


from eli.cognition.llm_intent import _catalogue, _EXPLICIT_ONLY_ACTIONS, _INTERNAL_ACTIONS
from eli.execution.executor_enhanced import SUPPORTED_ACTIONS

_PORTED = (
    "DOC_GENERATE", "SET_AI_MODE", "USER_INFO_REPORT", "SET_USER_NAME",
    "SET_COMMUNICATION_STYLE", "SKIP_YOUTUBE_AD", "ANALYZE_PDF_FOLDER",
    "IMAGE_STATUS", "LISTEN_FOR_COMMAND", "PLUGIN_STATUS", "STT_DIAGNOSTICS",
)


def test_the_ported_actions_are_listed():
    missing = [a for a in _PORTED if a not in set(SUPPORTED_ACTIONS)]
    assert not missing, f"regressed out of SUPPORTED_ACTIONS: {missing}"


def test_the_ported_actions_are_reachable_by_the_resolver():
    """The catalogue is what the grammar is built from — absence means unreachable.
    Explicit-only actions (renaming the user, changing ELI's own settings) are left out on
    purpose: the router reaches them from the user's exact words."""
    cat = set(_catalogue())
    missing = [a for a in _PORTED if a not in cat and a not in _EXPLICIT_ONLY_ACTIONS]
    assert not missing, f"not reachable by llm_intent: {missing}"


def test_a_model_guess_can_never_rename_the_user_or_change_elis_setup():
    """Live 2026-10-03: "Eli, can you not tell me who i am??" was guessed as
    PERSONA_LOCK_CLEAR and the agent bus ran it before the turn fell back to chat."""
    leaked = sorted(set(_catalogue()) & _EXPLICIT_ONLY_ACTIONS)
    assert not leaked, f"explicit-only actions offered to the resolver: {leaked}"
    assert {"PERSONA_LOCK_CLEAR", "SET_USER_NAME", "SHELL_EXEC", "PLUGIN_UNINSTALL"} <= _EXPLICIT_ONLY_ACTIONS
    assert not (_EXPLICIT_ONLY_ACTIONS - set(SUPPORTED_ACTIONS)), "names an action that doesn't exist"


def test_internal_actions_stay_out_of_the_user_catalogue():
    cat = set(_catalogue())
    leaked = sorted(cat & set(_INTERNAL_ACTIONS))
    assert not leaked, f"internal actions leaked into the catalogue: {leaked}"
