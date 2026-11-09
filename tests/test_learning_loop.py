"""ELI learns from what became of its decisions: each kind of figure with its own features and base
rate, gates that hold a line only while the outcomes bear it out, routes and recalled memories assessed
where they are decided and settled by the next turn, the world's thoughts in its feed, a reply held to
the record it denies, proposals settled by their adoption, actions checked and tried another way, the
user's measured interest and ELI's measured curiosity, and awareness measured over the sitting."""
import time
from types import SimpleNamespace

import pytest

from eli.cognition import epistemics as ep
from eli.cognition import thoughts as th
from eli.runtime import evidence_ledger as led


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite3"
    monkeypatch.setattr(led, "_default_db_path", lambda: path)
    monkeypatch.setattr(th, "_default_db_path", lambda: path)
    monkeypatch.setattr(th, "_trimmed_at", 0.0)
    ep.forget_fits()
    yield path
    ep.forget_fits()


# ── the calibration, per kind ──────────────────────────────────────────────────────────────────────

def test_each_kind_reads_its_own_features_and_has_a_base_rate(db):
    assert ep.features_of(ep.RECALL) == ("accessibility", "importance", "overlap", "agreement")
    assert ep.features_of(ep.CLAIM) == ("confirmations", "contested") and ep.features_of("other") == ep.FEATURES
    assert ep.base_rate(ep.ROUTE, db_path=db) == (0.5, 0), "nothing settled: an even chance and nothing behind it"
    for i in range(6):
        ep.assess(ep.ROUTE, 0.9, request_id=f"route:r{i}", class_="router", db_path=db)
        ep.settle(f"route:r{i}", i < 4, kind=ep.ROUTE, db_path=db)
    p, n = ep.base_rate(ep.ROUTE, class_="router", db_path=db)
    assert n == 6 and p == pytest.approx(5 / 8)
    assert ep.base_rate(ep.ROUTE, class_="parser", db_path=db) == (0.5, 0)
    assert ep.report(ep.ROUTE, db_path=db)["weights"].keys() == {"score", "stage_rank", "parser_agrees"}


def test_a_gate_holds_its_line_until_the_outcomes_say_replies_below_it_do_as_well(db):
    assert ep.worse_below(ep.ANSWER, "aggregated", 0.7, db_path=db) is None, "too few on either side: the line holds"
    for i in range(8):
        ep.assess(ep.ANSWER, 0.8, request_id=f"lo{i}", features={"aggregated": 0.4, "grounding": 0.2}, db_path=db)
        ep.settle(f"lo{i}", False, db_path=db)
    for i in range(8):
        ep.assess(ep.ANSWER, 0.8, request_id=f"hi{i}", features={"aggregated": 0.9, "grounding": 0.2}, db_path=db)
        ep.settle(f"hi{i}", True, db_path=db)
    assert ep.worse_below(ep.ANSWER, "aggregated", 0.7, db_path=db) is True, "replies under the line proved worse"
    assert ep.worse_below(ep.ANSWER, "grounding", 0.35, db_path=db) is None, "all on one side of that line"
    for i in range(8):
        ep.assess(ep.ANSWER, 0.8, request_id=f"g{i}", features={"grounding": 0.5}, db_path=db)
        ep.settle(f"g{i}", i % 2 == 0, db_path=db)
    for i in range(8):
        ep.assess(ep.ANSWER, 0.8, request_id=f"h{i}", features={"grounding": 0.1}, db_path=db)
        ep.settle(f"h{i}", i % 2 == 0, db_path=db)
    # Eight a side, as often right: nothing shown either way. Read as "no worse", it opened the gate.
    assert ep.worse_below(ep.ANSWER, "grounding", 0.35, db_path=db) is None, "not shown as good: the line holds"


def test_a_gate_opens_only_once_replies_below_its_line_are_shown_to_do_as_well(db):
    """'Not shown worse' opened every gate: four of five right below the line against forty-five of
    fifty above (ten points worse) opened it. Opening now needs the replies below to be shown within
    the margin of those above."""
    def settle(prefix, n, right, value):
        for i in range(n):
            ep.assess(ep.ANSWER, 0.8, request_id=f"{prefix}{i}", features={"grounding": value}, db_path=db)
            ep.settle(f"{prefix}{i}", i < right, db_path=db)
    settle("a", 50, 45, 0.9)
    settle("b", 5, 4, 0.1)
    assert ep.worse_below(ep.ANSWER, "grounding", 0.35, db_path=db) is None
    settle("c", 150, 138, 0.1)
    settle("d", 150, 135, 0.9)
    assert ep.worse_below(ep.ANSWER, "grounding", 0.35, db_path=db) is False, "shown as good: it opens"


def test_a_gate_is_judged_by_the_replies_that_took_its_path(db):
    """Under the memory line the replies whose context the gate dropped were compared with replies
    above it that kept theirs: the gate's own doing was what it was judged by."""
    for i in range(10):
        ep.assess(ep.ANSWER, 0.8, request_id=f"kept{i}", features={"memory_conf": 0.9, "memory_ctx": True}, db_path=db)
        ep.settle(f"kept{i}", True, db_path=db)
        ep.assess(ep.ANSWER, 0.8, request_id=f"dropped{i}", features={"memory_conf": 0.2, "memory_ctx": False},
                  db_path=db)
        ep.settle(f"dropped{i}", False, db_path=db)
    assert ep.worse_below(ep.ANSWER, "memory_conf", 0.5, db_path=db) is True
    assert ep.worse_below(ep.ANSWER, "memory_conf", 0.5, where={"memory_ctx": True}, db_path=db) is None, \
        "no reply under the line kept its context: nothing is known of that path"


def test_several_figures_of_one_turn_are_assessed_at_once_and_settled_by_prefix_and_age(db):
    probs = ep.assess_many(ep.RECALL, [("recall:t1:1", 0.6, {"accessibility": 0.5}), ("recall:t1:2", 0.3, {})], db_path=db)
    assert [round(p.p, 3) for p in probs] == [0.6, 0.3] and all(p.kind == ep.RECALL for p in probs)
    assert ep.settle_open(ep.RECALL, "recall:t1:", True, how="endorsement", db_path=db) == 2
    assert ep.settle_open(ep.RECALL, "recall:t1:", True, db_path=db) == 0, "settled once"
    fit = ep.weights_for(ep.RECALL, db_path=db)
    assert fit["n"] == 2 and tuple(fit["names"]) == ep.features_of(ep.RECALL)
    now = time.time()
    ep.assess(ep.ACT, 0.5, request_id="act:9", db_path=db)
    assert ep.settle_open(ep.ACT, "act:", True, how="stood", before=now - 60, db_path=db) == 0, "given just now: not yet"
    assert ep.settle_open(ep.ACT, "act:", True, how="stood", before=now + 60, db_path=db) == 1


# ── the engine: routes and recalled memories, assessed and settled ───────────────────────────────

def _engine(**attrs):
    base = dict(_pending_route=None, _pending_recall_ids=None, _pending_assessment=None, _pending_assessment_p=0.5,
                _pending_user_input="", _pending_claims=None, user_id="",
                memory=SimpleNamespace(record_recall_outcome=lambda ids, helped: 0))
    base.update(attrs)
    return SimpleNamespace(**base)


def test_a_route_is_assessed_where_it_is_decided_and_settled_by_the_next_turn(db):
    from eli.kernel import request_context as rc
    from eli.kernel.engine import CognitiveEngine
    fake = _engine()
    token = rc.request_id_var.set("req-7")
    try:
        CognitiveEngine._assess_route(fake, {"action": "OPEN_APP", "confidence": 0.9,
                                             "meta": {"matched_by": "app.wake_prefix_contract"}}, "open spotify")
    finally:
        rc.request_id_var.reset(token)
    assert fake._pending_route == ("req-7", "OPEN_APP")
    kept = th.recent(5, kinds=["route"])[0]
    assert kept["what"] == "read as open app" and "the parser agrees" in kept["why"] and kept["status"] == "open"
    assert ep.outcomes(ep.ROUTE, db_path=db) == [], "open until the next turn"
    CognitiveEngine._settle_recall_outcome(_engine(_pending_route=("req-7", "OPEN_APP")), "no, that's not what I asked", "user")
    rows = ep.outcomes(ep.ROUTE, db_path=db)
    assert len(rows) == 1 and rows[0][1] == 0.0 and rows[0][2]["via"] == "app.wake_prefix_contract"
    assert th.recent(5, kinds=["route"])[0]["outcome"] == "wrong"
    token = rc.request_id_var.set("req-8")
    try:
        CognitiveEngine._assess_route(fake, {"action": "CHAT", "confidence": 0.6, "meta": {"matched_by": "fallback.chat"}}, "hello there")
    finally:
        rc.request_id_var.reset(token)
    CognitiveEngine._settle_recall_outcome(_engine(_pending_route=fake._pending_route), "what is the weather like", "user")
    assert sorted(y for _s, y, _f in ep.outcomes(ep.ROUTE, db_path=db)) == [0.0, 1.0], "moving on settles a route right"
    CognitiveEngine._settle_recall_outcome(_engine(_pending_route=("req-9", "CHAT")), "anything", "habit_scheduler")
    assert len(ep.outcomes(ep.ROUTE, db_path=db)) == 2, "an autonomous turn settles nothing"


def test_the_memories_shown_to_a_reply_are_settled_with_it_and_a_correction_is_a_surprise(db, monkeypatch):
    from eli.kernel.engine import CognitiveEngine
    from eli.memory import salience
    noted = []
    monkeypatch.setattr(salience, "note_surprise", lambda kind, magnitude, **k: noted.append((kind, magnitude, k)) or {"touched": [], "gain": 0.0})
    helped_ = []
    ep.assess_many(ep.RECALL, [("recall:req-1:7", 0.6, {"accessibility": 0.5, "importance": 0.5, "overlap": 0.3, "agreement": 0.0})], db_path=db)
    th.record("recall", "you prefer early starts", chance=0.6, decision="shown", ref="recall:req-1:7")
    ep.assess(ep.ANSWER, 0.8, request_id="req-1", db_path=db)
    fake = _engine(_pending_recall_ids=[7], _pending_assessment="req-1", _pending_assessment_p=0.8,
                   _pending_user_input="when do i like to start the day",
                   memory=SimpleNamespace(record_recall_outcome=lambda ids, helped=True: helped_.append((ids, helped)) or 1))
    CognitiveEngine._settle_recall_outcome(fake, "thanks, spot on", "user")
    assert helped_ == [([7], True)] and fake._pending_recall_ids is None
    assert ep.outcomes(ep.RECALL, db_path=db)[0][1] == 1.0 and ep.outcomes(ep.ANSWER, db_path=db)[0][1] == 1.0
    assert th.recent(5, kinds=["recall"])[0]["outcome"] == "right" and noted == []
    ep.assess_many(ep.RECALL, [("recall:req-2:8", 0.7, {})], db_path=db)
    ep.assess(ep.ANSWER, 0.9, request_id="req-2", db_path=db)
    fake = _engine(_pending_recall_ids=[8], _pending_assessment="req-2", _pending_assessment_p=0.9,
                   _pending_user_input="what did i say about the canal path")
    CognitiveEngine._settle_recall_outcome(fake, "no, that's not true", "user")
    assert [y for _s, y, _f in ep.outcomes(ep.RECALL, db_path=db)] == [0.0, 1.0]
    assert noted and noted[0][0] == "answer" and noted[0][1] == pytest.approx(0.9)
    assert "canal" in noted[0][2]["subjects"] and "corrected" in noted[0][2]["why"]


# ── the world: thoughts in the feed, by room ─────────────────────────────────────────────────────

WORLD = {
    "avatar": {"name": "ELI", "room": "workshop", "posture": "leaning", "expression": "focused", "activity": "repairing"},
    "awareness": {"focus": 0.9, "uncertainty": 0.1, "evidence_confidence": 0.8, "tool_activity": 0.7},
    "objects": {}, "events": [], "actions": [], "timestamp": 123.0,
}


def test_elis_thoughts_ride_in_the_feed_by_room(db, monkeypatch):
    from eli.world import shown, spatial_feed
    monkeypatch.setattr(shown, "items", lambda now=None: [])
    th.record("action", "open app for spotify failed", chance=0.2, decision="advised", now=1_800_000_000.0)
    th.record("recall", "a walk by the canal", chance=0.7, decision="shown", now=1_800_000_001.0)
    shown.forget()
    assert "thoughts" in spatial_feed.CAPABILITIES[2]
    msg = spatial_feed.spatial_state(WORLD, version=2)
    assert [t["kind"] for t in msg["thoughts"]] == ["recall", "action"], "newest first"
    assert msg["thoughts"][1]["room"] == "debug_basement" and msg["thoughts"][1]["words"].startswith("Open app for spotify failed")
    assert "thoughts" not in spatial_feed.spatial_state(WORLD), "version 1 is as it was"
    assert [t["what"] for t in shown.kept(room="debug_basement")] == ["open app for spotify failed"]
    assert [t["what"] for t in shown.kept(kinds=["recall"])] == ["a walk by the canal"]
    th.record("claim", "lives in: Berlin", chance=0.8, decision="shown", now=1_800_000_002.0)
    assert len(spatial_feed.spatial_state(WORLD, version=2)["thoughts"]) == 2, "read as often as the records, not every poll"
    shown.forget()
    assert len(spatial_feed.spatial_state(WORLD, version=2)["thoughts"]) == 3


# ── a reply held to the record it denies ──────────────────────────────────────────────────────────

def test_a_reply_denying_the_record_of_a_period_in_hand_gives_way_to_the_record(monkeypatch):
    from eli.cognition import self_claims as sc
    from eli.kernel import request_context as rc
    monkeypatch.setattr(sc, "_live_facts", lambda: "")
    lines = ["[Tue 27 Oct 13:01] You: the tide tables", "[Tue 27 Oct 13:02] ELI ran WEB_SEARCH: tide times"]
    token = rc.turn_facts_var.set({"user_input": "what did we do on the 27th?", "_period_found": True, "_period_lines": lines})
    try:
        check = sc._Check()
        assert check.why("I have no record of that session in this runtime.") == \
            "says it has no record of that period, and the record of it is in hand"
        assert check.why("The logs from October 27th do not exist in my active context.")
        assert check.why("That afternoon you looked up the tide tables.") == ""
        assert "".join(check.feed("I have no record of that session in this runtime. ")).strip() == ""
        after = check.afterword()
        assert after.startswith("What I have for that period:") and "ELI ran WEB_SEARCH: tide times" in after
    finally:
        rc.turn_facts_var.reset(token)
    token = rc.turn_facts_var.set({"user_input": "what did we do on the 27th?"})
    try:
        assert sc._Check().why("I have no record of that session.") == "", "with no record in hand, the sentence stands"
    finally:
        rc.turn_facts_var.reset(token)


# ── the router: the three misroutes, generally ────────────────────────────────────────────────────

def test_a_catalogue_phrase_an_evening_alarm_and_a_web_search_are_read_right():
    from eli.execution.router_enhanced import route
    assert (route("start a pomodoro") or {}).get("action") == "POMODORO_START"
    alarm = route("set an alarm for 7:30pm") or {}
    assert alarm.get("action") == "SET_ALARM" and alarm["args"]["time"] == "19:30"
    assert (route("set an alarm for 7:30") or {})["args"]["time"] == "07:30"
    search = route("search the web for the weather in Paris") or {}
    assert search.get("action") == "WEB_SEARCH" and search["args"]["query"].endswith("weather in Paris")
    assert not search["args"]["query"].startswith("for "), "the preposition after the stem is not the query"
    asked = route("which of your actions are unreliable?") or {}
    assert asked.get("action") == "EXPLAIN_FAILURE_LOG" and asked["args"]["scope"] == "outcomes"
    assert (route("how do your agents work") or {}).get("action") == "EXPLAIN_COGNITION_RUNTIME"


# ── self-repair: a proposal is a figure settled by what became of it ─────────────────────────────

def test_a_proposal_is_a_figure_settled_by_its_adoption(db, monkeypatch, tmp_path):
    from eli.memory.memory import Memory
    from eli.runtime.self_improvement import SelfImprovementEngine
    si = SelfImprovementEngine(memory=Memory(db_path=tmp_path / "agent.sqlite3"))
    monkeypatch.setattr(si, "verify_in_workspace", lambda patch, **k: {"verdict": "regression", "cost_s": 1.0})
    bad = si.propose_candidate({"file": "eli/x.py", "old": "a", "new": "b", "description": "swap a for b"}, hypothesis="h")
    assert bad["status"] == "rejected" and ep.outcomes(ep.PROPOSAL, db_path=db)[0][1] == 0.0
    assert th.recent(5, kinds=["proposal"])[0]["outcome"] == "rejected"
    monkeypatch.setattr(si, "verify_in_workspace", lambda patch, **k: {"verdict": "fixed", "cost_s": 1.0})
    good = si.propose_candidate({"file": "eli/x.py", "old": "a", "new": "b", "description": "swap a for b"}, hypothesis="h")
    assert good["status"] == "verified" and th.recent(5, kinds=["proposal"])[0]["status"] == "open"
    si._settle_proposal(good["candidate_id"], "adopted")
    assert sorted(y for _s, y, _f in ep.outcomes(ep.PROPOSAL, db_path=db)) == [0.0, 1.0]
    assert th.recent(5, kinds=["proposal"])[0]["outcome"] == "right"


# ── the executor: a result checked, and a proven alternative tried ────────────────────────────────

def test_a_result_that_claims_a_file_is_checked_before_it_is_recorded(tmp_path):
    from eli.execution import executor_enhanced as ex
    gone = ex._checked_result("CREATE_DOCUMENT", {"path": str(tmp_path / "gone.txt")}, {"ok": True, "response": "Wrote it"}, time.time())
    assert gone["ok"] is False and gone["verified"] is False and "did not take effect" in gone["error"] and gone["claimed"] == "Wrote it"
    (tmp_path / "here.txt").write_text("x")
    here = ex._checked_result("CREATE_DOCUMENT", {"path": str(tmp_path / "here.txt")}, {"ok": True}, time.time() - 5)
    assert here["ok"] is True and here["verified"] is True
    empty = ex._checked_result("WEB_SEARCH", {"query": "x"}, {"ok": True, "results": []}, time.time())
    assert empty["ok"] is True and empty["effect"] is False
    assert ex._checked_result("OPEN_URL", {"url": "https://a.b"}, {"ok": True}, time.time()) == {"ok": True}, "unchecked is left as it was"


def test_a_proven_alternative_is_tried_when_an_action_fails_and_the_failure_is_a_thought(db, monkeypatch):
    from eli.execution import executor_enhanced as ex
    from eli.runtime import action_outcomes as ao
    advice = {"action": "OPEN_APP", "subject": "spotify", "cause": "missing_resource",
              "cause_words": "what it was pointed at was not there", "chance": {"p": 0.2}, "failed_lately": 3, "runs_lately": 3,
              "alternatives": [{"action": "OPEN_URL", "p": 0.8, "n": 2}],
              "attempt": {"action": "OPEN_URL", "args": {"url": "https://open.spotify.com", "_alternative_of": "OPEN_APP"}}}
    monkeypatch.setattr(ao, "advice", lambda action, args, result, **k: dict(advice))
    ran = []
    monkeypatch.setattr(ex, "execute", lambda action, args=None, **k: ran.append((action, args)) or
                        {"ok": True, "action": action, "response": "Opened URL: https://open.spotify.com"})
    out = ex._action_post_dispatch("OPEN_APP", {"name": "spotify"}, {"ok": False, "error": "not found",
                                                                     "response": "Spotify did not start."})
    assert ran == [("OPEN_URL", {"url": "https://open.spotify.com", "_alternative_of": "OPEN_APP"})]
    # what the failed run said stays first; then what was tried, and what came of it
    assert out["ok"] and out["instead_of"]["action"] == "OPEN_APP" and out["response"].startswith("Spotify did not start.")
    assert "open url worked instead before, so I tried that" in out["response"]
    assert out["response"].endswith("Opened URL: https://open.spotify.com")
    kept = th.recent(5, kinds=["action"])[0]
    assert kept["decision"] == "tried" and kept["room"] == "debug_basement" and kept["chance"] == pytest.approx(0.2)
    advice["attempt"] = None
    out = ex._action_post_dispatch("OPEN_APP", {"name": "spotify"}, {"ok": False, "error": "not found", "response": "⚡ not found"})
    assert out["ok"] is False and len(ran) == 1 and th.recent(5, kinds=["action"])[0]["decision"] == "advised"


def test_a_failed_play_after_paused_music_runs_nothing_and_keeps_what_the_player_said(db, monkeypatch):
    # the real post-dispatch on a real ledger: songs asked of Spotify failed and the music was paused
    # after each; the next failed song must not pause it again, nor lose what the player said
    from eli.execution import executor_enhanced as ex
    from eli.runtime import action_outcomes as ao
    from eli.runtime import grounded_remediation as gr
    from eli.kernel import request_context as rc
    now = time.time()
    for k in range(3):
        for action, args, ok in (("PLAY_MEDIA", {"target": "spotify", "query": f"song {k}"}, False),
                                 ("STOP_MEDIA", {"target": "spotify"}, True)):
            result = {"ok": ok, **({} if ok else {"played": False, "mismatch": True})}
            led.record_event("executor_action", source="executor.post_dispatch", action=action,
                             subject=ao.subject_of(action, args), content=f"{action} {k}",
                             payload={"args": args, "result": result}, outcome="ok" if ok else "failed",
                             session_id="s1", timestamp=now - 600 + 60 * k + (20 if ok else 0))
    ran = []
    monkeypatch.setattr(ex, "execute", lambda action, args=None, **k: ran.append(action) or {"ok": True})
    monkeypatch.setattr(gr, "capture_executor_failure", lambda *a, **k: None)
    said = "Spotify is playing another track, not the one asked for."
    token = rc.session_id_var.set("s1")
    try:
        out = ex._action_post_dispatch("PLAY_MEDIA", {"target": "spotify", "query": "a new song"},
                                       {"ok": False, "played": False, "mismatch": True, "response": said})
    finally:
        rc.session_id_var.reset(token)
    ao.forget()
    assert ran == [], "nothing runs of ELI's own accord"
    assert out["ok"] is False and out["response"].startswith(said)
    assert "stop media" not in out["response"] and "error inside ELI" not in out["response"]


# ── the user's interest and ELI's curiosity, measured ─────────────────────────────────────────────

def test_the_users_interest_is_the_activation_of_their_own_mentions(monkeypatch):
    import eli.memory.memory as memory_module
    from eli.memory import interest
    now = 1_800_000_000.0
    turns = [{"role": "user", "content": "the router keeps dropping the wifi", "timestamp": now - 60},
             {"role": "user", "content": "router again, the wifi router", "timestamp": now - 3600},
             {"role": "user", "content": "is the router worth replacing", "timestamp": now - 7200},
             {"role": "user", "content": "bought a kayak last spring", "timestamp": now - 20 * 86400},
             {"role": "assistant", "content": "router router router router", "timestamp": now - 10}]
    monkeypatch.setattr(memory_module, "get_memory", lambda *a, **k: SimpleNamespace(
        get_recent_conversation=lambda limit=500, user_id=None: turns))
    interest.forget()
    focus = interest.focus(3, now=now)
    assert focus[0][0] == "router" and focus[0][1] > focus[-1][1] and sum(s for _w, s in interest.focus(50, now=now)) == pytest.approx(1.0, abs=1e-3)
    assert interest.share("my wifi router", now=now) > interest.share("the kayak", now=now) > 0.0
    assert interest.share("nothing of note", now=now) == 0.0 and interest.share("", now=now) == 0.0
    interest.forget()


def test_elis_curiosity_is_what_surprised_it_lately_and_what_is_still_open(db, monkeypatch):
    import eli.memory.memory as memory_module
    from eli.cognition import curiosity
    th.record("surprise", "open app for spotify was expected to work (90%) and failed", chance=0.9, subject="spotify")
    monkeypatch.setattr(memory_module, "get_memory", lambda *a, **k: SimpleNamespace(claim_conflicts=lambda: [
        {"relation": "lives_in", "standing": [{"value": "Berlin"}], "disputed": [{"value": "Lisbon"}]}]))
    assert curiosity.about("play spotify") > 0.8 and curiosity.about("the weather") == 0.0
    assert curiosity.about("Berlin or Lisbon") == 2.0, "one for each open conflict about it"
    by_subject = dict(curiosity.subjects(10))
    assert by_subject["spotify"] > 0.8 and by_subject["berlin"] == 1.0
    assert curiosity.sitting([0.2, 0.6]) == pytest.approx(0.4) and curiosity.sitting([]) == 0.0


# ── awareness measured over the sitting ───────────────────────────────────────────────────────────

def test_repair_pressure_and_curiosity_are_measured_over_the_sitting():
    from eli.world.agency.autonomy_engine import EliWorldAutonomyEngine
    from eli.world.core.schemas import EliWorldState, WorldEvent
    engine = EliWorldAutonomyEngine.__new__(EliWorldAutonomyEngine)
    state = EliWorldState()
    state.awareness.timestamp -= 30 * 24 * 3600.0      # made long ago, as a world on disk is: each event moves it
    state.awareness.repair_pressure = 0.0
    for kind in ("error_detected", "task_completed", "task_completed", "task_completed"):
        engine._update_awareness_from_event(state, WorldEvent(event_type=kind, source="executor", summary="x", payload={"action": "X"}))
    assert state.awareness.tools == 4 and state.awareness.repair_pressure == pytest.approx(0.25), "one failure in four runs"
    for size in (0.2, 0.6):
        engine._update_awareness_from_event(state, WorldEvent(event_type="surprise", source="salience", summary="s", payload={"magnitude": size}))
    assert state.awareness.surprises == 2 and state.awareness.curiosity == pytest.approx(0.4)
    before = state.awareness.curiosity
    engine._update_awareness_from_event(state, WorldEvent(event_type="reflection", source="reflection_engine", summary="r"))
    assert state.awareness.curiosity == before, "a reflection is not a surprise"


def test_measured_accuracy_is_one_line_per_kind_with_settled_figures(db):
    assert ep.measured_lines(db_path=db) == [], "nothing settled: nothing claimed"
    for i in range(10):
        ep.assess(ep.ANSWER, 0.8, request_id=f"m{i}", db_path=db)
        ep.settle(f"m{i}", i < 7, db_path=db)
    lines = ep.measured_lines(db_path=db)
    assert len(lines) == 2 and "replies to you: 10 settled" in lines[1] and "70% proved right" in lines[1]
    assert "calibration error" in lines[1]
