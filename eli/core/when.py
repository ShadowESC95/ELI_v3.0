"""Natural-language "when" parsing — the single owner for scheduling times.

Replaces a parser that understood only `in N hours` and `at H[:MM]`, and silently
fell back to "next 02:00" for everything else. That fallback is how "reschedule it
for 8:45am today", typed at 08:44, was scheduled for 02:00 the following morning:
the detector regex accepted `for|by|at`, the parser only handled `at`, and the
mismatch was invisible because the failure path produced a plausible timestamp
instead of an error.

Two rules make this parser safe where the old one was not:

  * `now` is injectable, so "today at 08:45 when it is 08:44" is a table test
    rather than a thing you can only observe in production;
  * unparseable input returns None. The caller asks the user rather than
    inventing a time. Only an explicit "overnight"/"tonight" means 02:00.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

OVERNIGHT_HOUR = 2
_TOMORROW_DEFAULT_HOUR = 9

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2,
    "wed": 2, "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

# "in 20 minutes", "in 2 hrs", "in 3 days"
_REL_RX = re.compile(r"\bin\s+(\d+)\s*(hours?|hrs?|minutes?|mins?|days?)\b", re.I)
# "at 7", "for 8:45am", "by 19:30", "@ 9pm", and a bare "8:45am"
_CLOCK_RX = re.compile(
    r"(?:\b(?:at|for|by|around|@)\s*)?\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b"
    r"|(?:\b(?:at|for|by|around|@)\s*)\b(\d{1,2})(?::(\d{2}))\b"
    r"|(?:\b(?:at|for|by|around|@)\s*)\b(\d{1,2})\b"
    r"|\b(\d{1,2}):(\d{2})\b",
    re.I,
)
_NOON_RX = re.compile(r"\b(noon|midday|midnight)\b", re.I)
_WEEKDAY_RX = re.compile(r"\b(?:on\s+)?(" + "|".join(_WEEKDAYS) + r")\b", re.I)
_MONTHDAY_RX = re.compile(
    r"\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(" + "|".join(_MONTHS) + r")\b"
    r"|\b(?:on\s+)?(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.I,
)
_ISO_RX = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _clock_from(text: str) -> Optional[Tuple[int, int]]:
    """Extract (hour, minute) in 24h form, or None."""
    m = _NOON_RX.search(text)
    if m:
        word = m.group(1).lower()
        return (0, 0) if word == "midnight" else (12, 0)

    m = _CLOCK_RX.search(text)
    if not m:
        return None
    g = m.groups()
    if g[0] is not None:            # H[:MM] am/pm
        hh, mm, ap = int(g[0]), int(g[1] or 0), (g[2] or "").lower()
    elif g[3] is not None:          # prefixed H:MM
        hh, mm, ap = int(g[3]), int(g[4] or 0), ""
    elif g[5] is not None:          # prefixed bare H
        hh, mm, ap = int(g[5]), 0, ""
    elif g[6] is not None:          # bare H:MM
        hh, mm, ap = int(g[6]), int(g[7] or 0), ""
    else:
        return None

    if ap == "pm" and hh < 12:
        hh += 12
    elif ap == "am" and hh == 12:
        hh = 0
    # Reject nonsense rather than silently wrapping: "at 25" used to become 01:00.
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm


def parse_when(text: str, *, now: Optional[datetime] = None,
               default: Optional[str] = None) -> Optional[float]:
    """Parse `text` into a unix timestamp.

    Returns None when no time can be determined — callers must then ask the user
    instead of inventing one. Pass default="overnight" to keep the legacy
    next-02:00 behaviour for callers that genuinely want it.
    """
    t = " ".join(str(text or "").lower().split())
    now = now or datetime.now()
    if not t:
        return _default_ts(now, default)

    # 1. Relative offsets win — they are unambiguous.
    m = _REL_RX.search(t)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit.startswith("day"):
            delta = timedelta(days=n)
        elif unit.startswith(("hour", "hr")):
            delta = timedelta(hours=n)
        else:
            delta = timedelta(minutes=n)
        return (now + delta).timestamp()

    clock = _clock_from(t)
    has_today = bool(re.search(r"\btoday\b", t))
    has_tonight = bool(re.search(r"\btonight\b", t))
    has_tomorrow = bool(re.search(r"\btomorrow\b", t))
    has_overnight = bool(re.search(r"\bovernight\b", t))

    # 2. Explicit calendar dates.
    base: Optional[datetime] = None
    m = _ISO_RX.search(t)
    if m:
        base = now.replace(year=int(m.group(1)), month=int(m.group(2)),
                           day=int(m.group(3)))
    if base is None:
        m = _MONTHDAY_RX.search(t)
        if m:
            if m.group(1):
                day, month = int(m.group(1)), _MONTHS[m.group(2).lower()]
            else:
                month, day = _MONTHS[m.group(3).lower()], int(m.group(4))
            try:
                base = now.replace(month=month, day=day)
                if base.date() < now.date():
                    base = base.replace(year=now.year + 1)
            except ValueError:
                base = None

    # 3. Weekday names ("on sunday").
    if base is None:
        m = _WEEKDAY_RX.search(t)
        if m:
            target_dow = _WEEKDAYS[m.group(1).lower()]
            ahead = (target_dow - now.weekday()) % 7
            if ahead == 0 and clock is None:
                ahead = 7
            base = now + timedelta(days=ahead)

    # 4. Day words.
    if base is None:
        if has_tomorrow:
            base = now + timedelta(days=1)
        elif has_today or has_tonight:
            base = now

    if base is None and clock is None:
        if has_overnight:
            return _overnight_ts(now)
        return _default_ts(now, default)

    if clock is None:
        # A day with no clock: overnight/tonight → 02:00, otherwise a sane morning.
        hh, mm = ((OVERNIGHT_HOUR, 0) if (has_overnight or has_tonight)
                  else (_TOMORROW_DEFAULT_HOUR, 0))
    else:
        hh, mm = clock

    base = base or now
    target = base.replace(hour=hh, minute=mm, second=0, microsecond=0)

    if target > now:
        return target.timestamp()

    # The resolved instant is in the past. What that means depends on the phrasing:
    #   "tonight"/"overnight" mean the COMING night — roll forward.
    #   a bare "at 7" after 07:00 plainly means tomorrow — roll forward.
    #   but "8:45am today" typed at 08:46 is impossible. Silently booking it for
    #   tomorrow is how "8:45am today" became 02:00 the next morning; return None
    #   so the caller can say so instead of inventing a time.
    if has_tonight or has_overnight:
        return (target + timedelta(days=1)).timestamp()
    names_a_calendar_day = bool(has_today or has_tomorrow
                                or _WEEKDAY_RX.search(t) or _ISO_RX.search(t)
                                or _MONTHDAY_RX.search(t))
    if names_a_calendar_day:
        return None
    return (target + timedelta(days=1)).timestamp()


def _overnight_ts(now: datetime) -> float:
    target = now.replace(hour=OVERNIGHT_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.timestamp()


def _default_ts(now: datetime, default: Optional[str]) -> Optional[float]:
    if default == "overnight":
        return _overnight_ts(now)
    return None


def describe(ts: float) -> str:
    """Human-readable form for confirmations and task listings."""
    return datetime.fromtimestamp(float(ts)).strftime("%H:%M on %a %d %b")
