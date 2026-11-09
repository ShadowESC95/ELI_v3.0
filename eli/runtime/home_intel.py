"""ELI home intelligence — weave the LLM into the home/device system.

Turns raw device usage into things ELI can actually use:
  • note_usage()    — record a *preference* signal into ELI's memory (throttled) so
                      "what do you know about me" reflects how you use your home.
  • home_context()  — a short snapshot (connection, what's on, your usual habits) that
                      ELI's awareness can inject so it *knows* the state of your home.
  • suggestions()   — proactive automation ideas derived from usage ("you usually turn
                      the desk lamp on around 20:00 — automate it?").

All best-effort: never raises into callers, degrades cleanly with no devices.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

log = logging.getLogger(__name__)

# Throttle memory writes: at most one note per (device, command) per this many seconds.
_NOTE_THROTTLE = 12 * 3600.0
_last_note: Dict[str, float] = {}


def _server():
    try:
        from eli.runtime.device_server import get_server
        return get_server()
    except Exception:
        return None


def note_usage(row: Dict[str, Any]) -> None:
    """Record a home-use preference into ELI's memory — throttled so toggling a light
    repeatedly doesn't spam memory. Best-effort."""
    try:
        key = f"{row.get('id')}:{row.get('command')}"
        now = time.time()
        if now - _last_note.get(key, 0) < _NOTE_THROTTLE:
            return
        _last_note[key] = now
        name = row.get("name") or row.get("id")
        room = (row.get("room") or "").strip()
        hour = row.get("hour")
        where = f" in the {room}" if room else ""
        when = f" around {int(hour):02d}:00" if isinstance(hour, (int, float)) else ""
        text = f"Home preference: turns the {name}{where} {row.get('command')}{when}."
        from eli.memory.memory import get_memory
        mem = get_memory()
        if hasattr(mem, "add_memory"):
            mem.add_memory(text, tags=["home", "preference"])
    except Exception:
        log.debug("home_intel: note_usage failed", exc_info=True)


def home_context(max_chars: int = 420) -> str:
    """One-paragraph home snapshot for ELI's awareness/context. Empty string if there's
    nothing meaningful (no broker / no devices) so it never pollutes the prompt."""
    srv = _server()
    if srv is None:
        return ""
    try:
        state = srv.home_state()
    except Exception:
        return ""
    if not state.get("device_count"):
        return ""
    parts = []
    if state.get("connected"):
        parts.append(f"Home: {state.get('device_count')} device(s) connected.")
    else:
        parts.append("Home: device server configured (broker offline).")
    on = state.get("on") or []
    if on:
        parts.append("On now: " + ", ".join(on[:8]) + ".")
    try:
        usage = srv.usage_summary().get("devices", [])
        habits = [f"{d['name']}" + (f" (~{int(d['favourite_hour']):02d}:00)" if d.get("favourite_hour") is not None else "")
                  for d in usage[:3] if d.get("uses", 0) >= 3]
        if habits:
            parts.append("You most use: " + ", ".join(habits) + ".")
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        from eli.runtime import home_mesh
        line = home_mesh.mesh_context_line()
        if line:
            parts.append(line)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return " ".join(parts)[:max_chars]


# What each command does, in the words a suggestion uses; a command not here is named as it is.
_DOING = {"on": "turn on", "off": "turn off", "toggle": "switch", "brightness": "set the brightness of",
          "set_brightness": "set the brightness of", "set": "set"}


def suggestions(min_days: int = 3, days: int = 14) -> List[Dict[str, Any]]:
    """Proactive automation ideas from usage patterns: one per routine, a command given to a device
    around the same time on at least `min_days` separate days (eli/planning/routine_stats, the
    routine test the app habits use). Each: {device, name, room, command, value, hour, minute, days,
    text}. Uses were counted per device across every command, and the suggestion carried none, so a
    lamp switched OFF most nights at 23:00 was offered, and accepted, as an automation turning it ON."""
    srv = _server()
    if srv is None:
        return []
    from collections import Counter, defaultdict
    from datetime import datetime
    from eli.planning import routine_stats as rs
    out: List[Dict[str, Any]] = []
    try:
        by_command: Dict[tuple, list] = defaultdict(list)
        info: Dict[str, Dict[str, Any]] = {}
        for r in srv.usage_events(days):
            try:
                when = datetime.fromtimestamp(float(r.get("ts") or 0))
            except (OverflowError, OSError, ValueError):
                continue
            key = (str(r.get("id") or ""), str(r.get("command") or "").lower())
            if not key[0] or not key[1]:
                continue
            by_command[key].append((rs.minute_of_day(when.hour, when.minute), when.date().toordinal(),
                                    r.get("value")))
            info[key[0]] = {"name": r.get("name") or key[0], "room": r.get("room") or ""}
        for (device, command), uses in by_command.items():
            for cluster in rs.cluster_times([(m, d) for m, d, _v in uses]):
                if cluster.distinct_days < int(min_days):
                    continue
                hour, minute = rs.to_hour_minute(int(round(cluster.centre)))
                values = Counter(v for m, _d, v in uses if v is not None and m in cluster.minutes)
                value = values.most_common(1)[0][0] if values else None
                name, room = info[device]["name"], info[device]["room"]
                where = f" in the {room}" if room else ""
                doing = _DOING.get(command, command)
                setting = f" to {value}" if value is not None else ""
                out.append({
                    "device": device, "name": name, "room": room, "command": command, "value": value,
                    "hour": int(hour), "minute": int(minute), "days": int(cluster.distinct_days),
                    "text": f"You usually {doing} the {name}{where}{setting} around {hour:02d}:{minute:02d} "
                            f"(on {cluster.distinct_days} days lately). Want ELI to automate it?",
                })
    except Exception:
        log.debug("home_intel: suggestions failed", exc_info=True)
    out.sort(key=lambda s: s["days"], reverse=True)
    return out[:6]
