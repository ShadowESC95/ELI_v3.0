"""Phase 45 direct-fast-path: the actions whose executor result IS the answer.

Deterministic OS / media / control / status actions must never wait for the
AgentBus, memory retrieval, persona synthesis, or GGUF — their executor payload is
authoritative and gets returned verbatim. This module owns the action sets and the
helpers that force a direct result. Extracted from engine.py (v3 modularisation);
imported back there so existing references are unchanged.
"""
from __future__ import annotations
import logging


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


_PHASE45_DIRECT_FAST_ACTIONS |= _catalogue_controls()

# NOOP = fragment rejected or truly empty input; return silence, store nothing
_PHASE45_SILENT_FAST_ACTIONS = {'NOOP'}

# Actions where the executor payload is the answer (control/OS, status/self-report), returned
# straight out rather than back through the model. Single source of truth for the direct-return
# block and _is_soft_informational_action(). Fails closed for side-effecting actions if the
# authority gate misbehaves; read-only ones fail open so ELI is never mute.
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
    "NEXT_MEDIA", "PREVIOUS_MEDIA", "NOW_PLAYING", "VOLUME", "TILE_WINDOWS",
    "KEYBOARD", "MOUSE_CONTROL", "SCREENSHOT",
    "SPEAK", "DICTATE", "TRANSCRIBE",
    "NEWS_FETCH", "WEB_SEARCH", "WEB_FETCH", "GET_WEATHER",
    "GET_TIME", "GET_DATE", "TIME", "DATE",
    "CPU_USAGE", "RAM_USAGE", "SYSTEM_STATS",
    "RUNTIME_STATUS", "REASONING_MODE_STATUS", "MEMORY_STATUS", "COGNITION_STATUS",
    "GUI_RUNTIME_AUDIT", "RUNTIME_AUDIT", "IMPORT_AUDIT",
    "RESOLVE_RUNTIME_PATHS", "EXPLAIN_LAST_RESPONSE",
    "EXPLAIN_LAST_FAILURE", "EXPLAIN_MEMORY_RUNTIME", "EXPLAIN_COGNITION_RUNTIME",
    "EXPLAIN_GGUF_DIAGNOSTICS", "EXPLAIN_FAILURE_LOG",
    "CHECK_TARGET_STATUS", "PREPARE_REMEDIATION",
    "CONFIRM_PENDING_REMEDIATION", "CANCEL_PENDING_REMEDIATION",
    "SELF_REPORT", "USER_IDENTITY_SUMMARY", "PERSONAL_MEMORY_SUMMARY",
    "PERSONAL_MEMORY_DEEP_EXPLAIN", "ROUTING_FAULT_EXPLAIN", "NAME_SOURCE_AUDIT",
    "SELF_ANALYZE", "SELF_IMPROVE", "SELF_IMPROVEMENT_LOG", "SELF_UPDATE",
    "SELF_TEST",
    "SELF_UPGRADE", "SELF_PATCH", "SELF_REPAIR_PLAYBOOK",
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


# Actions whose handler already returns the finished answer. Their result is shown as it is, on
# every path that returns an action result. The path the agent bus takes kept a shorter list of
# its own, so a calendar or timer result was handed to the model to reword.
_DIRECT_RESULT_ACTIONS = frozenset({
    # News/report briefings are already a complete persona-voiced synthesis built in the executor
    # (50/50 stories + interest + follow-ups). Running them through GGUF again collapses them to a
    # 2-line summary and doubles latency. Return verbatim.
    "NEWS_FETCH",
    "MORNING_REPORT",
    "DAILY_REPORT",
    "RUNTIME_AUDIT",
    "IMPORT_AUDIT",
    "GUI_RUNTIME_AUDIT",
    "RESOLVE_RUNTIME_PATHS",
    "EXPLAIN_MEMORY_RUNTIME",
    "EXPLAIN_COGNITION_RUNTIME",
    "RUNTIME_STATUS",
    "REASONING_MODE_STATUS",
    "MEMORY_STATUS",
    "COGNITION_STATUS",
    # GET_PROPOSALS is a data action: its content (the live agenda or a
    # plain "no active proposals") must surface as-is. Synthesis made the
    # model invent suggestions on the empty state.
    "GET_PROPOSALS",
    "EXPLAIN_LAST_RESPONSE",
    "EXPLAIN_ALL_REASONING_MODES",
    "EXPLAIN_FAILURE_LOG",
    "EXPLAIN_GGUF_DIAGNOSTICS",
    "EXPLAIN_LAST_FAILURE",
    "SELF_UPDATE",
    # Self-maintenance actions (upgrade/improve/patch) produce a complete authoritative step
    # report in the executor ("Upgrade complete. 6/6 steps succeeded."). Return it verbatim.
    # Synthesis either invented progress ("running now, check back later" after it finished) or
    # degenerated to a lone "-Auto".
    "SELF_UPGRADE",
    "SELF_IMPROVE",
    "SELF_PATCH",
    "SELF_REPAIR_PLAYBOOK",
    "EXPLAIN_FAILURE_LOG",
    "EXPLAIN_GGUF_DIAGNOSTICS",
    "EXPLAIN_LAST_FAILURE",
    # Code examiner: tiered error report + per-step patch
    # outcomes are grounded fact — surface verbatim, never
    # re-narrated (a weak model would corrupt the findings).
    "EXAMINE_CODE",
    "CONFIRM_CODE_FIX",
    "CANCEL_CODE_FIX",
    "CONFIRM_HABIT",
    "DECLINE_HABIT",
    "DIAGNOSE_WRAPPERS",
    "SELF_REPORT",
    # Identity/profile actions: the executor evidence is the grounded
    # answer; compact synthesis avoids the 7K-token prompt overflow they hit
    # on the standard broker path.
    "USER_IDENTITY_SUMMARY",
    "PERSONAL_MEMORY_SUMMARY",
    "PERSONAL_MEMORY_DEEP_EXPLAIN",
    # Deterministic OS-command and system-read actions where the executor result is the whole answer
    # ("Volume set to 40%", "Tiled 5 windows"). Verbatim in quick, synthesised otherwise. Quick still
    # ran broker synthesis on them and corrupted results ("Wrote note" became "Bought note").
    # Web/weather/vision aren't here, their result is evidence the model should phrase.
    "OPEN_APP", "CLOSE_APP", "OPEN_URL", "OPEN_BROWSER",
    "OPEN_FILE_SYSTEM", "OPEN_IN_IDE", "OPEN_IDE",
    "OPEN_SYSTEM_SETTINGS", "OPEN_AUDIO_SETTINGS",
    "OPEN_POWER_SETTINGS", "OPEN_NETWORK_BROWSER",
    "OPEN_COMMUNICATION_HUB", "OPEN_MEDIA_HUB",
    "FOCUS_APP", "MINIMIZE_APP", "MINIMISE_APP",
    "MINIMISE_ALL", "MINIMIZE_WINDOW", "MINIMISE_WINDOW",
    "MAXIMISE_WINDOW", "NEXT_WINDOW", "PREVIOUS_WINDOW",
    "RESTORE_WINDOWS", "SWITCH_WORKSPACE", "TILE_WINDOWS",
    "MEDIA_CONTROL", "PLAY_MEDIA", "PAUSE_MEDIA",
    "STOP_MEDIA", "NEXT_MEDIA", "PREVIOUS_MEDIA",
    "NOW_PLAYING",
    "SHUFFLE_MEDIA", "REPEAT_MEDIA", "VOLUME",
    "KEYBOARD", "MOUSE_CONTROL", "SCREENSHOT",
    "SET_CLIPBOARD", "GET_CLIPBOARD",
    "TIME", "DATE", "GET_TIME", "GET_DATE",
    "CPU_USAGE", "RAM_USAGE", "SYSTEM_STATS", "GPU_STATUS",
    "CREATE_FILE", "CREATE_FOLDER", "WRITE_NOTE",
    "NEW_NOTE", "LIST_NOTES", "SET_TIMER", "SET_ALARM",
    "LIST_DIR", "SPEAK",
    # Actions whose handlers already return a complete answer string (checked by reading each),
    # same as GPU_STATUS/MEMORY_STATUS. Left out, a small model re-narrated them, and READ_FILE
    # had the file content rewritten instead of shown as it is on disk.
    "READ_FILE", "HARDWARE_PROFILE", "AWARENESS_STATUS",
    "FRONTIER_STATUS", "BACKGROUND_JOBS", "CHECK_JOB",
    "ORCHESTRATION_STATUS", "LORA_STATUS", "PROACTIVE_STATUS",
    "PERSONA_LOCK_STATUS", "POMODORO_STATUS", "HABIT_STATUS",
    "GAZE_STATUS", "TIMESTAMP_DIAG", "ELI_IDENTITY_AUDIT",
    "FILE_AUDIT", "CODEBASE_GRAPH", "AUTOPILOT_DEBUG",
    "LIST_EVENTS", "SEARCH_NOTES", "MCP_STATUS", "MCP_TOOLS",
    "MCP_LIST", "STT_DIAGNOSTICS", "NAME_SOURCE_AUDIT",
    "ROUTING_FAULT_EXPLAIN",
    # SHELL_EXEC delegates to RUN_CMD's handler, which returns the command's
    # raw stdout+stderr as content/response. Same danger as READ_FILE: an
    # LLM synthesis pass could misreport what a command printed.
    "SHELL_EXEC",
    # More confirmation/status/report actions whose handler already builds a complete
    # content/response string, checked by reading each. MCP_CALL is the same danger class as
    # READ_FILE/SHELL_EXEC: it returns a live tool's raw output, which must not be re-narrated.
    "ADD_EVENT", "PLUGIN_STATUS", "MEMORY_STORE", "MEMORY_FORGET", "CANCEL_TASK", "COMPUTER_USE", "LIST_TASKS", "RESCHEDULE_TASK",
    "GET_WEATHER", "PERSONA_LOCK_SET", "PERSONA_LOCK_CLEAR",
    "SET_TONE", "CLEAR_TONE", "SET_USER_NAME",
    "SET_COMMUNICATION_STYLE", "SET_VOICE",
    "POMODORO_START", "POMODORO_STOP",
    "WAKE_SET", "WAKE_ENROLL", "WAKE_TRAIN", "TRAIN_VOICE",
    "GAZE_CALIBRATE", "GAZE_CLICK", "GAZE_ENABLE", "GAZE_DISABLE",
    "MCP_ADD", "MCP_REMOVE", "MCP_DOCTOR", "MCP_CALL",
    "PLUGIN_LIST", "PLUGIN_SEARCH", "PLUGIN_INSTALL",
    "PLUGIN_ENABLE", "PLUGIN_DISABLE", "PLUGIN_UNINSTALL",
    "LIST_VOICES", "DOWNLOAD_VOICE",
    "SCHEDULE_TASK", "SKIP_YOUTUBE_AD", "SCREEN_LOCATE",
    "LORA_TRAIN", "LISTEN_FOR_COMMAND", "AMBIENT_VISION",
    "CANCEL_PENDING_REMEDIATION", "CONFIRM_PENDING_REMEDIATION",
    "CLEAR_CHAT_HISTORY", "REFRESH_USER_INFO",
    "MESSAGE_TIME_QUERY",
    # More confirmation/report/raw-content actions. TRANSCRIBE and OCR_IMAGE return raw recognised
    # text, same danger as READ_FILE/SHELL_EXEC/MCP_CALL. Deliberately left out: FIX_FILE (content is
    # a JSON blob) and RUN_TESTS (meant to be summarised).
    "CODE_CHANGES", "TRANSCRIBE", "DICTATE", "SMART_HOME",
    "LIST_SPEAKERS", "SPEAKER_ENROLL", "SPEAKER_STATUS", "VOICE_DIAGNOSTICS",
    "PERSONA_REFRESH", "PROACTIVE_START", "PROACTIVE_STOP",
    "HELP", "LIST_CAPABILITIES", "MEMORY_RECALL",
    "TEST_REVIEW", "OCR_IMAGE", "SUMMARIZE_FILE",
    "CONVERT_DOCUMENT", "ANALYZE_CSV", "GENERATE_TESTS",
    "CREATE_DOCUMENT", "GENERATE_DOCUMENT", "DOC_GENERATE",
    "DESIGN_VOICE", "CREATE_VOICE",
    # These run their own evidence-constrained model call (or none) and already return the final
    # answer, so a second synthesis is redundant and can drop a fact (a page count, an OCR'd line).
    # GENERATE_PROJECT embeds real code that mustn't be paraphrased. SEQUENCE and MULTI_COMMAND call
    # no model, they just join finished sub-steps.
    "ANALYZE_IMAGE", "ANALYZE_PDF", "ANALYZE_PDF_FOLDER",
    "SCREEN_READ_ANALYZE", "DATA_FABRICATOR", "GENERATE_PROJECT",
    "SEQUENCE", "MULTI_COMMAND",

    # calendar and reminder confirmations
    "REMOVE_EVENT",
    # RESUME_TASK builds its own message about how stale the task is
    "RESUME_TASK",
})


# Confirmations with nothing to add: what was put on the calendar, what was set. Shown as they
# are in every reasoning mode.
_ALWAYS_AS_IS = frozenset(("ADD_EVENT", "LIST_EVENTS", "REMOVE_EVENT", "SET_ALARM", "SET_TIMER", "MULTI_COMMAND", "SEQUENCE"))


def _shown_as_is(action, reasoning_mode=None) -> bool:
    """Whether an action's own result is the reply, with no model call to reword it. There
    were three places that decided this, each with its own list; two of them sent a calendar
    or timer result through a full generation."""
    act = str(action or "").upper()
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
