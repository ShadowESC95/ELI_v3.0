"""
eli/cognition/adjustments.py
────────────────────────────
What ELI changes about itself when a part of it is shown to do worse, and whether the change helped.

The scoreboard (eli/cognition/scoreboard.py) says which parts require adjustment and what has done
their job better. An adjustment pulls one lever on one part:

    parser_first       a routing rule the command parser beats where the two read a sentence
                       differently: while it is in force, where they differ, the parser's reading is
                       taken (eli/kernel/engine.py, at the routing site)
    alternative_first  an action that keeps failing on a kind of target where another action has
                       worked on that very target: the proven one runs first and the original only if
                       it fails too (eli/execution/executor_enhanced.execute); only an action safe to
                       run unasked
    hold               a kind of offer the user keeps turning down: ELI stops making it for a half-life,
                       then makes it again and sees (eli/planning/initiatives.py)
    repair             a part no lever reaches: one goal for it, with its evidence, offered to the user
                       as a fix to work on (eli/planning/conductor.py; self_improvement's coding agent
                       on their word)

Each is a figure of kind `adjustment` (eli/cognition/epistemics.py), its class the lever, so how often
each lever helps is learned like anything else, and a thought in the Upgrade Bay. It is judged on the
part's outcomes after it against those before it (`judge`): once MIN_EACH_SIDE have come in, worse at
Z_SIG → reverted (wrong), better at Z_SIG → kept (right); otherwise once as many have come in as stood
before it, kept when at least as good (right), reverted when not (wrong). A hold is judged when it ends,
on the offers of its kind made after it. A repair is judged on whether the part recovers: shown no longer
to do worse on outcomes after it → done (right); not within the board's window → closed (wrong).

A kept adjustment stays in force for the board's window and is then lifted, so the part is tested again
on what it does now. A part whose adjustment by a lever was settled wrong is not adjusted by that lever
again for a half-life. Nothing here is a hand-set size: the window and half-life are the board's, the
test and its minimum are epistemics'.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from eli.cognition.epistemics import ADJUSTMENT, INITIATIVE, MIN_EACH_SIDE, ROUTE, Z_SIG, shortfall
from eli.utils.log import get_logger

log = get_logger(__name__)

LEVERS = ("parser_first", "alternative_first", "hold", "repair")
LEVER_WORDS = {"parser_first": "taking the command parser's reading first",
               "alternative_first": "running the action that has worked first",
               "hold": "holding back", "repair": "opening a repair"}
_FAILURES = (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError, RuntimeError, sqlite3.Error)
_lock = threading.Lock()
_read: Dict[str, Any] = {"path": "", "mtime": -1.0, "state": None}


def _path() -> Path:
    from eli.core.paths import artifacts_dir
    return Path(artifacts_dir()) / "runtime" / "adjustments.json"


def _empty() -> Dict[str, Any]:
    return {"active": [], "settled": []}


def load() -> Dict[str, Any]:
    """The adjustments in force and those settled within the board's window. Read again only when the
    file changes, so the turn path asks for a lever at the cost of a stat."""
    path = _path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _empty()
    if _read["path"] == str(path) and _read["mtime"] == mtime and _read["state"] is not None:
        return _read["state"]
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as failure:
        log.warning("the adjustments file could not be read, none is in force: %s", failure)
        return _empty()
    if not isinstance(state, dict):
        return _empty()
    state.setdefault("active", [])
    state.setdefault("settled", [])
    _read.update({"path": str(path), "mtime": mtime, "state": state})
    return state


def _save(state: Dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)
    _read.update({"path": "", "mtime": -1.0, "state": None})


def in_force(lever: str, unit: str) -> Optional[Dict[str, Any]]:
    """The adjustment by this lever on this part that is in force now, or None."""
    for adj in load().get("active") or []:
        if adj.get("lever") == lever and adj.get("unit") == unit and adj.get("state") in ("active", "kept"):
            return adj
    return None


def judge(before: Tuple[float, int], after: Tuple[float, int]) -> Optional[bool]:
    """Whether the part did better after the change than before it: True kept, False reverted, None
    not yet known. (right, n) on each side."""
    a_right, a_n = float(after[0]), int(after[1])
    b_right, b_n = float(before[0]), int(before[1])
    if a_n < MIN_EACH_SIDE:
        return None
    if b_n >= MIN_EACH_SIDE:
        if shortfall(a_right, a_n, b_right, b_n) >= Z_SIG:
            return False
        if shortfall(b_right, b_n, a_right, a_n) >= Z_SIG:
            return True
        if a_n < b_n:
            return None
    return a_right / a_n >= (b_right / b_n if b_n else 0.0)


def _half_life_s() -> float:
    from eli.runtime.action_outcomes import HALF_LIFE_DAYS
    return HALF_LIFE_DAYS * 86400.0


def _window_s() -> float:
    from eli.runtime.action_outcomes import DAYS
    return DAYS * 86400.0


def recently_failed(lever: str, unit: str, now: float, state: Optional[Dict[str, Any]] = None) -> bool:
    """Whether an adjustment by this lever on this part was settled wrong within a half-life."""
    state = state if state is not None else load()
    return any(s.get("lever") == lever and s.get("unit") == unit and s.get("outcome") == "wrong"
               and now - float(s.get("settled_at") or 0.0) < _half_life_s() for s in state.get("settled") or [])


def _before_of(entry: Dict[str, Any]) -> Dict[str, Any]:
    """What the change is judged against: for the parser lever, the rule's sentences the parser read
    otherwise (the only turns it changes); for every other, the part's outcomes in the window."""
    instead = entry.get("instead") or {}
    if entry.get("lever") == "parser_first" and isinstance(instead.get("where_split"), dict):
        return {"n": int(instead["where_split"]["n"]), "right": float(instead["where_split"]["right"])}
    return {"n": int(entry.get("n") or 0), "right": float(entry.get("right") or 0.0)}


def begin(entry: Dict[str, Any], *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Pull the lever the board names for this part. None when none is pulled: no lever, one already in
    force on the part, the same lever failed on it within a half-life, or the lever lacks what it needs."""
    now = time.time() if now is None else float(now)
    lever = str(entry.get("lever") or "")
    kind, unit = str(entry.get("kind") or ""), str(entry.get("unit") or "")
    if lever not in LEVERS or not unit:
        return None
    instead = entry.get("instead") or {}
    if lever == "parser_first" and not instead.get("where_split"):
        return None
    if lever == "alternative_first" and not instead.get("action"):
        return None
    with _lock:
        state = json.loads(json.dumps(load()))
        if any(a.get("unit") == unit and a.get("kind") == kind for a in state["active"]):
            return None
        if recently_failed(lever, unit, now, state):
            return None
        adj_id = f"adjust:{lever}:{kind}:{unit}:{int(now)}"
        adj = {"id": adj_id, "lever": lever, "kind": kind, "unit": unit, "label": str(entry.get("label") or unit),
               "started": now, "before": _before_of(entry), "evidence_z": round(float(entry.get("evidence_z") or 0.0), 3),
               "instead": instead, "state": "active", "rate_before": round(float(entry.get("rate") or 0.0), 4)}
        state["active"].append(adj)
        _save(state)
    _assess(adj, entry)
    return adj


def _assess(adj: Dict[str, Any], entry: Dict[str, Any]) -> None:
    """The adjustment as a figure (the share of this lever's adjustments that were kept) and a thought."""
    try:
        from eli.cognition import epistemics
        from eli.cognition.scoreboard import entry_words
        from eli.cognition.thoughts import record
        prior, _n = epistemics.base_rate(ADJUSTMENT, class_=adj["lever"])
        prob = epistemics.assess(ADJUSTMENT, prior, request_id=adj["id"], class_=adj["lever"],
                                 features={"evidence_z": adj["evidence_z"]})
        why = [entry_words(entry)]
        if adj.get("instead"):
            why.append(f"{adj['instead'].get('what')} has done better")
        record("adjustment", f"{LEVER_WORDS[adj['lever']]} for {adj['label']}", why=why, chance=float(prob.p),
               decision="adjusted", reason=f"it did worse with the evidence to say so ({adj['evidence_z']:.1f} standard errors)",
               subject=adj["label"][:120], ref=adj["id"])
    except _FAILURES as failure:
        log.warning("an adjustment was made but not recorded as a figure: %s", failure)


def _after(adj: Dict[str, Any], now: float) -> Tuple[float, int]:
    """The part's outcomes since the change (for a hold, the offers of its kind since it ended)."""
    from eli.cognition.scoreboard import ACTION, outcomes_of
    lever = adj["lever"]
    if lever == "parser_first":
        rows = outcomes_of(ROUTE, "", since=float(adj["started"]), where={"adjusted": adj["id"]})
    elif lever == "alternative_first":
        rows = outcomes_of(ACTION, "", since=float(adj["started"]), where={"adjusted": adj["id"]})
    elif lever == "hold":
        rows = outcomes_of(INITIATIVE, adj["unit"], since=float(adj.get("released_at") or now))
    else:
        rows = outcomes_of(adj["kind"], adj["unit"], since=float(adj["started"]))
    return sum(float(r["y"]) for r in rows), len(rows)


def _settle(adj: Dict[str, Any], right: bool, how: str, now: float, after: Tuple[float, int]) -> None:
    try:
        from eli.cognition import epistemics
        from eli.cognition.thoughts import settle
        epistemics.settle(adj["id"], right, how=how, kind=ADJUSTMENT)
        settle(adj["id"], "right" if right else "wrong", how=how)
    except _FAILURES as failure:
        log.warning("an adjustment's outcome was not recorded as a figure: %s", failure)
    adj["outcome"] = "right" if right else "wrong"
    adj["how"] = how
    adj["settled_at"] = now
    adj["after"] = {"right": after[0], "n": after[1]}


def settle_due(*, now: Optional[float] = None, board: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Judge every adjustment that can be judged now, lift the kept ones whose window has passed and the
    holds whose half-life has, and return what changed (each with its words)."""
    now = time.time() if now is None else float(now)
    changed: List[Dict[str, Any]] = []
    with _lock:
        state = json.loads(json.dumps(load()))
        keep: List[Dict[str, Any]] = []
        for adj in state["active"]:
            done = _step(adj, now, board)
            if done:
                changed.append(adj)
            if adj.get("state") in ("active", "kept", "released"):
                keep.append(adj)
            else:
                state["settled"].append(adj)
        state["active"] = keep
        state["settled"] = [s for s in state["settled"] if now - float(s.get("settled_at") or s.get("started") or 0.0)
                            < _window_s()]
        if changed:
            _save(state)
    for adj in changed:
        adj["words"] = words_of(adj)
        _after_settled(adj)
    return changed


def _step(adj: Dict[str, Any], now: float, board: Optional[Dict[str, Any]]) -> bool:
    """Move one adjustment on if it can be: True when it changed."""
    lever, age = adj["lever"], now - float(adj["started"])
    if adj.get("state") == "kept":
        if age >= _window_s():
            adj["state"] = "lifted"
            adj["lifted_at"] = now
            return True
        return False
    if lever == "hold" and adj.get("state") == "active":
        if age >= _half_life_s():
            adj["state"] = "released"
            adj["released_at"] = now
            return True
        return False
    try:
        after = _after(adj, now)
    except _FAILURES as failure:
        log.debug("an adjustment could not be judged yet: %s", failure)
        return False
    if lever == "repair":
        return _step_repair(adj, now, board, after)
    before = adj.get("before") or {}
    verdict = judge((float(before.get("right") or 0.0), int(before.get("n") or 0)), after)
    if verdict is None:
        if age >= _window_s() and lever != "hold":
            adj["state"] = "lifted"
            adj["lifted_at"] = now
            return True
        return False
    _settle(adj, verdict, "kept" if verdict else "reverted", now, after)
    if verdict and lever != "hold":
        adj["state"] = "kept"
        adj["kept_at"] = now
    else:
        adj["state"] = "settled"
    return True


def _step_repair(adj: Dict[str, Any], now: float, board: Optional[Dict[str, Any]], after: Tuple[float, int]) -> bool:
    """A repair is done once the part, on outcomes after it opened, no longer does worse; closed as not
    repaired when the window passes without that."""
    a_right, a_n = after
    if a_n >= MIN_EACH_SIDE:
        try:
            from eli.cognition.scoreboard import find
            entry = find(adj["kind"], adj["unit"], board)
        except _FAILURES as failure:
            log.debug("the board is unreadable for a repair: %s", failure)
            entry = None
        before = adj.get("before") or {}
        recovered = (entry is None or not entry.get("requires_adjustment")) and judge(
            (float(before.get("right") or 0.0), int(before.get("n") or 0)), after) is not False
        if recovered:
            _settle(adj, True, "the part recovered", now, after)
            adj["state"] = "settled"
            return True
    if now - float(adj["started"]) >= _window_s():
        _settle(adj, False, "not repaired within the window", now, after)
        adj["state"] = "settled"
        return True
    return False


def _after_settled(adj: Dict[str, Any]) -> None:
    """What follows a settled repair: its goal closed with the reason, its failure row resolved."""
    if adj.get("lever") != "repair" or not adj.get("outcome"):
        return
    try:
        from eli.planning.conductor import close_repair
        close_repair(adj)
    except _FAILURES as failure:
        log.warning("a settled repair's goal was not closed: %s", failure)


def words_of(adj: Dict[str, Any]) -> str:
    """One adjustment and how it went, from its own figures."""
    when = time.strftime("%d %b", time.localtime(float(adj.get("started") or 0.0)))
    head = f"Since {when}, {LEVER_WORDS.get(adj.get('lever'), adj.get('lever'))} for {adj.get('label')}"
    before, after = adj.get("before") or {}, adj.get("after") or {}
    tally = ""
    if after.get("n"):
        tally = (f": right {int(round(float(after['right'])))} of {int(after['n'])} since, against "
                 f"{int(round(float(before.get('right') or 0)))} of {int(before.get('n') or 0)} before")
    state = adj.get("state")
    if adj.get("outcome") == "right":
        tail = "kept" if state == "kept" else ("done: the part recovered" if adj.get("lever") == "repair" else "it helped")
    elif adj.get("outcome") == "wrong":
        tail = "reverted" if adj.get("lever") != "repair" else "not repaired"
    elif state == "released":
        tail = "offering it again to see"
    elif state == "lifted":
        tail = "lifted, to test the part again on what it does now"
    else:
        tail = "still being judged"
    return f"{head}{tally} — {tail}."


def report(*, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Every adjustment in force or settled within the window, newest first, each with its words."""
    state = load()
    rows = list(state.get("active") or []) + list(state.get("settled") or [])
    rows.sort(key=lambda a: -float(a.get("started") or 0.0))
    return [{**a, "words": words_of(a)} for a in rows]


def note_lever_run(lever: str, unit: str, adj_id: str) -> None:
    """A lever fired on the turn path: kept as a thought so the change is visible where it acted."""
    try:
        from eli.cognition.thoughts import record
        record("adjustment", f"{LEVER_WORDS.get(lever, lever)} for {unit}", why=["an adjustment in force"],
               decision="adjusted", reason="the record showed it doing better", subject=unit[:120],
               ref=f"{adj_id}:run:{int(time.time() * 1000)}")
    except _FAILURES as failure:
        log.debug("a lever's run was not kept as a thought: %s", failure)


__all__ = ["LEVERS", "LEVER_WORDS", "begin", "in_force", "judge", "load", "note_lever_run", "recently_failed",
           "report", "settle_due", "words_of"]
