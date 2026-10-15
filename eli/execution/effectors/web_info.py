"""Web/info effector — WEB_SEARCH, NEWS_FETCH, GET_WEATHER, MORNING_REPORT."""
from __future__ import annotations

import re

from eli.utils.log import get_logger

log = get_logger(__name__)

_LOC_PATS = [
    re.compile(r"weather (?:in|at|for) ([A-Za-z][A-Za-z ,]+?)(?:\?|$|[,;])", re.I),
    re.compile(r"(?:in|at|for) ([A-Za-z][A-Za-z ,]+?) weather", re.I),
    re.compile(r"(?:temperature|forecast) (?:in|at) ([A-Za-z][A-Za-z ,]+?)(?:\?|$)", re.I),
]


def _h_morning_report(a, args):
    try:
        from eli.runtime.reflection import run_reflection, report_label
        from datetime import datetime as _dt
        result = run_reflection(hours=24)
        insights = result.get("insights", [])
        _stamp = _dt.now().strftime("%A %d %B %Y, %H:%M")
        # Named for the hour it is actually produced. "Morning report" was
        # hardcoded, so an 18:16 report announced itself as a morning one
        # directly beside the 18:16 it had just printed.
        _title = report_label()
        if insights:
            msg = (f"{_title} — {_stamp} (activity over the last 24h):\n"
                   + "\n".join(f"  • {i}" for i in insights))
        else:
            msg = f"{_title} — {_stamp}: no notable activity in the last 24h."
        # Personalised news (ELI-derived interests; network-gated → empty when
        # offline, never fabricated).
        try:
            from eli.tools.news.news_synthesis import interest_news_block
            _news = interest_news_block()
            if _news:
                msg += "\n\n" + _news
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_get_weather(a, args):
    try:
        location = str((args or {}).get("location") or (args or {}).get("city") or "").strip()

        from eli.plugins.weather.plugin import clean_place as _clean_place
        _raw = str((args or {}).get("_raw_user_text") or (args or {}).get("query") or "").strip()
        location = _clean_place(location)
        if not location:
            for _p in _LOC_PATS:
                _m = _p.search(_raw)
                if _m:
                    location = _clean_place(_m.group(1))
                    if location:
                        break

        # The day asked about ("tomorrow", "on friday"), so the answer is that day's forecast.
        _day = None
        try:
            from eli.runtime.agenda import parse_when as _when_in
            _asked_when = _when_in(_raw) if _raw else None
            if _asked_when is not None and _asked_when.has_date:
                _day = _asked_when.start.date()
        except Exception:
            log.debug("weather: day not read", exc_info=True)

        from eli.kernel import request_context as _rc_weather
        _sid = _rc_weather.session_id_var.get()
        if not location:
            # the place last asked about in this session, before asking again
            location = str(_rc_weather.get_session_sticky(_sid, "weather_place") or "")
        if not location:
            _msg = "Where? Give me a town or city."
            return {"ok": False, "action": a, "error": "missing_location", "content": _msg, "response": _msg,
                    "asks_user": True,
                    "awaiting": {"command": f"{_raw or 'weather'} in {{answer}}", "action": a, "needs": "place"}}

        # Network gate: weather is a live open-meteo call. Refuse honestly
        # when the Net toggle is off rather than confabulating a reading.
        from eli.core.netguard import should_block_network, offline_response, OfflineError
        if should_block_network():
            return offline_response(a, "check the weather")

        from eli.plugins.weather.plugin import get_weather as _gw
        try:
            result = _gw(location, _day)
        except OfflineError:
            return offline_response(a, "check the weather")
        if isinstance(result, dict):
            result.setdefault("action", a)
            if result.get("location"):
                _rc_weather.set_session_sticky(_sid, "weather_place", str(result["location"]).split(",")[0])
            return result
        return {"ok": True, "action": a, "content": str(result), "response": str(result)}

    except Exception as _e:
        return {"ok": False, "action": a, "error": str(_e), "content": str(_e), "response": str(_e)}


# A result that presents several things keeps them open for a follow-up (executor `listed` /
# `follow_up`, runtime.pending_proposal): "the second one" or "tell me more about the tennis
# story" re-fetches that story; after a search, a result is searched further.
_NEWS_FOLLOW_UP = {"action": "NEWS_FETCH", "arg": "topic",
                   "args": {"mode": "fetch_and_show", "sources": ["all"]}}
_WEB_FOLLOW_UP = {"action": "WEB_SEARCH", "arg": "query"}


def _listed_articles(articles) -> list:
    return [{"title": r.get("title"), "ref": r.get("url"), "summary": r.get("summary"),
             "source": r.get("source")}
            for r in (articles or []) if isinstance(r, dict) and r.get("title")]


def _keeping_open(result: dict, articles, follow_up: dict) -> dict:
    listed = _listed_articles(articles)
    if listed:
        result["listed"] = listed
        result["follow_up"] = dict(follow_up)
    return result


def _h_news_fetch(a, args):
    topic   = (args or {}).get('topic', '')
    sources = (args or {}).get('sources', ['all'])
    query   = (args or {}).get('query', '')
    mode    = (args or {}).get('mode', 'fetch_and_show')  # fetch_and_show | fetch | search | recent | stats
    try:
        from eli.tools.news.news_fetcher import NewsFetcher
        fetcher = NewsFetcher()

        def _format_articles(articles, header="", with_summaries=False):
            if not articles:
                return None
            from datetime import datetime as _dt, date as _date_cls
            _today = _date_cls.today()
            lines = [header] if header else []
            for r in articles:
                fetched_raw = r.get('fetched_at')
                pub_raw = (r.get('published') or '')[:10]
                # Show fetch time (HH:MM) for today's articles; date otherwise.
                # This prevents yesterday's RSS publication dates appearing as
                # "current" news when ELI fetched them today.
                if fetched_raw:
                    try:
                        fetched_dt = _dt.fromtimestamp(float(fetched_raw))
                        if fetched_dt.date() == _today:
                            date_str = f" (fetched {fetched_dt.strftime('%H:%M')})"
                        else:
                            # Article is from a prior fetch — show the fetch date
                            date_str = f" (fetched {fetched_dt.strftime('%d %b')})"
                    except Exception:
                        date_str = f" ({pub_raw})" if pub_raw else ""
                elif pub_raw:
                    date_str = f" ({pub_raw})"
                else:
                    date_str = ""
                lines.append(f"• [{r['source']}]{date_str} {r['title']}")
                if with_summaries and r.get('summary') and len(r['summary'].strip()) > 20:
                    lines.append(f"  {r['summary'][:160].strip()}…")
            return "\n".join(lines)

        if mode == 'search' and query:
            results = fetcher.search(query, limit=10)
            if not results:
                msg = f"No stored news found for '{query}'. Try: 'fetch latest news' first."
            else:
                msg = _format_articles(results, f"News results for '{query}':", with_summaries=True)
            return _keeping_open({'ok': True, 'action': a, 'content': msg, 'response': msg},
                                 results, _NEWS_FOLLOW_UP)

        elif mode == 'recent':
            # Auto-refresh if last fetch was more than 6 hours ago
            import time as _news_time
            st_info = fetcher.stats()
            _last_ts_str = st_info.get('last_fetched') or ''
            _stale = True
            if _last_ts_str and _last_ts_str != 'never':
                try:
                    import time as _nt
                    _last_ts = _nt.mktime(_nt.strptime(_last_ts_str, "%Y-%m-%d %H:%M:%S"))
                    _stale = (_news_time.time() - _last_ts) > 6 * 3600
                except Exception:
                    _stale = True
            if _stale:
                fetcher.fetch(sources=None, topic=topic)
            results = fetcher.get_recent(limit=12, category=topic)
            if not results:
                msg = "No news stored yet — fetching now…"
                res = fetcher.fetch(sources=None, topic=topic)
                results = fetcher.get_recent(limit=12, category=topic)
                if not results:
                    msg = f"Fetched {res['fetched']} articles but none stored. Check network."
                    return {'ok': True, 'action': a, 'content': msg, 'response': msg}
            msg = _format_articles(results, f"Recent headlines ({len(results)}):")
            return _keeping_open({'ok': True, 'action': a, 'content': msg, 'response': msg},
                                 results, _NEWS_FOLLOW_UP)

        elif mode == 'stats':
            st = fetcher.stats()
            msg = (f"News DB: {st['total_articles']} articles stored, "
                   f"last fetched {st['last_fetched']}.\n"
                   + "\n".join(f"  {src}: {cnt}" for src, cnt in st['by_source'].items()))
            return {'ok': True, 'action': a, 'content': msg, 'response': msg}

        else:  # fetch_and_show (default) or plain fetch
            _src = sources if sources and sources != ['all'] else None
            res = fetcher.fetch(sources=_src, topic=topic)
            errs = res.get('errors', [])
            _fetched = res['fetched']
            _new = res['stored_new']
            _new_str = f", {_new} new" if _new else ""
            header = f"Here are the latest headlines ({_fetched} fetched{_new_str}):"
            if _fetched == 0 and errs:
                msg = f"News fetch failed. Errors: {'; '.join(errs[:3])[:300]}"
                return {'ok': False, 'action': a, 'content': msg, 'response': msg}
            # Conversational news read: ELI synthesises a bounded briefing
            # (a sentence of context each, 1-2 follow-ups, no timestamps)
            # rather than dumping raw headlines. General ask = 50/50 top
            # stories + interest matches; a topic ask = that topic's stories.
            # Falls back to the raw list if synthesis is empty (offline/none).
            # The brief is built here and handed to the synthesis so its articles stay open
            # for a follow-up on the stories the briefing was written from.
            _brief: dict = {}
            try:
                from eli.tools.news.news_synthesis import build_news_briefing, synthesise_news_briefing
                _brief = build_news_briefing(topic=topic, refresh=False) or {}
                _read = synthesise_news_briefing(topic=topic, refresh=False, brief=_brief)
            except Exception:
                _read = ""
            if _read:
                return _keeping_open({'ok': True, 'action': a, 'content': _read,
                                      'response': _read,
                                      'meta': {'response_mode': 'news_briefing'}},
                                     list(_brief.get('top') or []) + list(_brief.get('interest') or []),
                                     _NEWS_FOLLOW_UP)
            recent = fetcher.get_recent(limit=10, category=topic)
            shown = recent
            msg = _format_articles(recent, header)
            if not msg and _fetched > 0:
                try:
                    from eli.tools.news.news_synthesis import synthesise_window as _synth_w
                    _sw = _synth_w()
                    msg = (_sw.get('digest') or _sw.get('summary') or '').strip() or None
                except Exception:
                    msg = None
                if not msg:
                    recent2 = fetcher.get_recent(limit=20)
                    msg = _format_articles(recent2, header)
                    shown = recent2
                msg = msg or f"Fetched {_fetched} articles — ask 'morning report' for a full digest."
            elif not msg:
                msg = f"Fetched {_fetched} articles."
            if errs:
                msg += f"\n\n⚠ Some sources failed: {'; '.join(errs[:2])[:200]}"
            return _keeping_open({'ok': True, 'action': a, 'content': msg, 'response': msg},
                                 shown, _NEWS_FOLLOW_UP)

    except Exception as _exc:
        return {'ok': False, 'action': a, 'error': str(_exc), 'content': str(_exc), 'response': str(_exc)}


def _h_web_search(a, args):
    try:
        query = (args.get("query") or args.get("text") or args.get("message") or "").strip()
        if not query:
            return {"ok": False, "action": a, "error": "No query provided",
                    "content": "Provide a search query.", "response": "Provide a search query."}

        # Network gate: never open a browser or confabulate when offline.
        try:
            from eli.core.config import network_allowed
            _net = network_allowed()
        except Exception:
            _net = False
        if not _net:
            msg = ("I can't search the web right now — network access is off. "
                   "Turn on the Net toggle (or set network_enabled) and ask again "
                   "and I'll look it up. I won't guess at current facts I can't verify.")
            return {"ok": False, "action": a, "query": query, "offline": True,
                    "content": msg, "response": msg}

        # Live DuckDuckGo text fetch — return real snippets as grounding,
        # NOT a browser window. The model answers from these, not its priors.
        results = []
        try:
            from eli.plugins.web.plugin import _web_search_results
            results = _web_search_results(query, max_results=5) or []
        except Exception:
            results = []
        # Supplement with the local news store if the live fetch was empty.
        if not results:
            try:
                from eli.tools.news.news_fetcher import search_stored_news
                for h in (search_stored_news(query, limit=5) or []):
                    results.append({"title": h.get("title", ""), "href": h.get("url", ""), "body": ""})
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        if not results:
            msg = (f"I searched the web for \"{query}\" but the search returned no usable "
                   f"results just now (the search backend may be rate-limited). I won't "
                   f"guess at an answer I can't verify — try rephrasing, or I can retry.")
            return {"ok": True, "action": a, "query": query, "results": [],
                    "content": msg, "response": msg}

        lines = [f"Live web results for \"{query}\" (authoritative — answer from these, not prior knowledge):"]
        for i, item in enumerate(results, 1):
            title = (item.get("title") or item.get("name") or "Untitled").strip()
            href = (item.get("href") or item.get("url") or "").strip()
            body = (item.get("body") or item.get("snippet") or "").strip()
            entry = f"{i}. {title}"
            if body:
                entry += f" — {body[:220]}"
            if href:
                entry += f"\n   {href}"
            lines.append(entry)
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "query": query, "results": results,
                "content": msg, "response": msg, "web_grounded": True,
                "listed": [{"title": (r.get("title") or r.get("name") or "").strip(),
                            "ref": (r.get("href") or r.get("url") or "").strip(),
                            "summary": (r.get("body") or r.get("snippet") or "").strip()}
                           for r in results if isinstance(r, dict)],
                "follow_up": dict(_WEB_FOLLOW_UP)}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "MORNING_REPORT": _h_morning_report,
    "GET_WEATHER": _h_get_weather,
    "NEWS_FETCH": _h_news_fetch,
    "WEB_SEARCH": _h_web_search,
}.items():
    _register(_action, _fn)
