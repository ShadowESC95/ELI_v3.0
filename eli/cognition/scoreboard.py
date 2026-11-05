"""
eli/cognition/scoreboard.py
───────────────────────────
What works in ELI and what does not, ranked by what it costs, and what needs adjusting.

Every part of ELI that decides something leaves a figure that something later settles
(eli/cognition/epistemics.py: a reply, a claim about the user, a memory shown to a reply, the route a
sentence was read as, an act, a proposed fix, an offer made unasked, an adjustment) or a run whose
effect was checked (eli/runtime/action_outcomes.py). Each kind was calibrated as a whole. Nothing set
the parts of a kind beside each other, or a part beside its own past, so a rule that misread one
sentence in three, or an offer the user always turned down, went on as it was. This reads those
records as one board:

    unit                 one part that is right or wrong as a whole: a routing rule, the path a reply
                         took, where a claim came from, how a memory was found, an act's kind, a fix's
                         verdict, an offer's kind, an adjustment's lever, an action on a kind of target
    rate                 how often it proved right: its settled outcomes, each weighted by its recency
                         (half-life HALF_LIFE_DAYS, as action_outcomes weighs runs), shrunk toward its
                         kind's share by SHRINK_RUNS pseudo-outcomes
    worse / better       whether it proved right less (more) often than the rest of its kind: the
                         one-sided two-proportion test (epistemics.shortfall) at Z_SIG, with at least
                         MIN_EACH_SIDE settled outcomes on each side
    slipping             whether it proved right less often in the last half-life than before it: the
                         same test on its own outcomes
    harm                 the wrong outcomes it is expected to cause in a week at its rate and its pace:
                         the order in which parts are worth adjusting
    instead              what has done the same job better, where the record shows it: the command
                         parser where it read a rule's sentences otherwise, an action proven on the same
                         target, the best of the kind's other parts
    lever                what ELI can change about the part (eli/cognition/adjustments.py): take the
                         parser's reading first, run the proven action first, hold an unwelcome offer,
                         or open one repair for it; "" when the part's own machinery adapts already
                         (the reply gates, the acts' kinds, the claims' and recalls' calibration)
    requires_adjustment  worse than its peers or slipping, with the evidence to say so

Nothing here is a hand-set size: the window, half-life and shrinkage are action_outcomes', the test and
its minimum are epistemics'. Read in the background (the proactive daemon), kept CACHE_SECONDS.
"""
from __future__ import annotations

import math
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from eli.cognition.epistemics import (ACT, ADJUSTMENT, ANSWER, CLAIM, INITIATIVE, MIN_EACH_SIDE, PROPOSAL, RECALL,
                                      ROUTE, Z_SIG, settled, shortfall)
from eli.runtime.action_outcomes import CACHE_SECONDS, DAYS, HALF_LIFE_DAYS, SHRINK_RUNS
from eli.utils.log import get_logger

log = get_logger(__name__)

ACTION = "action"   # a run of an action on a kind of target (eli/runtime/action_outcomes.py)
KINDS = (ROUTE, ANSWER, CLAIM, RECALL, ACT, PROPOSAL, INITIATIVE, ADJUSTMENT)
# The stages whose reading is not a command rule's (engine._assess_route names the stage by the first
# part of matched_by): the command parser's reading is never preferred over these.
NOT_A_RULE = frozenset({"fallback", "phatic", "llm_intent", "chat", "parser", "unknown", ""})
_DAY = 86400.0
_READ_FAILURES = (sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError, RuntimeError)
_ORIGIN_WORDS = {"user": "from what you told me", "document": "from documents", "observed": "from what I observed",
                 "inferred": "that I inferred"}

_cache: Dict[str, Any] = {"at": 0.0, "board": None, "key": None}


def stage_of(via: str) -> str:
    """The stage a route came from, as the engine names it: the first part of its matched_by."""
    return re.split(r"[.>]", str(via or ""), maxsplit=1)[0]


def unit_of(kind: str, row: Dict[str, Any]) -> str:
    """Which part of ELI a settled figure belongs to."""
    features = row.get("features") or {}
    if kind == ROUTE:
        return str(features.get("via") or row.get("class") or "unknown")
    if kind == ANSWER:
        return str(features.get("reply_path") or row.get("class") or "reply")
    if kind == RECALL:
        if features.get("channel"):
            return str(features["channel"])
        return "more than one retriever" if features.get("agreement") else "one retriever"
    return str(row.get("class") or kind)


def label(kind: str, unit: str) -> str:
    """The part in words."""
    if kind == ROUTE:
        return f"the routing rule {unit}"
    if kind == ANSWER:
        return f"replies by the {unit.replace('_', ' ')} path"
    if kind == CLAIM:
        return f"claims about you {_ORIGIN_WORDS.get(unit, 'of origin ' + unit if unit else 'of no stated origin')}"
    if kind == RECALL:
        return f"memories found by {unit.replace('+', ' and ')}"
    if kind == ACT:
        return f"things I made of my own accord ({unit.replace('_', ' ')})"
    if kind == PROPOSAL:
        return f"fixes I proposed that were judged {unit or 'without a verdict'}"
    if kind == INITIATIVE:
        return f"my offers of {unit.replace('_', ' ')}"
    if kind == ADJUSTMENT:
        return f"my adjustments by {unit.replace('_', ' ')}"
    if kind == ACTION:
        from eli.runtime.action_outcomes import plain
        action, _, target = unit.partition("|")
        return f"{plain(action)} for {target}" if target else plain(action)
    return unit


def weight(ts: float, now: float) -> float:
    """A settled outcome's weight by its age: halved every HALF_LIFE_DAYS, as a run's is."""
    return 0.5 ** (max(0.0, float(now) - float(ts or 0.0)) / _DAY / HALF_LIFE_DAYS)


def _rows(days: float, db_path) -> Dict[str, List[Dict[str, Any]]]:
    """Every settled outcome of the window by kind, each with the unit it belongs to."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for kind in KINDS:
        try:
            got = settled(kind, days=days, db_path=db_path)
        except _READ_FAILURES as failure:
            log.debug("figures of kind %s unreadable: %s", kind, failure)
            got = []
        out[kind] = [{**r, "unit": unit_of(kind, r)} for r in got]
    try:
        from eli.runtime.action_outcomes import _runs
        runs = _runs(days=days, db_path=db_path)
    except _READ_FAILURES as failure:
        log.debug("action runs unreadable: %s", failure)
        runs = []
    out[ACTION] = [{"ts": float(r.get("ts") or 0.0), "y": 1.0 if r.get("worked") else 0.0,
                    "unit": f"{r.get('action')}|{r.get('class') or ''}" if r.get("class") else str(r.get("action")),
                    "class": str(r.get("class") or ""), "request_id": str(r.get("id") or ""),
                    "how": str(r.get("cause") or ""),
                    "features": {"action": str(r.get("action") or ""), "subject": str(r.get("subject") or ""),
                                 "args": dict(r.get("args") or {}), "cause": str(r.get("cause") or ""),
                                 "adjusted": str((r.get("args") or {}).get("_adjusted") or "")}}
                   for r in runs]
    return out


def _count(rows: List[Dict[str, Any]]) -> Tuple[float, int]:
    return sum(float(r["y"]) for r in rows), len(rows)


def stats(rows: List[Dict[str, Any]], now: float, prior: float) -> Dict[str, Any]:
    """A part's figures from its settled outcomes: count, right, the recency-weighted rate shrunk toward
    `prior`, the pace (outcomes a week, from the weighted count: a steady pace of r a day sums to
    r·H/ln 2 at half-life H), and the expected wrong outcomes a week at that rate and pace."""
    right, n = _count(rows)
    w = sum(weight(r["ts"], now) for r in rows)
    w_right = sum(weight(r["ts"], now) * float(r["y"]) for r in rows)
    rate = (w_right + prior * SHRINK_RUNS) / (w + SHRINK_RUNS)
    per_week = w * math.log(2.0) / HALF_LIFE_DAYS * 7.0
    return {"n": n, "right": right, "rate": rate, "per_week": per_week, "harm": (1.0 - rate) * per_week}


def compare(rows: List[Dict[str, Any]], peer_right: float, peer_n: int, now: float) -> Dict[str, Any]:
    """The part beside the rest of its kind and beside its own past: the two statistics, and what they
    show at Z_SIG with MIN_EACH_SIDE on each side (None where a side is too thin to say)."""
    right, n = _count(rows)
    z_peers = shortfall(right, n, peer_right, peer_n) if n >= MIN_EACH_SIDE and peer_n >= MIN_EACH_SIDE else None
    split = now - HALF_LIFE_DAYS * _DAY
    recent = [r for r in rows if float(r["ts"]) >= split]
    before = [r for r in rows if float(r["ts"]) < split]
    (r_right, r_n), (b_right, b_n) = _count(recent), _count(before)
    z_slip = shortfall(r_right, r_n, b_right, b_n) if r_n >= MIN_EACH_SIDE and b_n >= MIN_EACH_SIDE else None
    worse = z_peers is not None and z_peers >= Z_SIG
    slipping = z_slip is not None and z_slip >= Z_SIG
    triggered = [z for z, hit in ((z_peers, worse), (z_slip, slipping)) if hit and z is not None]
    return {"z_peers": z_peers, "z_slip": z_slip, "worse": worse, "slipping": slipping,
            "better": z_peers is not None and -z_peers >= Z_SIG, "requires_adjustment": worse or slipping,
            "evidence_z": max(triggered) if triggered else 0.0,
            "recent": {"n": r_n, "right": r_right}, "before": {"n": b_n, "right": b_right}}


def _parser_instead(entry: Dict[str, Any], route_rows: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """For a routing rule doing worse: whether the command parser did better where the two read a
    sentence differently. Its accuracy there is unseen (only the rule's reading ran), so it is taken
    as the parser's own accuracy where it routed; the adjustment that follows tests it on the turns."""
    if stage_of(entry["unit"]) in NOT_A_RULE:
        return None, "repair"
    split = [r for r in route_rows if r["unit"] == entry["unit"]
             and str((r.get("features") or {}).get("parser_action") or "") not in
             ("", "CHAT", str((r.get("features") or {}).get("action") or ""))]
    parsed = [r for r in route_rows if str(r.get("class") or "") == "parser"]
    (s_right, s_n), (p_right, p_n) = _count(split), _count(parsed)
    if s_n >= MIN_EACH_SIDE and p_n >= MIN_EACH_SIDE:
        z = shortfall(s_right, s_n, p_right, p_n)
        if z >= Z_SIG:
            return ({"what": "the command parser's reading", "rate": (p_right + 1.0) / (p_n + 2.0), "n": p_n,
                     "where_split": {"n": s_n, "right": s_right}, "z": z}, "parser_first")
    return None, "repair"


def _action_instead(entry: Dict[str, Any], action_rows: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """For an action doing worse on a kind of target: an action proven on that very target after it
    failed, likelier than it; run first only when it is safe to run unasked."""
    from eli.runtime.action_outcomes import _safe_on_its_own, alternatives, plain
    failed = [r for r in action_rows if r["unit"] == entry["unit"] and not r["y"]]
    if not failed:
        return None, "repair"
    newest = max(failed, key=lambda r: float(r["ts"]))
    action = str((newest.get("features") or {}).get("action") or entry["unit"].partition("|")[0])
    args = dict((newest.get("features") or {}).get("args") or {})
    try:
        others = alternatives(action, args)
    except _READ_FAILURES as failure:
        log.debug("alternatives unreadable: %s", failure)
        return None, "repair"
    for other in others:
        if other.get("same_target") and float(other.get("p") or 0.0) > float(entry["rate"]):
            return ({"what": plain(other["action"]), "action": other["action"], "args": dict(other.get("args") or {}),
                     "rate": float(other["p"]), "n": int(other.get("n") or 0), "for": action},
                    "alternative_first" if _safe_on_its_own(other["action"]) else "repair")
    return None, "repair"


def _best_peer(entry: Dict[str, Any], peers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    better = [u for u in peers if u is not entry and u["n"] >= MIN_EACH_SIDE and u["rate"] > entry["rate"]]
    if not better:
        return None
    best = max(better, key=lambda u: u["rate"])
    return {"what": best["label"], "unit": best["unit"], "rate": best["rate"], "n": best["n"]}


def _instead_and_lever(entry: Dict[str, Any], rows: Dict[str, List[Dict[str, Any]]],
                       peers: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    kind = entry["kind"]
    if kind == ROUTE:
        return _parser_instead(entry, rows.get(ROUTE) or [])
    if kind == ACTION:
        return _action_instead(entry, rows.get(ACTION) or [])
    if kind == INITIATIVE:
        return _best_peer(entry, peers), "hold"
    return _best_peer(entry, peers), ""


def board(days: float = DAYS, *, db_path=None, now: Optional[float] = None, fresh: bool = False) -> Dict[str, Any]:
    """Every part with outcomes in the window, those requiring adjustment first and the costliest of
    them first, then the rest by harm: {"at", "days", "units": [...], "kinds": {kind: {"n", "right",
    "rate"}}}. Kept CACHE_SECONDS unless `fresh`."""
    now = time.time() if now is None else float(now)
    key = (str(db_path or ""), float(days))
    if not fresh and _cache["board"] is not None and _cache["key"] == key and now - float(_cache["at"]) < CACHE_SECONDS:
        return _cache["board"]
    rows = _rows(days, db_path)
    units: List[Dict[str, Any]] = []
    kinds: Dict[str, Dict[str, Any]] = {}
    for kind, krows in rows.items():
        if not krows:
            continue
        k_right, k_n = _count(krows)
        prior = (k_right + 1.0) / (k_n + 2.0)
        kinds[kind] = {"n": k_n, "right": k_right, "rate": prior}
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for r in krows:
            grouped.setdefault(str(r["unit"]), []).append(r)
        kind_units = []
        for unit, urows in grouped.items():
            u_right, u_n = _count(urows)
            entry = {"kind": kind, "unit": unit, "label": label(kind, unit), **stats(urows, now, prior),
                     **compare(urows, k_right - u_right, k_n - u_n, now),
                     "peers_rate": (k_right - u_right + 1.0) / (k_n - u_n + 2.0),
                     "wrong_refs": [str(r.get("request_id") or "") for r in sorted(urows, key=lambda r: -float(r["ts"]))
                                    if not r["y"] and r.get("request_id")],
                     "instead": None, "lever": ""}
            kind_units.append(entry)
        for entry in kind_units:
            if entry["requires_adjustment"]:
                entry["instead"], entry["lever"] = _instead_and_lever(entry, rows, kind_units)
        units.extend(kind_units)
    units.sort(key=lambda e: (not e["requires_adjustment"], -float(e["harm"]), -float(e["evidence_z"])))
    result = {"at": now, "days": float(days), "units": units, "kinds": kinds}
    _cache.update({"at": now, "board": result, "key": key})
    return result


def forget() -> None:
    """Read the records again on the next question."""
    _cache["at"] = 0.0
    _cache["board"] = None


def flagged(result: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The parts that require adjustment, the costliest first."""
    result = result if result is not None else board()
    return [u for u in result.get("units") or [] if u.get("requires_adjustment")]


def find(kind: str, unit: str, result: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """One part's entry, or None when it has no outcomes in the window."""
    result = result if result is not None else board()
    return next((u for u in result.get("units") or [] if u["kind"] == kind and u["unit"] == unit), None)


def outcomes_of(kind: str, unit: str, *, since: float = 0.0, until: Optional[float] = None, days: float = DAYS,
                db_path=None, where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The settled outcomes of one part between two times (with `where`, only those whose features hold
    these values): what an adjustment is judged on."""
    rows = _rows(days, db_path).get(kind) or []
    out = []
    for r in rows:
        ts = float(r["ts"])
        if r["unit"] != unit and not where:
            continue
        if ts < float(since) or (until is not None and ts >= float(until)):
            continue
        if where and not all(str((r.get("features") or {}).get(k) or "") == str(v) for k, v in where.items()):
            continue
        out.append(r)
    return out


def examples(entry: Dict[str, Any], *, db_path=None) -> List[str]:
    """What the part did when it was wrong, newest first, from the thoughts kept of those decisions (a
    route's sentence, a reply's question, an action's subject): as many as the test rests on."""
    refs = list(entry.get("wrong_refs") or [])[:MIN_EACH_SIDE]
    if entry.get("kind") == ACTION:
        return []
    try:
        from eli.cognition.thoughts import by_refs
        found = by_refs(refs, db_path=db_path)
    except _READ_FAILURES as failure:
        log.debug("thoughts unreadable for the board: %s", failure)
        return []
    said = []
    for ref in refs:
        thought = found.get(ref)
        if thought and str(thought.get("subject") or "").strip():
            said.append(str(thought["subject"]).strip())
    return said


def _share(right: float, n: int) -> str:
    return f"{int(round(right))} of {n}"


def entry_words(entry: Dict[str, Any]) -> str:
    """One part's standing in a sentence, from its own figures."""
    head = (f"{entry['label'][:1].upper()}{entry['label'][1:]}: right {_share(entry['right'], entry['n'])} "
            f"({entry['rate']:.0%} lately)")
    why = []
    if entry.get("worse"):
        why.append(f"below the rest of its kind ({entry['peers_rate']:.0%})")
    if entry.get("slipping"):
        rec, bef = entry["recent"], entry["before"]
        why.append(f"slipping: {_share(rec['right'], rec['n'])} in the last {HALF_LIFE_DAYS:g} days against "
                   f"{_share(bef['right'], bef['n'])} before")
    if entry.get("better"):
        why.append(f"above the rest of its kind ({entry['peers_rate']:.0%})")
    text = head + ("; " + "; ".join(why) if why else "")
    instead = entry.get("instead")
    if instead:
        text += f". {str(instead['what'])[:1].upper()}{str(instead['what'])[1:]} has done better ({float(instead['rate']):.0%})"
    return text + "."


def words(result: Optional[Dict[str, Any]] = None, *, adjustments: Optional[List[Dict[str, Any]]] = None) -> str:
    """The board in words: what needs adjusting and why, what does best, and what has been adjusted and
    how it went. "" when nothing has been settled in the window."""
    result = result if result is not None else board()
    units = result.get("units") or []
    if not units:
        return ""
    lines: List[str] = []
    needing = [u for u in units if u.get("requires_adjustment")]
    if needing:
        lines.append("Needs adjusting, the costliest first:")
        lines.extend(f"- {entry_words(u)}" for u in needing)
    best = sorted((u for u in units if u.get("better")), key=lambda u: -float(u["rate"]))
    if best:
        lines.append("Doing best:")
        lines.extend(f"- {entry_words(u)}" for u in best)
    if not needing and not best:
        total = sum(int(k["n"]) for k in (result.get("kinds") or {}).values())
        lines.append(f"Nothing stands out yet: {total} outcomes settled in the last {float(result.get('days') or DAYS):g} "
                     f"days, and no part is shown by them to do better or worse than the rest of its kind.")
    for adj in adjustments or []:
        lines.append(f"- {adj.get('words') or ''}".rstrip())
    return "\n".join(line for line in lines if line.strip() != "-")


__all__ = ["ACTION", "KINDS", "NOT_A_RULE", "board", "compare", "entry_words", "examples", "find", "flagged",
           "forget", "label", "outcomes_of", "stage_of", "stats", "unit_of", "weight", "words"]
