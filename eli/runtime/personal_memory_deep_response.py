from __future__ import annotations

import re
import sqlite3
from typing import List, Tuple

from eli.runtime.personal_memory_clean_response import build_clean_personal_memory_response
from eli.utils.log import get_logger
log = get_logger(__name__)

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone() is not None
    except Exception:
        return False

def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    try:
        return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []

def build_personal_memory_deep_response(user_input: str = "", mode_label: str = "") -> str:
    """What ELI keeps about the user, in full: the clean personal-memory report."""
    return build_clean_personal_memory_response(user_input=user_input, mode_label=mode_label)


def _recent_user_inputs(limit: int = 6) -> List[str]:
    """The last few things the user actually typed, newest first. [] on any error."""
    try:
        from eli.core.paths import user_db_path
        db = user_db_path()
        if not db.exists():
            return []
        with sqlite3.connect(str(db), timeout=3.0) as conn:
            if not _table_exists(conn, "conversation_turns"):
                return []
            from eli.runtime.personal_memory_render import rows_for_this_user
            scope = rows_for_this_user(_columns(conn, "conversation_turns"))
            if scope is None:
                return []
            rows = conn.execute(
                f"SELECT content FROM conversation_turns WHERE role = 'user' AND {scope[0]} "
                "ORDER BY COALESCE(timestamp, ts, 0) DESC LIMIT ?",
                (*scope[1], int(limit)),
            ).fetchall()
        return [str(r[0]).strip() for r in rows if r and str(r[0] or "").strip()]
    except Exception:
        log.debug("recent user inputs unavailable", exc_info=True)
        return []


def _route_probe(phrase: str) -> Tuple[str, str]:
    """Route *phrase* through the live router. Returns (description, action)."""
    try:
        from eli.execution.router_enhanced import route
        r = route(phrase) or {}
        action = str(r.get("action") or "?")
        matched = str((r.get("meta") or {}).get("matched_by") or "unmatched")
        args = r.get("args") or {}
        detail = ""
        for key in ("path", "name", "url", "query"):
            if str(args.get(key) or "").strip():
                detail = f" {key}={args[key]}"
                break
        return (f'- "{phrase[:70]}" -> {action}{detail}  [{matched}]', action)
    except Exception as e:
        return (f'- "{phrase[:70]}" -> routing probe failed ({type(e).__name__})', "")


def build_routing_fault_explanation(user_input: str = "") -> str:
    """Explain how recent inputs were actually routed, from real evidence.

    This used to be a fixed paragraph asserting the request "should not have
    gone to browser/search" — it ignored its argument entirely, so asking why
    one phrasing worked and another didn't produced a confident answer about a
    browser that was never involved.

    Routing is a pure function of the input text, so the honest explanation is
    to re-run the router over the phrases in question and report exactly which
    rule matched. Nothing here is asserted that was not measured.
    """
    question = str(user_input or "").strip()
    low = question.lower()
    lines: List[str] = []
    # Asked how long something took, the answer is the turns' times (newest first) and where the model
    # runs; how other phrasings would route is beside the point there.
    about_time = bool(re.search(r"\b(?:took|take|taking|long|slow|minutes?|seconds?|ages|forever|latency)\b", low))

    # Phrases the user quoted are what they're asking about.
    quoted = [q for pair in re.findall(r'"([^"]{2,70})"|\'([^\']{2,70})\'', question)
              for q in pair if q and q.strip()]

    probe_lines: List[str] = []
    seen: set = set()
    if quoted:
        # Explicitly quoted phrases: report every one, whatever it routes to —
        # the user named them, so a CHAT result is itself the answer.
        for p in quoted:
            key = p.strip().lower()
            if key and key not in seen:
                seen.add(key)
                probe_lines.append(_route_probe(p.strip())[0])
    else:
        # Otherwise infer from recent turns — but only ones that route to a
        # real action. Listing recent small talk back at the user is the noise
        # they complained about, and it explains nothing about a dispatch.
        for turn in _recent_user_inputs(limit=10):
            key = turn.strip().lower()
            if key == low or len(turn) > 120 or key in seen:
                continue
            seen.add(key)
            desc, act = _route_probe(turn)
            if act and act != "CHAT":
                probe_lines.append(desc)
            if len(probe_lines) >= 3:
                break

    if probe_lines and not about_time:
        lines.append("How those inputs route right now (re-run through the live router):")
        lines.extend(probe_lines)

    # What the recent turns actually did, as the pipeline recorded it in the audit ledger: which
    # rule routed each, what ran, whether it went to the web and why.
    recorded: List[str] = []
    try:
        from eli.cognition.self_claims import turn_record_lines
        from eli.memory.memory import get_memory
        recorded = turn_record_lines(get_memory(), limit=4)
    except Exception:
        log.debug("turn record unavailable", exc_info=True)
    if recorded:
        if about_time:
            lines.append("How long the last turns took (from the audit ledger, the last one first; the seconds "
                         "are the whole turn, model time the part spent running the model):")
            lines.extend(f"- {r}" for r in reversed(recorded))
        else:
            lines.append("What the last turns did (from the audit ledger):")
            lines.extend(f"- {r}" for r in recorded)
        # Asked about time, say where the model runs: that is what decides how long its part takes.
        if about_time:
            try:
                from eli.runtime.deterministic_grounding_gate import _inference_runtime_lines
                lines.append(_inference_runtime_lines())
            except Exception:
                log.debug("runtime lines unavailable", exc_info=True)
    else:
        try:
            from eli.runtime.last_trace import load_last_trace
            trace = load_last_trace() or {}
            if trace:
                act = trace.get("result_action") or trace.get("route_action") or trace.get("action")
                conf = trace.get("aggregated_confidence", trace.get("confidence"))
                conf_s = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else "n/a"
                if act:
                    lines.append(f"Last completed turn: {act} (confidence {conf_s}).")
        except Exception:
            log.debug("last trace unavailable", exc_info=True)

    if not recorded and re.search(r"\b(browser|web|online|search)\b", low):
        lines.append(
            "This kind of message should be answered locally from the runtime "
            "evidence — not sent to the browser or a web search."
        )

    if not lines:
        return ("I have no recorded routing evidence for that yet — nothing in the "
                "trace or the recent-turn log to explain from.")

    if probe_lines:
        lines.append(
            "A different phrasing landing on a different action means a router rule "
            "matched, not that anything learned or changed between the two tries."
        )
    if recorded:
        lines.append("Each row is what the pipeline wrote as the turn ran: the rule that routed it, what it "
                     "retrieved, whether it went to the web and why, how often the model ran and for how long. "
                     "Where a row gives no reason, none was recorded, and I won't guess one.")
        # In the slower modes the model rewords this answer; what it writes is held to these rows.
        try:
            from eli.kernel.request_context import note_turn_fact
            note_turn_fact("_record_lines", list(recorded))
            note_turn_fact("_record", "\n".join(lines))
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    return "\n".join(lines)

__all__ = [
    "build_personal_memory_deep_response",
    "build_routing_fault_explanation",
]
