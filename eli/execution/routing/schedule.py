"""Router schedule intent — defer-to-background scheduling prepass (time markers, imperatives, question/dedicated-handler guards). Moved from router_enhanced.py."""
from __future__ import annotations

import re


_SCHEDULE_TIME_RX = re.compile(
    r"\b(overnight|tonight|later tonight|tomorrow|today"
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

# Imperative command verbs — "open spotify at 8pm", "play X at 9", "close steam in
# 2 hours". With a future-time marker these mean DEFER the command to the workers.
#
# Deliberately NARROW. This previously included stop|check|run|go|take|find|read|
# show|get|turn|send — everyday conversational verbs that made almost any sentence
# containing a clock time look like a command. "Stop being so grumpy … at 8:30 on a
# saturday? … go back to sleep haha" matched on "Stop" and was scheduled as a code
# job. Only unambiguous app/media/system verbs remain; a genuine heavy task is
# still caught by _SCHEDULE_VERB_RX or _SCHEDULE_ACTION_RX.
_IMPERATIVE_RX = re.compile(
    r"\b(open|launch|play|pause|close|quit|kill|fetch|mute|unmute"
    r"|screenshot|search|enable|disable|download|email|post)\b", re.I)

# Don't treat genuine questions as scheduling ("what's on tonight?").
_QUESTION_RX = re.compile(r"^\s*(what|whats|what's|how|why|who|where|which|whose|tell me|show me what)\b", re.I)

# Conversational address — banter, opinions and second-person commentary are never
# task requests, however many clock words they happen to contain.
_CHATTER_RX = re.compile(
    r"\b(you think|you're|you are|being so|stop being|i want to|i think|i reckon"
    r"|do you want|don't want|dont want|go back to sleep|haha|lol|jesus christ"
    r"|for fuck'?s sake|shut up|calm down|wake up)\b", re.I)

# These have their own dedicated time-aware handlers — never hijack them.
_DEDICATED_TIME_RX = re.compile(r"\b(alarm|timer|stopwatch|pomodoro)\b", re.I)

# A time marker and a command verb must belong to the SAME clause. Without this
# they could sit 200 chars apart in unrelated sentences and still schedule.
_PROXIMITY_CHARS = 45


def _near(a: tuple[int, int], b: tuple[int, int], window: int = _PROXIMITY_CHARS) -> bool:
    """True when two regex spans are close enough to be one clause."""
    if a[0] <= b[1] and b[0] <= a[1]:
        return True                       # overlapping
    gap = b[0] - a[1] if b[0] > a[1] else a[0] - b[1]
    return gap <= window


_DAY_QUALIFIER_RX = re.compile(r"^(today|tonight|tomorrow|overnight)$", re.I)


def _extract_when(t: str) -> str:
    """Collect the time markers that form the `when` spec.

    A clock time and its day qualifier are separate matches and the parser needs
    both ("…for 8:45am today"). But an explicit clock time beats a vague day-part
    from another clause: in "…woke up on the wrong side of the bed this morning.
    Schedule … for 8:45am today", "this morning" is narration, not the request.
    """
    parts: list[str] = []
    for m in _SCHEDULE_TIME_RX.finditer(t):
        p = m.group(0).strip()
        if p.lower() not in {s.lower() for s in parts}:
            parts.append(p)
    if not parts:
        return ""
    has_clock = any(any(ch.isdigit() for ch in p) for p in parts)
    if has_clock:
        parts = [p for p in parts
                 if any(ch.isdigit() for ch in p) or _DAY_QUALIFIER_RX.match(p)]
    return " ".join(parts).strip()


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
    m_time = _SCHEDULE_TIME_RX.search(t)
    if not m_time:
        return None
    if _DEDICATED_TIME_RX.search(t):
        return None
    if _QUESTION_RX.search(t):
        return None
    # Any question mark, anywhere — mirrors the command splitter's guard. The
    # ^-anchored rule above misses mid-sentence questions, which is how a rhetorical
    # "you think i want to debug and test you at 8:30 on a saturday?" got scheduled.
    if "?" in t:
        return None
    if _CHATTER_RX.search(t):
        return None

    m_action = _SCHEDULE_ACTION_RX.search(t)
    m_verb = _SCHEDULE_VERB_RX.search(t) or m_action or _IMPERATIVE_RX.search(t)
    if not m_verb:
        return None
    # The verb and the time must be in the same clause (see _PROXIMITY_CHARS).
    if not _near(m_verb.span(), m_time.span()):
        # A named schedulable action anywhere in the utterance is explicit enough
        # ("get a morning report ready for me tomorrow morning").
        if not m_action:
            return None

    inner = m_action.group(0) if m_action else _strip_schedule_time(t)
    return {
        "action": "SCHEDULE_TASK",
        # `when` is the extracted time PHRASE, never the whole utterance. Passing the
        # raw sentence meant the parser saw a paragraph, found nothing it understood,
        # and silently fell back to 02:00 tomorrow — so "8:45am today" was scheduled
        # for the small hours of the next day.
        "args": {"request": inner, "when": _extract_when(t)},
        "confidence": 0.9,
        "meta": {"matched_by": "schedule.prepass"},
    }
