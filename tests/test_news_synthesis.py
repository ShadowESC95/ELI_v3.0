"""News synthesis — pure helpers + the freshness/recency guarantees.

Covers the model-free logic of the news read: one-sentence gisting, cross-domain
spread (so one busy category can't dominate), article-age computation, and the
freshness disclosure that stops a stale cache being narrated as "the latest"
(added to close the "two-week-old news reported as breaking" bug). Runs in the
normal suite — no model, no network.
"""
from __future__ import annotations

import datetime as dt

import pytest

from eli.tools.news import news_synthesis as ns


# --------------------------------------------------------------------------- #
# _one_sentence_gist
# --------------------------------------------------------------------------- #
def test_gist_takes_first_sentence():
    assert ns._one_sentence_gist("First sentence. Second sentence.") == "First sentence."


def test_gist_multi_sentence():
    out = ns._one_sentence_gist("One. Two. Three.", max_sentences=2)
    assert out == "One. Two."


def test_gist_collapses_whitespace():
    assert ns._one_sentence_gist("hello   world\n\n  foo!") == "hello world foo!"


def test_gist_respects_cap():
    long = "word " * 200  # far over the cap
    assert len(ns._one_sentence_gist(long, cap=240)) <= 240


def test_gist_empty_is_empty():
    assert ns._one_sentence_gist("") == ""
    assert ns._one_sentence_gist("   \n  ") == ""
    assert ns._one_sentence_gist(None) == ""


# --------------------------------------------------------------------------- #
# _spread_across_domains
# --------------------------------------------------------------------------- #
def _art(title, category):
    return {"title": title, "category": category, "source": category}


def test_spread_prefers_domain_diversity():
    arts = [
        _art("a1", "world"), _art("a2", "world"), _art("a3", "world"),
        _art("b1", "science"), _art("c1", "tech"),
    ]
    picked = ns._spread_across_domains(arts, 3)
    cats = {p["category"] for p in picked}
    # Round-robin: 3 picks should span all 3 domains, not 3 from "world".
    assert cats == {"world", "science", "tech"}


def test_spread_dedupes_by_title():
    arts = [_art("dup", "world"), _art("dup", "science"), _art("unique", "tech")]
    picked = ns._spread_across_domains(arts, 5)
    titles = [p["title"] for p in picked]
    assert titles.count("dup") == 1 and "unique" in titles


def test_spread_caps_at_n_and_available():
    arts = [_art("x", "world"), _art("y", "science")]
    assert len(ns._spread_across_domains(arts, 5)) == 2   # only 2 available
    assert len(ns._spread_across_domains(arts, 1)) == 1   # capped at n
    assert ns._spread_across_domains([], 5) == []


# --------------------------------------------------------------------------- #
# _article_age_days
# --------------------------------------------------------------------------- #
def test_age_from_iso_published():
    # "now minus a bit" — not a hardcoded 08:00, which becomes a FUTURE time when the
    # test runs in the small hours and throws the day-math off by one.
    recent = (dt.datetime.now() - dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert ns._article_age_days({"published": recent}) == 0


def test_age_counts_days_back():
    ten_ago = (dt.datetime.now() - dt.timedelta(days=10, minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert ns._article_age_days({"published": ten_ago}) == 10


def test_age_from_rfc822_pubdate():
    # RSS <pubDate> style
    d = (dt.datetime.now() - dt.timedelta(days=3))
    rfc = d.strftime("%a, %d %b %Y %H:%M:%S +0000")
    age = ns._article_age_days({"published": rfc})
    assert age in (2, 3, 4)  # tolerant of tz/rounding


def test_age_falls_back_to_fetched_at():
    ts = (dt.datetime.now() - dt.timedelta(days=5)).timestamp()
    assert ns._article_age_days({"fetched_at": ts}) == 5


def test_age_none_when_no_usable_date():
    assert ns._article_age_days({}) is None
    assert ns._article_age_days({"published": "not a date"}) is None


# --------------------------------------------------------------------------- #
# Freshness disclosure — offline must never be narrated as "the latest"
# --------------------------------------------------------------------------- #
def test_offline_briefing_flags_stale_and_discloses(monkeypatch):
    import eli.core.config as cfg
    monkeypatch.setattr(cfg, "network_allowed", lambda: False)
    b = ns.build_news_briefing(refresh=False, top_n=3, interest_n=2)
    assert b.get("ok") is True
    assert b.get("offline") is True and b.get("stale") is True
    prompt = b.get("synthesis_prompt", "")
    assert "FRESHNESS" in prompt
    assert "MUST NOT imply any story is from today" in prompt


def test_derive_interest_terms_merges_profile_and_conversation(monkeypatch):
    import eli.kernel.state as st
    monkeypatch.setattr(st, "load_user_profile", lambda user_id=None: {
        "research": ["quantum computing and photonics"],
    })
    monkeypatch.setattr(
        ns,
        "_conversation_focus_terms",
        lambda user_id=None, limit=5, min_count=2: ["netflix", "streaming"],
    )
    terms = ns._derive_interest_terms(limit=8)
    assert any(t in terms for t in ("quantum", "computing", "photonics"))
    assert any(t in terms for t in ("netflix", "streaming"))


def test_conversation_focus_terms_are_the_users_interests_less_noise(monkeypatch):
    """The news reads the one estimator of the user's interests (eli/memory/interest.py): a topic said
    on one day only is not yet an interest, and self-referential noise is left out."""
    import time as _time
    from types import SimpleNamespace
    from eli.memory import interest

    now = _time.time()
    turns = [{"role": "user", "content": "tell me about astrophysics, the system and dark matter", "timestamp": now - 3 * 86400},
             {"role": "user", "content": "more on astrophysics and the system", "timestamp": now - 60}]
    monkeypatch.setattr("eli.memory.memory.get_memory", lambda *a, **k: SimpleNamespace(
        get_recent_conversation=lambda **k: turns))
    monkeypatch.setattr("eli.runtime.reflection.topic_words",
                        lambda text: {w for w in ("astrophysics", "system", "matter") if w in text})
    interest.forget()
    terms = ns._conversation_focus_terms(limit=5)
    interest.forget()
    assert terms == ["astrophysics"], terms
    import eli.core.config as cfg
    monkeypatch.setattr(cfg, "network_allowed", lambda: True)
    b = ns.build_news_briefing(refresh=False, top_n=3, interest_n=2)
    assert b.get("offline") is False


# ── the user's interests are matched here, never sent out ──────────────────────
def _record_fetches(monkeypatch):
    import eli.core.config as cfg
    from eli.tools.news import news_fetcher as nf
    from eli.tools.news import news_synthesis as ns
    topics = []
    monkeypatch.setattr(cfg, "network_allowed", lambda: True)
    monkeypatch.setattr(ns, "_derive_interest_terms", lambda user_id=None, limit=8: ["quasars", "beekeeping"])
    monkeypatch.setattr(nf.NewsFetcher, "fetch", lambda self, sources=None, topic="", **k: topics.append(topic) or {})
    monkeypatch.setattr(nf, "fetch_news", lambda topic="", sources=None: topics.append(topic) or {})
    monkeypatch.setattr(nf.NewsFetcher, "get_recent", lambda self, limit=20, category="", topic="": [])
    monkeypatch.setattr(nf, "search_stored_news", lambda q, limit=10: [])
    return topics


def test_the_morning_report_never_sends_the_users_interests_out(monkeypatch):
    """Each interest word from the profile became a Reddit address (/r/<word>) and an arXiv query."""
    from eli.tools.news import news_synthesis as ns
    topics = _record_fetches(monkeypatch)
    ns.interest_news_block()
    assert topics and not {"quasars", "beekeeping"} & set(topics)


def test_the_news_briefing_never_sends_the_users_interests_out(monkeypatch):
    from eli.tools.news import news_synthesis as ns
    topics = _record_fetches(monkeypatch)
    ns.build_news_briefing()
    assert topics and not {"quasars", "beekeeping"} & set(topics)
