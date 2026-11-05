"""
eli/runtime/action_outcomes.py
──────────────────────────────
What became of ELI's actions, as something ELI learns from.

The evidence ledger records every execution (eli/runtime/evidence_ledger.py: a tool_execution row
with whether it worked and the chance predicted beforehand, an executor_action row with what it was
about and what came back). That record answered "how reliable is this action" (action_reliability)
and nothing else: a failure was logged, classified (runtime/failure_taxonomy) and, for a missing app
or tool, met with an offer to install it (runtime/grounded_remediation), and the next request tried
the same thing the same way. This reads the record back as knowledge ELI acts on:

    target_class(action, args)      the kind of target a run was about: an app by name, a site by its
                                    host, a path by its extension or folder, a query, or the action alone
    verify(action, args, result)    the world checked after a run, where it can be: a written file is
                                    there and new, a search brought results; (verified, how), or
                                    (None, "") when nothing here can see the effect
    cause_of(result)                why a run failed, by the one taxonomy, with "nothing_found" for a
                                    search that came back empty and "unverified" for an effect ELI
                                    could not see
    chance(action, args)            the chance a run like this works here: a Beta estimate over the runs
                                    of this target class, shrunk toward the action's rate and that toward
                                    every action's, each run weighted by its recency as the ledger's
                                    reliability is; with how many runs it rests on and at which level
    alternatives(action, args)      what has worked instead: an action that succeeded for the same
                                    target soon after this one failed, ranked by how often it did
    advice(action, args, result)    for a failed run: the cause, the chance, how often this has failed
                                    lately, the alternatives, and the one to try now when one is safe,
                                    likelier, and proven on this very target
    said(advice)                    the advice in words, for the reply
    report(days, action)            what worked and did not, by action and target, with the causes and
                                    what worked instead: ELI's own record of its abilities, as data and
                                    in words, for the status reports, the self-analysis and the world

Nothing here phrases a reply beyond these words, and nothing is a hand-set limit: the shrinkage is
the usual pseudo-count, the window the ledger's own, the significance the project's one.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from eli.utils.log import get_logger

log = get_logger(__name__)

DAYS = 30.0                     # how far back runs count, as action_reliability counts them
HALF_LIFE_DAYS = 7.0            # a run's weight halves each week, as action_reliability weighs them
# A target class with this many runs stands half on its own against its action's rate, and an action
# with this many against every action's: the usual pseudo-count shrinkage, no more.
SHRINK_RUNS = 3.0
# A success within this long of a failure in the same session, for the same target, is the same
# request tried another way: what worked instead.
ALTERNATIVE_WINDOW_S = 600.0
CACHE_SECONDS = 30.0            # the ledger is read again after this long, as the agent bus reads it
NOTHING_FOUND = "nothing_found"
UNVERIFIED = "unverified"
# Actions never run of ELI's own accord as an alternative: those that destroy, send, pay, or reach
# outside, and those the catalogue keeps explicit-only.
_NEVER_ON_ITS_OWN = re.compile(r"DELETE|REMOVE|SHELL|RUN_COMMAND|EXEC|SEND|EMAIL|PAY|BUY|UNINSTALL|FORMAT|KILL|"
                               r"SHUTDOWN|REBOOT|RESET|FORGET|WIPE|PURGE", re.I)
_WRITES = re.compile(r"WRITE|CREATE|GENERATE|SAVE|CONVERT|DOWNLOAD|EXPORT|RENDER|SCREENSHOT", re.I)
_SEARCHES = re.compile(r"SEARCH|NEWS_FETCH|LOOKUP|FIND", re.I)
_APPS = frozenset({"OPEN_APP", "CLOSE_APP", "FOCUS_APP", "MAXIMISE_WINDOW", "MINIMIZE_APP"})
_PATH_KEYS = ("path", "file", "folder", "doc_path", "filename", "output_path", "dest")
_URL_KEYS = ("url", "href", "link")
_NAME_KEYS = ("app", "name", "target")
_QUERY_KEYS = ("query", "topic", "q", "instruction", "text", "message")

# What a cause means, in words (the taxonomy's categories, plus the two read from a result's shape).
CAUSE_WORDS = {
    "dependency": "something it needs is not installed",
    "missing_resource": "what it was pointed at was not there",
    "permission": "it was not allowed",
    "timeout": "it took too long",
    "network": "the network did not answer",
    "resource": "the machine was short of memory or space",
    "user_input": "the request was missing a part",
    "data": "the data was not in the shape it expected",
    "interface": "an error inside ELI",
    "correctness": "an error inside ELI",
    "concurrency": "something else held what it needed",
    "stability": "an error inside ELI",
    NOTHING_FOUND: "nothing came back",
    UNVERIFIED: "nothing changed that ELI could see",
}


# ── what a run was about ──────────────────────────────────────────────────────────────────────────

def _first(args: Dict[str, Any], keys: Tuple[str, ...]) -> str:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_url(text: str) -> bool:
    return bool(re.match(r"^(?:https?://|www\.)", text, re.I)) or bool(re.match(r"^[\w.-]+\.[a-z]{2,}(?:/|$)", text, re.I))


def subject_of(action: str, args: Optional[Dict[str, Any]]) -> str:
    """The thing a run was about, as said: the app, the address, the path or the query."""
    args = dict(args or {})
    return (_first(args, _URL_KEYS) or _first(args, _PATH_KEYS) or _first(args, _NAME_KEYS)
            or _first(args, _QUERY_KEYS))


def target_class(action: str, args: Optional[Dict[str, Any]]) -> str:
    """The kind of target a run was about, so runs that are alike are judged together: "app:spotify",
    "site:example.org", "path:.pdf" (or "path:folder"), "query", or "" for an action alone."""
    args = dict(args or {})
    url = _first(args, _URL_KEYS)
    name = _first(args, _NAME_KEYS)
    if url or (name and _is_url(name)):
        host = urlparse(url or name if (url or name).startswith(("http://", "https://")) else "http://" + (url or name)).netloc
        host = host.lower()[4:] if host.lower().startswith("www.") else host.lower()
        return f"site:{host}" if host else "query"
    path = _first(args, _PATH_KEYS)
    if path:
        ext = os.path.splitext(path)[1].lower()
        return f"path:{ext}" if ext else "path:folder"
    if name and (str(action or "").upper() in _APPS or "app" in args):
        try:
            from eli.execution.app_aliases import APP_ALIASES
            name = APP_ALIASES.get(name.lower(), name)
        except ImportError as failure:   # the name as said is the class
            log.debug("app aliases unavailable: %s", failure)
        return f"app:{name.lower()}"
    if _first(args, _QUERY_KEYS):
        return "query"
    return f"app:{name.lower()}" if name else ""


# ── what a run did, checked ───────────────────────────────────────────────────────────────────────

def _file_named(args: Dict[str, Any], result: Dict[str, Any]) -> str:
    for source in (result, args):
        for key in _PATH_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def verify(action: str, args: Optional[Dict[str, Any]], result: Any, *, started: Optional[float] = None) -> Tuple[Optional[bool], str]:
    """The world checked after a run, where this can see it. A run that writes names a file: it is
    there and newer than the run began. A search brings results: the result carries them. Anything
    else is (None, ""): not checked, which is not a verdict either way."""
    if not isinstance(result, dict) or not result.get("ok", False):
        return None, ""
    name = str(action or "").upper()
    args = dict(args or {})
    if _WRITES.search(name):
        path = _file_named(args, result)
        if path:
            full = os.path.expanduser(path)
            if not os.path.exists(full):
                return False, f"the file {os.path.basename(full)} is not there"
            try:
                if started is not None and os.path.getmtime(full) < float(started) - 1.0:
                    return False, f"the file {os.path.basename(full)} was not written by this run"
            except OSError:
                return None, ""
            return True, f"the file {os.path.basename(full)} is there"
    if _SEARCHES.search(name):
        for key in ("results", "items", "listed", "entries", "articles", "hits"):
            found = result.get(key)
            if isinstance(found, (list, tuple)):
                return (True, f"{len(found)} results came back") if found else (False, "nothing came back")
    return None, ""


def cause_of(result: Any, action: str = "") -> str:
    """Why a run failed: the taxonomy's category for its error, NOTHING_FOUND for a search that came
    back empty, UNVERIFIED for an effect ELI could not see; "" for a run that worked."""
    if not isinstance(result, dict):
        return ""
    if result.get("verified") is False:
        return NOTHING_FOUND if "came back" in str(result.get("verified_how") or "") else UNVERIFIED
    if result.get("ok", False) and result.get("played") is not False and result.get("effect") is not False:
        return ""
    if result.get("effect") is False:
        return NOTHING_FOUND
    text = " ".join(str(result.get(k) or "") for k in ("error", "message", "response", "content"))
    try:
        from eli.runtime.failure_taxonomy import classify_category
        return classify_category(text, str(action or ""))
    except ImportError:
        return "stability"


def cause_words(cause: str) -> str:
    return CAUSE_WORDS.get(str(cause or ""), "an error inside ELI")


# ── the record, read once ─────────────────────────────────────────────────────────────────────────

_cache: Dict[str, Any] = {"at": 0.0, "runs": [], "where": ""}


def _runs(*, days: float = DAYS, db_path=None) -> List[Dict[str, Any]]:
    """Every execution of the last `days`, oldest first, each with its target class and whether it
    worked; read once and kept CACHE_SECONDS."""
    from eli.runtime.evidence_ledger import executor_runs
    now = time.time()
    where = str(db_path or "")
    if now - float(_cache["at"]) < CACHE_SECONDS and _cache["where"] == where and _cache.get("days") == days:
        return _cache["runs"]
    out: List[Dict[str, Any]] = []
    try:
        rows = executor_runs(since=now - float(days) * 86400.0, db_path=db_path)
    except Exception as failure:  # noqa: BLE001 - the ledger unreadable: nothing is known, nothing is invented
        log.debug("executor runs unavailable: %s", failure)
        rows = []
    for row in rows:
        payload = row.get("payload") or {}
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        outcome = str(row.get("outcome") or "")
        if outcome not in ("ok", "failed", "no_effect"):
            continue
        out.append({**row, "args": args, "result": result, "worked": outcome == "ok",
                    "class": target_class(row["action"], args), "subject": subject_of(row["action"], args),
                    "cause": str(payload.get("cause") or cause_of(result, row["action"]) or ""),
                    "alternative_of": str(args.get("_alternative_of") or "")})
    _cache.update({"at": now, "runs": out, "where": where, "days": days})
    return out


def forget() -> None:
    """Read the ledger again on the next question (a run was just recorded)."""
    _cache["at"] = 0.0


def _weight(ts: float, now: float) -> float:
    return 0.5 ** (max(0.0, now - float(ts or 0.0)) / 86400.0 / HALF_LIFE_DAYS)


def _rate(runs: List[Dict[str, Any]], now: float, prior_p: float, prior_runs: float) -> Tuple[float, int, float]:
    """A Beta estimate over these runs, each weighted by its recency, shrunk toward `prior_p` by
    `prior_runs` pseudo-runs: (chance, how many runs, their summed weight)."""
    ok = sum(_weight(r["ts"], now) for r in runs if r["worked"])
    total = sum(_weight(r["ts"], now) for r in runs)
    return (ok + prior_p * prior_runs) / (total + prior_runs), len(runs), total


def chance(action: str, args: Optional[Dict[str, Any]] = None, *, days: float = DAYS, db_path=None,
           now: Optional[float] = None) -> Dict[str, Any]:
    """The chance a run like this works here: the runs of this target class, shrunk toward the
    action's rate, which is shrunk toward every action's (Beta(1, 1) at the top), each run weighted
    by recency. {"p", "n", "by": "target" | "action" | "all", "action_p", "action_n", "cause": the
    commonest cause of its failures}."""
    now = time.time() if now is None else now
    name = str(action or "").upper()
    cls = target_class(name, args)
    every = _runs(days=days, db_path=db_path)
    p_all, _n_all, _w = _rate(every, now, 0.5, 2.0)
    mine = [r for r in every if r["action"] == name]
    p_action, n_action, _w = _rate(mine, now, p_all, SHRINK_RUNS)
    same = [r for r in mine if cls and r["class"] == cls]
    p_target, n_target, _w = _rate(same, now, p_action, SHRINK_RUNS)
    failures = [r["cause"] for r in (same or mine) if not r["worked"] and r["cause"]]
    cause = max(set(failures), key=failures.count) if failures else ""
    if same:
        return {"p": p_target, "n": n_target, "by": "target", "class": cls, "action_p": p_action,
                "action_n": n_action, "cause": cause}
    if mine:
        return {"p": p_action, "n": n_action, "by": "action", "class": cls, "action_p": p_action,
                "action_n": n_action, "cause": cause}
    return {"p": p_all, "n": 0, "by": "all", "class": cls, "action_p": p_action, "action_n": 0, "cause": ""}


def _same_aim(failed: Dict[str, Any], later: Dict[str, Any]) -> bool:
    """Whether a later run was the same request tried another way: the same target class with the
    same subject, or a subject that names the failed one ("spotify" in "open.spotify.com")."""
    a, b = failed["subject"].lower(), later["subject"].lower()
    if failed["class"] and failed["class"] == later["class"] and a == b:
        return True
    core = re.sub(r"[^a-z0-9]+", "", a)
    return bool(core) and len(core) >= 3 and core in re.sub(r"[^a-z0-9]+", "", b)


def alternatives(action: str, args: Optional[Dict[str, Any]] = None, *, days: float = DAYS, db_path=None,
                 now: Optional[float] = None) -> List[Dict[str, Any]]:
    """What has worked instead: each action that succeeded for the same aim within
    ALTERNATIVE_WINDOW_S of a failure of this action, with the chance it works after such a failure
    (Beta over the times it was tried that way), how many times, the last time, and whether it was
    proven after a failure on this very target (`same_target`). Likeliest first."""
    now = time.time() if now is None else now
    name = str(action or "").upper()
    cls = target_class(name, args)
    subject = subject_of(name, args).lower()
    every = _runs(days=days, db_path=db_path)
    found: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for i, run in enumerate(every):
        if run["action"] != name or run["worked"] or (cls and run["class"] != cls):
            continue
        for later in every[i + 1:]:
            if later["ts"] - run["ts"] > ALTERNATIVE_WINDOW_S:
                break
            if later["session_id"] != run["session_id"] or later["action"] == name or not _same_aim(run, later):
                continue
            key = (later["action"], later["subject"].lower())
            slot = found.setdefault(key, {"action": later["action"], "args": dict(later["args"]), "ok": 0.0,
                                          "tried": 0.0, "n": 0, "last": 0.0, "subject": later["subject"],
                                          "on_this": False})
            w = _weight(later["ts"], now)
            slot["tried"] += w
            slot["n"] += 1
            slot["last"] = max(slot["last"], later["ts"])
            slot["on_this"] = slot["on_this"] or run["subject"].lower() == subject
            if later["worked"]:
                slot["ok"] += w
    out = []
    for slot in found.values():
        p = (slot["ok"] + 1.0) / (slot["tried"] + 2.0)
        out.append({"action": slot["action"], "args": {k: v for k, v in slot["args"].items() if not k.startswith("_")},
                    "p": p, "n": slot["n"], "last": slot["last"], "subject": slot["subject"],
                    "same_target": bool(slot["on_this"])})
    out.sort(key=lambda a: (a["p"], a["same_target"], a["last"]), reverse=True)
    return out


def _safe_on_its_own(action: str) -> bool:
    name = str(action or "").upper()
    if not name or name == "CHAT" or _NEVER_ON_ITS_OWN.search(name):
        return False
    try:
        from eli.cognition.llm_intent import _EXPLICIT_ONLY_ACTIONS
        if name in _EXPLICIT_ONLY_ACTIONS:
            return False
    except ImportError as failure:   # with no list of explicit-only actions, only the name's own words say
        log.debug("explicit-only actions unavailable: %s", failure)
    return True


def advice(action: str, args: Optional[Dict[str, Any]], result: Any, *, days: float = DAYS, db_path=None,
           now: Optional[float] = None) -> Dict[str, Any]:
    """For a failed run: its cause, the chance a run like it works here, how many of its recent runs
    failed, what has worked instead, and `attempt`: the alternative to try now, or None. One is tried
    of ELI's own accord only when it is safe to run unasked, it was proven on this very target, and its
    chance is better than this action's own."""
    now = time.time() if now is None else now
    name = str(action or "").upper()
    args = dict(args or {})
    cause = cause_of(result, name)
    odds = chance(name, args, days=days, db_path=db_path, now=now)
    others = alternatives(name, args, days=days, db_path=db_path, now=now)
    cls = target_class(name, args)
    recent = [r for r in _runs(days=days, db_path=db_path) if r["action"] == name and (not cls or r["class"] == cls)]
    failed_lately = sum(1 for r in recent if not r["worked"])
    attempt = None
    if not args.get("_alternative_of"):
        for other in others:
            if other["same_target"] and other["p"] > odds["p"] and _safe_on_its_own(other["action"]):
                attempt = {"action": other["action"], "args": {**other["args"], "_alternative_of": name}}
                break
    return {"action": name, "subject": subject_of(name, args), "cause": cause, "cause_words": cause_words(cause),
            "chance": odds, "failed_lately": failed_lately, "runs_lately": len(recent),
            "alternatives": others, "attempt": attempt}


def plain(action: str) -> str:
    """An action's name as words: OPEN_APP is "open app"."""
    return str(action or "").replace("_", " ").lower()


_plain = plain


def _rate_words(ok: int, n: int) -> str:
    if n <= 0:
        return "has not run here before"
    if ok == n:
        return f"worked every time ({n})" if n > 1 else "worked the one time it ran"
    if ok == 0:
        return f"failed every time ({n})" if n > 1 else "failed the one time it ran"
    return f"worked {ok} of {n} times"


def said(advice_: Dict[str, Any]) -> str:
    """The advice in words: what failed and why, how it has fared here, and what has worked instead,
    with what ELI is doing about it. "" with nothing to say."""
    if not advice_:
        return ""
    name = _plain(advice_.get("action", ""))
    subject = str(advice_.get("subject") or "").strip()
    about = f"{name} for {subject}" if subject else name
    parts: List[str] = []
    n, failed = int(advice_.get("runs_lately") or 0), int(advice_.get("failed_lately") or 0)
    if n > 1:
        parts.append(f"{about} has {_rate_words(n - failed, n)} here lately")
    cause = str(advice_.get("cause_words") or "")
    if cause:
        parts.append(("this time " if n > 1 else "") + cause)
    others = [o for o in (advice_.get("alternatives") or []) if o.get("p", 0) > 0.5]
    attempt = advice_.get("attempt")
    if attempt:
        parts.append(f"{_plain(attempt['action'])} worked instead before, so I am trying that now")
    elif others:
        o = others[0]
        when = f"{o['n']} time{'s' if o['n'] != 1 else ''}"
        parts.append(f"{_plain(o['action'])} worked instead {when}; say so if you want me to try that")
    return ("; ".join(parts) + ".").strip() if parts else ""


# ── the record, told ──────────────────────────────────────────────────────────────────────────────

def report(days: float = DAYS, action: str = "", *, limit: int = 12, db_path=None,
           now: Optional[float] = None) -> Dict[str, Any]:
    """What worked and did not: by action (and within it by target class), with the causes of its
    failures and what worked instead, as data and in words. Actions with failures first, then by
    how often they ran. `action` keeps it to one."""
    now = time.time() if now is None else now
    every = _runs(days=days, db_path=db_path)
    want = str(action or "").upper()
    by_action: Dict[str, Dict[str, Any]] = {}
    for run in every:
        if want and run["action"] != want:
            continue
        if run["alternative_of"]:
            continue
        slot = by_action.setdefault(run["action"], {"action": run["action"], "n": 0, "ok": 0, "causes": {},
                                                    "targets": {}, "last": 0.0})
        slot["n"] += 1
        slot["ok"] += 1 if run["worked"] else 0
        slot["last"] = max(slot["last"], run["ts"])
        if not run["worked"] and run["cause"]:
            slot["causes"][run["cause"]] = slot["causes"].get(run["cause"], 0) + 1
        t = slot["targets"].setdefault(run["class"] or "(none)", {"n": 0, "ok": 0})
        t["n"] += 1
        t["ok"] += 1 if run["worked"] else 0
    rows = []
    for slot in by_action.values():
        p, n, _w = _rate([r for r in every if r["action"] == slot["action"] and not r["alternative_of"]], now, 0.5, 2.0)
        slot["p"] = p
        worst = min(slot["targets"].items(), key=lambda kv: (kv[1]["ok"] + 1.0) / (kv[1]["n"] + 2.0)) if slot["targets"] else None
        slot["worst_target"] = worst[0] if worst and worst[1]["ok"] < worst[1]["n"] else ""
        sample = {"_alternative_of": ""}
        if slot["worst_target"].startswith("app:"):
            sample = {"name": slot["worst_target"][4:]}
        elif slot["worst_target"].startswith("site:"):
            sample = {"url": "https://" + slot["worst_target"][5:]}
        elif slot["worst_target"].startswith("path:"):
            sample = {"path": "x" + slot["worst_target"][5:] if slot["worst_target"] != "path:folder" else "x/"}
        slot["alternatives"] = [a for a in alternatives(slot["action"], sample, days=days, db_path=db_path, now=now)
                                if a["p"] > 0.5][:3]
        rows.append(slot)
    rows.sort(key=lambda s: (s["n"] - s["ok"], s["n"], s["last"]), reverse=True)
    rows = rows[:int(limit)]
    lines = []
    for slot in rows:
        line = f"{_plain(slot['action'])} {_rate_words(slot['ok'], slot['n'])}"
        if slot["causes"]:
            why = ", ".join(f"{cause_words(c)} ({k})" if k > 1 else cause_words(c)
                            for c, k in sorted(slot["causes"].items(), key=lambda kv: -kv[1])[:2])
            line += f"; the failures were because {why}"
        if slot["worst_target"] and len(slot["targets"]) > 1:
            t = slot["targets"][slot["worst_target"]]
            line += f"; weakest for {slot['worst_target'].split(':', 1)[-1]} ({t['ok']} of {t['n']})"
        if slot["alternatives"]:
            o = slot["alternatives"][0]
            line += f"; {_plain(o['action'])} worked instead {o['n']} time{'s' if o['n'] != 1 else ''}"
        lines.append(line + ".")
    return {"days": float(days), "actions": rows, "runs": len(every), "lines": lines,
            "words": ("What has worked here in the last %d days, and what has not: " % int(days)) + " ".join(lines)
            if lines else f"No action has run here in the last {int(days)} days."}


def words(days: float = DAYS, *, limit: int = 6, db_path=None) -> str:
    """The report's lines, for a status answer: the actions with failures first."""
    return "\n".join(report(days, limit=limit, db_path=db_path)["lines"])


__all__ = ["ALTERNATIVE_WINDOW_S", "CAUSE_WORDS", "DAYS", "NOTHING_FOUND", "SHRINK_RUNS", "UNVERIFIED", "advice",
           "alternatives", "cause_of", "cause_words", "chance", "forget", "plain", "report", "said", "subject_of",
           "target_class", "verify", "words"]
