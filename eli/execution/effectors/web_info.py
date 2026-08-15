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

        if not location:
            _raw = str((args or {}).get("_raw_user_text") or (args or {}).get("query") or "").strip()
            for _p in _LOC_PATS:
                _m = _p.search(_raw)
                if _m:
                    location = _m.group(1).strip().rstrip("?.!, ")
                    break

        if not location:
            _msg = "I need a location. Try: 'What's the weather in Wexford?'"
            return {"ok": False, "action": a, "error": "missing_location", "content": _msg, "response": _msg}

        # Network gate: weather is a live open-meteo call. Refuse honestly
        # when the Net toggle is off rather than confabulating a reading.
        from eli.core.netguard import should_block_network, offline_response, OfflineError
        if should_block_network():
            return offline_response(a, "check the weather")

        from eli.plugins.weather.plugin import get_weather as _gw
        try:
            result = _gw(location)
        except OfflineError:
            return offline_response(a, "check the weather")
        if isinstance(result, dict):
            result.setdefault("action", a)
            return result
        return {"ok": True, "action": a, "content": str(result), "response": str(result)}

    except Exception as _e:
        return {"ok": False, "action": a, "error": str(_e), "content": str(_e), "response": str(_e)}


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
            return {'ok': True, 'action': a, 'content': msg, 'response': msg}

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
            return {'ok': True, 'action': a, 'content': msg, 'response': msg}

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
            try:
                from eli.tools.news.news_synthesis import synthesise_news_briefing
                _read = synthesise_news_briefing(topic=topic, refresh=False)
            except Exception:
                _read = ""
            if _read:
                return {'ok': True, 'action': a, 'content': _read,
                        'response': _read,
                        'meta': {'response_mode': 'news_briefing'}}
            recent = fetcher.get_recent(limit=10, category=topic)
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
                msg = msg or f"Fetched {_fetched} articles — ask 'morning report' for a full digest."
            elif not msg:
                msg = f"Fetched {_fetched} articles."
            if errs:
                msg += f"\n\n⚠ Some sources failed: {'; '.join(errs[:2])[:200]}"
            return {'ok': True, 'action': a, 'content': msg, 'response': msg}

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
                "content": msg, "response": msg, "web_grounded": True}
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
