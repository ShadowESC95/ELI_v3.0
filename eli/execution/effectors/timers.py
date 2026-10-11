"""Timers effector — SET_ALARM, SET_TIMER (background thread + notify/sound)."""
from __future__ import annotations

import threading
import time

from eli.utils.log import get_logger

log = get_logger(__name__)

def notify(*a, **k):
    from eli.execution.executor_enhanced import notify as _f
    return _f(*a, **k)

def play_sound(*a, **k):
    from eli.utils.platform_compat import play_alarm_sound as _f
    return _f(*a, **k)


def _h_set_alarm(a, args):
    if a == "SET_ALARM" and str(args.get("text") or "").strip():
        # "remind me at 4pm to prepare for the presentation": a labelled reminder that survives a restart
        try:
            from eli.execution.effectors.datetime_calendar import agenda_turn_context
            from eli.runtime import agenda as _agenda
            _who, _earlier = agenda_turn_context(args)
            return _agenda.do_remind(args, user_id=_who, earlier=_earlier)
        except Exception as e:
            log.debug("reminder failed", exc_info=True)
            msg = f"The reminder could not be saved: {e}"
            return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}
    try:
        duration = args.get("duration") or args.get("seconds")
        alarm_time = args.get("time") or args.get("alarm_time")
        label = args.get("label") or "ELI Timer"

        if duration:
            secs = int(duration)
            # Background thread timer with notify-send
            def _timer_fire():
                time.sleep(secs)
                try:
                    notify("ELI Timer", f"Timer complete! ({secs}s) - {label}")
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
                try:
                    play_sound()
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            t = threading.Thread(target=_timer_fire, daemon=True)
            t.start()
            msg = f"Timer set for {secs} seconds."
            return {"ok": True, "action": a, "content": msg, "response": msg}

        elif alarm_time:
            # Parse a natural-language time and compute seconds until it. Handles
            # "7am", "7 am", "7:30pm", "07:00", "07:00 hours", "19:00", "noon",
            # "midnight", "tomorrow at 7am", "7am tomorrow", bare "7" (was: int()
            # on the raw string → "Bad time format" for everything but "HH:MM").
            from datetime import datetime as _dt, timedelta as _td
            import re as _re_alarm
            now = _dt.now()
            try:
                _s = str(alarm_time).strip().lower()
                _force_tomorrow = "tomorrow" in _s
                _s = _re_alarm.sub(r"\b(tomorrow|at|hours|hrs|o'?clock)\b", " ", _s)
                if "noon" in _s:
                    target_h, target_m = 12, 0
                elif "midnight" in _s:
                    target_h, target_m = 0, 0
                else:
                    _ampm = "pm" if _re_alarm.search(r"\bpm\b|pm$", _s) else (
                        "am" if _re_alarm.search(r"\bam\b|am$", _s) else None)
                    _s2 = _s.replace("am", " ").replace("pm", " ").replace(".", ":")
                    _m = _re_alarm.search(r"(\d{1,2})(?::(\d{1,2}))?", _s2)
                    if not _m:
                        raise ValueError(f"no recognisable time in {alarm_time!r}")
                    target_h = int(_m.group(1))
                    target_m = int(_m.group(2) or 0)
                    if _ampm == "pm" and target_h < 12:
                        target_h += 12
                    elif _ampm == "am" and target_h == 12:
                        target_h = 0
                if not (0 <= target_h <= 23 and 0 <= target_m <= 59):
                    raise ValueError(f"time out of range: {target_h:02d}:{target_m:02d}")
                target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
                if _force_tomorrow or target <= now:
                    target = target + _td(days=1)   # timedelta (safe across month/year ends)
                alarm_time = f"{target_h:02d}:{target_m:02d}"
                secs = int((target - now).total_seconds())
                # Kept in the agenda, not in a sleeping thread: an alarm set for 7am has to
                # survive ELI being closed and reopened before then.
                from eli.execution.effectors.datetime_calendar import agenda_turn_context
                from eli.runtime import agenda as _agenda
                _agenda.add_reminder(args.get("label") or f"Alarm ({alarm_time})", target.timestamp(),
                                     user_id=agenda_turn_context(args)[0], source="SET_ALARM")
                msg = f"Alarm set for {alarm_time} ({secs}s from now)."
                return {"ok": True, "action": a, "content": msg, "response": msg}
            except Exception as e:
                return {"ok": False, "action": a, "error": f"Bad time format: {e}", "content": f"Bad time format: {alarm_time}", "response": f"Bad time format: {alarm_time}"}
        else:
            return {"ok": False, "action": a, "error": "No duration or time specified", "content": "No duration or time specified", "response": "No duration or time specified"}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "SET_ALARM": _h_set_alarm,
    "SET_TIMER": _h_set_alarm,
}.items():
    _register(_action, _fn)
