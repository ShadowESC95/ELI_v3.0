"""Habits effector — CONFIRM_HABIT, DECLINE_HABIT (habit-offer yes/no)."""
from __future__ import annotations



from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_confirm_habit(a, args):
    try:
        from eli.planning.habits import get_pending_habit, clear_pending_habit
        from eli.memory import get_memory as _get_mem
    except Exception as _imp_exc:
        msg = f"Habit confirm unavailable: {_imp_exc}"
        return {'ok': False, 'action': a, 'error': str(_imp_exc), 'content': msg, 'response': msg}
    pending = get_pending_habit()
    if not pending:
        msg = "There's no habit offer waiting for a yes/no right now."
        return {'ok': True, 'action': a, 'content': msg, 'response': msg}
    rid = int(pending.get('rule_id', -1))
    name = pending.get('name', 'that habit')
    hh, mm = int(pending.get('hour', 0)), int(pending.get('minute', 0))
    mem = _get_mem()
    try:
        if a == 'CONFIRM_HABIT':
            ok = mem.set_habit_rule_enabled(rid, True) if hasattr(mem, 'set_habit_rule_enabled') else False
            clear_pending_habit()
            if not ok:
                msg = "I couldn't enable that habit (it may have been removed). Nothing activated."
                return {'ok': False, 'action': a, 'content': msg, 'response': msg,
                        'evidence_source': 'habit_confirm'}
            # Catch-up: if today's scheduled time has already passed, run it NOW so
            # the user doesn't wait until tomorrow for the habit they just approved.
            # Otherwise the live scheduler (polling every 30 s) fires it at the time.
            _cmd = str(pending.get('command') or '').strip()
            _ran_now = False
            try:
                from datetime import datetime as _dt
                _now = _dt.now()
                _already_passed = (hh * 60 + mm) < (_now.hour * 60 + _now.minute)
                if _cmd and _already_passed:
                    from eli.planning.habits_scheduler import get_scheduler
                    get_scheduler().run_now({'id': rid, 'name': name, 'command': _cmd,
                                             'hour': hh, 'minute': mm})
                    _ran_now = True
            except Exception as _rn:
                log.debug(f"[HABIT] catch-up run skipped: {_rn}")
            if _ran_now:
                msg = (f"Done — “{name}” is now an active habit. Today's {hh:02d}:{mm:02d} "
                       f"slot already passed, so I'm running it now; from tomorrow it runs "
                       f"automatically at {hh:02d}:{mm:02d}. Edit or disable it any time in "
                       f"the Habits tab.")
            else:
                msg = (f"Done — “{name}” is now an active habit; I'll run it around "
                       f"{hh:02d}:{mm:02d}. You can edit or disable it any time in the "
                       f"Habits tab.")
            return {'ok': True, 'action': a, 'content': msg, 'response': msg,
                    'evidence_source': 'habit_confirm'}
        else:  # DECLINE_HABIT — remove the suggestion so it doesn't linger
            if hasattr(mem, 'delete_habit_rule'):
                try:
                    mem.delete_habit_rule(rid)
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            clear_pending_habit()
            msg = f"No problem — I won't add “{name}”. I'll stop suggesting it."
            return {'ok': True, 'action': a, 'content': msg, 'response': msg,
                    'evidence_source': 'habit_decline'}
    except Exception as _hx:
        clear_pending_habit()
        msg = f"Habit update failed: {_hx}"
        return {'ok': False, 'action': a, 'error': str(_hx), 'content': msg, 'response': msg}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "CONFIRM_HABIT": _h_confirm_habit,
    "DECLINE_HABIT": _h_confirm_habit,
}.items():
    _register(_action, _fn)
