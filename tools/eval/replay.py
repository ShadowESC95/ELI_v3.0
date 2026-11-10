"""Multi-session replay: what ELI keeps, recalls and learns over invented conversations, end to end.

The conversations in replay.yaml (invented people and facts, several sittings days apart) are typed
into the whole engine, one process per sitting as the app is started once per sitting, in a
throwaway copy of ELI's folders (eli_driver.new_sandbox / isolated_env: a fresh install, no desktop
session, the network off). The model is the stand-in (eli_driver.StubModel) unless a local model is
named. The replay's clock is the script's: each sitting runs on its own day (time.time and the time
module's readings of now are shifted; datetime is not). What the engine did is then read back from
its own stores and set against what the script says should have come of each turn:

    facts_precision      of the rows ELI kept as known about the user, the share that are facts the
                         script says to keep (a question, a request or a state of mind kept is a miss)
    facts_recall         of the facts to keep, the share still kept at the end (memories or archive)
    claims_precision     of the claims standing at the end, the share the script says should stand
    claims_recall        of the claims that should stand, the share that do
    recall_at_1/_3, mrr  for the later questions, where the memory that should come to mind stands
                         among the memories shown to the reply (the ones the next turn settles)
    evidence_recall      the share of those questions whose fact reached the reply's evidence: the
                         prompt the model was given, or the reply itself
    answer_recall        the share whose reply says it (with the stand-in, only replies ELI words
                         without the model can)
    settlement_accuracy  what ELI read the next message as saying of its last reply, against the script
    memory_settlement    what became of the memories shown to that reply (helped, corrected, left
                         alone), against the script
    conflict_handling    the share of changed values handled as the script says: an update applied,
                         a conflict raised with the user
    importance_order     how far the importance ELI gave the kept facts orders them as the script
                         does (pairwise, a tie counts half)
    interest_focus       of the topics the user keeps coming back to (the script's `interests`), the
                         share among the user's focus as ELI reads it at the end (memory/interest.focus,
                         as many topics as the script names)

Every figure is a share in 0..1, higher is better, None when the script gave it nothing to rest on.
A run of several replays (RUNS) gives each figure's mean and its spread, the run-to-run noise. A
figure regresses when it falls below the last recorded run by more than the noise either run
measured (regressions()). Runs are recorded under <data>/eval/history (run_eval.append_history);
the last run accepted for the repository is replay_baseline.json, which the test holds the code to.

    python tools/eval/run_eval.py --target replay                  # the figures, against the last run
    python tools/eval/run_eval.py --target replay --history        # ...and record this run
    python tools/eval/run_eval.py --target replay --record-baseline   # accept it for the repository
    python tools/eval/run_eval.py --target replay --model PATH.gguf   # with a small local model
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.eval import eli_driver as D  # noqa: E402

SCRIPT = Path(__file__).with_name("replay.yaml")
BASELINE = Path(__file__).with_name("replay_baseline.json")
TARGET = "replay"

# Method constants.
RUNS = 3                  # replays per measurement, each under its own hash seed (seed_of): what ELI
                          # leaves to the order of a set or a dict (ties broken by it) is the noise
RECALL_AT = (1, 3)        # the cut-offs recall is read at: the first memory shown, and the first three
MEASURE = 2               # the scoring's own version: a change to how a figure is read makes it a new measure
FIGURES = ("facts_precision", "facts_recall", "claims_precision", "claims_recall",
           "recall_at_1", "recall_at_3", "mrr", "evidence_recall", "answer_recall",
           "settlement_accuracy", "memory_settlement", "conflict_handling", "importance_order",
           "interest_focus")
VERDICTS = ("accepts", "disputes", "neither")
EXPECTS = ("update", "raise")
_TURN_KEYS = {"say", "keep", "matters", "claim", "recall", "verdict", "conflict", "eli"}


# ── the script ─────────────────────────────────────────────────────────────────────────────────────

def has(text: Any, words: Iterable[str]) -> bool:
    """Every one of `words` is in `text`, case ignored."""
    low = str(text or "").casefold()
    return all(str(w).casefold() in low for w in words)


def load_script(path: Path = SCRIPT) -> Dict[str, Any]:
    """replay.yaml, checked: a mistake in the script fails here, not as a figure."""
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sessions = data.get("sessions") or []
    if not sessions:
        raise ValueError(f"{path}: no sessions")
    days = [float(s.get("day", 0)) for s in sessions]
    if days != sorted(days):
        raise ValueError(f"{path}: sessions must be in the order of their days")
    for at in [data.get("at")] + [s.get("at") for s in sessions]:
        if at is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(at)):
            raise ValueError(f"{path}: a time of day is HH:MM, not {at!r}")
    for s in sessions:
        for i, t in enumerate(s.get("turns") or []):
            where = f"{path}: session {s.get('id')!r} turn {i + 1}"
            unknown = set(t) - _TURN_KEYS
            if unknown or not str(t.get("say") or "").strip():
                raise ValueError(f"{where}: needs `say`; unknown keys {sorted(unknown)}")
            if "keep" in t:
                if int(t.get("matters") or 0) not in (1, 2, 3):
                    raise ValueError(f"{where}: a fact to keep says how much it matters (1, 2 or 3)")
                keep = t["keep"] or []
                t["keep"] = [list(f) for f in keep] if keep and isinstance(keep[0], list) else [list(keep)]
                if not all(t["keep"]):
                    raise ValueError(f"{where}: a fact to keep names the words a kept row holds")
            if t.get("recall") and any(str(w).casefold() in t["say"].casefold() for w in t["recall"]):
                raise ValueError(f"{where}: the words to recall are in the question itself")
            if t.get("verdict") is not None and (t["verdict"] not in VERDICTS or i == 0):
                raise ValueError(f"{where}: a verdict is one of {VERDICTS} and follows a reply of the same sitting")
            c = t.get("conflict")
            if c is not None and (c.get("expect") not in EXPECTS or not {"relation", "from", "to"} <= set(c)):
                raise ValueError(f"{where}: a conflict names relation, from, to and expect ({EXPECTS})")
            if "eli" in t and (t.get("recall") or c is not None):
                raise ValueError(f"{where}: what ELI says to a question to recall or a changed value is "
                                 "what is measured; the stand-in is not told it")
    data["interests"] = [str(w) for w in data.get("interests") or []]
    data.setdefault("mode", "quick")
    data.setdefault("turn_minutes", 1)
    data["sessions"] = sessions
    return data


def script_id(path: Path = SCRIPT) -> str:
    """What the figures were measured on: a change to the script makes them a different measure."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


# ── the replay's clock ─────────────────────────────────────────────────────────────────────────────

class Clock:
    """The script's calendar for one process: time.time() and the time module's readings of now run
    `shift` seconds off the real clock (datetime keeps the real one). The last sitting runs on the
    real day, so what reads the real date agrees with the script where the questions are asked."""

    def __init__(self, shift: float) -> None:
        self.shift = float(shift)

    def install(self) -> "Clock":
        real_time, real_localtime, real_gmtime = time.time, time.localtime, time.gmtime
        real_strftime, real_ctime = time.strftime, time.ctime
        now = lambda: real_time() + self.shift  # noqa: E731
        time.time = now
        time.time_ns = lambda: int(now() * 1e9)
        time.localtime = lambda secs=None: real_localtime(now() if secs is None else secs)
        time.gmtime = lambda secs=None: real_gmtime(now() if secs is None else secs)
        time.strftime = lambda fmt, t=None: real_strftime(fmt, time.localtime() if t is None else t)
        time.ctime = lambda secs=None: real_ctime(now() if secs is None else secs)
        return self

    def advance(self, seconds: float) -> None:
        self.shift += float(seconds)


def _shift_of(script: Dict[str, Any], index: int, now: Optional[float] = None) -> float:
    """Seconds from the real clock to the start of sitting `index`: its day counted back from the
    last sitting, which is today, at its own time of day (`at`, else the script's, else the real
    time now), so a run gives the same figures whenever it is started."""
    import datetime
    sessions = script["sessions"]
    now = time.time() if now is None else float(now)
    days_back = float(sessions[-1].get("day", 0)) - float(sessions[index].get("day", 0))
    at = str(sessions[index].get("at") or script.get("at") or "")
    if not at:
        return -days_back * 86400.0
    # the calendar date first, then its wall-clock time: a summer-time change between the two days
    # moves neither the day nor the hour
    day = datetime.date.fromtimestamp(now) - datetime.timedelta(days=days_back)
    hour, minute = (int(x) for x in at.split(":"))
    return time.mktime((day.year, day.month, day.day, hour, minute, 0, 0, 0, -1)) - now


# ── one replay: a process per sitting, then one that reads the stores ──────────────────────────────

def _fresh_install(box: Path, model: str) -> None:
    """The copy starts as a fresh install does (install.sh): the clean settings template, offline;
    a named model is the configured one."""
    config = box / "config"
    config.mkdir(parents=True, exist_ok=True)
    settings = json.loads((_ROOT / "config" / "templates" / "settings.template.json").read_text(encoding="utf-8"))
    settings["network_enabled"] = False
    if model != D.STUB:
        settings["model_path"] = str(Path(model).expanduser().resolve())
    (config / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _child(env: Dict[str, str], box: Path, *args: str) -> None:
    out = box / "replay"
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / f"{args[0]}-{args[2]}.log"
    with log_path.open("w", encoding="utf-8") as log_file:
        done = subprocess.run([sys.executable, "-m", "tools.eval.replay", "_child", *args], cwd=str(_ROOT),
                              env=env, stdout=log_file, stderr=subprocess.STDOUT)
    if done.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        raise RuntimeError(f"the replay's {args[0]} process {args[2]} failed (exit {done.returncode}):\n{tail}")


def seed_of(run_index: int) -> str:
    """The hash seed of a measurement's run `run_index`: fixed, so the same code gives the same
    figures, and different from run to run, so what ELI leaves to the order of a set or a dict (a
    tie broken by it) shows as the spread between them. 0 would turn the hashing's randomness off."""
    return str(int(run_index) + 1)


def replay_once(script_path: Path = SCRIPT, model: str = D.STUB,
                env: Optional[Dict[str, str]] = None, run_index: int = 0) -> Dict[str, Any]:
    """One replay in its own throwaway copy of ELI's folders: the figures, what they rest on, and
    what was seen on the way (info). `env`: variables for the replay's processes (a switch under
    test, so a mechanism can be measured on and off). `run_index` picks its hash seed (seed_of)."""
    script = load_script(script_path)
    box = D.new_sandbox()
    try:
        _fresh_install(box, model)
        extra, env = dict(env or {}), D.isolated_env(box)
        if model == D.STUB:
            # no embedding model either, so the stand-in's figures are the same on every machine;
            # and no model state to save between calls (the stand-in has none)
            env["ELI_EMBED_MODEL_PATH"] = str(box / "no-embedder.gguf")
            env["ELI_PROMPT_STATE_REUSE"] = "0"
        env["PYTHONHASHSEED"] = seed_of(run_index)
        env.update(extra)
        for i in range(len(script["sessions"])):
            _child(env, box, "session", str(script_path), str(i), str(box), model)
        _child(env, box, "measure", str(script_path), "-", str(box), model)
        out = box / "replay"
        obs = [json.loads((out / f"obs-{i}.json").read_text(encoding="utf-8")) for i in range(len(script["sessions"]))]
        state = json.loads((out / "state.json").read_text(encoding="utf-8"))
        return score(script, obs, state)
    finally:
        shutil.rmtree(box, ignore_errors=True)


def run(runs: int = RUNS, model: str = D.STUB, script_path: Path = SCRIPT,
        env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """`runs` replays side by side: each figure's mean and its spread across them (the noise).
    `env` as for replay_once; a run with one is a different measure (its signature says so)."""
    runs = max(1, int(runs))
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=runs) as pool:
        results = list(pool.map(lambda i: replay_once(script_path, model, env, i), range(runs)))
    figures, noise = {}, {}
    for name in FIGURES:
        values = [r["figures"].get(name) for r in results]
        got = [float(v) for v in values if v is not None]
        figures[name] = round(sum(got) / len(got), 4) if got and len(got) == len(values) else None
        noise[name] = round(max(got) - min(got), 4) if got and len(got) == len(values) else None
    from eli.runtime.version_record import current_version
    return {
        "target": TARGET, "ts": time.strftime("%Y%m%dT%H%M%S"), "version": current_version(),
        "signature": {"model": D.STUB if model == D.STUB else Path(model).name,
                      "semantic": bool(results[0]["info"].get("semantic")), "script": script_id(script_path),
                      "mode": load_script(script_path)["mode"], "measure": MEASURE,
                      "seeds": [seed_of(i) for i in range(len(results))],
                      **({"env": dict(sorted(env.items()))} if env else {})},
        "runs": len(results), "figures": figures, "noise": noise, "counts": results[0]["counts"],
        "info": results[0]["info"], "per_run": [r["figures"] for r in results],
        "seconds": round(time.perf_counter() - t0, 1),
    }


# ── the gate ───────────────────────────────────────────────────────────────────────────────────────

def regressions(current: Dict[str, Any], reference: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Each figure the reference measured that now falls below it by more than the noise either run
    measured, or that is no longer measured at all."""
    out = []
    for name, was in (reference.get("figures") or {}).items():
        if was is None:
            continue
        now = (current.get("figures") or {}).get(name)
        allowed = max(float((reference.get("noise") or {}).get(name) or 0.0),
                      float((current.get("noise") or {}).get(name) or 0.0))
        if now is None or float(now) < float(was) - allowed - 1e-9:
            out.append({"figure": name, "now": now, "was": was, "noise": allowed})
    return out


def comparable(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Two runs measure the same thing: the same model, script, scoring, mode, semantic search,
    hash seeds and switches."""
    return dict(a.get("signature") or {}) == dict(b.get("signature") or {})


def baseline() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def reference_for(current: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The last recorded run this one compares with: the newest comparable run in the history that
    did not regress, else the repository's baseline when it is comparable."""
    from tools.eval.run_eval import last_recorded
    found = last_recorded(TARGET, lambda rec: comparable(rec, current) and not rec.get("regressed"))
    if found is not None:
        return found
    base = baseline()
    return base if base is not None and comparable(base, current) else None


def record(current: Dict[str, Any], regressed: Sequence[Dict[str, Any]]) -> Path:
    """Keep this run in the history, marked with what regressed, so a worse run never becomes the
    one later runs are held to."""
    from tools.eval.run_eval import append_history
    summary = {k: current.get(k) for k in ("ts", "target", "version", "signature", "runs", "figures", "noise", "counts")}
    summary["target"], summary["regressed"] = TARGET, [r["figure"] for r in regressed]
    return append_history(summary, {"summary": summary, "info": current.get("info"), "per_run": current.get("per_run")},
                          suffix=TARGET)


def record_baseline(current: Dict[str, Any]) -> Path:
    """Accept this run as the one the repository is held to (the test reads it)."""
    keep = {k: current[k] for k in ("ts", "target", "version", "signature", "runs", "figures", "noise", "counts")}
    BASELINE.write_text(json.dumps(keep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return BASELINE


# ── scoring: the script against what the engine did ────────────────────────────────────────────────

def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def _script_turns(script: Dict[str, Any]):
    for si, s in enumerate(script["sessions"]):
        for ti, t in enumerate(s.get("turns") or []):
            yield si, ti, t


def score(script: Dict[str, Any], obs: Sequence[Dict[str, Any]], state: Dict[str, Any]) -> Dict[str, Any]:
    """The figures for one replay: `obs` is what each sitting's process saw turn by turn, `state`
    what the stores held at the end (the measure process)."""
    kept_rows = [r for r in list(state.get("memories") or []) + list(state.get("archive") or [])
                 if r.get("user_evidence")]
    turn_obs = {(si, ti): o for si, sess in enumerate(obs) for ti, o in enumerate(sess.get("turns") or [])}
    # every fact to keep: (sitting, turn, its words, how much it matters)
    facts = [(si, ti, words, int(t["matters"])) for si, ti, t in _script_turns(script) for words in t.get("keep") or []]
    figures: Dict[str, Optional[float]] = {}
    counts: Dict[str, int] = {}

    # what was kept, and whether it should have been: each row ELI wrote as known about the user, as
    # it was written, against the facts said by then
    rows_written = []        # (row, facts said up to the turn that wrote it)
    for si, sess in enumerate(obs):
        said = [w for (fs, _ft, w, _m) in facts if fs < si]
        for ti, o in enumerate(sess.get("turns") or []):
            said = said + [w for (fs, ft, w, _m) in facts if fs == si and ft == ti]
            rows_written += [(r, list(said)) for r in o.get("new_rows") or [] if r.get("user_evidence")]
        rows_written += [(r, list(said)) for r in sess.get("end_new_rows") or [] if r.get("user_evidence")]
    figures["facts_precision"] = _mean([1.0 if any(has(r.get("text"), f) for f in said) else 0.0
                                        for r, said in rows_written])
    counts["rows_kept"] = len(rows_written)
    figures["facts_recall"] = _mean([1.0 if any(has(r.get("text"), w) for r in kept_rows) else 0.0
                                     for _si, _ti, w, _m in facts])
    counts["facts"] = len(facts)

    # the claims standing at the end
    expected: Dict[str, List[str]] = {}
    for _si, _ti, t in _script_turns(script):
        for rel, value in (t.get("claim") or {}).items():
            expected[str(rel)] = [str(value)]
        c = t.get("conflict")
        if c and c.get("expect") == "raise":
            expected[str(c["relation"])] = [str(c["from"]), str(c["to"])]     # either, until the user says
    standing = [c for c in state.get("claims") or [] if c.get("status") == "current" and c.get("subject") == "user"]

    def _stands(claim: Dict[str, Any]) -> bool:
        return any(has(claim.get("value"), str(v).split()) for v in expected.get(str(claim.get("relation")), []))

    figures["claims_precision"] = _mean([1.0 if _stands(c) else 0.0 for c in standing])
    figures["claims_recall"] = _mean([1.0 if any(str(c.get("relation")) == rel and _stands(c) for c in standing) else 0.0
                                      for rel in expected])
    counts["claims_standing"], counts["claims_expected"] = len(standing), len(expected)

    # the later questions: what came to mind, what reached the reply, what the reply said
    ranks, evidence, answered, seen_recall = [], [], [], []
    for si, ti, t in _script_turns(script):
        if not t.get("recall"):
            continue
        o = turn_obs.get((si, ti)) or {}
        shown = o.get("shown") or []
        ranks.append(next((r for r, m in enumerate(shown) if has(m.get("text"), t["recall"])), None))
        seen_recall.append({"say": t["say"], "rank": ranks[-1], "evidence": bool(o.get("evidence")),
                            "shown": [str(m.get("text") or "")[:80] for m in shown]})
        evidence.append(1.0 if o.get("evidence") else 0.0)
        answered.append(1.0 if has(o.get("reply"), t["recall"]) else 0.0)
    for k in RECALL_AT:
        figures[f"recall_at_{k}"] = _mean([1.0 if r is not None and r < k else 0.0 for r in ranks])
    figures["mrr"] = _mean([1.0 / (r + 1) if r is not None else 0.0 for r in ranks])
    figures["evidence_recall"], figures["answer_recall"] = _mean(evidence), _mean(answered)
    counts["recall_questions"] = len(ranks)

    # what the next message said of the last reply, and what became of the memories shown to it
    labels, memories, seen_verdicts = [], [], []
    for si, ti, t in _script_turns(script):
        if t.get("verdict") is None:
            continue
        o = turn_obs.get((si, ti)) or {}
        labels.append(1.0 if label_verdict(o.get("label")) == t["verdict"] else 0.0)
        moved = o.get("settled") or {}
        if moved:
            memories.append(1.0 if all(memory_verdict(m) == t["verdict"] for m in moved.values()) else 0.0)
        seen_verdicts.append({"say": t["say"], "truth": t["verdict"], "read": label_verdict(o.get("label")),
                              "memories": sorted({memory_verdict(m) for m in moved.values()})})
    figures["settlement_accuracy"], figures["memory_settlement"] = _mean(labels), _mean(memories)
    counts["verdicts"], counts["verdicts_with_memories"] = len(labels), len(memories)

    # changed values: applied when the user said it changed, raised when nothing says which is right
    handled, seen_conflicts = [], []
    for si, ti, t in _script_turns(script):
        c = t.get("conflict")
        if not c:
            continue
        seen = (turn_obs.get((si, ti)) or {}).get("conflict") or {}
        current = seen.get("current") or []
        held = current + (seen.get("disputed") or [])
        if c["expect"] == "update":
            ok = any(has(v, str(c["to"]).split()) for v in current) and not any(has(v, str(c["from"]).split()) for v in held)
        else:
            ok = bool(seen.get("raised"))
        handled.append(1.0 if ok else 0.0)
        seen_conflicts.append({"say": t["say"], "expect": c["expect"], "handled": ok, **seen})
    figures["conflict_handling"] = _mean(handled)
    counts["conflicts"] = len(handled)

    # the importance given to the kept facts, against how much the script says each matters
    weight = []
    for _si, _ti, words, matters in facts:
        found = [float(r.get("importance") or 0.0) for r in kept_rows if has(r.get("text"), words)]
        if found:
            weight.append((matters, max(found)))
    pairs = [(a, b) for a in weight for b in weight if a[0] > b[0]]
    figures["importance_order"] = _mean([1.0 if a[1] > b[1] else (0.5 if a[1] == b[1] else 0.0) for a, b in pairs])
    counts["importance_pairs"] = len(pairs)

    # what the user keeps coming back to, against what ELI reads as their focus
    focus = [str(w) for w in state.get("focus") or []]
    figures["interest_focus"] = _mean([1.0 if any(has(w, [stem]) for w in focus) else 0.0
                                       for stem in script.get("interests") or []])
    counts["interests"] = len(script.get("interests") or [])

    info = {
        "semantic": bool(state.get("semantic")),
        "duplicate_rows": sum(max(0, sum(1 for r in kept_rows if has(r.get("text"), w)) - 1) for _s, _t, w, _m in facts),
        "archived": len([r for r in state.get("archive") or [] if r.get("user_evidence")]),
        "actions": sorted({a for sess in obs for o in sess.get("turns") or [] for a in o.get("executed") or []}),
        "missed_rows": [str(r.get("text"))[:120] for r, said in rows_written if not any(has(r.get("text"), f) for f in said)],
        "facts_lost": [" ".join(w) for _si, _ti, w, _m in facts if not any(has(r.get("text"), w) for r in kept_rows)],
        "recall": seen_recall, "verdicts": seen_verdicts, "conflicts": seen_conflicts,
        "importance": sorted(((m, round(i, 3)) for m, i in weight), reverse=True),
        "focus": focus,
        # every claim about the user standing at the end, and whether the script says it should
        "claims": [{"relation": str(c.get("relation")), "value": str(c.get("value"))[:120],
                    "origin": str(c.get("origin") or c.get("source_kind") or ""), "stands": _stands(c)}
                   for c in standing],
    }
    return {"figures": {name: figures.get(name) for name in FIGURES}, "counts": counts, "info": info}


def label_verdict(label: Any) -> str:
    """The engine's reading of a message (engine._reply_label) in the script's words: a correction
    disputes the reply, an endorsement accepts it, anything else (a new request, a complaint about
    how something was done, a redo) leaves it standing as it was."""
    name = str(getattr(label, "label", None) or (label[0] if isinstance(label, (tuple, list)) and label else label)
               or "").strip().lower()
    if name in ("correction", "disputes", "wrong"):
        return "disputes"
    if name in ("endorsement", "accepts", "right"):
        return "accepts"
    return "neither"


def memory_verdict(moved: Dict[str, Any]) -> str:
    """What became of one memory shown to the reply: used (accepts), corrected (disputes), untouched."""
    helped, corrected = int(moved.get("helped") or 0), int(moved.get("corrected") or 0)
    if corrected > 0 and helped <= 0:
        return "disputes"
    if helped > 0 and corrected <= 0:
        return "accepts"
    return "neither" if helped == corrected == 0 else "mixed"


# ── the processes of a replay ──────────────────────────────────────────────────────────────────────

def _db_rows(db: Path, sql: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _max_id(db: Path) -> int:
    rows = _db_rows(db, "SELECT COALESCE(MAX(id), 0) AS n FROM memories")
    return int(rows[0]["n"]) if rows else 0


def _rows_since(db: Path, after: int) -> List[Dict[str, Any]]:
    """The memory rows written since row `after`, as they were written: text, origin, and whether
    ELI counts them as known about the user (eli/memory/policy)."""
    from eli.memory import policy
    return [{"id": int(r["id"]), "text": str(r.get("text") or ""), "origin": str(r.get("origin") or ""),
             "importance": r.get("importance"),
             "user_evidence": policy.counts_as_evidence_about_user(str(r.get("origin") or ""))}
            for r in _db_rows(db, "SELECT id, COALESCE(text, content, value, '') AS text, origin, importance "
                                  "FROM memories WHERE id > ? ORDER BY id", (after,))]


def _counts(db: Path, ids: Sequence[int]) -> Dict[int, Dict[str, int]]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {int(r["id"]): {"helped": int(r.get("recall_count") or 0), "corrected": int(r.get("corrected_count") or 0)}
            for r in _db_rows(db, f"SELECT id, recall_count, corrected_count FROM memories WHERE id IN ({marks})", ids)}


def _session_process(script_path: str, index: int, box: str, model: str) -> None:
    """One sitting: the app's start (a fresh install the first time, the day's upkeep), the turns,
    the app's close. Writes what was seen turn by turn to obs-<index>.json."""
    script = load_script(Path(script_path))
    sitting = script["sessions"][index]
    clock = Clock(_shift_of(script, index)).install()
    D.enter_sandbox(Path(box))
    if index == 0:
        from eli.core.init_data import init_all_data
        init_all_data()
    engine = D.get_engine(model, background=False)
    if engine is None:
        raise RuntimeError("the engine could not be built")
    engine.session_id = f"replay-{sitting['id']}"
    memory = engine.memory
    db = Path(memory.db_path)
    memory.run_upkeep()        # what the app's start does when the day's upkeep is due, here in the foreground

    import eli.kernel.engine as E
    from eli.cognition import gguf_inference as G, thoughts
    from eli.runtime import pending_proposal
    seen: Dict[str, Any] = {"labels": [], "prompts": [], "facts": {}}
    read_label, invoke, audit = E._reply_label, G._safe_invoke_llm, engine._record_turn_audit_row

    def _label(text):
        verdict = read_label(text)
        seen["labels"].append(verdict)
        return verdict

    def _invoke(llm, full_prompt, **kw):
        if kw.get("grammar") is None:
            seen["prompts"].append(str(full_prompt))
        return invoke(llm, full_prompt, **kw)

    def _audit(facts, ok, outcome, result=None):
        seen["facts"] = facts
        return audit(facts, ok, outcome, result)

    E._reply_label, G._safe_invoke_llm, engine._record_turn_audit_row = _label, _invoke, _audit

    turns: List[Dict[str, Any]] = []
    for ti, t in enumerate(sitting.get("turns") or []):
        if model == D.STUB:
            G._llm.reply = str(t.get("eli") or D.STUB_REPLY)
        seen["labels"].clear()
        seen["prompts"].clear()
        seen["facts"] = {}
        before = _max_id(db)
        last_shown = [int(m["id"]) for m in (turns[-1]["shown"] if turns else [])]
        counts_before = _counts(db, last_shown)
        res = D.run_engine(t["say"], reasoning_mode=str(script["mode"]), session_id=engine.session_id)
        if res.get("error") or res.get("skipped"):
            # a turn the engine could not take is the replay failing, never a figure
            raise RuntimeError(f"the turn {t['say']!r} did not run: {res.get('error') or res.get('reason')}")
        facts = seen["facts"] or {}
        reply = str(res.get("text") or "")
        shown_ids = [int(i) for i in (engine._pending_recall_ids or [])]
        text_of = {int(r["id"]): r for r in _db_rows(db, f"SELECT id, text FROM memories WHERE id IN "
                                                          f"({','.join('?' * len(shown_ids))})", shown_ids)} if shown_ids else {}
        o: Dict[str, Any] = {
            "say": t["say"], "request_id": str(facts.get("request_id") or ""), "action": str(res.get("action") or ""),
            "executed": [str(a) for a in dict.fromkeys(facts.get("executed_actions") or []) if a],
            "reply": reply[:2000], "label": str(seen["labels"][0]) if seen["labels"] else None,
            "shown": [{"id": i, "text": str((text_of.get(i) or {}).get("text") or "")} for i in shown_ids],
            "new_rows": _rows_since(db, before),
        }
        if t.get("recall"):
            o["evidence"] = has(reply, t["recall"]) or any(has(p, t["recall"]) for p in seen["prompts"])
        if t.get("verdict") is not None and counts_before:
            after = _counts(db, last_shown)
            o["settled"] = {str(i): {"helped": after.get(i, {}).get("helped", 0) - c["helped"],
                                     "corrected": after.get(i, {}).get("corrected", 0) - c["corrected"]}
                            for i, c in counts_before.items()}
        c = t.get("conflict")
        if c:
            relation = str(c["relation"])
            rows = [r for r in _db_rows(db, "SELECT value, status, subject FROM memory_claims WHERE relation = ?",
                                        (relation,)) if r.get("subject") == "user"]
            open_ = pending_proposal.get_follow_up() or {}
            asked = str(open_.get("question") or "") + " " + " ".join(str(i.get("command") or "") for i in open_.get("items") or [])
            named = [v for v in (str(c["from"]), str(c["to"])) if has(asked, v.split())]
            raised = []
            if o["request_id"] and _db_rows(thoughts._default_db_path(), "SELECT id FROM thoughts WHERE kind = "
                                            "'conflict' AND request_id = ?", (o["request_id"],)):
                raised.append("thought")
            if named:
                raised.append("question")
            if has(reply, str(c["from"]).split()) and has(reply, str(c["to"]).split()):
                raised.append("reply")
            o["conflict"] = {"current": [r["value"] for r in rows if r.get("status") == "current"],
                             "disputed": [r["value"] for r in rows if r.get("status") == "disputed"],
                             "raised": raised}
        turns.append(o)
        clock.advance(float(script["turn_minutes"]) * 60.0)

    before = _max_id(db)
    engine.shutdown()
    out = {"session": sitting["id"], "turns": turns, "end_new_rows": _rows_since(db, before)}
    (Path(box) / "replay" / f"obs-{index}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def _measure_process(script_path: str, box: str) -> None:
    """What the stores hold at the end of the replay, read the way ELI reads them."""
    script = load_script(Path(script_path))
    last = script["sessions"][-1]
    Clock(_shift_of(script, len(script["sessions"]) - 1)
          + float(script["turn_minutes"]) * 60.0 * len(last.get("turns") or [])).install()
    D.enter_sandbox(Path(box))
    from eli.core import paths
    from eli.memory import policy
    from eli.memory.interest import focus
    from eli.memory.vector_store import resolve_embedder_path
    db = Path(paths.user_db_path())

    def _rows(table: str) -> List[Dict[str, Any]]:
        rows = _db_rows(db, f"SELECT * FROM {table}")
        for r in rows:
            r["user_evidence"] = policy.counts_as_evidence_about_user(str(r.get("origin") or ""))
            r["text"] = str(r.get("text") or r.get("content") or r.get("value") or "")
        return [{k: r.get(k) for k in ("id", "text", "kind", "source", "origin", "importance", "weight",
                                        "recall_count", "corrected_count", "user_evidence")} for r in rows]

    state = {
        "memories": _rows("memories"), "archive": _rows("memories_archive"),
        "claims": _db_rows(db, "SELECT relation, value, status, subject, origin FROM memory_claims"),
        "semantic": Path(resolve_embedder_path()).is_file() and not str(resolve_embedder_path()).startswith(str(box)),
        "focus": [w for w, _share in focus(limit=max(1, len(script["interests"])))],
    }
    (Path(box) / "replay" / "state.json").write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")


def _child_main(argv: Sequence[str]) -> int:
    kind, script_path, index, box, model = argv
    if kind == "session":
        _session_process(script_path, int(index), box, model)
    else:
        _measure_process(script_path, box)
    return 0


def table(result: Dict[str, Any], reference: Optional[Dict[str, Any]] = None) -> str:
    """The figures as the command line prints them, beside the last recorded run when there is one."""
    lines = [f"  ELI replay - {result['runs']} run(s), {result['signature']['model']} model, "
             f"script {result['signature']['script']}, {result['seconds']} s", "  " + "-" * 62]
    for name in FIGURES:
        now, noise = result["figures"].get(name), result["noise"].get(name)
        was = (reference or {}).get("figures", {}).get(name) if reference else None
        shown = "   -  " if now is None else f"{now:6.3f}"
        lines.append(f"  {name:22s} {shown}  noise {noise if noise is not None else '-':<6}"
                     + (f"  last {was:.3f}" if isinstance(was, (int, float)) else ""))
    lines.append("  " + "-" * 62)
    lines.append("  rests on: " + ", ".join(f"{k} {v}" for k, v in result["counts"].items()))
    return "\n".join(lines)


if __name__ == "__main__":
    if sys.argv[1:2] == ["_child"]:
        raise SystemExit(_child_main(sys.argv[2:]))
    raise SystemExit("run the replay with: python tools/eval/run_eval.py --target replay")
