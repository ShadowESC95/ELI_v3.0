"""A result that presents several things keeps them open, and the next message is read against them.

An action says what it listed (`listed`) and how to go further into one item (`follow_up`: an
action, the argument the item fills, the item field it comes from). The executor records it for
the turn, the reply keeps it open in runtime.pending_proposal beside what the reply offered or
asked, and the router turns "the second one", "the tennis one" or "tell me more about X" into
that follow-up. Nothing here is about news in particular: news, web search, notes and folders
all declare their listings the same way.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from eli.kernel import request_context as rc
from eli.runtime import pending_proposal as pp

NOTES_FOLLOW_UP = {"action": "READ_FILE", "arg": "path", "field": "ref"}
NEWS_FOLLOW_UP = {"action": "NEWS_FETCH", "arg": "topic", "args": {"mode": "fetch_and_show"}}


@pytest.fixture()
def proposals(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "_path", lambda: tmp_path / "pending_proposal.json")
    yield


def _news(*titles):
    return {"action": "NEWS_FETCH", "items": [{"title": t} for t in titles], "follow_up": NEWS_FOLLOW_UP}


def _reply(text, *, listed=None):
    """ELI's reply stored the way every reply path stores it, after a turn that listed `listed`."""
    from eli.kernel.engine import CognitiveEngine
    me = SimpleNamespace(memory=SimpleNamespace(add_conversation_turn=lambda *a, **k: None),
                         session_id="s", user_id="u", _in_nested_turn=lambda: False)
    facts = {"user_input": "x"}
    if listed is not None:
        facts["executed_actions"] = [listed["action"]]
        facts["listed"] = listed
    token = rc.turn_facts_var.set(facts)
    try:
        CognitiveEngine._store_assistant_turn(me, text)
    finally:
        rc.turn_facts_var.reset(token)


# ── the shape of a listing ───────────────────────────────────────────────────

def test_a_listing_needs_items_and_a_way_to_follow_one_up():
    assert pp.listing(_news("A", "B"))["items"] == [{"n": 1, "title": "A"}, {"n": 2, "title": "B"}]
    assert pp.listing({"action": "X", "items": [{"title": "A"}]}) is None          # no follow_up
    assert pp.listing({"action": "X", "items": [{"ref": "/a"}], "follow_up": NOTES_FOLLOW_UP}) is None
    assert pp.listing(None) is None


# ── how long it stays open ───────────────────────────────────────────────────

def test_a_listing_outlives_a_reply_that_lists_nothing(proposals):
    _reply("Here is the news.", listed=_news("Hubble images a newborn star", "Rates held"))
    _reply("Honestly, nobody knows yet.")                    # chat in between
    assert [i["title"] for i in pp.get_follow_up()["listed"]["items"]] == [
        "Hubble images a newborn star", "Rates held"]


def test_the_next_listing_replaces_it(proposals):
    _reply("Here is the news.", listed=_news("Hubble images a newborn star"))
    _reply("Your notes.", listed={"action": "LIST_NOTES", "items": [{"title": "shopping", "ref": "/n/shopping.md"}],
                                  "follow_up": NOTES_FOLLOW_UP})
    listed = pp.get_follow_up()["listed"]
    assert listed["action"] == "LIST_NOTES" and listed["items"][0]["ref"] == "/n/shopping.md"


def test_a_listing_and_an_offer_sit_side_by_side(proposals):
    _reply("Here is the news. Want me to set a reminder to check again tonight?",
           listed=_news("Hubble images a newborn star"))
    left = pp.get_follow_up()
    assert left["items"] and left["listed"]["items"]


def test_a_lapsed_listing_is_gone(proposals):
    _reply("Here is the news.", listed=_news("Hubble images a newborn star"))
    data = pp._stored()
    data["listed"]["ts"] = time.time() - pp._TTL_SECONDS - 5
    pp._path().write_text(__import__("json").dumps(data), encoding="utf-8")
    assert pp.get_follow_up() is None


def test_closing_an_offer_keeps_the_listing(proposals):
    pp.set_pending_proposal("set a reminder for tonight", listed=_news("Hubble images a newborn star"))
    pp.clear_pending_proposal()
    left = pp.get_follow_up()
    assert not left["command"] and left["listed"]["items"]


# ── reading the next message against it ──────────────────────────────────────

@pytest.mark.parametrize("said,title", [
    ("the second one", "Rates held at 4%"),
    ("2", "Rates held at 4%"),
    ("the hubble one", "Hubble images a newborn star"),
    ("tell me more about the rates story", "Rates held at 4%"),
])
def test_a_listed_item_is_picked_by_number_name_or_a_deeper_look(proposals, said, title):
    pp.set_pending_proposal("", listed=_news("Hubble images a newborn star", "Rates held at 4%"))
    follow = pp.follow_up_on(pp.get_follow_up(), said)
    assert follow["action"] == "NEWS_FETCH" and follow["args"] == {"mode": "fetch_and_show", "topic": title}


@pytest.mark.parametrize("said", ["yes please", "I don't know, how?", "what do you think about it?",
                                  "rates are a disaster for everyone", "play some music"])
def test_anything_else_is_not_a_follow_up(proposals, said):
    pp.set_pending_proposal("", listed=_news("Hubble images a newborn star", "Rates held at 4%"))
    assert pp.follow_up_on(pp.get_follow_up(), said) is None


def test_the_follow_up_uses_the_field_the_listing_names(proposals):
    pp.set_pending_proposal("", listed={"action": "LIST_NOTES", "follow_up": NOTES_FOLLOW_UP,
                                        "items": [{"title": "shopping", "ref": "/n/shopping.md"},
                                                  {"title": "ideas", "ref": "/n/ideas.md"}]})
    assert pp.follow_up_on(pp.get_follow_up(), "the ideas one")["args"] == {"path": "/n/ideas.md"}


def test_yes_agrees_to_the_offer_and_never_opens_the_listing(proposals):
    from eli.execution.router_enhanced import route
    _reply("Here is the news. Want me to list my notes?",
           listed=_news("Hubble images a newborn star", "Rates held at 4%"))
    r = route("yes please")
    assert r["action"] == "LIST_NOTES", r
    assert r["meta"]["matched_by"] == "pending_proposal.confirm"


def test_what_is_open_is_described_for_the_model(proposals):
    _reply("Here is the news. Which story interests you most?", listed=_news("Hubble images a newborn star"))
    said = pp.describe_open(pp.get_follow_up())
    assert "NEWS_FETCH" in said and "1. Hubble images a newborn star" in said
    assert 'asking: "Which story interests you most?"' in said
    assert pp.describe_open(None) == ""


# ── who declares listings ────────────────────────────────────────────────────

def test_the_executor_records_what_a_result_listed(tmp_path, monkeypatch):
    from eli.execution.executor_enhanced import execute
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "shopping_list.md").write_text("# shopping\n", encoding="utf-8")
    monkeypatch.setenv("ELI_NOTES_DIR", str(notes))
    facts = {}
    token = rc.turn_facts_var.set(facts)
    try:
        execute("LIST_NOTES", {})
    finally:
        rc.turn_facts_var.reset(token)
    assert facts["listed"]["action"] == "LIST_NOTES"
    assert facts["listed"]["items"] == [{"title": "shopping list", "ref": str(notes / "shopping_list.md")}]
    assert facts["listed"]["follow_up"]["action"] == "READ_FILE"


def test_a_folder_listing_follows_up_into_the_entry(tmp_path, monkeypatch):
    from pathlib import Path
    from eli.execution.effectors.file_ops import _h_list_dir
    # LIST_DIR maps ~/eli/... onto the project (an old layout); keep the test folder out of it.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    got = _h_list_dir("LIST_DIR", {"path": str(tmp_path)})
    assert {i["title"]: i["ref"] for i in got["listed"]} == {
        "a.txt": str(tmp_path / "a.txt"), "sub/": str(tmp_path / "sub")}
    assert got["follow_up"] == {"action": "LIST_DIR", "arg": "path", "field": "ref"}


def test_a_web_search_lists_its_results(monkeypatch):
    from eli.execution.effectors import web_info
    monkeypatch.setattr("eli.core.config.network_allowed", lambda: True)
    monkeypatch.setattr("eli.plugins.web.plugin._web_search_results",
                        lambda q, max_results=5: [{"title": "pgvector HNSW", "href": "https://x.org/a", "body": "fast"}])
    got = web_info._h_web_search("WEB_SEARCH", {"query": "vector index"})
    assert got["listed"] == [{"title": "pgvector HNSW", "ref": "https://x.org/a", "summary": "fast"}]
    assert got["follow_up"] == {"action": "WEB_SEARCH", "arg": "query"}


def test_a_news_briefing_lists_the_stories_it_was_written_from(monkeypatch):
    from eli.execution.effectors import web_info
    from eli.tools.news import news_fetcher, news_synthesis

    class _Fetcher:
        def fetch(self, sources=None, topic=""):
            return {"fetched": 2, "stored_new": 0, "errors": []}

    brief = {"ok": True, "article_count": 2, "synthesis_prompt": "p",
             "top": [{"title": "Hubble images a newborn star", "url": "https://x.org/h", "source": "BBC"}],
             "interest": [{"title": "Rates held at 4%", "url": "https://x.org/r", "source": "FT"}]}
    built = []
    monkeypatch.setattr(news_fetcher, "NewsFetcher", _Fetcher)
    monkeypatch.setattr(news_synthesis, "build_news_briefing", lambda **kw: built.append(kw) or brief)
    monkeypatch.setattr(news_synthesis, "synthesise_news_briefing",
                        lambda **kw: "A briefing." if kw.get("brief") is brief else "")
    got = web_info._h_news_fetch("NEWS_FETCH", {"mode": "fetch_and_show"})
    assert got["content"] == "A briefing." and len(built) == 1, "the brief is built once and read from"
    assert [i["title"] for i in got["listed"]] == ["Hubble images a newborn star", "Rates held at 4%"]
    assert got["listed"][0]["ref"] == "https://x.org/h"
    assert got["follow_up"]["action"] == "NEWS_FETCH" and got["follow_up"]["arg"] == "topic"


# ── the model resolving intent sees what is open ─────────────────────────────

def test_the_intent_model_reads_the_message_against_what_is_open(monkeypatch):
    from eli.cognition import gguf_inference, llm_intent
    prompts = []
    monkeypatch.setattr(llm_intent, "_action_grammar", lambda catalogue: None)
    monkeypatch.setattr(gguf_inference, "chat_completion",
                        lambda prompt, **kw: prompts.append(prompt) or '{"action": "CHAT", "args": {}, "confidence": 0.9}')
    llm_intent._cache.clear()
    opened = 'ELI\'s last reply ended by asking: "How might the delay affect the season?"'
    llm_intent.parse_cached("I don't know, how?", opened)
    llm_intent.parse_cached("I don't know, how?", opened)                 # same situation: cached
    llm_intent.parse_cached("I don't know, how?", "")                     # different situation: asked again
    assert len(prompts) == 2
    assert opened in prompts[0] and prompts[0].rstrip().endswith('USER: "I don\'t know, how?"\nJSON:')
    assert "CONVERSATION SO FAR" not in prompts[1]


# ── the last action is the session's own ─────────────────────────────────────

def test_the_last_action_belongs_to_its_session():
    from eli.kernel.engine import CognitiveEngine
    one = CognitiveEngine.__new__(CognitiveEngine)
    one.session_id = "a"
    other = CognitiveEngine.__new__(CognitiveEngine)
    other.session_id = "a"                       # same id, different engine: not shared either
    one._last_command_action = {"action": "PLAY_MEDIA", "ts": time.time()}
    assert one._last_command_action["action"] == "PLAY_MEDIA"
    assert other._last_command_action is None
    one.session_id = "b"
    assert one._last_command_action is None


# ── an action run by an agent records into the turn that asked for it ────────

def test_what_an_agent_records_reaches_the_turn():
    """The agent bus runs agents on pool threads, which do not inherit the turn's context: the
    system agent's NEWS_FETCH recorded its listing (and what it ran) into nothing, so the briefing
    could not be followed up and the audit row missed the action."""
    from eli.cognition.agent_bus import AgentBus, AgentResult

    class _ActingAgent:
        name = "system"
        timeout_s = 2.0

        def run(self, user_input, intent, session_id, user_id):
            facts = rc.turn_facts_var.get()
            facts.setdefault("executed_actions", []).append("NEWS_FETCH")
            facts["listed"] = _news("Hubble images a newborn star")
            return AgentResult(agent=self.name, ok=True, confidence=0.9, data={})

    bus = AgentBus()
    facts = {}
    token = rc.turn_facts_var.set(facts)
    try:
        bus._collect_layer([_ActingAgent()], "what's the news", {"action": "NEWS_FETCH"}, "s1", "u1")
    finally:
        rc.turn_facts_var.reset(token)
        bus._pool.shutdown(wait=False)
    assert facts["executed_actions"] == ["NEWS_FETCH"]
    assert facts["listed"]["items"][0]["title"] == "Hubble images a newborn star"
