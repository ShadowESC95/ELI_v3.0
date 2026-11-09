"""
eli/cognition/affect_state.py
─────────────────────────────
The user's affect as a latent state, read through its observations.

Each turn gives a read of how the user sounds or writes: an emotion label with how sure the
reader was, and from the voice an arousal figure. Read one at a time, the labels jumped from turn
to turn, a mood was a count of reads of one kind, and a read the reader was unsure of counted as
much as one it was sure of once it passed a line. Here the reads are what they are: noisy
observations of a state that moves smoothly and fades toward neutral when nothing is heard.

    x_t = φ x_{t−1} + w_t          the state [valence, arousal], with inertia φ = e^(−Δt/τ)
    y_t = c_t x_t + v_t            a read: valence from the label at the reader's sureness c_t,
                                   arousal from the voice (seen whole: y = x + v)

A Kalman filter (Kalman, 1960) keeps the state and its uncertainty. Each observation's noise is
its own: a label the reader was sure of says more than one it was unsure of, a read with no voice
says nothing of arousal, and a modality that is missing is simply not observed. The time constant
τ is identified from this user's own reads, by the likelihood of the filter's innovations over a
grid of time constants, so a person whose mood turns on a sentence and one whose mood lasts an
afternoon each get their own, and with it how far the state wanders between reads; the long-run
spread of the state is the spread of their reads about neutral.

    estimate(user_id)      the state now: valence and arousal in −1..1, their uncertainty, the
                           reads it rests on, the time constant, and a label with the chance it
                           is meant (a state, not a spike)
    trajectory(reads, τ)   the state after each read, oldest first, for tests and the panel

Nothing here phrases a reply or names a feeling of ELI's own; it reads the user.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

# The time constants tried, in minutes, when identifying this user's: the search space of the
# method, from a mood that turns within a few sentences to one that lasts a working day.
TAU_GRID_MINUTES = (5.0, 15.0, 30.0, 60.0, 120.0, 240.0, 480.0)
# How much the state wanders, as a share of the reads' spread, tried alongside: a mood that sits
# where it is between reads, up to one that wanders as far as the reads do.
Q_GRID = (0.05, 0.1, 0.25, 0.5, 1.0)
# Below this many reads the likelihood cannot tell the constants apart; the grids' middles stand.
IDENTIFY_MIN_READS = 4
# A mood that turns is a change the wander does not foresee. When the last two innovations ran
# larger than the filter expected (their mean normalised square past the 95 % point of a chi-square
# with two degrees of freedom, divided by two), the state's uncertainty is raised to what the
# innovations say the state must have moved beyond the reads' noise (the innovation-based adaptive
# filter, Mehra, 1970), so the state can move to where the reads now are.
SURPRISE_WINDOW = 2
SURPRISE_95 = 5.991 / SURPRISE_WINDOW
# A read's value is a label at ±1 (or 0 for neutral) taken at the reader's sureness c, and it sees
# the state only as far as the reader is sure: y = c·x + v. Its noise is the label's own uncertainty
# c(1 − c) plus a floor measured from this user's reads (half the variance of successive
# differences), so what a read says of the state, c²/(c(1 − c) + floor), rises with the reader's
# sureness and is nothing at c = 0. (Taken as y = x + v, a turn with nothing read, kept as neutral at
# sureness 0, had the least noise there is: the most precise evidence of a neutral user.) A turn with
# nothing read is not an observation at all: the state only fades over it. The state's long-run
# spread is the reads' mean square about neutral. Until there are reads enough to measure, both
# are the variance of a label as likely either way, 1/4.
FLOOR_PRIOR = 0.25
# A state is meant, not a spike, when it is at least this far from neutral (the same line the reads
# were held to one by one) and stands at least Z_SIG standard deviations from zero (90 %, one-sided:
# a single read reaches about 0.75, three reads in a row about 1.7).
STATE_MIN = 0.45
Z_SIG = 1.282
HOW_MANY_READS = 40


@dataclass
class AffectState:
    valence: float = 0.0
    arousal: float = 0.0
    var_valence: float = FLOOR_PRIOR
    var_arousal: float = FLOOR_PRIOR
    reads: int = 0
    tau_minutes: float = 60.0
    q_scale: float = 0.25                 # the state's wander per time constant, as a share of the spread
    label: str = ""                       # "negative" | "positive" | "neutral" | "" (nothing read)
    confidence: float = 0.0               # the chance the state is meant: Φ(|valence| / σ)
    run_length: int = 0                   # reads in a row whose filtered sign is the label's
    transition: Dict[str, str] = field(default_factory=dict)
    at: float = 0.0

    @property
    def z_valence(self) -> float:
        return self.valence / math.sqrt(self.var_valence) if self.var_valence > 0 else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {"valence": round(self.valence, 3), "arousal": round(self.arousal, 3),
                "sd_valence": round(math.sqrt(max(self.var_valence, 0.0)), 3),
                "sd_arousal": round(math.sqrt(max(self.var_arousal, 0.0)), 3),
                "reads": self.reads, "tau_minutes": self.tau_minutes, "q_scale": self.q_scale, "label": self.label,
                "confidence": round(self.confidence, 3), "run_length": self.run_length,
                "transition": dict(self.transition)}


def _phi(z: float) -> float:
    """The standard normal distribution function."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _sign_of(valence_class: str) -> float:
    return {"positive": 1.0, "negative": -1.0}.get(str(valence_class or "").lower(), 0.0)


def observations(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The reads as observations, oldest first: {ts, y_v, c, y_a}. `y_v` is the label's sign at
    the reader's sureness c (a neutral read observes 0); `y_a` is the voice's arousal or None. A
    turn kept with nothing read (sureness 0) is left out: it says nothing of the user's state."""
    out = []
    for row in rows:
        row = row or {}
        try:
            c = max(0.0, min(1.0, float(row.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            c = 0.0
        if c <= 0.0:
            continue
        valence = row.get("valence")
        if not valence:
            try:
                from eli.cognition.emotion_timeline import valence_of
                valence = valence_of(str(row.get("detected") or ""))
            except (ImportError, AttributeError):
                valence = "neutral"
        try:
            ts = float(row.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        arousal = row.get("arousal")
        try:
            y_a = max(-1.0, min(1.0, float(arousal))) if arousal is not None else None
        except (TypeError, ValueError):
            y_a = None
        out.append({"ts": ts, "y_v": c * _sign_of(valence), "c": c, "y_a": y_a,
                    "valence": str(valence or "neutral")})
    out.sort(key=lambda o: o["ts"])
    return out


def _mean_square(values: Sequence[float], prior: float) -> float:
    """How far from neutral the reads go: their mean square about zero, never under the prior."""
    vals = [float(v) for v in values]
    if len(vals) < 2:
        return prior
    return max(sum(v * v for v in vals) / len(vals), prior)


def _noise_floor(values: Sequence[float], prior: float) -> float:
    """The reads' own noise beyond their labels' uncertainty: half the variance of successive
    differences (a state that moves slowly leaves the differences to the noise); the prior until
    there are three reads."""
    vals = [float(v) for v in values]
    if len(vals) < 3:
        return prior
    diffs = [b - a for a, b in zip(vals, vals[1:])]
    mean = sum(diffs) / len(diffs)
    return max(sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1) / 2.0, 1e-6)


def _run(obs: Sequence[Dict[str, Any]], tau_minutes: float, q_scale: float = 0.25, *, spread_v: float,
         spread_a: float, floor_v: float, floor_a: float) -> Tuple[List[Tuple[float, float, float, float]], float]:
    """The filter over the observations: [(valence, var_v, arousal, var_a)] after each, and the
    log-likelihood of the innovations, by which the time constant and the wander are chosen."""
    tau = max(1e-6, float(tau_minutes) * 60.0)
    x_v, p_v, x_a, p_a = 0.0, spread_v, 0.0, spread_a
    last_ts = None
    loglik = 0.0
    innovations: List[Tuple[float, float]] = []       # (innovation, its expected variance), in order
    out = []
    for o in obs:
        if last_ts is not None:
            phi = math.exp(-max(0.0, o["ts"] - last_ts) / tau)
            x_v, x_a = phi * x_v, phi * x_a
            p_v = phi * phi * p_v + (1.0 - phi * phi) * q_scale * spread_v   # the state wanders its own share
            p_a = phi * phi * p_a + (1.0 - phi * phi) * q_scale * spread_a
        last_ts = o["ts"]
        h = o["c"]                                  # the read sees the state only as far as it is sure
        r_v = h * (1.0 - h) + floor_v
        recent = innovations[-SURPRISE_WINDOW:]
        if len(recent) == SURPRISE_WINDOW and sum(i * i / v for i, v in recent) / len(recent) > SURPRISE_95:
            p_v = max(p_v, sum(i * i for i, _ in recent) / len(recent) - r_v)   # the state moved: the reads say so
        s = h * h * p_v + r_v
        innovation = o["y_v"] - h * x_v
        loglik += -0.5 * (math.log(2.0 * math.pi * s) + innovation * innovation / s)
        innovations.append((innovation, s))
        k = p_v * h / s
        x_v += k * innovation
        p_v *= (1.0 - k * h)
        if o["y_a"] is not None:
            s_a = p_a + floor_a
            k_a = p_a / s_a
            x_a += k_a * (o["y_a"] - x_a)
            p_a *= (1.0 - k_a)
        out.append((x_v, p_v, x_a, p_a))
    return out, loglik


def identify(obs: Sequence[Dict[str, Any]], **spreads) -> Tuple[float, float]:
    """(time constant in minutes, wander share) under which this user's reads were most likely:
    the grids' middles until there are enough reads to tell."""
    middle = (TAU_GRID_MINUTES[len(TAU_GRID_MINUTES) // 2], Q_GRID[len(Q_GRID) // 2])
    if len(obs) < IDENTIFY_MIN_READS:
        return middle
    best, best_ll = middle, -math.inf
    for tau in TAU_GRID_MINUTES:
        for q in Q_GRID:
            _states, ll = _run(obs, tau, q, **spreads)
            if ll > best_ll + 1e-9:
                best, best_ll = (tau, q), ll
    return best


def identify_tau(obs: Sequence[Dict[str, Any]], **spreads) -> float:
    return identify(obs, **spreads)[0]


def _spreads(obs: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    """What the state's long-run spread and the reads' noise floor are for this user: measured
    from the reads, with the prior until there are two."""
    y_v = [o["y_v"] for o in obs]
    y_a = [o["y_a"] for o in obs if o["y_a"] is not None]
    return {"spread_v": _mean_square(y_v, FLOOR_PRIOR), "spread_a": _mean_square(y_a, FLOOR_PRIOR),
            "floor_v": _noise_floor(y_v, FLOOR_PRIOR), "floor_a": _noise_floor(y_a, FLOOR_PRIOR)}


def trajectory(rows: Sequence[Dict[str, Any]], tau_minutes: Optional[float] = None) -> List[Dict[str, Any]]:
    """The state after each read, oldest first: [{ts, valence, sd_valence, arousal, sd_arousal}]."""
    obs = observations(rows)
    if not obs:
        return []
    spreads = _spreads(obs)
    tau, q = identify(obs, **spreads)
    if tau_minutes:
        tau = float(tau_minutes)
    states, _ll = _run(obs, tau, q, **spreads)
    return [{"ts": o["ts"], "valence": s[0], "sd_valence": math.sqrt(max(s[1], 0.0)),
             "arousal": s[2], "sd_arousal": math.sqrt(max(s[3], 0.0))} for o, s in zip(obs, states)]


def _label_of(valence: float, var: float) -> str:
    if abs(valence) < STATE_MIN or var <= 0:
        return "neutral"
    return ("negative" if valence < 0 else "positive") if abs(valence) / math.sqrt(var) >= Z_SIG else "neutral"


def estimate(user_id: str = "", *, rows: Optional[Sequence[Dict[str, Any]]] = None,
             now: Optional[float] = None, limit: int = HOW_MANY_READS) -> AffectState:
    """The user's affect now, from their recent reads (eli/cognition/emotion_timeline), faded
    toward neutral for the time since the last read."""
    if rows is None:
        try:
            from eli.cognition import emotion_timeline
            rows = emotion_timeline.recent(limit=limit, user_id=user_id)
        except (ImportError, AttributeError, OSError) as failure:
            log.debug("affect reads unavailable: %s", failure)
            rows = []
    obs = observations(rows or [])
    at = float(now if now is not None else time.time())
    if not obs:
        return AffectState(at=at)
    spreads = _spreads(obs)
    tau, q = identify(obs, **spreads)
    states, _ll = _run(obs, tau, q, **spreads)
    x_v, p_v, x_a, p_a = states[-1]
    # faded for the quiet since the last read, the uncertainty growing back by the state's wander
    phi = math.exp(-max(0.0, at - obs[-1]["ts"]) / (tau * 60.0))
    x_v, x_a = phi * x_v, phi * x_a
    p_v = phi * phi * p_v + (1.0 - phi * phi) * q * spreads["spread_v"]
    p_a = phi * phi * p_a + (1.0 - phi * phi) * q * spreads["spread_a"]
    label = _label_of(x_v, p_v)
    signs = [(1 if s[0] > 0 else -1 if s[0] < 0 else 0) for s in states]
    head = 1 if label == "positive" else -1 if label == "negative" else 0
    run = 0
    for sgn in reversed(signs):
        if sgn != head:
            break
        run += 1
    transition: Dict[str, str] = {}
    if len(signs) >= 2 and signs[-1] != signs[-2]:
        names = {1: "positive", -1: "negative", 0: "neutral"}
        transition = {"from": names[signs[-2]], "to": names[signs[-1]]}
    confidence = 2.0 * _phi(abs(x_v) / math.sqrt(p_v)) - 1.0 if p_v > 0 else 0.0   # the chance the sign is meant
    return AffectState(valence=x_v, arousal=x_a, var_valence=p_v, var_arousal=p_a, reads=len(obs),
                       tau_minutes=tau, q_scale=q, label=label, confidence=max(0.0, min(1.0, confidence)),
                       run_length=run, transition=transition, at=at)


__all__ = ["AffectState", "SURPRISE_95", "SURPRISE_WINDOW", "FLOOR_PRIOR", "IDENTIFY_MIN_READS", "Q_GRID", "STATE_MIN", "TAU_GRID_MINUTES", "Z_SIG",
           "estimate", "identify", "identify_tau", "observations", "trajectory"]
