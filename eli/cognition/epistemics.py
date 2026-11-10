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
                         the calibration rests on and how far the chances given have been from
                         the truth
    calibrate(kind, score, class_, features)   the chance for a raw score of this kind, from the
                         outcomes recorded so far: a logistic fit on the score's logit and on the
                         kind's features (FEATURES_BY_KIND), each standardised over the outcomes
                         and with a sign of its own for being missing, so a reply with no token
                         figures is not read as the surest reply there could be. The prior says
                         the score already is the chance and the other figures count for nothing,
                         so with nothing recorded the chance is the score, and each figure weighs
                         only what the outcomes have shown it to tell. A class's fit is drawn
                         toward its kind's (partial pooling): one outcome of a class does not throw
                         away the hundreds of its kind
    assess(kind, score, features, request_id)   make and record a Probability for a turn
    settle(request_id, right, how)   record what happened to it: once; a later, different verdict
                         takes the place of the earlier one (an act that stood and was then undone)
    assess_many(kind, items, request_id)   the same for several figures of one turn, fitted once
    settle_open(kind, prefix, right)   settle every open figure under an id prefix
    base_rate(kind, class_)   the share of figures of a kind that proved right (Beta(1,1))
    worse_below(kind, feature, value)   whether figures below a level of one of their features
                         proved right less often than those at or above it: what a gate asks
                         before it holds a reply to a line nothing has checked
    worse_than(kind, where, than)   the same test between two sets of figures: a router rule's
                         readings against the resolver's, a part against its peers
    compare(a, b)        the one test both use, on two (right, settled) counts: True shown worse,
                         False shown no worse (non-inferiority), None neither
    gate_open(kind, feature, value)   a gate's decision below its line: open once shown no worse,
                         closed once shown worse, and while neither is shown, opened by Thompson
                         sampling over the two Beta posteriors, so a gate judged on its own path
                         can learn what that path is worth instead of never trying it
    rank(kind)           the parts of a kind (a router rule, a reply path, a claim's origin) whose
                         figures proved right less often than their peers', flagged at a false
                         discovery rate (Benjamini-Hochberg) set from the same significance point
    standing(kind, unit) a part's settled count and its rate, shrunk toward its kind's by the
                         prior strength the parts themselves show (empirical Bayes)
    weights_for(kind, class_)   the fit in force, for a ranking to apply by itself (apply_fit)
    report(kind, days)   the share right, Brier score, expected calibration error and reliability
                         by band of the chances as they were given (prequential: each scored
                         against what happened after it was given), beside the raw scores' and the
                         fit's own, and the share right week by week of when each was given (by_week)
    measured(days)       that for every kind with settled figures, as data; measured_lines in words
    brier(pairs), ece(pairs), reliability(pairs, edges)   the scoring rules, one owner
    entropy(counts)      the normalised entropy of a vote (self-consistency keeps it with the
                         winner's share: a 3-2 split and five different answers are not alike)
    recency_weight(ts, now), shrunk_rate(...), Z_SIG   the shared building blocks: a run's or a
                         figure's weight by its age, a rate shrunk toward a prior, the one
                         significance point (eli/runtime/action_outcomes, curiosity, affect_state
                         and the agent bus read them here)

Kinds, each with its own ledger events and the features its fit reads (FEATURES_BY_KIND):
    answer    a reply (the token figures, what it rested on)   settled by the next turn
    claim     a claim about the user a reply used    settled with the reply, or by the user
    recall    a memory shown to a reply              settled with the reply: used, or corrected
    route     the action a sentence was read as      settled by what ran and what the next turn said
    act       something ELI made of its own accord   settled when it stands, is undone, or is refused
    proposal  a fix ELI proposed for itself          settled when it is adopted or rolled back
    lesson    a change ELI made to how one of its parts works (eli/runtime/lessons.py)
                                                     settled when it is kept or reverted

Outcomes are kept in the evidence ledger (eli/runtime/evidence_ledger.py), the tamper-evident
record of what ELI did: an `answer_assessed` row when the figure is given, an `answer_outcome`
row when the next turn says it was right or wrong. Nothing here phrases a reply.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

ANSWER = "answer"                 # a reply to the user: right unless the user says otherwise
ASSESSED = "answer_assessed"      # ledger event: a figure was given
OUTCOME = "answer_outcome"        # ledger event: what the next turn said of it
CLAIM = "claim"                   # a claim about the user that a reply rested on (eli/memory/claims.py)
RECALL = "recall"                 # a memory shown to a reply (eli/cognition/orchestrator.py, reranker.py)
ROUTE = "route"                   # the action a sentence was read as (eli/kernel/engine.py)
ACT = "act"                       # something ELI made of its own accord (eli/world/urges.py)
PROPOSAL = "proposal"             # a fix ELI proposed for itself (eli/runtime/self_improvement.py)
LESSON = "lesson"                 # a change ELI made to one of its parts (eli/runtime/lessons.py)
# each kind's pair of ledger events: the figure given, and what became of it
EVENTS = {ANSWER: (ASSESSED, OUTCOME), CLAIM: ("claim_assessed", "claim_outcome"),
          RECALL: ("recall_assessed", "recall_outcome"), ROUTE: ("route_assessed", "route_outcome"),
          ACT: ("act_assessed", "act_outcome"), PROPOSAL: ("proposal_assessed", "proposal_outcome"),
          LESSON: ("lesson_assessed", "lesson_outcome")}
DAYS = 90.0                       # how far back outcomes count toward a calibration
# A score of exactly 0 or 1 has no finite logit; it is read as a thousandth short of the edge.
EDGE = 1e-3
# Reliability is read in ten equal bands of the chance, the usual way (Naeini et al., 2015).
BANDS = 10
# The prior on the fit: the score's weight about one, every other weight and the shift about zero,
# each standard normal, so the fit stays at "the score is the chance" until outcomes outweigh one
# observation's worth of doubt, and a figure that has not told right from wrong weighs nothing. The
# features are standardised over the outcomes first, so one prior means the same for each of them.
PRIOR_SD = 1.0
# The model's own confidence in the tokens of the reply (eli/cognition/gguf_inference.py,
# _TokenConfidence), read beside the score: the mean and least log-probability of the chosen
# tokens, the mean entropy of the distributions they came from, and the share chosen at under an
# even chance. A reply the model was unsure of token by token is more often wrong, whatever its
# figure. A reply with no token figures (the bus's fast path, a backend that gives none) has them
# missing, which is a sign of its own (MISSING), never a figure of certainty.
FEATURES = ("mean_logprob", "min_logprob", "mean_entropy", "low_share")
# What a reply rests on beside its tokens: the least chance of the claims about the user it used (a
# reply is no surer than what it stands on), how far several samples of it agreed and how spread their
# vote was, and the path it took (grounded in evidence, with memory context, the bus's fast path).
ANSWER_FEATURES = FEATURES + ("claims_min_p", "agreement", "entropy", "grounded", "memory_ctx", "bus_fast")
# What each kind's fit reads beside its score. A reply: the token figures and what it rested on. A
# claim: how many times it was confirmed and whether it is contested. A recalled memory: how readily it
# came to mind, its importance, how much of the question it holds, and whether more than one retriever
# found it. A route: how far down the stages it was read (a rule first, the parser's tail last) and
# whether the parser agreed. An act: how long the user had been quiet. A proposal: how often the
# failure it answers had recurred. A lesson: how strongly the part it changed was shown worse.
FEATURES_BY_KIND = {
    ANSWER: ANSWER_FEATURES,
    CLAIM: ("confirmations", "contested"),
    RECALL: ("accessibility", "importance", "overlap", "agreement"),
    ROUTE: ("stage_rank", "parser_agrees"),
    ACT: ("quiet_minutes",),
    PROPOSAL: ("recurrence",),
    LESSON: ("evidence_z",),
}
# The fit's name for "this feature was not there" (one indicator per feature some outcome lacked).
MISSING = "_missing"
# The part of a kind a figure belongs to, for rank and standing: a route by the rule or stage that read
# it, a reply by the path it took, a claim by its origin (its class); any other kind by its class.
UNIT_KEY = {ROUTE: "via", ANSWER: "reply_path", CLAIM: "class"}
# A gate holds a reply to a line only once the outcomes have shown replies below it to be worse, at the
# usual 90 % one-sided significance (z = 1.282), with at least this many settled figures on each side of
# the line: fewer tell nothing either way. Every test of a proportion in ELI uses this one point.
Z_SIG = 1.282
MIN_EACH_SIDE = 5
# A gate opens only once the outcomes have shown replies below its line to be no worse than those above
# it by more than this share of right answers (a non-inferiority margin, at the same 90 %): that nothing
# was shown worse is not that they were shown as good.
NON_INFERIORITY_MARGIN = 0.10
# A fit (and the settled outcomes under it) is kept this long before the ledger is read again: a turn
# assesses several figures of a kind, and a settle of a kind drops that kind's at once.
FIT_SECONDS = 30.0
# A run's or a figure's weight halves each week: how far back "lately" reaches (the ledger's actions,
# ELI's surprises, a held part's release).
HALF_LIFE_DAYS = 7.0


@dataclass
class Probability:
    """A chance ELI can stand behind, with what it rests on."""
    p: float
    kind: str
    score: float                              # the raw figure it was read from
    class_: str = ""                          # the task class the calibration was fitted for
    features: Dict[str, Any] = field(default_factory=dict)
    n: int = 0                                # outcomes the calibration rests on
    ece: Optional[float] = None               # the calibration error of the chances given, when any
    slope: float = 1.0                        # the score's weight in the fit
    shift: float = 0.0
    weights: Dict[str, float] = field(default_factory=dict)   # what the score and each feature weighs in the fit

    def as_dict(self) -> Dict[str, Any]:
        return {"p": round(self.p, 4), "kind": self.kind, "class": self.class_, "score": round(self.score, 4),
                "n": self.n, "ece": None if self.ece is None else round(self.ece, 4),
                "slope": round(self.slope, 4), "shift": round(self.shift, 4),
                "weights": {k: round(v, 4) for k, v in self.weights.items()}, "features": dict(self.features)}


# ── the scoring rules and the shared building blocks ─────────────────────────────────────────────

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


def phi(z: float) -> float:
    """The standard normal distribution function."""
    return 0.5 * (1.0 + math.erf(float(z) / math.sqrt(2.0)))


def recency_weight(ts: float, now: float, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """A run's or a figure's weight by its age: one now, a half after `half_life_days`."""
    return 0.5 ** (max(0.0, float(now) - float(ts or 0.0)) / 86400.0 / float(half_life_days))


def shrunk_rate(successes: float, n: float, prior_p: float, prior_n: float) -> float:
    """A rate shrunk toward `prior_p` by `prior_n` pseudo-observations (the mean of a Beta posterior)."""
    return (float(successes) + float(prior_p) * float(prior_n)) / (float(n) + float(prior_n))


def prior_strength(counts: Sequence[Tuple[float, float]]) -> float:
    """How many pseudo-observations the parts' own spread says a part's rate is worth beside the
    whole's (empirical Bayes, by the method of moments): from (right, settled) counts per part, the
    Beta prior whose variance is the variance of the parts' rates beyond what their sizes explain
    (weighted spread S, beyond-chance variance S - m·μ(1-μ)/N for m parts of N figures, strength
    μ(1-μ)/that - 1). Parts that differ little are pooled hard; parts that differ much keep their own
    rate. With no spread beyond chance (or every figure alike), as strong as all the parts together;
    with fewer than two parts there is nothing to read a spread from, and one pseudo-observation
    stands (the Beta(1, 1) of everything else here)."""
    parts = [(float(k), float(n)) for k, n in counts if float(n) > 0]
    if len(parts) < 2:
        return 1.0
    total = sum(n for _k, n in parts)
    mean = sum(k for k, _n in parts) / total
    if mean <= 0.0 or mean >= 1.0:
        return total
    spread = sum(n * (k / n - mean) ** 2 for k, n in parts) / total
    beyond = spread - len(parts) * mean * (1.0 - mean) / total
    if beyond <= 0.0:
        return total
    return max(EDGE, mean * (1.0 - mean) / beyond - 1.0)


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

def _numeric(value: Any) -> Optional[float]:
    """A feature as a number: a boolean as 1 or 0, None when it is not there or not a number."""
    if value is None or isinstance(value, (str, bytes, list, tuple, dict)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def scale_of(rows: Sequence[Tuple[float, float, Optional[Dict[str, Any]]]], names: Sequence[str]) -> Dict[str, Any]:
    """How each feature is read in the fit: its mean and spread over the outcomes that have it, and
    which features some outcome lacks (those get a missing sign of their own). A feature no outcome
    had is read as nothing at all."""
    mean: Dict[str, float] = {}
    sd: Dict[str, float] = {}
    missing: List[str] = []
    for key in names:
        values = [_numeric((f or {}).get(key)) for _s, _y, f in rows]
        present = [v for v in values if v is not None]
        m = sum(present) / len(present) if present else 0.0
        var = sum((v - m) ** 2 for v in present) / len(present) if present else 0.0
        mean[key], sd[key] = m, (math.sqrt(var) if var > 0.0 else 1.0)
        if present and len(present) < len(values):
            missing.append(key)
    return {"mean": mean, "sd": sd, "missing": missing}


def _row(score: float, features: Optional[Dict[str, Any]], names: Sequence[str] = FEATURES,
         scale: Optional[Dict[str, Any]] = None) -> List[float]:
    """The fit's inputs for one figure: the score's logit, then each named feature (standardised by
    `scale`; a missing one at its mean, the outcomes' own), then one missing sign per feature that
    `scale` says some outcome lacked. With no scale, each feature as it is (0 when not known)."""
    out = [logit(float(score))]
    feats = features or {}
    if scale is None:
        for key in names:
            v = _numeric(feats.get(key))
            out.append(0.0 if v is None else v)
        return out
    mean, sd = scale.get("mean") or {}, scale.get("sd") or {}
    for key in names:
        v = _numeric(feats.get(key))
        out.append(0.0 if v is None else (v - float(mean.get(key, 0.0))) / float(sd.get(key, 1.0) or 1.0))
    for key in scale.get("missing") or []:
        out.append(1.0 if _numeric(feats.get(key)) is None else 0.0)
    return out


def _newton(X, Y, mu, *, prior_sd: float, steps: int):
    """The penalised maximum-likelihood weights (a normal prior about `mu`), by Newton's method."""
    import numpy as np
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
    return theta


def fit_model(rows: Sequence[Tuple[float, float, Optional[Dict[str, Any]]]], names: Sequence[str] = FEATURES, *,
              prior: Optional[Dict[str, Any]] = None, scale: Optional[Dict[str, Any]] = None,
              prior_sd: float = PRIOR_SD, steps: int = 25) -> Dict[str, Any]:
    """The fit that most probably gave these (score, outcome, features) rows, where the chance is
    sigmoid(weights · [logit(score), standardised features, missing signs] + shift): Newton's method
    on the penalised log-loss. The prior is standard normal about 1 for the score's weight and about
    0 for the rest, or about `prior`'s own weights (a kind's fit, for its class: partial pooling).
    `scale` (scale_of) is read from the rows unless given. With no rows the score itself:
    {"weights", "shift", "names", "scale"}."""
    names = tuple(names)
    scale = scale if scale is not None else scale_of(rows, names)
    width = 1 + len(names) + len(scale.get("missing") or [])
    if prior is not None and len(prior.get("weights") or []) == width:
        mu_w, mu_b = [float(w) for w in prior["weights"]], float(prior.get("shift") or 0.0)
    else:
        mu_w, mu_b = [1.0] + [0.0] * (width - 1), 0.0
    if not rows:
        return {"weights": mu_w, "shift": mu_b, "names": names, "scale": scale}
    import numpy as np
    X = np.array([_row(s, f, names, scale) + [1.0] for s, _y, f in rows], dtype=float)
    Y = np.array([1.0 if float(y) >= 0.5 else 0.0 for _s, y, _f in rows], dtype=float)
    theta = _newton(X, Y, np.array(mu_w + [mu_b], dtype=float), prior_sd=prior_sd, steps=steps)
    return {"weights": [float(v) for v in theta[:-1]], "shift": float(theta[-1]), "names": names, "scale": scale}


def apply_fit(fit: Dict[str, Any], score: float, features: Optional[Dict[str, Any]] = None) -> float:
    """The chance a fit (weights_for, fit_model) gives a score with these features."""
    x = _row(score, features, fit.get("names") or FEATURES, fit.get("scale"))
    return sigmoid(sum(float(w) * v for w, v in zip(fit.get("weights") or [1.0], x)) + float(fit.get("shift") or 0.0))


def weight_names(fit: Dict[str, Any]) -> Tuple[str, ...]:
    """The name of each of a fit's weights: the score, each feature, each feature's missing sign."""
    scale = fit.get("scale") or {}
    return ("score",) + tuple(fit.get("names") or ()) + tuple(f"{k}{MISSING}" for k in scale.get("missing") or [])


def fit_rows(rows: Sequence[Tuple[float, float, Optional[Dict[str, Any]]]], *, prior_sd: float = PRIOR_SD,
             steps: int = 25, names: Sequence[str] = FEATURES) -> Tuple[List[float], float]:
    """fit_model's weights and shift alone; with no rows ([1, 0, ...], 0): the score itself."""
    model = fit_model(rows, names, prior_sd=prior_sd, steps=steps)
    return model["weights"], model["shift"]


def apply_rows(score: float, features: Optional[Dict[str, Any]], weights: Sequence[float], shift: float,
               names: Sequence[str] = FEATURES, scale: Optional[Dict[str, Any]] = None) -> float:
    """The chance for these weights and shift (fit_rows), the features read by `scale` when given."""
    return apply_fit({"weights": list(weights), "shift": shift, "names": tuple(names), "scale": scale}, score, features)


def features_of(kind: str) -> Tuple[str, ...]:
    """The features a kind's fit reads (FEATURES_BY_KIND; a kind not listed reads the reply's tokens)."""
    return tuple(FEATURES_BY_KIND.get(kind, FEATURES))


# ── outcomes, in the ledger ───────────────────────────────────────────────────────────────────────

def _ledger():
    from eli.runtime import evidence_ledger
    return evidence_ledger


def _where_of(db_path) -> str:
    try:
        return str(db_path) if db_path else str(_ledger()._default_db_path())
    except Exception:  # noqa: BLE001 - the ledger's own path unknown: kept by kind alone
        return str(db_path or "")


# (kind, ledger, days) -> (read at, every settled figure of the kind, newest first)
_settled_cache: Dict[Tuple[str, str, float], Tuple[float, List[Dict[str, Any]]]] = {}


def settled(kind: str = ANSWER, *, days: float = DAYS, db_path=None) -> List[Dict[str, Any]]:
    """Every figure of this kind settled within `days`, newest first, each once with its latest
    outcome: {"request_id", "score", "y" (1 right, 0 wrong), "features", "class", "p" (the chance it
    was given, when recorded), "ts", "how"}. Read once per FIT_SECONDS; a settle of the kind drops it."""
    key = (str(kind), _where_of(db_path), float(days))
    now = time.time()
    kept = _settled_cache.get(key)
    if kept and now - kept[0] < FIT_SECONDS:
        return kept[1]
    assessed_type, outcome_type = EVENTS.get(kind, (ASSESSED, OUTCOME))
    rows = _ledger().assessment_pairs(kind, since=now - float(days) * 86400.0, assessed_type=assessed_type,
                                      outcome_type=outcome_type, db_path=db_path)
    out: List[Dict[str, Any]] = []
    for assessed, outcome in rows:
        payload = assessed.get("payload") or {}
        try:
            score = float(payload.get("score"))
        except (TypeError, ValueError):
            continue
        try:
            given = float(payload["p"]) if payload.get("p") is not None else None
        except (TypeError, ValueError):
            given = None
        out.append({"request_id": str(assessed.get("request_id") or ""), "score": score,
                    "y": 1.0 if outcome.get("outcome") == "ok" else 0.0,
                    "features": dict(payload.get("features") or {}), "class": str(payload.get("class") or ""),
                    "p": given, "ts": float(assessed.get("timestamp") or 0.0),
                    "how": str((outcome.get("payload") or {}).get("how") or "")})
    _settled_cache[key] = (now, out)
    return out


def _matches(row: Dict[str, Any], where: Optional[Dict[str, Any]]) -> bool:
    """Whether a settled figure has these values ("class" is its class, anything else a feature)."""
    for k, v in (where or {}).items():
        have = row.get("class") if k == "class" else (row.get("features") or {}).get(k)
        if have != v:
            return False
    return True


def outcomes(kind: str = ANSWER, *, class_: str = "", days: float = DAYS,
             db_path=None) -> List[Tuple[float, float, Dict[str, Any]]]:
    """(score, outcome, features) for every figure of this kind (and class, when given) that the
    next turn settled within `days`."""
    return [(r["score"], r["y"], r["features"]) for r in settled(kind, days=days, db_path=db_path)
            if not class_ or r["class"] == class_]


def week_start(ts: float) -> float:
    """The start of the calendar week a time falls in: Monday 00:00, local time."""
    t = time.localtime(float(ts))
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday - t.tm_wday, 0, 0, 0, 0, 0, -1))


def by_week(points: Iterable[Tuple[float, float]], *, days: float, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """(time, value) points counted by calendar week (week_start), every week of the last `days` up to
    now, oldest first, an empty one too: [{"from": the week's start, "n": points, "sum": their values}].
    One owner for "week by week" in ELI's records (action_outcomes.report and report here)."""
    now = time.time() if now is None else float(now)
    weeks: List[Dict[str, Any]] = []
    start = week_start(now - float(days) * 86400.0)
    while start <= now:
        weeks.append({"from": start, "n": 0, "sum": 0.0})
        start = week_start(start + 8 * 86400.0)      # past the next Monday whatever the clocks did
    for ts, value in points:
        begun = week_start(ts)
        for week in weeks:
            if week["from"] == begun:
                week["n"] += 1
                week["sum"] += float(value)
                break
    return weeks


_fits: Dict[Tuple[str, str, str, float], Tuple[float, Dict[str, Any]]] = {}


def _fit_for(kind: str, class_: str, days: float, db_path) -> Dict[str, Any]:
    """The fit in force for a kind (and class): the kind's fit on all its outcomes, and for a class
    with outcomes of its own, a fit on those drawn toward the kind's (its prior), read on the kind's
    scale; the score itself until outcomes say otherwise. `ece` is the calibration error of the
    chances that were given for the outcomes it rests on. Kept FIT_SECONDS."""
    key = (str(kind), str(class_ or ""), _where_of(db_path), float(days))
    now = time.time()
    kept = _fits.get(key)
    if kept and now - kept[0] < FIT_SECONDS:
        return kept[1]
    names = features_of(kind)
    try:
        rows = settled(kind, days=days, db_path=db_path)
    except Exception as failure:   # noqa: BLE001 - the ledger unreadable: the score stands as the chance
        log.debug("outcomes unavailable: %s", failure)
        rows = []
    triples = [(r["score"], r["y"], r["features"]) for r in rows]
    model = fit_model(triples, names)
    fitted_for, used = "", rows
    mine = [r for r in rows if class_ and r["class"] == class_]
    if mine:
        model = fit_model([(r["score"], r["y"], r["features"]) for r in mine], names, prior=model,
                          scale=model["scale"])
        fitted_for, used = class_, mine
    given = [(r["p"], r["y"]) for r in used if r["p"] is not None]
    fit = dict(model, n=len(used), ece=ece(given) if given else None, **{"class": fitted_for})
    _fits[key] = (now, fit)
    return fit


def forget_fits(kind: Optional[str] = None) -> None:
    """Drop the kept fits and outcomes of a kind (every kind's with none), so the next figure reads the
    outcomes again: an outcome of that kind was just settled."""
    if kind is None:
        _fits.clear()
        _settled_cache.clear()
        return
    for key in [k for k in _fits if k[0] == kind]:
        _fits.pop(key, None)
    for key in [k for k in _settled_cache if k[0] == kind]:
        _settled_cache.pop(key, None)


def calibrate(kind: str, score: float, *, class_: str = "", features: Optional[Dict[str, Any]] = None,
              days: float = DAYS, db_path=None) -> Probability:
    """The chance for a raw score of this kind: fitted on the outcomes of its class when that class
    has any (drawn toward its kind's), else of the kind as a whole; the score itself until outcomes
    say otherwise."""
    score = float(score)
    fit = _fit_for(kind, class_, days, db_path)
    return Probability(p=apply_fit(fit, score, features), kind=kind, score=score, class_=fit["class"],
                       features=dict(features or {}), n=fit["n"], ece=fit["ece"], slope=fit["weights"][0],
                       shift=fit["shift"], weights=dict(zip(weight_names(fit), fit["weights"])))


def weights_for(kind: str, *, class_: str = "", days: float = DAYS, db_path=None) -> Dict[str, Any]:
    """The fit in force for a kind, for a ranking that applies it to many figures itself (apply_fit):
    {"weights", "shift", "names", "scale", "n", "ece", "class"}; with n == 0 the score is the chance."""
    return dict(_fit_for(kind, class_, days, db_path))


def _record_figure(kind: str, prob: Probability, request_id: str, class_: str, features: Optional[Dict[str, Any]],
                   session_id: str, user_id: str, db_path) -> None:
    try:
        _ledger().record_event(
            EVENTS.get(kind, (ASSESSED, OUTCOME))[0], source="epistemics", action=kind.upper(), subject=str(request_id),
            payload={"kind": kind, "class": class_, "score": round(float(prob.score), 4), "p": round(prob.p, 4),
                     "n": prob.n, "features": dict(features or {})},
            confidence=prob.p, reusable=False, request_id=str(request_id),
            session_id=session_id, user_id=user_id, db_path=db_path)
    except Exception as failure:   # noqa: BLE001 - a ledger that cannot be written leaves the figure unrecorded
        log.warning("a figure of kind %s was not recorded: %s", kind, failure)


def assess(kind: str, score: float, *, request_id: str, features: Optional[Dict[str, Any]] = None,
           class_: str = "", session_id: str = "", user_id: str = "", db_path=None) -> Probability:
    """Make the Probability for this turn and put it in the ledger, so the next turn can settle it."""
    prob = calibrate(kind, score, class_=class_, features=features, db_path=db_path)
    if request_id:
        _record_figure(kind, prob, request_id, class_, features, session_id, user_id, db_path)
    return prob


def assess_many(kind: str, items: Sequence[Tuple[str, float, Optional[Dict[str, Any]]]], *, class_: str = "",
                session_id: str = "", user_id: str = "", db_path=None) -> List[Probability]:
    """Several figures of one kind for one turn, each (request_id, score, features), fitted once and
    each put in the ledger under its own id (the memories a reply was shown, the claims it used)."""
    out: List[Probability] = []
    for request_id, score, features in items:
        prob = calibrate(kind, score, class_=class_, features=features, db_path=db_path)
        if request_id:
            _record_figure(kind, prob, str(request_id), class_, features, session_id, user_id, db_path)
        out.append(prob)
    return out


def settle(request_id: str, right: bool, *, how: str = "", kind: str = ANSWER, db_path=None) -> bool:
    """What happened to the figure given for this turn. A figure is settled once: the same verdict
    again records nothing, and a different, later one takes the place of the earlier (the ledger keeps
    both; the outcomes read only the latest), as an act that stood and was then undone was undone.
    True when the verdict stands recorded, False when it could not be recorded."""
    if not request_id:
        return False
    assessed_type, outcome_type = EVENTS.get(kind, (ASSESSED, OUTCOME))
    verdict = "ok" if right else "failed"
    try:
        had = _ledger().latest_outcome(str(request_id), outcome_type=outcome_type, action=kind.upper(),
                                       db_path=db_path)
    except (OSError, ValueError, AttributeError) as failure:
        log.debug("earlier outcome unreadable: %s", failure)
        had = None
    if had == verdict:
        return True
    try:
        _ledger().record_event(
            outcome_type, source="epistemics", action=kind.upper(), subject=str(request_id),
            # the verdict in the row's words, so a reversed verdict is never taken for a repeat of the first
            content=f"{'right' if right else 'wrong'}{' (' + how + ')' if how else ''}"
                    + (f", in place of {'right' if had == 'ok' else 'wrong'}" if had else ""),
            payload={"kind": kind, "how": how, **({"replaces": had} if had else {})}, outcome=verdict,
            reusable=False, request_id=str(request_id), db_path=db_path)
        forget_fits(kind)
        return True
    except Exception as failure:   # noqa: BLE001 - a ledger that cannot be written leaves the outcome unrecorded
        log.warning("an answer's outcome was not recorded: %s", failure)
        return False


def claim_request_id(claim_id: Any, request_id: str) -> str:
    """The id under which a claim's figure is kept for one turn: the claim's and the turn's."""
    return f"claim:{int(claim_id)}:{request_id}"


def settle_open(kind: str, subject_prefix: str, right: bool, *, how: str = "", before: Optional[float] = None,
                db_path=None) -> int:
    """Settle every figure of this kind whose id starts with `subject_prefix` that nothing has
    settled yet (a claim's, when the user settles the claim itself; with `before`, only figures given
    before that time: an act nobody undid in a day stood). How many were settled."""
    assessed_type, outcome_type = EVENTS.get(kind, (ASSESSED, OUTCOME))
    try:
        open_ids = _ledger().open_assessments(kind, assessed_type=assessed_type, outcome_type=outcome_type,
                                              subject_prefix=subject_prefix, until=before, db_path=db_path)
    except (OSError, ValueError, AttributeError) as failure:
        log.debug("open figures unreadable: %s", failure)
        return 0
    return sum(1 for rid in open_ids if settle(rid, right, how=how, kind=kind, db_path=db_path))


def report(kind: str = ANSWER, *, days: float = DAYS, db_path=None) -> Dict[str, Any]:
    """How good the figures of this kind have been. `n` settled and `right`, the share of them that proved
    right; `brier`, `ece` and `bands` score the chances as they were given, each against what became of
    it afterwards (prequential: the honest record, since no chance saw its own outcome); `brier_score`/
    `ece_score` the raw scores; `brier_fitted`/`ece_fitted` the fit now in force on the outcomes it was
    fitted on (in-sample, so flattering), with its weights; `weeks` the share right week by week of when
    each figure was given (by_week, oldest first; "right" None for a week with none settled)."""
    names = features_of(kind)
    rows = settled(kind, days=days, db_path=db_path)
    triples = [(r["score"], r["y"], r["features"]) for r in rows]
    model = fit_model(triples, names)
    given = [(r["p"], r["y"]) for r in rows if r["p"] is not None]
    scores = [(r["score"], r["y"]) for r in rows]
    fitted = [(apply_fit(model, s, f), y) for s, y, f in triples]
    weeks = [{"from": w["from"], "n": w["n"], "right": (w["sum"] / w["n"]) if w["n"] else None}
             for w in by_week([(r["ts"], r["y"]) for r in rows], days=days)]
    return {"kind": kind, "n": len(rows), "right": (sum(r["y"] for r in rows) / len(rows)) if rows else None,
            "n_given": len(given),
            "brier": brier(given), "ece": ece(given), "bands": reliability(given),
            "brier_score": brier(scores), "ece_score": ece(scores),
            "brier_fitted": brier(fitted), "ece_fitted": ece(fitted),
            "slope": model["weights"][0], "shift": model["shift"],
            "weights": dict(zip(weight_names(model), model["weights"])),
            "weeks": weeks}


# What each kind's figures are of, for the record a reply about ELI's own accuracy is given.
_KIND_WORDS = {ANSWER: "replies to you", ROUTE: "readings of what a sentence asked for",
               RECALL: "memories shown to a reply", CLAIM: "claims about you a reply rested on",
               ACT: "things ELI did of its own accord", PROPOSAL: "fixes ELI proposed for itself",
               LESSON: "changes ELI made to how its own parts work"}


def measured(*, days: float = DAYS, db_path=None) -> List[Dict[str, Any]]:
    """ELI's measured accuracy as data, one row per kind that has settled figures: {"kind", "words" (what
    the figures are of), "n" settled, "right" (the share that proved right), "ece" (the calibration error
    of the chances as they were given), "n_given" (how many were scored as given), "weeks" (report's,
    oldest first)}. measured_lines tells it; a body draws it (eli/world/shown.evidence)."""
    rows: List[Dict[str, Any]] = []
    for kind, words in _KIND_WORDS.items():
        try:
            rep = report(kind, days=days, db_path=db_path)
        except (OSError, ValueError, AttributeError) as failure:
            log.debug("measured accuracy unreadable for %s: %s", kind, failure)
            continue
        if not int(rep.get("n") or 0):
            continue
        rows.append({"kind": kind, "words": words, "n": int(rep["n"]), "right": float(rep["right"]),
                     "ece": rep.get("ece"), "n_given": int(rep.get("n_given") or 0),
                     "weeks": list(rep.get("weeks") or [])})
    return rows


def measured_lines(*, days: float = DAYS, db_path=None) -> List[str]:
    """ELI's measured accuracy, one line per kind that has settled figures (measured): how many were
    settled, the share that proved right, and how far the chances given were from what became of them
    afterwards (the calibration error of the chances as given). For a reply asked about ELI's own
    accuracy: measured here, and the claim check holds figures to what the record says
    (eli/cognition/self_claims)."""
    lines: List[str] = []
    for row in measured(days=days, db_path=db_path):
        line = f"- {row['words']}: {row['n']} settled in the last {int(days)} days, {row['right']:.0%} proved right"
        if row.get("ece") is not None:
            line += (f"; the chances given were off by {float(row['ece']):.2f} on average (calibration error, "
                     f"{row['n_given']} scored as given)")
        lines.append(line)
    if lines:
        lines.insert(0, "Measured accuracy (settled outcomes in the evidence ledger, not estimates):")
    return lines


def base_rate(kind: str = ANSWER, *, class_: str = "", where: Optional[Dict[str, Any]] = None,
              days: float = DAYS, db_path=None) -> Tuple[float, int]:
    """The share of figures of this kind (of this class; with `where`, those whose features equal the
    given values) that proved right, as the mean of a Beta(1, 1) posterior, and how many were settled.
    With none, (0.5, 0): an even chance and nothing behind it."""
    rows = [r for r in settled(kind, days=days, db_path=db_path)
            if (not class_ or r["class"] == class_) and _matches(r, where)]
    n = len(rows)
    return ((sum(r["y"] for r in rows) + 1.0) / (n + 2.0), n)


# ── the one test, and what is decided by it ───────────────────────────────────────────────────────

def compare(a: Tuple[float, float], b: Tuple[float, float], *, min_each: int = MIN_EACH_SIDE,
            margin: float = NON_INFERIORITY_MARGIN) -> Optional[bool]:
    """Whether the figures counted in `a` (right, settled) proved right less often than those in `b`.
    True: shown worse (a one-sided two-proportion test at Z_SIG). False: shown no worse than `b` by
    more than `margin` (the Beta(1, 1) posteriors of the two rates: the chance that a's rate is within
    the margin of b's at least that of Z_SIG). None: neither shown, or fewer than `min_each` settled on
    a side. Every gate, rule hand-over and lesson in ELI is judged by this."""
    (ok_a, n_a), (ok_b, n_b) = (float(a[0]), float(a[1])), (float(b[0]), float(b[1]))
    if n_a < int(min_each) or n_b < int(min_each):
        return None
    p_a, p_b = ok_a / n_a, ok_b / n_b
    pooled = (ok_a + ok_b) / (n_a + n_b)
    spread = math.sqrt(max(EDGE, pooled * (1.0 - pooled)) * (1.0 / n_a + 1.0 / n_b))
    if (p_b - p_a) / spread >= Z_SIG:
        return True

    def _posterior(ok: float, n: float) -> Tuple[float, float]:
        al, be = ok + 1.0, n - ok + 1.0
        mean = al / (al + be)
        return mean, mean * (1.0 - mean) / (al + be + 1.0)

    (m_a, v_a), (m_b, v_b) = _posterior(ok_a, n_a), _posterior(ok_b, n_b)
    if (m_a - m_b + float(margin)) / math.sqrt(max(EDGE, v_a + v_b)) >= Z_SIG:
        return False
    return None


def _count(rows: Iterable[Dict[str, Any]]) -> Tuple[float, float]:
    rows = list(rows)
    return sum(r["y"] for r in rows), float(len(rows))


def _split(kind: str, feature: str, value: float, *, class_: str, where: Optional[Dict[str, Any]], days: float,
           db_path) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """The (right, settled) counts of the figures below `value` of `feature` and at or above it."""
    below: List[Dict[str, Any]] = []
    above: List[Dict[str, Any]] = []
    for r in settled(kind, days=days, db_path=db_path):
        if (class_ and r["class"] != class_) or not _matches(r, where):
            continue
        raw = r["score"] if feature == "score" else _numeric((r["features"] or {}).get(feature))
        if raw is None:
            continue
        (below if float(raw) < float(value) else above).append(r)
    return _count(below), _count(above)


def worse_below(kind: str, feature: str, value: float, *, class_: str = "", where: Optional[Dict[str, Any]] = None,
                min_each: int = MIN_EACH_SIDE, margin: float = NON_INFERIORITY_MARGIN,
                days: float = DAYS, db_path=None) -> Optional[bool]:
    """Whether the figures of this kind whose `feature` was below `value` proved right less often than
    those at or above it (compare: True shown worse, False shown no worse, None neither).

    `where` keeps only the figures whose features equal the given values: the replies that took the
    path the gate decides (memory context kept, the fast path taken), so a gate is judged by what
    became of its own path and not by replies it had already turned away. `feature` may be "score",
    the figure itself. What a gate asks before holding a reply to a line: with no evidence, or shown
    worse, the gate stands as it was; shown that replies below the line do as well, it opens."""
    below, above = _split(kind, feature, value, class_=class_, where=where, days=days, db_path=db_path)
    return compare(below, above, min_each=min_each, margin=margin)


def worse_than(kind: str, where: Dict[str, Any], than: Dict[str, Any], *, min_each: int = MIN_EACH_SIDE,
               margin: float = NON_INFERIORITY_MARGIN, days: float = DAYS, db_path=None) -> Optional[bool]:
    """Whether the figures of this kind matching `where` proved right less often than those matching
    `than` ("class" is the class, anything else a feature): a router rule's readings where the parser
    read the sentence otherwise against the parser's own, a reply path against another. compare's
    answer."""
    rows = settled(kind, days=days, db_path=db_path)
    return compare(_count(r for r in rows if _matches(r, where)), _count(r for r in rows if _matches(r, than)),
                   min_each=min_each, margin=margin)


def thompson(arms: Sequence[Tuple[float, float]], *, rng: Optional[random.Random] = None) -> int:
    """Thompson sampling: which arm, of (right, settled) counts, has the highest draw from its Beta(1, 1)
    posterior. An arm nothing is known of is drawn as often as its uncertainty deserves."""
    draw = rng or random
    best, best_value = 0, -1.0
    for i, (ok, n) in enumerate(arms):
        v = draw.betavariate(float(ok) + 1.0, max(0.0, float(n) - float(ok)) + 1.0)
        if v > best_value:
            best, best_value = i, v
    return best


def gate_open(kind: str, feature: str, value: float, *, class_: str = "", where: Optional[Dict[str, Any]] = None,
              margin: float = NON_INFERIORITY_MARGIN, days: float = DAYS, db_path=None,
              rng: Optional[random.Random] = None) -> Tuple[bool, str]:
    """A gate's decision for a figure below its line: (open, how). Shown no worse than the figures
    above the line: open ("shown"). Shown worse: closed ("shown"). Neither shown: a Thompson draw from
    the two Beta posteriors, open when the draw below comes within the non-inferiority margin of the
    draw above ("explored"), so the gate gathers what its own path is worth in proportion to how
    unsure it is, and stops exploring once the outcomes have spoken."""
    below, above = _split(kind, feature, value, class_=class_, where=where, days=days, db_path=db_path)
    verdict = compare(below, above, margin=margin)
    if verdict is not None:
        return verdict is False, "shown"
    draw = rng or random
    a = draw.betavariate(below[0] + 1.0, below[1] - below[0] + 1.0)
    b = draw.betavariate(above[0] + 1.0, above[1] - above[0] + 1.0)
    return a + float(margin) >= b, "explored"


# ── the parts of a kind ───────────────────────────────────────────────────────────────────────────

def unit_of(kind: str, row: Dict[str, Any]) -> str:
    """The part a settled figure belongs to (UNIT_KEY): a route's rule or stage, a reply's path, a
    claim's origin, else its class."""
    key = UNIT_KEY.get(kind, "class")
    if key == "class":
        return str(row.get("class") or "")
    return str((row.get("features") or {}).get(key) or "")


def standing(kind: str, unit: str, *, days: float = DAYS, db_path=None) -> Dict[str, Any]:
    """A part's record: how many of its figures were settled and how many proved right, its rate
    shrunk toward its kind's by the prior strength the kind's parts show (empirical Bayes), and the
    kind's own rate. {"unit", "n", "right", "rate", "kind_rate", "prior_n"}."""
    rows = settled(kind, days=days, db_path=db_path)
    by_unit: Dict[str, List[float]] = {}
    for r in rows:
        by_unit.setdefault(unit_of(kind, r), []).append(r["y"])
    total = sum(len(v) for v in by_unit.values())
    kind_rate = (sum(sum(v) for v in by_unit.values()) + 1.0) / (total + 2.0)
    strength = prior_strength([(sum(v), len(v)) for v in by_unit.values()])
    mine = by_unit.get(str(unit), [])
    return {"unit": str(unit), "n": len(mine), "right": sum(mine),
            "rate": shrunk_rate(sum(mine), len(mine), kind_rate, strength), "kind_rate": kind_rate,
            "prior_n": strength}


def rank(kind: str, *, days: float = DAYS, min_each: int = MIN_EACH_SIDE, db_path=None) -> List[Dict[str, Any]]:
    """The parts of this kind whose figures proved right less often than their peers' (all the kind's
    other figures), flagged by the Benjamini-Hochberg procedure at a false discovery rate of 1 - Φ(Z_SIG)
    (the same point every test here uses, about 0.10), most strongly flagged first:
    [{"unit", "n", "right", "rate", "peer_rate", "z", "p_value"}]. A part with fewer than `min_each`
    settled figures, or peers with fewer, is not tested."""
    rows = settled(kind, days=days, db_path=db_path)
    by_unit: Dict[str, List[float]] = {}
    for r in rows:
        unit = unit_of(kind, r)
        if unit:
            by_unit.setdefault(unit, []).append(r["y"])
    total_ok, total_n = sum(r["y"] for r in rows), float(len(rows))
    tested: List[Dict[str, Any]] = []
    for unit, ys in by_unit.items():
        n_a, ok_a = float(len(ys)), float(sum(ys))
        n_b, ok_b = total_n - n_a, total_ok - ok_a
        if n_a < min_each or n_b < min_each:
            continue
        pooled = (ok_a + ok_b) / (n_a + n_b)
        spread = math.sqrt(max(EDGE, pooled * (1.0 - pooled)) * (1.0 / n_a + 1.0 / n_b))
        z = (ok_b / n_b - ok_a / n_a) / spread
        tested.append({"unit": unit, "n": int(n_a), "right": int(ok_a), "rate": ok_a / n_a,
                       "peer_rate": ok_b / n_b, "z": z, "p_value": 1.0 - phi(z)})
    q = 1.0 - phi(Z_SIG)
    tested.sort(key=lambda t: t["p_value"])
    m = len(tested)
    cut = 0
    for i, t in enumerate(tested, start=1):
        if t["p_value"] <= q * i / m:
            cut = i
    return sorted(tested[:cut], key=lambda t: t["z"], reverse=True)


__all__ = ["ACT", "ANSWER", "ANSWER_FEATURES", "ASSESSED", "CLAIM", "EVENTS", "FEATURES", "FEATURES_BY_KIND",
           "HALF_LIFE_DAYS", "LESSON", "MIN_EACH_SIDE", "MISSING", "NON_INFERIORITY_MARGIN", "OUTCOME",
           "PROPOSAL", "Probability", "RECALL", "ROUTE", "UNIT_KEY", "Z_SIG", "apply_fit", "apply_rows", "assess",
           "assess_many", "base_rate", "brier", "calibrate", "claim_request_id", "compare", "ece", "entropy",
           "features_of", "fit_model", "fit_rows", "forget_fits", "gate_open", "logit", "measured_lines", "outcomes",
           "phi", "prior_strength", "rank", "recency_weight", "reliability", "report", "scale_of", "settle",
           "settle_open", "settled", "shrunk_rate", "sigmoid", "standing", "thompson", "unit_of", "weight_names",
           "weights_for", "worse_below", "worse_than"]
