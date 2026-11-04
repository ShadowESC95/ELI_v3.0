"""
eli/cognition/epistemics.py
───────────────────────────
What ELI's numbers mean, and whether they are borne out.

ELI keeps many quantities in 0..1 called confidence that mean different things: how surely a
sentence was routed, how much an agent brought back, how good a reply looks, how far several
samples agreed. None of them is the chance of being right until it has been checked against
what happened. This is where that check lives, so a figure ELI gives for an answer can mean
"of the answers I gave this figure, this share proved right".

    Probability          a calibrated chance with its basis: the kind of outcome it is about,
                         the raw score it came from, the features behind it, how many outcomes
                         the calibration rests on and how far it has been from the truth
    calibrate(kind, score, class_)   the chance for a raw score of this kind, from the outcomes
                         recorded so far: Platt scaling (a logistic fit on the score's logit) with
                         a prior that the score already is the chance, so with nothing recorded
                         the chance is the score, and it moves only as outcomes say it should
    assess(kind, score, features, request_id)   make and record a Probability for a turn
    settle(request_id, right, how)   record what happened to it
    report(kind, days)   Brier score, expected calibration error and reliability by band
    brier(pairs), ece(pairs), reliability(pairs, edges)   the scoring rules, one owner
    entropy(counts)      the normalised entropy of a vote (self-consistency keeps it with the
                         winner's share: a 3-2 split and five different answers are not alike)

Outcomes are kept in the evidence ledger (eli/runtime/evidence_ledger.py), the tamper-evident
record of what ELI did: an `answer_assessed` row when the figure is given, an `answer_outcome`
row when the next turn says it was right or wrong. Nothing here phrases a reply.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

ANSWER = "answer"                 # a reply to the user: right unless the user says otherwise
ASSESSED = "answer_assessed"      # ledger event: a figure was given
OUTCOME = "answer_outcome"        # ledger event: what the next turn said of it
DAYS = 90.0                       # how far back outcomes count toward a calibration
# A score of exactly 0 or 1 has no finite logit; it is read as a thousandth short of the edge.
EDGE = 1e-3
# Reliability is read in ten equal bands of the chance, the usual way (Naeini et al., 2015).
BANDS = 10
# The prior on the fit: the slope's departure from one and the shift are each standard normal, so
# the fit stays at "the score is the chance" until outcomes outweigh one observation's worth of doubt.
PRIOR_SD = 1.0


@dataclass
class Probability:
    """A chance ELI can stand behind, with what it rests on."""
    p: float
    kind: str
    score: float                              # the raw figure it was read from
    class_: str = ""                          # the task class the calibration was fitted for
    features: Dict[str, Any] = field(default_factory=dict)
    n: int = 0                                # outcomes the calibration rests on
    ece: Optional[float] = None               # its expected calibration error on them, when any
    slope: float = 1.0                        # the fit: p = sigmoid(slope * logit(score) + shift)
    shift: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {"p": round(self.p, 4), "kind": self.kind, "class": self.class_, "score": round(self.score, 4),
                "n": self.n, "ece": None if self.ece is None else round(self.ece, 4),
                "slope": round(self.slope, 4), "shift": round(self.shift, 4), "features": dict(self.features)}


# ── the scoring rules ─────────────────────────────────────────────────────────────────────────────

def _clip(p: float) -> float:
    return min(1.0 - EDGE, max(EDGE, float(p)))


def logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1.0 - p))


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def brier(pairs: Iterable[Tuple[float, float]]) -> Optional[float]:
    """Mean squared distance between the chance given and what happened (0 best, 1 worst)."""
    pairs = list(pairs)
    if not pairs:
        return None
    return sum((float(p) - float(y)) ** 2 for p, y in pairs) / len(pairs)


def reliability(pairs: Iterable[Tuple[float, float]], edges: Optional[Sequence[float]] = None) -> List[Dict[str, Any]]:
    """By band of the chance given: how many, the mean chance given and the share that proved right.
    `edges` are the band limits (the last band includes 1.0); ten equal bands when none are given."""
    pairs = list(pairs)
    if edges is None:
        edges = [k / BANDS for k in range(BANDS + 1)]
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        last = hi >= edges[-1]
        sel = [(p, y) for p, y in pairs if lo <= float(p) < hi or (last and float(p) == hi)]
        if sel:
            out.append({"range": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": len(sel),
                        "predicted": round(sum(float(p) for p, _ in sel) / len(sel), 3),
                        "observed": round(sum(float(y) for _, y in sel) / len(sel), 3)})
    return out


def ece(pairs: Iterable[Tuple[float, float]], edges: Optional[Sequence[float]] = None) -> Optional[float]:
    """Expected calibration error: the gap between the chance given and the share that proved right,
    band by band, weighted by how many fell in each band."""
    pairs = list(pairs)
    if not pairs:
        return None
    return sum(b["n"] * abs(b["observed"] - b["predicted"]) for b in reliability(pairs, edges)) / len(pairs)


def entropy(counts: Iterable[int]) -> float:
    """How spread a vote is, 0 (all agree) to 1 (every vote different), from the counts per answer."""
    counts = [int(c) for c in counts if int(c) > 0]
    total = sum(counts)
    if total <= 1:
        return 0.0
    h = -sum((c / total) * math.log(c / total) for c in counts)
    return max(0.0, min(1.0, h / math.log(total)))


# ── the fit ───────────────────────────────────────────────────────────────────────────────────────

def fit(pairs: Sequence[Tuple[float, float]], *, prior_sd: float = PRIOR_SD, steps: int = 25) -> Tuple[float, float]:
    """Platt scaling with a prior: (slope, shift) that most probably gave these (score, outcome) pairs,
    where the chance is sigmoid(slope * logit(score) + shift). Newton's method on the penalised
    log-loss; with no pairs it is (1, 0), the score itself."""
    a, b = 1.0, 0.0
    if not pairs:
        return a, b
    xs = [logit(float(s)) for s, _ in pairs]
    ys = [1.0 if float(y) >= 0.5 else 0.0 for _, y in pairs]
    tau = 1.0 / (prior_sd * prior_sd)
    for _ in range(steps):
        ga = tau * (a - 1.0)
        gb = tau * b
        haa = tau
        hab = 0.0
        hbb = tau
        for x, y in zip(xs, ys):
            p = sigmoid(a * x + b)
            w = p * (1.0 - p)
            ga += (p - y) * x
            gb += (p - y)
            haa += w * x * x
            hab += w * x
            hbb += w
        det = haa * hbb - hab * hab
        if det <= 0:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a -= da
        b -= db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break
    return a, b


def apply(score: float, slope: float, shift: float) -> float:
    return sigmoid(slope * logit(score) + shift)


# ── outcomes, in the ledger ───────────────────────────────────────────────────────────────────────

def _ledger():
    from eli.runtime import evidence_ledger
    return evidence_ledger


def outcomes(kind: str = ANSWER, *, class_: str = "", days: float = DAYS,
             db_path=None) -> List[Tuple[float, float, Dict[str, Any]]]:
    """(score, outcome, features) for every figure of this kind (and class, when given) that the
    next turn settled within `days`."""
    rows = _ledger().assessment_pairs(kind, since=time.time() - float(days) * 86400.0, db_path=db_path)
    out = []
    for assessed, outcome in rows:
        payload = assessed.get("payload") or {}
        if class_ and str(payload.get("class") or "") != class_:
            continue
        try:
            score = float(payload.get("score"))
        except (TypeError, ValueError):
            continue
        out.append((score, 1.0 if outcome.get("outcome") == "ok" else 0.0, dict(payload.get("features") or {})))
    return out


def calibrate(kind: str, score: float, *, class_: str = "", features: Optional[Dict[str, Any]] = None,
              days: float = DAYS, db_path=None) -> Probability:
    """The chance for a raw score of this kind: fitted on the outcomes of its class when that class
    has any, else of the kind as a whole; the score itself until outcomes say otherwise."""
    score = float(score)
    try:
        pairs = outcomes(kind, class_=class_, days=days, db_path=db_path) if class_ else []
        fitted_for = class_ if pairs else ""
        if not pairs:
            pairs = outcomes(kind, days=days, db_path=db_path)
    except Exception as failure:   # noqa: BLE001 - the ledger unreadable: the score stands as the chance
        log.debug("outcomes unavailable: %s", failure)
        pairs, fitted_for = [], ""
    scored = [(s, y) for s, y, _ in pairs]
    slope, shift = fit(scored)
    error = ece([(apply(s, slope, shift), y) for s, y in scored]) if scored else None
    return Probability(p=apply(score, slope, shift), kind=kind, score=score, class_=fitted_for,
                       features=dict(features or {}), n=len(scored), ece=error, slope=slope, shift=shift)


def assess(kind: str, score: float, *, request_id: str, features: Optional[Dict[str, Any]] = None,
           class_: str = "", session_id: str = "", user_id: str = "", db_path=None) -> Probability:
    """Make the Probability for this turn and put it in the ledger, so the next turn can settle it."""
    prob = calibrate(kind, score, class_=class_, features=features, db_path=db_path)
    if request_id:
        try:
            _ledger().record_event(
                ASSESSED, source="epistemics", action=kind.upper(), subject=str(request_id),
                payload={"kind": kind, "class": class_, "score": round(float(score), 4), "p": round(prob.p, 4),
                         "n": prob.n, "features": dict(features or {})},
                confidence=prob.p, reusable=False, request_id=str(request_id),
                session_id=session_id, user_id=user_id, db_path=db_path)
        except Exception as failure:   # noqa: BLE001 - a ledger that cannot be written leaves the figure unrecorded
            log.warning("an answer's figure was not recorded: %s", failure)
    return prob


def settle(request_id: str, right: bool, *, how: str = "", kind: str = ANSWER, db_path=None) -> bool:
    """What happened to the figure given for this turn. False when it could not be recorded."""
    if not request_id:
        return False
    try:
        _ledger().record_event(
            OUTCOME, source="epistemics", action=kind.upper(), subject=str(request_id),
            payload={"kind": kind, "how": how}, outcome="ok" if right else "failed",
            reusable=False, request_id=str(request_id), db_path=db_path)
        return True
    except Exception as failure:   # noqa: BLE001 - a ledger that cannot be written leaves the outcome unrecorded
        log.warning("an answer's outcome was not recorded: %s", failure)
        return False


def report(kind: str = ANSWER, *, days: float = DAYS, db_path=None) -> Dict[str, Any]:
    """How good the figures of this kind have been: count, Brier score, calibration error, the
    reliability by band, and the fit now in force."""
    pairs = [(s, y) for s, y, _ in outcomes(kind, days=days, db_path=db_path)]
    slope, shift = fit(pairs)
    calibrated = [(apply(s, slope, shift), y) for s, y in pairs]
    return {"kind": kind, "n": len(pairs), "brier": brier(pairs), "brier_calibrated": brier(calibrated),
            "ece": ece(pairs), "ece_calibrated": ece(calibrated), "bands": reliability(pairs),
            "slope": slope, "shift": shift}


__all__ = ["ANSWER", "ASSESSED", "OUTCOME", "Probability", "apply", "assess", "brier", "calibrate", "ece",
           "entropy", "fit", "logit", "outcomes", "reliability", "report", "settle", "sigmoid"]
