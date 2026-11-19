"""News fetcher — the pure (non-network) helpers.

HTML stripping, science-topic detection, topic-term expansion, article/topic
matching, and the topic→source/feed routing. All pure functions; no network.
Runs in the normal suite.
"""
from __future__ import annotations

import pytest

from eli.tools.news.news_fetcher import (
    _strip_html,
    _is_science_topic,
    _topic_terms,
    _article_matches_topic,
    _topic_default_sources,
    _rss_feeds_for_topic,
)


# --------------------------------------------------------------------------- #
def test_strip_html_tags_and_entities():
    assert _strip_html("<b>Hi</b> &amp; bye") == "Hi & bye"
    assert _strip_html("<p>a</p><p>b</p>") == "a b"
    # a feed's escaped markup is markup, and a numeric entity is a character
    assert _strip_html("&lt;p&gt;it&#39;s &quot;q&quot;&lt;/p&gt; &lt;a href=&quot;x&quot;&gt;here&lt;/a&gt;") == 'it\'s "q" here'
    assert _strip_html("a &lt; b and c &gt; d") == "a < b and c > d"
    assert _strip_html("") == ""


# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("topic,expected", [
    ("physics", True), ("quantum computing", True), ("arxiv", True),
    ("machine learning", True), ("black hole physics", True),
    ("weather", False), ("sports", False), ("", False), ("dublin news", False),
])
def test_is_science_topic(topic, expected):
    assert _is_science_topic(topic) is expected


# --------------------------------------------------------------------------- #
def test_topic_terms_expands_known_topic():
    terms = _topic_terms("physics")
    assert "neutrino" in terms and "quantum" in terms


def test_topic_terms_empty():
    assert _topic_terms("") == []


def test_topic_terms_includes_long_words():
    assert "climate" in _topic_terms("climate")   # >=4 chars, kept


# --------------------------------------------------------------------------- #
def test_article_matches_topic():
    hit = {"title": "New quantum computer breaks records", "summary": ""}
    miss = {"title": "Local football team wins final", "summary": ""}
    assert _article_matches_topic(hit, "physics") is True
    assert _article_matches_topic(miss, "physics") is False
    # No topic → everything matches.
    assert _article_matches_topic(miss, "") is True


# --------------------------------------------------------------------------- #
def test_topic_default_sources():
    assert _topic_default_sources("physics", None) == ["reddit", "arxiv", "rss"]
    assert _topic_default_sources("ai", ["all"]) == ["hn", "reddit", "arxiv", "rss"]
    # Explicit non-"all" sources are respected untouched.
    assert _topic_default_sources("physics", ["hn"]) == ["hn"]


def test_rss_feeds_for_topic():
    all_feeds = _rss_feeds_for_topic("")
    phys = _rss_feeds_for_topic("physics")
    assert len(phys) <= len(all_feeds)
    assert all(f[2] in {"science", "physics"} for f in phys)


def test_articles_and_reflections_are_kept_in_one_database(monkeypatch, tmp_path):
    """Both override variables set: the fetcher read one and the synthesis the other."""
    from eli.tools.news import news_fetcher, news_synthesis
    monkeypatch.setenv("ELI_USER_DB", str(tmp_path / "user.sqlite3"))
    monkeypatch.setenv("ELI_MEMORY_DB", str(tmp_path / "memory.sqlite3"))
    assert news_fetcher._get_db() == news_synthesis._user_db()
