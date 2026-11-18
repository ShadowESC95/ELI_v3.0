"""Lessons: changes ELI makes to how it works, each checked against what happens next.

A lesson is a hypothesis, not a fact, and it is kept only while the record bears it out. Two kinds
live in the one table, and every decision about either is the one test (eli/cognition/epistemics.compare,
non-inferiority at the project's one significance point), never a hand-set rate.

**Action lessons** (trigger -> evidence -> proposed change -> predicted outcome) apply to one action:
shown before a run of it (brief), checked only on the runs they were shown for (observe_action), and
judged against how that action did before the lesson (its baseline, from the one reliability
estimator, eli/runtime/action_outcomes.reliability). A lesson shown worse than that is retired; one
shown no worse is renewed when it expires; one nothing has shown either way (or with no baseline to
hold it to) expires unconfirmed.

**Lever lessons** change a part of ELI that its outcomes flag as worse than its peers
(epistemics.rank: a router rule, a reply path; action_outcomes.unreliable: an action). Each pulls one
lever, at most one open lesson per part:

    parser_first   a router rule (kind route): the engine hands the sentences the rule matched to the
                   command parser's reading, or the resolver when the parser has none
                   (eli/kernel/engine._parse_intent reads lever("route", rule))
    hold           a reply path (kind answer, the bus's fast path): the path is held off for a half-life
                   (epistemics.HALF_LIFE_DAYS) and then released to be measured again
                   (eli/kernel/engine._fast_path_stands reads held("answer", path))
    proven_first   an action with an alternative proven for it (action_outcomes.SAME_DEED): the
                   alternative runs first (action_outcomes.first_choice reads it)
    repair         a part with no lever of its own: one goal with the part's evidence, closed when the
                   part is no longer flagged (eli/planning/goal_store)

A lever on trial is judged on the part's figures taken under it (they carry `lever_of`): kept once they
are shown no worse than the part's peers, reverted once shown worse than the part was before it, and
reverted at the end of the outcomes' window if nothing was shown. A kept lever stays in force until the
part, under it, is again shown worse than its peers. review() does all of it once per daemon tick and
opens at most one new lesson, for the most strongly flagged part. Each lesson is a figure of kind
lesson (settled kept or reverted) and a thought in the Upgrade Bay.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

DEFAULT_TTL_DAYS = 30.0

PARSER_FIRST, HOLD, PROVEN_FIRST, REPAIR = "parser_first", "hold", "proven_first", "repair"
LEVERS = (PARSER_FIRST, HOLD, PROVEN_FIRST, REPAIR)
OPEN, KEPT, REVERTED, LIFTED, RELEASED, CLOSED = "open", "kept", "reverted", "lifted", "released", "closed"
# The kind of a lesson about an action (its unit is the action's name).
ACTION = "action"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_key TEXT UNIQUE NOT NULL,
    applies_to TEXT NOT NULL,
    trigger_text TEXT NOT NULL,
    evidence TEXT,
    hypothesis TEXT,
    proposed_change TEXT NOT NULL,
    predicted_outcome TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    checks INTEGER NOT NULL DEFAULT 0,
    helped INTEGER NOT NULL DEFAULT 0,
    harmed INTEGER NOT NULL DEFAULT 0,
    renewals INTEGER NOT NULL DEFAULT 0,
    last_checked REAL,
    retired_reason TEXT,
    category TEXT,
    scope TEXT,
    baseline_rate REAL,
    baseline_n INTEGER,
    siblings TEXT,
    transfer_checks INTEGER NOT NULL DEFAULT 0,
    transfer_helped INTEGER NOT NULL DEFAULT 0,
    kind TEXT,
    unit TEXT,
    lever TEXT,
    lever_args TEXT,
    evidence_z REAL,
    before_json TEXT,
    after_json TEXT,
    state TEXT,
    goal_id TEXT,
    judged_at REAL
);
"""
_ADDED = (("category", "TEXT"), ("scope", "TEXT"), ("baseline_rate", "REAL"), ("baseline_n", "INTEGER"),
          ("siblings", "TEXT"), ("transfer_checks", "INTEGER NOT NULL DEFAULT 0"),
          ("transfer_helped", "INTEGER NOT NULL DEFAULT 0"), ("kind", "TEXT"), ("unit", "TEXT"), ("lever", "TEXT"),
          ("lever_args", "TEXT"), ("evidence_z", "REAL"), ("before_json", "TEXT"), ("after_json", "TEXT"),
          ("state", "TEXT"), ("goal_id", "TEXT"), ("judged_at", "REAL"))
# One open lever lesson per part: a second change while the first is on trial could never be judged.
_OPEN_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS idx_lessons_open ON lessons(kind, unit) WHERE state = 'open'"

# Lessons shown for an action about to run, per thread: (ACTION) -> {"direct": [ids], "transfer": [ids]}.
# A lesson is checked only on a run it was shown for; a run that never saw it says nothing about it.
_SHOWN: Dict[tuple, Dict[str, List[int]]] = {}
_SHOWN_LOCK = threading.Lock()
# The levers in force, read on the turn path: (kind, unit) -> lever, refreshed after FIT_SECONDS or a change.
_LEVERS: Dict[str, Any] = {"at": 0.0, "where": "", "map": {}}


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA.strip().rstrip(";"))
    have = {r[1] for r in conn.execute("PRAGMA table_info(lessons)")}
    for name, decl in _ADDED:
        if name not in have:
            conn.execute(f"ALTER TABLE lessons ADD COLUMN {name} {decl}")
    conn.execute(_OPEN_INDEX)


def _connect() -> sqlite3.Connection:
    from eli.core.paths import agent_db_path
    conn = sqlite3.connect(str(agent_db_path()), timeout=10)
    _ensure(conn)
    return conn


def _own(conn: Optional[sqlite3.Connection]) -> Tuple[sqlite3.Connection, bool]:
    if conn is None:
        return _connect(), True
    _ensure(conn)
    return conn, False


def _key(applies_to: str, trigger: str) -> str:
    return hashlib.sha1(f"{applies_to.upper()}|{trigger.strip().lower()}".encode()).hexdigest()[:20]


def _verdict(after: Tuple[float, float], before: Tuple[float, float]) -> Optional[bool]:
    """The one test (epistemics.compare): True when `after` is shown worse than `before`, False when
    shown no worse, None while neither is shown."""
    from eli.cognition.epistemics import compare
    return compare(after, before)


# ── action lessons ────────────────────────────────────────────────────────────────────────────────

def propose(applies_to: str, trigger: str, evidence: List[str], proposed_change: str, *,
            hypothesis: str = "", predicted_outcome: str = "", ttl_days: float = DEFAULT_TTL_DAYS,
            now: Optional[float] = None, conn: Optional[sqlite3.Connection] = None, category: str = "",
            scope: Optional[List[str]] = None, baseline: Optional[tuple] = None,
            siblings: Optional[List[str]] = None) -> Optional[int]:
    """Record a lesson. The same trigger again adds evidence instead of a second lesson. `baseline` is
    (rate, runs) of the action before the lesson, what the lesson is judged against. A lesson is kept
    whole: its texts were cut to a length, mid-word, and one kept cut short before is the same lesson
    as the whole one it begins when it stops inside a word (taken over and made whole, not kept beside it)."""
    if not applies_to or not trigger or not proposed_change:
        return None
    conn, own = _own(conn)
    try:
        now = float(now if now is not None else time.time())
        key = _key(applies_to, trigger)
        row = conn.execute("SELECT id, evidence, status FROM lessons WHERE lesson_key = ?", (key,)).fetchone()
        if not row:
            said = trigger.strip()
            row = next((r[:3] for r in conn.execute(
                "SELECT id, evidence, status, trigger_text FROM lessons WHERE applies_to = ? AND kind = ? "
                "AND status = 'active'", (applies_to.upper(), ACTION)).fetchall()
                if r[3] and len(r[3]) < len(said) and said.startswith(str(r[3]))
                and str(r[3])[-1].isalnum() and said[len(r[3])].isalnum()), None)      # it stops inside a word
            if row:
                conn.execute("UPDATE lessons SET lesson_key = ?, trigger_text = ?, proposed_change = ? WHERE id = ?",
                             (key, said, proposed_change, row[0]))
        if row:
            if row[2] != "active":
                return None
            seen = json.loads(row[1] or "[]")
            merged = (seen + [e for e in evidence if e not in seen])[-6:]
            conn.execute("UPDATE lessons SET evidence = ? WHERE id = ?", (json.dumps(merged), row[0]))
            conn.commit()
            return int(row[0])
        cur = conn.execute(
            "INSERT INTO lessons (lesson_key, applies_to, trigger_text, evidence, hypothesis, proposed_change, "
            "predicted_outcome, created_at, expires_at, category, scope, baseline_rate, baseline_n, siblings, kind, unit) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, applies_to.upper(), trigger.strip(), json.dumps(list(evidence)[-6:]), hypothesis,
             proposed_change, predicted_outcome, now, now + float(ttl_days) * 86400.0, category,
             json.dumps(list(scope or [])), baseline[0] if baseline else None, baseline[1] if baseline else 0,
             json.dumps(list(siblings or [])), ACTION, applies_to.upper()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        if own:
            conn.close()


def applicable(action: str, *, now: Optional[float] = None, limit: int = 3, args: Optional[Dict[str, Any]] = None,
               conn: Optional[sqlite3.Connection] = None, siblings: bool = False) -> List[Dict[str, Any]]:
    """Active lessons for this action. A lesson scoped to certain argument names applies only when they are present.
    With `siblings`, lessons learned on another action of the same kind that name this one as a sibling come too,
    marked `transfer`."""
    conn, own = _own(conn)
    try:
        now = float(now if now is not None else time.time())
        a = str(action).upper()
        rows = conn.execute(
            "SELECT id, applies_to, trigger_text, proposed_change, predicted_outcome, checks, helped, expires_at, scope, siblings "
            "FROM lessons WHERE status = 'active' AND lever IS NULL AND expires_at > ? AND (applies_to IN (?, '*')"
            + (" OR siblings LIKE ?" if siblings else "") + ") ORDER BY helped DESC, id DESC",
            (now, a, f'%"{a}"%') if siblings else (now, a)).fetchall()
        names = ("id", "applies_to", "trigger", "change", "predicted", "checks", "helped", "expires_at")
        out = []
        for r in rows:
            scope = json.loads(r[8] or "[]")
            if scope and args is not None and not all(k in args for k in scope):
                continue
            item = dict(zip(names, r[:8]))
            item["transfer"] = item["applies_to"] not in (a, "*")
            out.append(item)
        return out[:int(limit)]
    finally:
        if own:
            conn.close()


# A lever lesson (kind/unit/lever/state) is listed with the action lessons: its record is its state.
_LISTED = ("id", "applies_to", "trigger_text", "evidence", "hypothesis", "proposed_change", "predicted_outcome",
           "created_at", "expires_at", "status", "checks", "helped", "harmed", "renewals", "last_checked",
           "retired_reason", "category", "kind", "unit", "lever", "state", "judged_at")


def _evidence_as_read(item: Any) -> str:
    """A piece of a lesson's evidence in words: one kept as raw figures before is read out as such."""
    text = str(item)
    if text.startswith("{"):
        try:
            figures = json.loads(text)
            if isinstance(figures, dict) and "right" in figures and "n" in figures:
                return _settled_in_words({"peer_right": 0, "peer_n": 0, **figures})
        except (ValueError, TypeError):
            pass
    return text


def listed(*, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Every lesson on record, active and retired, newest first, each with its evidence as a list: the
    record ELI's rooms keep of what it learned (eli/world/books.py)."""
    own = conn is None
    conn = conn or _connect()
    try:
        rows = conn.execute(f"SELECT {', '.join(_LISTED)} FROM lessons ORDER BY created_at DESC, id DESC").fetchall()
    finally:
        if own:
            conn.close()
    out = []
    for r in rows:
        item = dict(zip(_LISTED, r))
        try:
            item["evidence"] = [_evidence_as_read(e) for e in json.loads(item.get("evidence") or "[]")]
        except (TypeError, ValueError):
            item["evidence"] = []
        out.append(item)
    return out


def _before(base_rate: Any, base_n: Any) -> Tuple[float, float]:
    """What an action lesson is judged against: (right, runs) of its action before it (its baseline);
    (0, 0), which shows nothing, when there was none."""
    if base_rate is not None and int(base_n or 0) > 0:
        return float(base_rate) * float(base_n), float(base_n)
    return 0.0, 0.0


def record_outcome(lesson_id: int, helped: bool, *, now: Optional[float] = None,
                   conn: Optional[sqlite3.Connection] = None) -> str:
    """Count one check (a run the lesson was shown for). A lesson whose runs are shown worse than its
    action's before it is retired. Returns the lesson's status afterwards."""
    conn, own = _own(conn)
    try:
        now = float(now if now is not None else time.time())
        conn.execute(
            "UPDATE lessons SET checks = checks + 1, helped = helped + ?, harmed = harmed + ?, last_checked = ? "
            "WHERE id = ? AND status = 'active'", (1 if helped else 0, 0 if helped else 1, now, int(lesson_id)))
        row = conn.execute("SELECT checks, helped, baseline_rate, baseline_n FROM lessons WHERE id = ?",
                           (int(lesson_id),)).fetchone()
        status = "active"
        if row:
            before = _before(row[2], row[3])
            if _verdict((float(row[1]), float(row[0])), before) is True:
                reason = (f"shown worse than before the lesson: {row[1]} of {row[0]} checks against "
                          f"{before[0] / before[1]:.0%} of {int(before[1])} runs")
                conn.execute("UPDATE lessons SET status = 'retired', retired_reason = ? WHERE id = ?",
                             (reason, int(lesson_id)))
                status = "retired"
        conn.commit()
        return status
    finally:
        if own:
            conn.close()


def _review_action_lessons(conn: sqlite3.Connection, now: float, out: Dict[str, int]) -> None:
    """Expired action lessons: renewed when their runs are shown no worse than their action's before
    them, retired otherwise (unconfirmed when nothing was shown)."""
    for lid, checks, helped, base_rate, base_n in conn.execute(
            "SELECT id, checks, helped, baseline_rate, baseline_n FROM lessons "
            "WHERE status = 'active' AND lever IS NULL AND expires_at <= ?", (now,)).fetchall():
        verdict = _verdict((float(helped), float(checks)), _before(base_rate, base_n))
        if verdict is False:
            conn.execute("UPDATE lessons SET expires_at = ?, renewals = renewals + 1 WHERE id = ?",
                         (now + DEFAULT_TTL_DAYS * 86400.0, lid))
            out["renewed"] += 1
        else:
            reason = "expired unconfirmed" if verdict is None else "expired, shown worse than before it"
            conn.execute("UPDATE lessons SET status = 'retired', retired_reason = ? WHERE id = ?", (reason, lid))
            out["retired"] += 1


def observe_action(action: str, ok: bool) -> None:
    """Called when an action finishes: each lesson shown for this run of it (brief) gets one check, a lesson
    shown on a sibling action one transfer check. Lessons nobody saw are not checked."""
    a = str(action or "").upper()
    if not a:
        return
    with _SHOWN_LOCK:
        shown = _SHOWN.pop((threading.get_ident(), a), None)
    if not shown:
        return
    try:
        for lid in shown.get("direct") or []:
            record_outcome(lid, bool(ok))
        if shown.get("transfer"):
            _record_transfer(shown["transfer"], bool(ok))
    except Exception:
        log.debug("lesson outcome not recorded", exc_info=True)


def _record_transfer(lesson_ids: List[int], ok: bool, conn: Optional[sqlite3.Connection] = None) -> None:
    """The same kind of difficulty on a different action: did the lesson's advice hold up there too?"""
    if not lesson_ids:
        return
    conn, own = _own(conn)
    try:
        conn.executemany("UPDATE lessons SET transfer_checks = transfer_checks + 1, transfer_helped = transfer_helped + ? "
                         "WHERE status = 'active' AND id = ?", [(1 if ok else 0, int(i)) for i in lesson_ids])
        conn.commit()
    finally:
        if own:
            conn.close()


def transfers(lesson_id: int, *, conn: Optional[sqlite3.Connection] = None) -> Optional[bool]:
    """True when the lesson held on sibling actions (their runs under it shown no worse than its own
    action's before it), False when shown worse, None while too little has been seen to say."""
    conn, own = _own(conn)
    try:
        row = conn.execute("SELECT transfer_checks, transfer_helped, baseline_rate, baseline_n FROM lessons "
                           "WHERE id = ?", (int(lesson_id),)).fetchone()
        if not row:
            return None
        verdict = _verdict((float(row[1]), float(row[0])), _before(row[2], row[3]))
        return None if verdict is None else verdict is False
    finally:
        if own:
            conn.close()


def brief(action: Optional[str] = None, *, args: Optional[Dict[str, Any]] = None, limit: int = 3) -> str:
    """The lessons for the action about to run, as plain lines for the prompt or the pre-run check that decides
    how it runs, or "" when there are none. What is returned is remembered as shown for this run (on this
    thread), so observe_action checks exactly those lessons when the run ends."""
    if not action:
        return ""
    try:
        rows = applicable(action, limit=limit, args=args, siblings=True)
    except Exception:
        return ""
    if not rows:
        return ""
    with _SHOWN_LOCK:
        _SHOWN[(threading.get_ident(), str(action).upper())] = {
            "direct": [r["id"] for r in rows if not r["transfer"]],
            "transfer": [r["id"] for r in rows if r["transfer"]],
        }
    lines = [f"- {r['change']} (from: {r['trigger']}; checked {r['checks']}x, helped {r['helped']}x)" for r in rows]
    return "Lessons to apply:\n" + "\n".join(lines)


def _failure_rows(src: sqlite3.Connection, since: float) -> List[tuple]:
    """(error, context, occurrence_count) of the open failures seen since `since`, from the one failure store
    (the failures table that Memory.log_failure writes). note_turn_anomalies rows are left out: their context
    names the turn the person complained in, not an action that failed."""
    have = {r[1] for r in src.execute("PRAGMA table_info(failures)")}
    if not {"error", "context"} <= have:
        return []
    seen = "COALESCE(last_seen, timestamp, 0)" if "last_seen" in have else "COALESCE(timestamp, 0)"
    where = [f"{seen} >= ?"]
    if "source" in have:
        where.append("COALESCE(source, '') != 'anomaly'")
    if "status" in have:
        where.append("COALESCE(status, 'open') NOT IN ('resolved', 'closed')")
    count = "occurrence_count" if "occurrence_count" in have else "1"
    return src.execute(f"SELECT error, context, {count} FROM failures WHERE " + " AND ".join(where),
                       (since,)).fetchall()


def _failure_action(context: Any) -> str:
    try:
        ctx = json.loads(context) if isinstance(context, str) else (context or {})
    except (ValueError, TypeError):
        return ""
    if not isinstance(ctx, dict):
        return ""
    intent = ctx.get("intent") if isinstance(ctx.get("intent"), dict) else {}
    return str(ctx.get("action") or intent.get("action") or "").upper()


def from_failure_clusters(*, days: float = 14.0, min_count: int = 3, now: Optional[float] = None) -> List[int]:
    """Lessons from failures that keep happening: same action and error, at least min_count times."""
    made: List[int] = []
    now = float(now if now is not None else time.time())
    try:
        from eli.core.paths import agent_db_path
        src = sqlite3.connect(str(agent_db_path()), timeout=10)
        try:
            rows = _failure_rows(src, now - days * 86400.0)
        finally:
            src.close()
    except Exception:
        log.debug("failure record unavailable to lessons", exc_info=True)
        return made
    grouped: Dict[tuple, int] = {}
    for error, context, count in rows:
        action = _failure_action(context)
        text = str(error or "").strip()
        if action and text and "mock" not in text.lower():
            grouped[(action, text)] = grouped.get((action, text), 0) + int(count or 1)
    rows = [(t, a, n, 0) for (a, t), n in sorted(grouped.items(), key=lambda kv: -kv[1])[:10] if n >= int(min_count)]
    for text, action, count, _unused in rows:
        said = next((line.strip() for line in text.splitlines() if line.strip()), text)   # what failed, whole
        try:
            from eli.runtime.failure_taxonomy import classify_category
            category = classify_category(text, action)
        except Exception:
            category = ""
        scope = scope_from_history(action, days=days)
        siblings = []
        if category:
            try:
                from eli.runtime.failure_taxonomy import classify_category as _cc
                siblings = sorted({a for (a, t) in grouped if a != action and _cc(t, a) == category})
            except Exception:
                log.debug("sibling actions not resolved", exc_info=True)
        base = None
        try:
            from eli.runtime.action_outcomes import reliability
            r = reliability(action)
            base = (round(r["p"], 4), r["n"]) if r["n"] else None
        except Exception:
            log.debug("baseline unavailable", exc_info=True)
        where = f" when called with {', '.join(scope)}" if scope else ""
        lid = propose(
            action, f"{action} failed with: {said}", [f"{count} occurrences in the last {int(days)} days"],
            f"Before running {action}{where}, check the cause of: {said}",
            hypothesis=(f"A {category or 'recurring'} fault" + (f" that shows up only {where.strip()}" if scope else " that shows up regardless of arguments")
                        + f", so a precondition of {action} is not being met."),
            predicted_outcome=f"{action} succeeds more often than its {base[0]:.0%} before this lesson." if base
            else f"{action} stops failing with this error.",
            now=now, category=category, scope=scope, baseline=base,
            siblings=siblings)
        if lid:
            made.append(lid)
    return made


def scope_from_history(action: str, *, days: float = 14.0, min_runs: int = 4) -> List[str]:
    """Argument names that appear in nearly every failed run of an action and almost none of the successful ones.

    That separates "fails whenever it is given X" from "fails whatever it is given", so a lesson is stated for the
    condition it was learned under and not as a rule for the whole action.
    """
    try:
        from eli.runtime.evidence_ledger import _connect as _ledger
        conn = _ledger()
        try:
            rows = conn.execute(
                "SELECT outcome, payload_json FROM runtime_events WHERE event_type = 'executor_action' AND action = ? "
                "AND COALESCE(ts, timestamp, 0) >= ?", (str(action).upper(), time.time() - days * 86400.0)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    fail, ok = [], []
    for outcome, payload in rows:
        try:
            keys = set((json.loads(payload).get("args") or {}).keys())
        except Exception:
            continue
        (fail if outcome == "failed" else ok).append(keys)
    if len(fail) < min_runs or len(ok) < min_runs:
        return []
    every = set.intersection(*fail) if fail else set()
    return sorted(k for k in every if sum(k in r for r in ok) / len(ok) <= 0.2)


# ── lever lessons: a part of ELI changed, and the change judged ──────────────────────────────────

def _lever_rows(conn: sqlite3.Connection, where: str, args: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    names = ("id", "kind", "unit", "lever", "lever_args", "evidence_z", "before_json", "after_json", "state",
             "goal_id", "created_at", "expires_at", "judged_at", "trigger_text")
    rows = conn.execute(f"SELECT {', '.join(names)} FROM lessons WHERE lever IS NOT NULL AND {where}", args).fetchall()
    out = []
    for r in rows:
        d = dict(zip(names, r))
        for k in ("lever_args", "before_json", "after_json"):
            try:
                d[k] = json.loads(d[k] or "{}")
            except (TypeError, ValueError):
                d[k] = {}
        out.append(d)
    return out


def _forget_levers() -> None:
    _LEVERS["at"] = 0.0


def levers(*, conn: Optional[sqlite3.Connection] = None) -> Dict[Tuple[str, str], str]:
    """Every lever in force (on trial or kept): (kind, unit) -> lever. Read once per FIT_SECONDS."""
    from eli.cognition.epistemics import FIT_SECONDS
    try:
        from eli.core.paths import agent_db_path
        where = str(agent_db_path())
    except (ImportError, OSError) as failure:
        log.debug("lesson store path unknown: %s", failure)
        where = ""
    now = time.time()
    if conn is None and now - float(_LEVERS["at"]) < FIT_SECONDS and _LEVERS["where"] == where:
        return dict(_LEVERS["map"])
    try:
        conn_, own = _own(conn)
    except (sqlite3.Error, OSError) as failure:
        log.debug("lessons unreadable: %s", failure)
        return {}
    try:
        found = {(str(r["kind"]), str(r["unit"])): str(r["lever"])
                 for r in _lever_rows(conn_, "state IN (?, ?)", (OPEN, KEPT))}
    finally:
        if own:
            conn_.close()
    if conn is None:
        _LEVERS.update({"at": now, "where": where, "map": found})
    return found


def lever(kind: str, unit: str, *, conn: Optional[sqlite3.Connection] = None) -> str:
    """The lever in force on this part ("" when none)."""
    return levers(conn=conn).get((str(kind), str(unit)), "")


def held(kind: str, unit: str, *, conn: Optional[sqlite3.Connection] = None) -> bool:
    """Whether this part is held off (lever "hold")."""
    return lever(kind, unit, conn=conn) == HOLD


def _figure(lesson_id: int, kind: str, unit: str, lever_name: str, z: float) -> None:
    """The lesson as a figure (epistemics kind lesson: the chance a lesson is kept, from how earlier
    ones fared) and a thought in the Upgrade Bay."""
    try:
        from eli.cognition import epistemics as _ep
        from eli.cognition.thoughts import record as _thought
        p_kept, n_kept = _ep.base_rate(_ep.LESSON)
        prob = _ep.assess(_ep.LESSON, p_kept, request_id=f"lesson:{int(lesson_id)}", class_=lever_name,
                          features={"evidence_z": round(float(z), 4)})
        _thought("lesson", f"{lever_name.replace('_', ' ')} on {kind} {unit}",
                 why=[f"its figures were shown worse than its peers' (z {float(z):.2f})",
                      f"{n_kept} earlier lessons were judged" if n_kept else "the first such lesson"],
                 chance=float(prob.p), decision="taken", reason="the most strongly flagged part",
                 subject=f"{kind}:{unit}", ref=f"lesson:{int(lesson_id)}")
    except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError, sqlite3.Error):
        log.debug("lesson figure not kept", exc_info=True)


def _settle_figure(lesson_id: int, kept: bool, how: str) -> None:
    try:
        from eli.cognition import epistemics as _ep
        from eli.cognition.thoughts import settle as _settle_thought
        _ep.settle(f"lesson:{int(lesson_id)}", kept, how=how, kind=_ep.LESSON)
        _settle_thought(f"lesson:{int(lesson_id)}", "done" if kept else "undone", how=how)
    except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, RuntimeError, sqlite3.Error):
        log.debug("lesson figure not settled", exc_info=True)


def open_lever(kind: str, unit: str, lever_name: str, *, evidence: Dict[str, Any], args: Optional[Dict[str, Any]] = None,
               now: Optional[float] = None, conn: Optional[sqlite3.Connection] = None) -> Optional[int]:
    """Pull one lever on a part. `evidence` is what flagged it: {"right", "n" (the part's settled
    figures), "peer_right", "peer_n", "z"}. None when the part already has an open lesson or a lever in
    force, or the lever is not one of LEVERS."""
    if lever_name not in LEVERS or not kind or not unit:
        return None
    from eli.cognition.epistemics import DAYS, HALF_LIFE_DAYS
    conn, own = _own(conn)
    try:
        if lever(kind, unit, conn=conn):
            return None
        now = float(now if now is not None else time.time())
        window = (HALF_LIFE_DAYS if lever_name == HOLD else DAYS) * 86400.0
        before = {"right": float(evidence.get("right") or 0), "n": float(evidence.get("n") or 0),
                  "peer_right": float(evidence.get("peer_right") or 0), "peer_n": float(evidence.get("peer_n") or 0)}
        words = {PARSER_FIRST: f"hand the sentences {unit} reads to the parser's reading",
                 HOLD: f"hold {unit} off for {HALF_LIFE_DAYS:g} days", PROVEN_FIRST: f"run what is proven for {unit} first",
                 REPAIR: f"repair {unit}"}[lever_name]
        try:
            cur = conn.execute(
                "INSERT INTO lessons (lesson_key, applies_to, trigger_text, evidence, proposed_change, created_at, "
                "expires_at, status, category, kind, unit, lever, lever_args, evidence_z, before_json, state) "
                "VALUES (?,?,?,?,?,?,?,'active','lever',?,?,?,?,?,?,?)",
                (f"lever:{kind}:{unit}:{now:.6f}", str(unit)[:300],
                 f"{kind} {unit} shown worse than its peers", json.dumps([_settled_in_words(before)]), words, now,
                 now + window, kind, unit, lever_name, json.dumps(dict(args or {})), float(evidence.get("z") or 0.0),
                 json.dumps(before), OPEN))
        except sqlite3.IntegrityError:
            return None
        lid = int(cur.lastrowid)
        if lever_name == REPAIR:
            goal_id = _open_repair_goal(lid, kind, unit, before)
            if goal_id:
                conn.execute("UPDATE lessons SET goal_id = ? WHERE id = ?", (goal_id, lid))
        conn.commit()
    finally:
        if own:
            conn.close()
    _forget_levers()
    _figure(lid, kind, unit, lever_name, float(evidence.get("z") or 0.0))
    return lid


def _settled_in_words(before: Dict[str, float]) -> str:
    """What flagged a part, as it is read: how many of its settled figures came out right, and its
    peers' rate. A lesson's evidence was kept as the raw figures and shown that way."""
    peers = before["peer_right"] / before["peer_n"] if before.get("peer_n") else None
    return (f"{int(before['right'])} of {int(before['n'])} settled right"
            + (f" against {peers:.0%} for the rest" if peers is not None else ""))


def _open_repair_goal(lesson_id: int, kind: str, unit: str, before: Dict[str, float]) -> str:
    """One goal for a failing part, with its evidence, waiting for the owner's yes (proposal only)."""
    try:
        from eli.planning.goal_store import upsert_goal
        evidence = _settled_in_words(before)
        goal = upsert_goal({"title": f"Repair {kind} {unit}", "objective": f"{kind} {unit}: {evidence}",
                            "enabled": False, "autonomy_mode": "proposal_only", "status": "proposed",
                            "tags": ["repair", "lesson"], "success_criteria": ["no longer shown worse than its peers"],
                            "metadata": {"lesson_id": int(lesson_id), "kind": kind, "unit": unit, "evidence": before}})
        return str(goal.goal_id)
    except (ImportError, OSError, ValueError, TypeError, AttributeError) as failure:
        log.debug("repair goal not opened: %s", failure)
        return ""


def _close_repair_goal(goal_id: str) -> None:
    if not goal_id:
        return
    try:
        from eli.planning.goal_store import update_goal

        def _done(goal):
            goal.status = "done"
            goal.enabled = False
        update_goal(goal_id, _done)
    except (ImportError, OSError, ValueError, TypeError, AttributeError) as failure:
        log.debug("repair goal not closed: %s", failure)


def _part_counts(kind: str, unit: str, since: float = 0.0, *, under: str = "") -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """(right, settled) of the part's figures since `since`, and of its peers' over the whole window:
    for an epistemics kind its settled figures (with `under`, only those taken under that part's lever:
    feature lever_of), for an action its runs (action_outcomes)."""
    if kind == ACTION:
        from eli.runtime.action_outcomes import _asked
        runs = _asked(days=_days())
        if under:   # the runs made in its place under the lever
            mine = [r for r in runs if (r.get("args") or {}).get("_proven_first") == unit and r["ts"] >= since]
        else:
            mine = [r for r in runs if r["action"] == unit and not (r.get("args") or {}).get("_proven_first")
                    and r["ts"] >= since]
        rest = [r for r in runs if r["action"] != unit and not (r.get("args") or {}).get("_proven_first")]
        return ((float(sum(r["worked"] for r in mine)), float(len(mine))),
                (float(sum(r["worked"] for r in rest)), float(len(rest))))
    from eli.cognition import epistemics as _ep
    rows = _ep.settled(kind)
    if under:
        mine = [r for r in rows if (r["features"] or {}).get("lever_of") == unit and r["ts"] >= since]
    else:
        mine = [r for r in rows if _ep.unit_of(kind, r) == unit and r["ts"] >= since]
    rest = [r for r in rows if _ep.unit_of(kind, r) != unit and (r["features"] or {}).get("lever_of") != unit]
    return ((sum(r["y"] for r in mine), float(len(mine))), (sum(r["y"] for r in rest), float(len(rest))))


def _days() -> float:
    from eli.runtime.action_outcomes import DAYS
    return DAYS


def judge(lesson: Dict[str, Any], *, now: Optional[float] = None) -> str:
    """The state a lever lesson on trial goes to: KEPT once the part's figures under it are shown no
    worse than its peers', REVERTED once shown worse than the part was before it or when its window
    ends with nothing shown, OPEN otherwise. A hold is RELEASED after its half-life; a repair CLOSED
    once its part is no longer shown worse than its peers."""
    now = float(now if now is not None else time.time())
    kind, unit, name = str(lesson["kind"]), str(lesson["unit"]), str(lesson["lever"])
    before = lesson.get("before_json") or {}
    if name == HOLD:
        return RELEASED if now >= float(lesson["expires_at"]) else OPEN
    if name == REPAIR:
        mine, peers = _part_counts(kind, unit, float(lesson["created_at"]))
        return CLOSED if _verdict(mine, peers) is False else (REVERTED if now >= float(lesson["expires_at"]) else OPEN)
    after, peers = _part_counts(kind, unit, float(lesson["created_at"]), under=unit)
    if _verdict(after, (float(before.get("right") or 0), float(before.get("n") or 0))) is True:
        return REVERTED
    if _verdict(after, peers) is False:
        return KEPT
    return REVERTED if now >= float(lesson["expires_at"]) else OPEN


def _flagged(kind: str) -> List[Dict[str, Any]]:
    """The parts of a kind flagged worse than their peers, most strongly first, with their evidence."""
    if kind == ACTION:
        from eli.runtime.action_outcomes import _asked, unreliable
        runs = _asked(days=_days())
        out = []
        for r in unreliable():
            mine = [x for x in runs if x["action"] == r["action"]]
            rest = [x for x in runs if x["action"] != r["action"]]
            out.append({"unit": r["action"], "n": len(mine), "right": sum(x["worked"] for x in mine),
                        "peer_n": len(rest), "peer_right": sum(x["worked"] for x in rest), "z": 0.0})
        return out
    from eli.cognition import epistemics as _ep
    out = []
    for f in _ep.rank(kind):
        peer_n = sum(1 for _ in _ep.settled(kind)) - f["n"]
        out.append({"unit": f["unit"], "n": f["n"], "right": f["right"], "peer_n": peer_n,
                    "peer_right": f["peer_rate"] * peer_n, "z": f["z"]})
    return out


def _lever_for(kind: str, unit: str) -> str:
    """Which lever a flagged part gets."""
    from eli.cognition import epistemics as _ep
    if kind == _ep.ROUTE and not unit.startswith(("parser", "llm_intent", "fallback", "pending_proposal")):
        return PARSER_FIRST
    if kind == _ep.ANSWER and unit == "bus_fast":
        return HOLD
    if kind == ACTION:
        try:
            from eli.runtime.action_outcomes import SAME_DEED
            if any(unit in group for group in SAME_DEED):
                return PROVEN_FIRST
        except ImportError as failure:
            log.debug("deed groups unavailable: %s", failure)
    return REPAIR


def _review_levers(conn: sqlite3.Connection, now: float, out: Dict[str, int]) -> None:
    for lesson in _lever_rows(conn, "state = ?", (OPEN,)):
        try:
            state = judge(lesson, now=now)
        except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, sqlite3.Error):
            log.debug("lesson not judged", exc_info=True)
            continue
        if state == OPEN:
            continue
        conn.execute("UPDATE lessons SET state = ?, judged_at = ?, status = ?, retired_reason = ? WHERE id = ?",
                     (state, now, "active" if state == KEPT else "retired", state, int(lesson["id"])))
        out[state] = out.get(state, 0) + 1
        if lesson["lever"] == REPAIR:
            _close_repair_goal(str(lesson.get("goal_id") or ""))
        _settle_figure(int(lesson["id"]), state in (KEPT, RELEASED, CLOSED), state)
    for lesson in _lever_rows(conn, "state = ?", (KEPT,)):
        # a kept lever stays while the part under it is no worse than its peers
        try:
            after, peers = _part_counts(str(lesson["kind"]), str(lesson["unit"]), float(lesson["judged_at"] or 0),
                                        under=str(lesson["unit"]))
        except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, sqlite3.Error):
            log.debug("kept lesson not checked", exc_info=True)
            continue
        if _verdict(after, peers) is True:
            conn.execute("UPDATE lessons SET state = ?, judged_at = ?, status = 'retired', retired_reason = ? "
                         "WHERE id = ?", (LIFTED, now, "shown worse than its peers under the lever", int(lesson["id"])))
            out[LIFTED] = out.get(LIFTED, 0) + 1


def _open_one(conn: sqlite3.Connection, now: float, out: Dict[str, int]) -> None:
    """At most one new lesson per review: for the most strongly flagged part with no lever in force."""
    from eli.cognition import epistemics as _ep
    in_force = levers(conn=conn)
    candidates: List[Tuple[float, str, Dict[str, Any]]] = []
    for kind in (_ep.ROUTE, _ep.ANSWER, ACTION):
        try:
            for f in _flagged(kind):
                if (kind, f["unit"]) not in in_force:
                    candidates.append((float(f.get("z") or 0.0), kind, f))
        except (OSError, ValueError, TypeError, ImportError, AttributeError, KeyError, sqlite3.Error):
            log.debug("parts of %s not ranked", kind, exc_info=True)
    if not candidates:
        return
    _z, kind, f = max(candidates, key=lambda c: c[0])
    if open_lever(kind, f["unit"], _lever_for(kind, f["unit"]), evidence=f, now=now, conn=conn):
        out["opened"] = out.get("opened", 0) + 1


def review(*, now: Optional[float] = None, conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    """Once per daemon tick: expired action lessons renewed or retired (the one test against their
    baseline), every lever on trial judged (kept, reverted, a hold released, a repair closed), kept
    levers lifted when their part is again shown worse, and at most one new lever lesson opened."""
    conn, own = _own(conn)
    out = {"retired": 0, "renewed": 0}
    try:
        now = float(now if now is not None else time.time())
        _review_action_lessons(conn, now, out)
        _review_levers(conn, now, out)
        conn.commit()
        _forget_levers()
        _open_one(conn, now, out)
        conn.commit()
        return out
    finally:
        if own:
            conn.close()
        _forget_levers()


def report(*, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """Every lever lesson, newest first, as facts: what part, which lever, its state, its evidence."""
    conn, own = _own(conn)
    try:
        rows = _lever_rows(conn, "1 = 1 ORDER BY id DESC")
    finally:
        if own:
            conn.close()
    return [{"id": r["id"], "kind": r["kind"], "unit": r["unit"], "lever": r["lever"], "state": r["state"],
             "evidence": r["before_json"], "z": r["evidence_z"], "opened": r["created_at"], "judged": r["judged_at"]}
            for r in rows]


__all__ = ["ACTION", "HOLD", "LEVERS", "PARSER_FIRST", "PROVEN_FIRST", "REPAIR", "applicable", "brief",
           "from_failure_clusters", "held", "judge", "lever", "levers", "listed", "observe_action", "open_lever", "propose",
           "record_outcome", "report", "review", "scope_from_history", "transfers"]
