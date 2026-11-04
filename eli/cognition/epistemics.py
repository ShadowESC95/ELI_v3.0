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
    calibrate(kind, score, class_, features)   the chance for a raw score of this kind, from the
                         outcomes recorded so far: a logistic fit on the score's logit and on the
                         model's own confidence in the tokens of the reply (FEATURES, from
                         gguf_inference._TokenConfidence), with a prior that the score already is
                         the chance and the other figures count for nothing, so with nothing
                         recorded the chance is the score, and each figure weighs only what the
                         outcomes have shown it to tell
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
CLAIM = "claim"                   # a claim about the user that a reply rested on (eli/memory/claims.py)
# each kind's pair of ledger events: the figure given, and what became of it
EVENTS = {ANSWER: (ASSESSED, OUTCOME), CLAIM: ("claim_assessed", "claim_outcome")}
DAYS = 90.0                       # how far back outcomes count toward a calibration
# A score of exactly 0 or 1 has no finite logit; it is read as a thousandth short of the edge.
EDGE = 1e-3
# Reliability is read in ten equal bands of the chance, the usual way (Naeini et al., 2015).
BANDS = 10
# The prior on the fit: the score's weight about one, every other weight and the shift about zero,
# each standard normal, so the fit stays at "the score is the chance" until outcomes outweigh one
# observation's worth of doubt, and a figure that has not told right from wrong weighs nothing.
PRIOR_SD = 1.0
# The model's own confidence in the tokens of the reply (eli/cognition/gguf_inference.py,
# _TokenConfidence), read beside the score: the mean and least log-probability of the chosen
# tokens, the mean entropy of the distributions they came from, and the share chosen at under an
# even chance. A reply the model was unsure of token by token is more often wrong, whatever its
# figure. Missing from an outcome, a figure counts as 0, the prior's own value.
FEATURES = ("mean_logprob", "min_logprob", "mean_entropy", "low_share")


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
    slope: float = 1.0                        # the fit: p = sigmoid(slope * logit(score) + weights · features + shift)
    shift: float = 0.0
    weights: Dict[str, float] = field(default_factory=dict)   # what each of FEATURES weighs in the fit

    def as_dict(self) -> Dict[str, Any]:
        return {"p": round(self.p, 4), "kind": self.kind, "class": self.class_, "score": round(self.score, 4),
                "n": self.n, "ece": None if self.ece is None else round(self.ece, 4),
                "slope": round(self.slope, 4), "shift": round(self.shift, 4),
                "weights": {k: round(v, 4) for k, v in self.weights.items()}, "features": dict(self.features)}


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

def _row(score: float, features: Optional[Dict[str, Any]]) -> List[float]:
    """The fit's inputs for one reply: the score's logit, then each of FEATURES (0 when not known)."""
    out = [logit(float(score))]
    for key in FEATURES:
        try:
            out.append(float((features or {}).get(key) or 0.0))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def fit_rows(rows: Sequence[Tuple[float, float, Optional[Dict[str, Any]]]], *, prior_sd: float = PRIOR_SD,
             steps: int = 25) -> Tuple[List[float], float]:
    """The weights and shift that most probably gave these (score, outcome, features) rows, where the
    chance is sigmoid(weights · [logit(score), *FEATURES] + shift): Newton's method on the penalised
    log-loss, the prior standard normal about 1 for the score's weight and about 0 for every other
    weight and the shift. With no rows, ([1, 0, ...], 0): the score itself."""
    prior = [1.0] + [0.0] * len(FEATURES)
    if not rows:
        return prior, 0.0
    import numpy as np
    X = np.array([_row(s, f) + [1.0] for s, _, f in rows], dtype=float)
    Y = np.array([1.0 if float(y) >= 0.5 else 0.0 for _, y, _ in rows], dtype=float)
    mu = np.array(prior + [0.0], dtype=float)
    theta = mu.copy()
    tau = 1.0 / (prior_sd * prior_sd)
    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-(X @ theta)))
        gradient = X.T @ (p - Y) + tau * (theta - mu)
        hessian = (X * (p * (1.0 - p))[:, None]).T @ X + tau * np.eye(len(theta))
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            break
        theta = theta - step
        if float(np.max(np.abs(step))) < 1e-9:
            break
    return [float(v) for v in theta[:-1]], float(theta[-1])


def fit(pairs: Sequence[Tuple[float, float]], *, prior_sd: float = PRIOR_SD, steps: int = 25) -> Tuple[float, float]:
    """Platt scaling with a prior, on the score alone: (slope, shift) for sigmoid(slope * logit(score)
    + shift). The same fit as fit_rows with no features; (1, 0) with no pairs."""
    weights, shift = fit_rows([(s, y, None) for s, y in pairs], prior_sd=prior_sd, steps=steps)
    return weights[0], shift


def apply_rows(score: float, features: Optional[Dict[str, Any]], weights: Sequence[float], shift: float) -> float:
    x = _row(score, features)
    return sigmoid(sum(float(w) * v for w, v in zip(weights, x)) + shift)


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
    assessed_type, outcome_type = EVENTS.get(kind, (ASSESSED, OUTCOME))
    rows = _ledger().assessment_pairs(kind, since=time.time() - float(days) * 86400.0, assessed_type=assessed_type,
                                      outcome_type=outcome_type, db_path=db_path)
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
    weights, shift = fit_rows(pairs)
    error = ece([(apply_rows(s, f, weights, shift), y) for s, y, f in pairs]) if pairs else None
    return Probability(p=apply_rows(score, features, weights, shift), kind=kind, score=score, class_=fitted_for,
                       features=dict(features or {}), n=len(pairs), ece=error, slope=weights[0], shift=shift,
                       weights=dict(zip(("score",) + FEATURES, weights)))


def assess(kind: str, score: float, *, request_id: str, features: Optional[Dict[str, Any]] = None,
           class_: str = "", session_id: str = "", user_id: str = "", db_path=None) -> Probability:
    """Make the Probability for this turn and put it in the ledger, so the next turn can settle it."""
    prob = calibrate(kind, score, class_=class_, features=features, db_path=db_path)
    if request_id:
        try:
            _ledger().record_event(
                EVENTS.get(kind, (ASSESSED, OUTCOME))[0], source="epistemics", action=kind.upper(), subject=str(request_id),
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
            EVENTS.get(kind, (ASSESSED, OUTCOME))[1], source="epistemics", action=kind.upper(), subject=str(request_id),
            payload={"kind": kind, "how": how}, outcome="ok" if right else "failed",
            reusable=False, request_id=str(request_id), db_path=db_path)
        return True
    except Exception as failure:   # noqa: BLE001 - a ledger that cannot be written leaves the outcome unrecorded
        log.warning("an answer's outcome was not recorded: %s", failure)
        return False


def claim_request_id(claim_id: Any, request_id: str) -> str:
    """The id under which a claim's figure is kept for one turn: the claim's and the turn's."""
    return f"claim:{int(claim_id)}:{request_id}"


def settle_open(kind: str, subject_prefix: str, right: bool, *, how: str = "", db_path=None) -> int:
    """Settle every figure of this kind whose id starts with `subject_prefix` that nothing has
    settled yet (a claim's, when the user settles the claim itself). How many were settled."""
    assessed_type, outcome_type = EVENTS.get(kind, (ASSESSED, OUTCOME))
    try:
        open_ids = _ledger().open_assessments(kind, assessed_type=assessed_type, outcome_type=outcome_type,
                                              subject_prefix=subject_prefix, db_path=db_path)
    except (OSError, ValueError, AttributeError) as failure:
        log.debug("open figures unreadable: %s", failure)
        return 0
    return sum(1 for rid in open_ids if settle(rid, right, how=how, kind=kind, db_path=db_path))


def report(kind: str = ANSWER, *, days: float = DAYS, db_path=None) -> Dict[str, Any]:
    """How good the figures of this kind have been: count, Brier score, calibration error, the
    reliability by band, and the fit now in force."""
    rows = outcomes(kind, days=days, db_path=db_path)
    pairs = [(s, y) for s, y, _ in rows]
    weights, shift = fit_rows(rows)
    calibrated = [(apply_rows(s, f, weights, shift), y) for s, y, f in rows]
    return {"kind": kind, "n": len(pairs), "brier": brier(pairs), "brier_calibrated": brier(calibrated),
            "ece": ece(pairs), "ece_calibrated": ece(calibrated), "bands": reliability(pairs),
            "slope": weights[0], "shift": shift, "weights": dict(zip(("score",) + FEATURES, weights))}


__all__ = ["ANSWER", "ASSESSED", "CLAIM", "EVENTS", "FEATURES", "OUTCOME", "Probability", "apply", "apply_rows",
           "assess", "brier", "calibrate", "claim_request_id", "ece", "entropy", "fit", "fit_rows", "logit", "outcomes",
           "reliability", "report", "settle", "settle_open", "sigmoid"]
