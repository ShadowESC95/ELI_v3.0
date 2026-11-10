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
    alternatives(action, args)      what has worked instead: an action that does the same deed by other
                                    means (SAME_DEED) and succeeded for the same thing soon after this
                                    one failed, ranked by how often it did; ELI's own tries are not
                                    evidence, and each later run counts once
    advice(action, args, result)    for a failed run: the cause, the chance, how often this has failed
                                    lately, the alternatives proven for this very thing, and the one to
                                    try now when one is likelier, built from this request's own words
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
# A success within this long of a failure in the same session, for the same thing, by an action that
# does the same deed, is the same request tried another way: what worked instead.
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
# The item asked for: a song asked of a service is the song, so it comes before the service's name.
_ITEM_KEYS = ("query", "topic", "q")
_QUERY_KEYS = _ITEM_KEYS + ("instruction", "text", "message")
# What a run carries as content, never replayed from an old run into a new one.
_CONTENT_KEYS = ("text", "content", "body", "message", "instruction", "code")

# Actions that do the same deed by other means. When one of a group fails, another of the same group
# done for the same thing soon after is the same request tried another way; nothing else is. This is
# declared, not read from the names: two actions that share a word need not do the same thing
# (PLAY_MEDIA and PLAY_GAME, READ_FILE and DELETE_FILE), and an action that undoes another (CLOSE_APP
# after OPEN_APP, STOP_MEDIA or PAUSE_MEDIA after PLAY_MEDIA) or writes, sends or destroys is never in
# a group with it (tests/test_action_outcomes.py holds every group to that). An action in no group has
# no alternative: its failure is told as it is.
SAME_DEED: Tuple[frozenset, ...] = (
    frozenset({"OPEN_APP", "OPEN_URL", "FOCUS_APP"}),        # bring a program or a site up
    frozenset({"WEB_SEARCH", "NEWS_FETCH"}),                 # find out about something
    frozenset({"MINIMIZE_APP", "MINIMISE_APP"}),             # the same deed, spelt two ways
    frozenset({"MINIMIZE_WINDOW", "MINIMISE_WINDOW"}),
)

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
    "stability": "the cause is not known",
    NOTHING_FOUND: "nothing came back",
    UNVERIFIED: "nothing changed that ELI could see",
    "mismatch": "what came up was not what was asked for",
    "not_a_program": "what it was pointed at is a site, not a program",
}
# The taxonomy's word for a failure nothing explains: its cause is not asserted.
UNKNOWN_CAUSE = "stability"
MISMATCH = "mismatch"


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
    """The thing a run was about, as said: the address, the path, the item asked for (a song asked of
    Spotify is the song, not Spotify), the app, or the query."""
    args = dict(args or {})
    return (_first(args, _URL_KEYS) or _first(args, _PATH_KEYS) or _first(args, _ITEM_KEYS)
            or _first(args, _NAME_KEYS) or _first(args, _QUERY_KEYS))


def _host(text: str) -> str:
    text = str(text or "").strip()
    host = urlparse(text if text.startswith(("http://", "https://")) else "http://" + text).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def target_class(action: str, args: Optional[Dict[str, Any]]) -> str:
    """The kind of target a run was about, so runs that are alike are judged together: "app:spotify",
    "site:example.org", "path:.pdf" (or "path:folder"), "service:spotify" for an item asked of a
    service (every song asked of Spotify), "query", or "" for an action alone."""
    args = dict(args or {})
    url = _first(args, _URL_KEYS)
    name = _first(args, _NAME_KEYS)
    if url or (name and _is_url(name)):
        host = _host(url or name)
        return f"site:{host}" if host else "query"
    path = _first(args, _PATH_KEYS)
    if path:
        ext = os.path.splitext(path)[1].lower()
        return f"path:{ext}" if ext else "path:folder"
    if name and _first(args, _ITEM_KEYS) and str(action or "").upper() not in _APPS:
        return f"service:{name.lower()}"
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

# Where a run that writes says it wrote: a place, so a bare file name ("filename": "x.py") is not one.
_WRITTEN_KEYS = ("path", "script_path", "doc_path", "output_path", "saved_to", "pdf_path", "file", "filepath", "dest")


def _file_named(args: Dict[str, Any], result: Dict[str, Any]) -> str:
    for source in (result, args):
        for key in _WRITTEN_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip() and os.path.dirname(os.path.expanduser(value.strip())):
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


def cause_of(result: Any, action: str = "", args: Optional[Dict[str, Any]] = None) -> str:
    """Why a run failed, from what the run itself says first: the cause its effector named, MISMATCH
    when something other than what was asked came up, NOTHING_FOUND for a search that came back
    empty, UNVERIFIED for an effect ELI could not see, "not_a_program" for a program's action handed
    a site; then the taxonomy's category for its error text, which is UNKNOWN_CAUSE when nothing
    explains it. "" for a run that worked."""
    if not isinstance(result, dict):
        return ""
    if result.get("verified") is False:
        return NOTHING_FOUND if "came back" in str(result.get("verified_how") or "") else UNVERIFIED
    if result.get("ok", False) and result.get("played") is not False and result.get("effect") is not False:
        return ""
    named = str(result.get("cause") or "")
    if named in CAUSE_WORDS:
        return named
    if result.get("mismatch"):
        return MISMATCH
    if result.get("effect") is False:
        return NOTHING_FOUND
    if args and str(action or "").upper() in _APPS and target_class(action, args).startswith("site:"):
        return "not_a_program"
    text = " ".join(str(result.get(k) or "") for k in ("error", "message", "response", "content"))
    try:
        from eli.runtime.failure_taxonomy import classify_category
        return classify_category(text, str(action or ""))
    except ImportError:
        return UNKNOWN_CAUSE


def cause_words(cause: str) -> str:
    return CAUSE_WORDS.get(str(cause or ""), CAUSE_WORDS[UNKNOWN_CAUSE])


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
                    "cause": str(payload.get("cause") or cause_of(result, row["action"], args) or ""),
                    "alternative_of": str(args.get("_alternative_of") or "")})
    _cache.update({"at": now, "runs": out, "where": where, "days": days})
    return out


def forget() -> None:
    """Read the ledger again on the next question (a run was just recorded)."""
    _cache["at"] = 0.0


def _asked(*, days: float, db_path=None) -> List[Dict[str, Any]]:
    """The runs the user asked for: an alternative ELI tried of its own accord is not evidence that
    it works, or each try would raise the case for the next."""
    return [r for r in _runs(days=days, db_path=db_path) if not r["alternative_of"]]


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
    every = _asked(days=days, db_path=db_path)
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


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").lower())


def same_deed(one: str, other: str) -> bool:
    """Whether two different actions do the same deed by other means (SAME_DEED)."""
    a, b = str(one or "").upper(), str(other or "").upper()
    return a != b and any(a in group and b in group for group in SAME_DEED)


def _same_aim(failed: Dict[str, Any], later: Dict[str, Any]) -> bool:
    """Whether a later run was the same request tried another way: an action that does the same deed
    for the same thing, said the same way ("It's okay" is "its okay") or as an address whose host
    names it ("spotify" is open.spotify.com; "code" is not leetcode.com)."""
    if not same_deed(failed["action"], later["action"]):
        return False
    a, b = _norm(failed["subject"]), _norm(later["subject"])
    if not a:
        return False
    if a == b:
        return True
    return bool(_is_url(later["subject"])) and a in {_norm(label) for label in _host(later["subject"]).split(".")}


def alternatives(action: str, args: Optional[Dict[str, Any]] = None, *, days: float = DAYS, db_path=None,
                 now: Optional[float] = None) -> List[Dict[str, Any]]:
    """What has worked instead: each action that does the same deed (SAME_DEED) and was asked for the
    same thing within ALTERNATIVE_WINDOW_S of a failure of this action in the same session, with the
    chance it works after such a failure (Beta over the times it was tried that way), how many times,
    the last time, and whether it was proven after a failure on this very thing (`same_target`).
    Each later run answers the one failure nearest before it, so it counts once; ELI's own tries do
    not count. Likeliest first."""
    now = time.time() if now is None else now
    name = str(action or "").upper()
    if not any(name in group for group in SAME_DEED):
        return []
    cls = target_class(name, args)
    subject = _norm(subject_of(name, args))
    every = _asked(days=days, db_path=db_path)
    found: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for j, later in enumerate(every):
        if not same_deed(name, later["action"]):
            continue
        for run in reversed(every[:j]):
            if later["ts"] - run["ts"] > ALTERNATIVE_WINDOW_S:
                break
            if (run["action"] != name or run["worked"] or (cls and run["class"] != cls)
                    or run["session_id"] != later["session_id"] or not _same_aim(run, later)):
                continue
            key = (later["action"], _norm(later["subject"]))
            slot = found.setdefault(key, {"action": later["action"], "args": dict(later["args"]), "ok": 0.0,
                                          "tried": 0.0, "n": 0, "last": 0.0, "subject": later["subject"],
                                          "on_this": False})
            w = _weight(later["ts"], now)
            slot["tried"] += w
            slot["n"] += 1
            slot["last"] = max(slot["last"], later["ts"])
            slot["on_this"] = slot["on_this"] or (bool(subject) and _norm(run["subject"]) == subject)
            if later["worked"]:
                slot["ok"] += w
            break
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
    if not name or name == "CHAT" or _NEVER_ON_ITS_OWN.search(name) or _WRITES.search(name):
        return False
    try:
        from eli.cognition.llm_intent import _EXPLICIT_ONLY_ACTIONS
        if name in _EXPLICIT_ONLY_ACTIONS:
            return False
    except ImportError as failure:   # with no list of explicit-only actions, only the name's own words say
        log.debug("explicit-only actions unavailable: %s", failure)
    return True


def _attempt_args(proven: Dict[str, Any], asked: Dict[str, Any], action: str) -> Dict[str, Any]:
    """The arguments to try an alternative with now: how it reached the thing when it worked (its
    address, its app), with the item this request asked for; never an old run's content or item."""
    out = {k: v for k, v in proven.items() if not k.startswith("_") and k not in _CONTENT_KEYS}
    item = _first(asked, _ITEM_KEYS) or subject_of(action, asked)
    for key in _ITEM_KEYS:
        if key in out:
            out[key] = item
    return out


def advice(action: str, args: Optional[Dict[str, Any]], result: Any, *, days: float = DAYS, db_path=None,
           now: Optional[float] = None) -> Dict[str, Any]:
    """For a failed run: its cause, the chance a run like it works here, how many of its recent runs
    failed (over its target class, named in `class`), the alternatives proven for this very thing, and
    `attempt`: the one to try now, or None. One is tried of ELI's own accord only when it does the same
    deed for this same thing, it was proven on it, it writes, sends and destroys nothing, and its chance
    is better than this action's own; it is built from this request's own arguments."""
    now = time.time() if now is None else now
    name = str(action or "").upper()
    args = dict(args or {})
    cause = cause_of(result, name, args)
    odds = chance(name, args, days=days, db_path=db_path, now=now)
    others = [o for o in alternatives(name, args, days=days, db_path=db_path, now=now) if o["same_target"]]
    cls = target_class(name, args)
    recent = [r for r in _asked(days=days, db_path=db_path) if r["action"] == name and (not cls or r["class"] == cls)]
    failed_lately = sum(1 for r in recent if not r["worked"])
    attempt = None
    if not args.get("_alternative_of"):
        for other in others:
            if other["p"] > odds["p"] and _safe_on_its_own(other["action"]):
                attempt = {"action": other["action"],
                           "args": {**_attempt_args(other["args"], args, name), "_alternative_of": name}}
                break
    return {"action": name, "subject": subject_of(name, args), "class": cls, "cause": cause,
            "cause_words": cause_words(cause), "chance": odds, "failed_lately": failed_lately,
            "runs_lately": len(recent), "alternatives": others, "attempt": attempt}


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


def _population(action: str, cls: str) -> str:
    """What a rate is over, in words: the runs of this action for that kind of target ("play media on
    spotify" is every song asked of Spotify, not the one asked now)."""
    name = _plain(action)
    kind, _, what = str(cls or "").partition(":")
    if kind == "service" and what:
        return f"{name} on {what}"
    if kind in ("app", "site") and what:
        return f"{name} for {what}"
    if kind == "path" and what:
        return f"{name} for folders" if what == "folder" else f"{name} for {what} files"
    return name


def said(advice_: Dict[str, Any], tried: Any = None) -> str:
    """The advice in words: how runs like this have fared here (and over which runs), why this one
    failed when that is known, and what has worked instead, with what ELI did about it: `tried` is
    what came of the alternative it tried. "" with nothing to say."""
    if not advice_:
        return ""
    parts: List[str] = []
    n, failed = int(advice_.get("runs_lately") or 0), int(advice_.get("failed_lately") or 0)
    if n > 1:
        about = _population(advice_.get("action", ""), str(advice_.get("class") or ""))
        parts.append(f"{about} has {_rate_words(n - failed, n)} here lately")
    cause = str(advice_.get("cause_words") or "")
    if cause and str(advice_.get("cause") or "") != UNKNOWN_CAUSE:
        parts.append(("this time " if n > 1 else "") + cause)
    others = [o for o in (advice_.get("alternatives") or []) if o.get("p", 0) > 0.5]
    attempt = advice_.get("attempt")
    if attempt:
        what = f"{_plain(attempt['action'])} worked instead before, so I "
        if not isinstance(tried, dict):
            parts.append(what + "am trying that now")
        else:
            from eli.runtime.evidence_ledger import execution_outcome
            parts.append(what + ("tried that" if execution_outcome(tried) == "ok" else "tried that, and it failed too"))
    elif others:
        o = others[0]
        when = f"{o['n']} time{'s' if o['n'] != 1 else ''}"
        parts.append(f"{_plain(o['action'])} worked instead {when}; say so if you want me to try that")
    return ("; ".join(parts) + ".").strip() if parts else ""


# ── the record, told ──────────────────────────────────────────────────────────────────────────────

def report(days: float = DAYS, action: str = "", *, limit: Optional[int] = 12, db_path=None,
           now: Optional[float] = None) -> Dict[str, Any]:
    """What worked and did not: by action (and within it by target class), with the causes of its
    failures, what worked instead, and how it went week by week ("weeks": every calendar week of the
    window, oldest first, {"from", "n", "ok"}: epistemics.by_week), as data and in words. Actions with
    failures first, then by how often they ran. `action` keeps it to one; `limit` None keeps every action."""
    now = time.time() if now is None else now
    every = _asked(days=days, db_path=db_path)
    want = str(action or "").upper()
    by_action: Dict[str, Dict[str, Any]] = {}
    for run in every:
        if want and run["action"] != want:
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
    from eli.cognition.epistemics import by_week
    for slot in by_action.values():
        runs = [r for r in every if r["action"] == slot["action"]]
        p, n, _w = _rate(runs, now, 0.5, 2.0)
        slot["p"] = p
        slot["weeks"] = [{"from": w["from"], "n": w["n"], "ok": int(w["sum"])}
                         for w in by_week([(r["ts"], 1.0 if r["worked"] else 0.0) for r in runs], days=days, now=now)]
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
    rows = rows[:int(limit)] if limit is not None else rows
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


__all__ = ["ALTERNATIVE_WINDOW_S", "CAUSE_WORDS", "DAYS", "MISMATCH", "NOTHING_FOUND", "SAME_DEED", "SHRINK_RUNS",
           "UNKNOWN_CAUSE", "UNVERIFIED", "advice", "alternatives", "cause_of", "cause_words", "chance", "forget",
           "plain", "report", "said", "same_deed", "subject_of", "target_class", "verify", "words"]
