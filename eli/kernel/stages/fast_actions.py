"""Phase 45 direct-fast-path: the actions whose executor result IS the answer.

Deterministic OS / media / control / status actions must never wait for the
AgentBus, memory retrieval, persona synthesis, or GGUF — their executor payload is
authoritative and gets returned verbatim. This module owns the action sets and the
helpers that force a direct result. Extracted from engine.py (v3 modularisation);
imported back there so existing references are unchanged.
"""
from __future__ import annotations


_PHASE45_DIRECT_FAST_ACTIONS = {
    'AMBIENT_VISION',  # deterministic toggle — return the executor's confirmation directly
    'ANALYZE_IMAGE',  # executor OCR/metadata is authoritative — never let the model "describe" an image it can't see
    'ASK_CLARIFY',
    'BACKGROUND_JOBS',  # deterministic job-list read — verbatim, never paraphrase
    'CHECK_JOB',  # job status/result is authoritative; the model invents elapsed times and drops the actual summary if it re-narrates this
    'CLOSE_APP',
    'CREATE_FILE',  # fs mutation: executor writes + reads back, result is authoritative — never let the heuristic agent profile drop `system` and punt "run touch yourself"
    'DATE',
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
    'OPEN_APP',
    'OPEN_BROWSER',
    'OPEN_FILE_SYSTEM',
    'OPEN_URL',
    'PAUSE_MEDIA',
    'PLAY_MEDIA',
    'PREVIOUS_MEDIA',
    'SCREEN_LOCATE',
    'SCREEN_READ_ANALYZE',  # vision/OCR result is authoritative — never re-narrate the screen
    'SHELL_EXEC',  # executor result is authoritative — never let model contradict it
    'SHUFFLE_MEDIA',
    'SPEAK',
    'STOP_MEDIA',
    'TIME',
    'UNMUTE',
    'VOLUME'
}

# NOOP = fragment rejected or truly empty input; return silence, store nothing
_PHASE45_SILENT_FAST_ACTIONS = {'NOOP'}

# Actions where the executor payload IS the answer — the single source of truth for
# the direct-return block and for _is_soft_informational_action(). Fail-closed: if the
# authority gate throws or returns garbage, DENY these side-effecting actions rather
# than risk it (read-only ones degrade open, so a gate bug can never leave ELI mute).
_AUTHORITY_FAILCLOSED_ACTIONS = frozenset({
    "RUN_CMD", "SHELL_EXEC", "GENERATE_SCRIPT", "GENERATE_PROJECT",
    "FIX_FILE", "CODE_SOLVE", "CREATE_FOLDER", "DELETE_FILE",
    "OPEN_APP", "CLOSE_APP", "MINIMIZE_APP", "OPEN_URL", "OPEN_IDE", "OPEN_IN_IDE",
    "VOLUME", "PLAY_MEDIA", "PAUSE_MEDIA", "NEXT_MEDIA", "SELF_PATCH", "SELF_UPGRADE",
    "KEYBOARD", "MOUSE_CONTROL",
})

_DIRECT_FINAL_ACTIONS = frozenset({
    "OPEN_APP", "OPEN_URL", "OPEN_BROWSER", "OPEN_FILE_SYSTEM",
    "OPEN_IN_IDE", "OPEN_SYSTEM_SETTINGS", "OPEN_AUDIO_SETTINGS",
    "OPEN_POWER_SETTINGS", "OPEN_NETWORK_BROWSER",
    "MEDIA_CONTROL", "PLAY_MEDIA", "PAUSE_MEDIA", "STOP_MEDIA",
    "NEXT_MEDIA", "PREVIOUS_MEDIA", "VOLUME", "TILE_WINDOWS",
    "KEYBOARD", "MOUSE_CONTROL", "SCREENSHOT",
    "SPEAK", "DICTATE", "TRANSCRIBE",
    "NEWS_FETCH", "WEB_SEARCH", "GET_WEATHER",
    "GET_TIME", "GET_DATE", "TIME", "DATE",
    "CPU_USAGE", "RAM_USAGE", "SYSTEM_STATS",
    "RUNTIME_STATUS", "REASONING_MODE_STATUS", "MEMORY_STATUS", "COGNITION_STATUS",
    "GUI_RUNTIME_AUDIT", "RUNTIME_AUDIT", "IMPORT_AUDIT",
    "RESOLVE_RUNTIME_PATHS", "EXPLAIN_LAST_RESPONSE",
    "EXPLAIN_LAST_FAILURE", "EXPLAIN_MEMORY_RUNTIME", "EXPLAIN_COGNITION_RUNTIME",
    "SELF_REPORT", "USER_IDENTITY_SUMMARY", "PERSONAL_MEMORY_SUMMARY",
    "PERSONAL_MEMORY_DEEP_EXPLAIN", "ROUTING_FAULT_EXPLAIN", "NAME_SOURCE_AUDIT",
    "SELF_ANALYZE", "SELF_IMPROVE", "SELF_IMPROVEMENT_LOG", "SELF_UPDATE",
    "SELF_UPGRADE", "SELF_PATCH",
    "EXAMINE_CODE", "CONFIRM_CODE_FIX", "CANCEL_CODE_FIX",
    "CONFIRM_HABIT", "DECLINE_HABIT",
    "MORNING_REPORT", "PROACTIVE_STATUS", "HABIT_STATUS", "LIST_CAPABILITIES",
    "LIST_DIR", "READ_FILE", "SET_TIMER", "SET_ALARM", "WRITE_NOTE",
    "CREATE_FOLDER", "SET_CLIPBOARD", "GET_CLIPBOARD", "GPU_STATUS",
    "OPEN_IDE",
    "OPEN_COMMUNICATION_HUB",
    "OPEN_MEDIA_HUB",
    "SCREEN_LOCATE",
    "SCREEN_READ_ANALYZE",
})


# Actions whose answer is produced by READING LIVE STATE (a socket, a player, a
# device, a runtime singleton) rather than by synthesising prose. These inherently
# score grounding 0.00 on the agent bus — there is nothing in memory to ground a
# question about what is playing *right now* — so the low-grounding downgrade would
# fire on them every single time and hand the question to CHAT, which then invents an
# answer. That is exactly backwards: these are the actions that can answer truthfully,
# because executing them IS the fact-check.
#
# The naming convention carries the meaning, so match on it rather than trusting a
# hand-kept list to stay complete — that list already drifted once and let NOW_PLAYING
# through, which is how ELI came to confabulate a YouTube playback explanation instead
# of reporting "Nothing is playing right now."
_LIVE_STATE_ACTION_SUFFIXES = ("_STATUS", "_STATS", "_USAGE")
# Only names the suffix rule cannot catch belong here.
_LIVE_STATE_ACTIONS = frozenset({
    "NOW_PLAYING",
})


def _is_live_state_action(a: str) -> bool:
    """True when the action answers by reading real live state. Never downgradeable."""
    return a in _LIVE_STATE_ACTIONS or a.endswith(_LIVE_STATE_ACTION_SUFFIXES)


def _is_soft_informational_action(action) -> bool:
    """True only for synthesised informational actions — the sole kind safe to
    silently re-route to CHAT on low grounding. CHAT itself and every control / OS /
    media / status / report action carry their own grounded payload and must never
    be downgraded."""
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
        pass
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
