"""Phase 45 direct-fast-path: the actions whose executor result IS the answer.

Deterministic OS / media / control / status actions must never wait for the
AgentBus, memory retrieval, persona synthesis, or GGUF — their executor payload is
authoritative and gets returned verbatim. This module owns the action sets and the
helpers that force a direct result. Extracted from engine.py (v3 modularisation);
imported back there so existing references are unchanged.
"""
from __future__ import annotations
import logging

from eli.execution.effectors.registry import Declared as _Declared


# Deterministic OS/media/control actions must never wait for AgentBus,
# memory retrieval, persona synthesis, or GGUF. Chat/questions still use
# the full cognitive path.
_PHASE45_DIRECT_FAST_ACTIONS = {
    'AMBIENT_VISION',  # deterministic toggle — return the executor's confirmation directly
    'ANALYZE_IMAGE',  # executor OCR/metadata is authoritative — never let the model "describe" an image it can't see
    'ASK_CLARIFY',
    'BACKGROUND_JOBS',  # deterministic job-list read — verbatim, never paraphrase
    'CHECK_JOB',  # job status/result is authoritative; the model invents elapsed times and drops the actual summary if it re-narrates this
    'CANCEL_PENDING_REMEDIATION',  # grounded cancel — never let GGUF invent "cancelled"
    'CHECK_TARGET_STATUS',
    'CLOSE_APP',
    'CONFIRM_PENDING_REMEDIATION',  # apt/mpv install confirm — executor must run, not narrate
    'CANCEL_TASK',  # deterministic executor result: the receipt or list is the answer
    'COMPUTER_USE',  # deterministic executor result: the receipt or list is the answer
    'LIST_TASKS',  # deterministic executor result: the receipt or list is the answer
    'RESCHEDULE_TASK',  # deterministic executor result: the receipt or list is the answer
    'MEMORY_FORGET',  # the list of what would be deleted, and the deletion receipt, are the executor's word
    'MEMORY_STORE',  # the executor's verified receipt (row, index) is the confirmation; a model paraphrase could deny it
    'CREATE_FILE',  # fs mutation: executor writes + reads back, result is authoritative — never let the heuristic agent profile drop `system` and punt "run touch yourself"
    'DATE',
    'EXPLAIN_LAST_FAILURE',
    'KEYBOARD',
    'LIST_EVENTS',
    'MEDIA_CONTROL',
    'MINIMISE_APP',  # window control: deterministic executor result is authoritative — never let GGUF fabricate "Done."
    'MINIMISE_WINDOW',
    'MINIMIZE_APP',
    'MINIMIZE_WINDOW',
    'HIDE_APP',
    'MOUSE_CONTROL',
    'MUTE',
    'NEXT_MEDIA',
    'NOOP',
    'NOW_PLAYING',  # live playerctl read — must never be GGUF-synthesised
    'OPEN_APP',
    'OPEN_BROWSER',
    'OPEN_FILE_SYSTEM',
    'OPEN_URL',
    'PAUSE_MEDIA',
    'PLAY_MEDIA',
    'PREPARE_REMEDIATION',
    'PREVIOUS_MEDIA',
    'REPEAT_MEDIA',
    'SCREEN_LOCATE',
    'SCREEN_READ_ANALYZE',  # vision/OCR result is authoritative — never re-narrate the screen
    'SHELL_EXEC',  # executor result is authoritative — never let model contradict it
    'SHUFFLE_MEDIA',
    'SPEAK',
    'STOP_MEDIA',
    'TIME',
    'TIMESTAMP_DIAG',  # wall clock + stored conversation turn timestamps
    'UNMUTE',
    'VOLUME'
}

# Every control of the desktop, an input device or a media player is one of these, whether or
# not it was written into the list above: the catalogue's own grouping says which they are. The
# list had MINIMISE_APP and not MAXIMISE_WINDOW, FOCUS_APP or SWITCH_WORKSPACE, so those went
# through the agent bus and a model pass, and a "maximise browser" that had already run was then
# reclassed as chat for low grounding and answered with nothing.
_CONTROL_CATEGORIES = ("App & window control", "Input control", "Media")


def _catalogue_controls() -> set:
    try:
        from eli.tools.registry.capabilities_doc import _C
        return {str(name).upper() for name, entry in _C.items() if entry[0] in _CONTROL_CATEGORIES}
    except Exception:
        logging.getLogger(__name__).debug("catalogue controls not read", exc_info=True)
        return set()


_PHASE45_DIRECT_FAST_ACTIONS = frozenset(_PHASE45_DIRECT_FAST_ACTIONS | _catalogue_controls())

# NOOP = fragment rejected or truly empty input; return silence, store nothing
_PHASE45_SILENT_FAST_ACTIONS = frozenset({'NOOP'})


# Declared by each action (direct=True: eli/tools/registry/capabilities_doc.py, or where its effector
# registers it): read from the action registry, not a list kept here.
_DIRECT_FINAL_ACTIONS = _Declared("direct")


# Actions whose handler already returns the finished answer: their result is shown as it is, in
# Quick (shown="quick") or in every reasoning mode (shown="always"), on every path that returns an
# action result. Declared by each action; the path the agent bus takes kept a shorter list of its own,
# so a calendar or timer result was handed to the model to reword, and the engine kept a third.
_DIRECT_RESULT_ACTIONS = _Declared("shown", "quick", "always")


# Shown as they are in every reasoning mode: confirmations with nothing to add (what was put on the
# calendar, what was set), the grounded reports a model re-narrated wrongly (self-maintenance steps,
# code-examiner findings, scheduler state), raw content.
_ALWAYS_AS_IS = _Declared("shown", "always")


def _shown_as_is(action, reasoning_mode=None, result=None) -> bool:
    """Whether an action's own result is the reply, with no model call to reword it. There
    were three places that decided this, each with its own list; two of them sent a calendar
    or timer result through a full generation. A result that says it is evidence for a question
    asked about it (`explain_in_words`: "why is the GPU at 1%?" after GPU_STATUS) is worded by the
    model in every mode; shown as it is, the question went unanswered."""
    act = str(action or "").upper()
    if isinstance(result, dict) and result.get("explain_in_words"):
        return False
    if act in _ALWAYS_AS_IS:
        return True
    if act not in _DIRECT_RESULT_ACTIONS:
        return False
    try:
        from eli.cognition.reasoning_modes import canonical_mode
        return canonical_mode(reasoning_mode) == "quick"
    except Exception:
        return not reasoning_mode or str(reasoning_mode).lower() == "quick"


# Actions that read live state (socket, player, device). They score 0.00 grounding because
# memory can't ground "what is playing right now", so the low-grounding downgrade would hand
# them to chat, which invents an answer. Executing them is the fact-check. Matched by naming
# convention, not a hand-kept list, since that list drifted once.
_LIVE_STATE_ACTION_SUFFIXES = ("_STATUS", "_STATS", "_USAGE")
# Only names the suffix rule cannot catch belong here.
_LIVE_STATE_ACTIONS = frozenset({
    "NOW_PLAYING",
})


def _is_live_state_action(a: str) -> bool:
    """True when the action answers by reading real live state. Never downgradeable."""
    return a in _LIVE_STATE_ACTIONS or a.endswith(_LIVE_STATE_ACTION_SUFFIXES)


def _held_to_requested_format(user_input: str, reply: str, trace: object) -> str:
    """"YES or NO only", "YYYY-MM-DD only" and the like, when the reply already holds that answer."""
    from eli.cognition.explicit_output_contract import enforce_explicit_output_contract
    held, kind = enforce_explicit_output_contract(user_input, reply)
    if kind and held != reply and isinstance(trace, dict):
        trace["explicit_output_contract"] = {"kind": kind, "changed": True}
    return held if kind else reply


def _told_to_do_it(text) -> bool:
    """Grounding says how well memory backs an answer. An instruction has nothing to be backed by
    until it has run: "do the pdfs!!!" was read rightly as the folder analysis and then turned into
    chat for want of evidence, and the model wrote "Processing the remaining 29 PDFs now"."""
    try:
        from eli.runtime.command_splitter import tells_eli_to_do
        return bool(tells_eli_to_do(text))
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)
        return False


def _is_soft_informational_action(action) -> bool:
    """True when `action` is a synthesised informational action (the only kind safe to silently
    re-route to CHAT on low grounding). False for CHAT itself and for every control / OS / media /
    status / report / deterministic action — those carry their own grounded payload and must never
    be downgraded. Reuses the module-level direct-final + phase45 + control-contract sets, plus the
    live-state naming convention above."""
    a = str(action or "").upper().strip()
    if not a or a == "CHAT":
        return False
    if a in _DIRECT_FINAL_ACTIONS or a in _PHASE45_DIRECT_FAST_ACTIONS:
        return False
    if _is_live_state_action(a):
        return False
    try:
        from eli.runtime.control_contracts import CONTROL_ACTIONS as _CC
        if a in {str(x).upper() for x in (_CC or set())}:
            return False
    except Exception:
        logging.getLogger(__name__).debug("suppressed exception", exc_info=True)
    return True


def _phase45_action_name(action) -> str:
    return str(action or "").strip().upper()


def _phase45_result_text(result, action=None) -> str:
    if not isinstance(result, dict):
        return str(result or "").strip()
    return str(
        result.get("response")
        or result.get("content")
        or result.get("message")
        or result.get("error")
        or ""
    ).strip()


def _phase45_force_direct_result(action, result):
    a = _phase45_action_name(action)
    if not isinstance(result, dict):
        result = {"ok": True, "action": a, "content": str(result or ""), "response": str(result or "")}
    else:
        result = dict(result)

    ok = bool(result.get("ok", True))
    text = _phase45_result_text(result, a)

    if a in _PHASE45_SILENT_FAST_ACTIONS and ok:
        text = ""

    result["action"] = result.get("action") or a
    result["content"] = text
    result["response"] = text
    result.setdefault("meta", {})
    if isinstance(result["meta"], dict):
        result["meta"].update({
            "response_mode": "direct_tool_result",
            "bypassed_agent_bus": True,
            "bypassed_memory": True,
            "bypassed_gguf": True,
            "phase45_fastpath": True,
        })
    return result
