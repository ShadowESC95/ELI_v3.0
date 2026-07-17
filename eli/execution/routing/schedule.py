"""Router schedule intent — defer-to-background scheduling prepass (time markers, imperatives, question/dedicated-handler guards). Moved from router_enhanced.py."""
from __future__ import annotations

import re


_SCHEDULE_TIME_RX = re.compile(
    r"\b(overnight|tonight|later tonight|tomorrow"
    r"|this\s+(?:morning|afternoon|evening|noon)"
    r"|every\s+(?:morning|day|night|evening|week)|each\s+(?:morning|day|night)"
    r"|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
    r"|(?:for|by|at)\s+\d{1,2}[:\s]\d{2}\s*(?:am|pm)?"   # 'for 7 15', 'at 7:15'
    r"|(?:for|by|at)\s+\d{1,2}\s*(?:am|pm)"               # 'for 7am'
    r"|in\s+\d+\s*(?:hours?|hrs?|minutes?|mins?))\b", re.I)

# Heavy/agentic task verbs (NOT 'set'/'get' — those would hijack alarms, weather,
# forecasts). Only unambiguous scheduling verbs are added.
_SCHEDULE_VERB_RX = re.compile(
    r"\b(build|make|write|code|create|generate|implement|develop|design"
    r"|research|analyse|analyze|investigate|study|work on|put together"
    r"|run|upgrade|update yourself|reflect|schedule|remind|prepare|queue)\b", re.I)

# Known schedulable ACTIONS. When one of these is named WITH a future-time marker,
# the command means "schedule this action", even with a casual verb ('get a morning
# report ready for tomorrow'). The re-run request becomes just the action (no time),
# so the scheduled task fires once and never re-schedules itself.
_SCHEDULE_ACTION_RX = re.compile(
    r"\b(morning report|daily report|intelligence report|morning briefing"
    r"|daily briefing|daily summary|engine eval|test suite|test report"
    r"|self[\s_-]?test|generate tests?|test generation|lora|fine[\s_-]?tune"
    r"|self[\s_-]?upgrade|reflection)\b", re.I)

# Imperative command verbs — "open spotify at 8pm", "get the news at 7am", "play X
# at 9", "close steam in 2 hours". With a future-time marker these mean DEFER the
# command to the background workers.
_IMPERATIVE_RX = re.compile(
    r"\b(open|launch|start|play|pause|stop|close|quit|kill|get|fetch|show|check"
    r"|run|remind|turn|mute|unmute|screenshot|take|send|search|find|read|enable"
    r"|disable|update|download|email|message|post)\b", re.I)

# Don't treat genuine questions as scheduling ("what's on tonight?").
_QUESTION_RX = re.compile(r"^\s*(what|whats|what's|how|why|who|where|which|whose|tell me|show me what)\b", re.I)

# These have their own dedicated time-aware handlers — never hijack them.
_DEDICATED_TIME_RX = re.compile(r"\b(alarm|timer|stopwatch|pomodoro)\b", re.I)


def _strip_schedule_time(t: str) -> str:
    """Remove time/scheduling words so a re-run can't re-trigger the prepass."""
    s = _SCHEDULE_TIME_RX.sub(" ", t)
    s = re.sub(r"\b(for me|ready|please|tomorrow|morning|tonight)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,.")
    return s or t


def _eli_schedule_prepass(user_text: str):
    """Detect 'do <any command> at <future time>' → SCHEDULE_TASK (background workers).

    Fires when a future-time marker is present AND the utterance is an IMPERATIVE
    command — a heavy task verb, a known schedulable action, OR an app/media/system
    verb (open/play/get/close…). So 'get a morning report ready for tomorrow',
    'open spotify at 8pm', and 'get the news at 7am' all SCHEDULE, while questions
    ('what's on tonight?'), alarms/timers (own handlers), and bare 'morning report'
    are NOT captured. The re-run `request` is the clean command (time stripped) so the
    background worker (eng.process) executes the action once and never re-schedules."""
    t = (user_text or "").strip()
    if len(t.split()) < 3:
        return None
    if not _SCHEDULE_TIME_RX.search(t):
        return None
    if _DEDICATED_TIME_RX.search(t):
        return None
    if _QUESTION_RX.search(t):
        return None
    m_action = _SCHEDULE_ACTION_RX.search(t)
    if not (_SCHEDULE_VERB_RX.search(t) or m_action or _IMPERATIVE_RX.search(t)):
        return None
    inner = m_action.group(0) if m_action else _strip_schedule_time(t)
    return {
        "action": "SCHEDULE_TASK",
        "args": {"request": inner, "when": t},
        "confidence": 0.9,
        "meta": {"matched_by": "schedule.prepass"},
    }
