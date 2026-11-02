"""Locks on defects from live v2 sessions (13:51, 15:13, 17:47), ported to v3.

In v3 the repeat guard lives in eli/kernel/stages/guards.py and the generation
stage in eli/kernel/stages/generation.py. Following up on a briefing is read by the
router against what the briefing listed (eli.runtime.pending_proposal).

The transcript is the specification here, so the fixtures are its actual text.

**1. ELI served the same paragraph four times.** At 13:52:23, 13:52:42, 13:55:16 and
13:56:31 it emitted the same 40-word reply about sleep stress-tests. The log shows
``[ANTI-REPEAT] contract injected (3 prior replies)`` on *every one* of those turns —
the guard was running and being ignored. It was prompt-level only: it asked the model
not to repeat and never checked whether it had. The worst instance came right after
the user said "Stop going on about my schedule" and ELI answered "I'll stop", then
repeated the paragraph on the next turn.

**2. "Good.. morning report?" did not produce a morning report.** The router matched
``MORNING_REPORT`` at confidence 0.95 via ``self.morning_report`` and the next log line
is ``news topic-deepen: MORNING_REPORT→CHAT``. Three things combined:
  * ``MORNING_REPORT`` is in the set that *arms* the deepen rule and was not in the set
    the rule refuses to touch — so a report suppressed the next report.
  * ``_last_command_action`` had no timestamp and CHAT never overwrites it, so "right
    after a briefing" was never checked; one report armed the rule indefinitely.
  * An explicit 0.95 router match was treated as an ambiguous follow-up.
That rule is gone. A follow-up is now read against what the briefing listed, and only a
message that picks or names a listed story (or asks to go deeper into one) is one.
"""
import time

import pytest

from eli.kernel.stages.guards import (
    _ANTI_REPEAT_BLOCK_RE,
    _RepeatDetected,
    _deprimed_persona_brief,
    _is_repeat_of_recent,
    _repeat_ratio,
    _stream_holding_back_repeats,
    _user_asked_for_a_repeat,
)

# The paragraph ELI actually repeated, verbatim from the transcript.
SLEEP_REPLY = (
    "You're late, alex. I've been running stress tests on your sleep schedule and "
    "recalibrating the quantum model of your morning routine. Summary: you're late, "
    "I'm bored, and your sleep habits are a mess. Want me to stop or should I keep going?"
)
# 13:56:31 — same content, different opening words.
SLEEP_REPLY_REPRISE = (
    "Morning report? You're late, alex. I've been running stress tests on your sleep "
    "schedule and recalibrating the quantum model of your morning routine. Summary: "
    "you're late, I'm bored, and your sleep habits are a mess."
)


# ── 1. the repeat is detected ───────────────────────────────────────────────
def test_the_exact_repeat_from_the_transcript_is_caught():
    assert _is_repeat_of_recent(SLEEP_REPLY, [SLEEP_REPLY])


def test_repeat_is_caught_against_older_turns_not_just_the_last():
    """It recurred three turns later, with other replies in between."""
    history = ["Something else entirely.", "A third unrelated reply.", SLEEP_REPLY]
    assert _is_repeat_of_recent(SLEEP_REPLY, history)


def test_near_verbatim_repeat_is_caught():
    """Punctuation/case drift must not launder a repeat."""
    tweaked = SLEEP_REPLY.replace("You're", "Youre").replace("—", "-").upper()
    assert _is_repeat_of_recent(tweaked, [SLEEP_REPLY])


def test_repeat_detection_reads_the_opening_not_the_whole_reply():
    """The guard runs on a buffered head, so a reply that opens identically and then
    continues must still register — otherwise the length difference alone drags the
    ratio under the threshold and the repeat streams out."""
    head = SLEEP_REPLY[:200]
    assert _is_repeat_of_recent(head, [SLEEP_REPLY])


# ── 2. genuine replies are NOT suppressed ───────────────────────────────────
def test_a_different_reply_is_not_flagged():
    other = ("I'm running clean, alex. No bugs, no glitches - just a few sarcastic "
             "remarks and a well-timed reminder that you're not sleeping enough.")
    assert not _is_repeat_of_recent(other, [SLEEP_REPLY])


def test_short_replies_are_never_flagged():
    """"Okay." twice is not a repetition bug, and blocking it would be worse than the
    disease — the retry costs a whole generation."""
    for short in ("Okay.", "Sure.", "Morning.", "Got it, will do."):
        assert not _is_repeat_of_recent(short, [short]), short


def test_same_topic_different_content_is_allowed():
    """Talking about sleep twice is fine; saying the SAME thing is not."""
    a = "Your sleep debt is accumulating faster than your productivity."
    b = "You hit snooze three times this morning, which is a new record."
    assert not _is_repeat_of_recent(b, [a])


def test_empty_and_none_are_safe():
    assert not _is_repeat_of_recent("", [SLEEP_REPLY])
    assert not _is_repeat_of_recent(SLEEP_REPLY, [])
    assert not _is_repeat_of_recent(SLEEP_REPLY, [None, ""])


def test_ratio_is_bounded():
    assert _repeat_ratio(SLEEP_REPLY, SLEEP_REPLY) == pytest.approx(1.0)
    assert 0.0 <= _repeat_ratio("abc", "xyz") <= 1.0


# ── 3. the streaming guard actually withholds the repeat ────────────────────


def _drive_guard(chunks, recent, *, retry_text="A genuinely different answer that "
                                              "responds to what was actually said."):
    """Drive the SHIPPED guard generator, mirroring how stream_chat calls it:
    consume it, and on _RepeatDetected re-run once with allow_retry=False."""
    try:
        return "".join(_stream_holding_back_repeats(chunks, recent, allow_retry=True)), False
    except _RepeatDetected:
        return "".join(
            _stream_holding_back_repeats([retry_text], recent, allow_retry=False)
        ), True


def test_a_repeated_reply_never_reaches_the_user():
    """The whole point: not 'detected afterwards', but never emitted."""
    chunks = [SLEEP_REPLY[i:i + 7] for i in range(0, len(SLEEP_REPLY), 7)]
    out, retried = _drive_guard(chunks, [SLEEP_REPLY])
    assert retried, "repeat was not caught"
    assert "recalibrating the quantum model" not in out
    assert out.strip(), "guard swallowed the turn entirely"


def test_a_normal_reply_streams_through_unchanged():
    text = ("Agenda's clear. You've got the QMSH grant follow-up and nothing else "
            "booked, so the morning is yours if you want it.")
    chunks = [text[i:i + 5] for i in range(0, len(text), 5)]
    out, retried = _drive_guard(chunks, [SLEEP_REPLY])
    assert not retried
    assert out == text, "guard altered a legitimate reply"


def test_guard_emits_short_replies_that_end_inside_the_buffer():
    """A reply shorter than the head buffer must still be delivered."""
    out, retried = _drive_guard(["Morning."], [SLEEP_REPLY])
    assert out == "Morning." and not retried


def test_second_repeat_is_served_rather_than_looping():
    """One retry only. An honest duplicate beats an empty turn or an infinite loop."""
    chunks = [SLEEP_REPLY[i:i + 9] for i in range(0, len(SLEEP_REPLY), 9)]
    out, retried = _drive_guard(chunks, [SLEEP_REPLY], retry_text=SLEEP_REPLY)
    assert retried
    assert out, "a second repeat must still produce a turn, not silence"


# ── 3b. the guard must not become its own bug ───────────────────────────────
@pytest.mark.parametrize("asked", [
    "say that again", "repeat that", "repeat it", "come again",
    "what did you just say?", "what did you say", "one more time",
    "read that back", "say it once more", "Again please",
])
def test_an_explicit_repeat_request_stands_the_guard_down(asked):
    """"Say that again" is correctly answered with the previous reply. Without this
    the guard would reject that answer and regenerate something not asked for."""
    assert _user_asked_for_a_repeat(asked), asked


@pytest.mark.parametrize("ordinary", [
    "Morning Eli",
    "summary please pal",
    "keep going haha",
    "I told you not to worry about my schedule, I will sort it out tonight.",
    "Good.. morning report?",
    "Eli, why the fuck do you keep repeating yourself??",
    "",
])
def test_ordinary_turns_do_not_stand_the_guard_down(ordinary):
    """Every line here is from the transcript that exposed the bug — if any of them
    disarmed the guard, the original repeats would still get through. The last one
    matters most: complaining about repetition must not license more of it."""
    assert not _user_asked_for_a_repeat(ordinary), ordinary


# ── 3c. the retry must not re-prime the model with the text it must avoid ───
# Live evidence (v2.1.60, 15:13:31): the guard caught a repeat, retried, and the
# retry produced the SAME opening — prompt_tokens 4501 -> 4495, barely changed,
# because the retry was `instruction + prompt` and `prompt` still carried the
# contract quoting three prior replies verbatim. A turn earlier the same retry had
# succeeded ('You' -> 'Hello'), so this is probabilistic, not deterministic.
_CONTRACT = (
    "YOU HAVE ALREADY SAID THE FOLLOWING — do not repeat any of it, and do "
    "not re-ask a question the user has already answered:\n"
    f"  - {SLEEP_REPLY[:220]}\n"
    "Reply to what the user just said. If you have already described your "
    "own state or mood, do not describe it again unless asked."
)
# The contract is injected into the PERSONA HANDOFF (situation_brief), which
# reaches the model as wm.persona_handoff. It is NOT in `prompt` — `prompt` is the
# user's own message. Getting that wrong made the first fix a silent no-op.
_BRIEF = _CONTRACT + "\n\nPERSONA: terse\nRECENT: user asked for a report"


def test_retry_prompt_no_longer_quotes_the_reply_it_must_avoid():
    """The whole point: stop handing the model the text it must not produce."""
    out = _deprimed_persona_brief(_BRIEF)
    assert "recalibrating the quantum model" not in out
    assert "stress tests on your sleep schedule" not in out


def test_retry_prompt_keeps_a_constraint_that_names_no_content():
    out = _deprimed_persona_brief(_BRIEF).lower()
    assert "not repeat anything you have already said" in out


def test_retry_prompt_preserves_the_rest_of_the_prompt():
    """Stripping must remove the quotes, not the persona or the user's question."""
    out = _deprimed_persona_brief(_BRIEF)
    assert "PERSONA: terse" in out
    assert "RECENT: user asked for a report" in out


def test_retry_prompt_is_materially_shorter():
    """4501 -> 4495 tokens was the symptom: the retry was effectively the same
    prompt. It has to actually change."""
    assert len(_deprimed_persona_brief(_BRIEF)) < len(_BRIEF)


def test_retry_prompt_survives_a_brief_with_no_contract():
    """Not every turn injects one (no prior replies on turn 1)."""
    bare = "PERSONA: terse\nRECENT: nothing yet"
    out = _deprimed_persona_brief(bare)
    assert "RECENT: nothing yet" in out and out != bare


def test_the_user_message_is_never_the_de_priming_target():
    """The bug this rename exists to prevent. `prompt` in stream_chat is the user's
    own message; the contract is in the persona handoff. Passing the user message
    here strips nothing and prepends an instruction to their words — the live log
    read "prior replies stripped from prompt: 26 -> 143 chars", and 26 is exactly
    len("fair point pal, fair point")."""
    user_message = "fair point pal, fair point"
    out = _deprimed_persona_brief(user_message)
    assert len(out) > len(user_message), (
        "a user message contains no contract, so this can only ever grow it — "
        "which is why it must never be the argument"
    )


def test_contract_and_stripper_stay_in_sync():
    """If the contract wording is edited without updating the regex, the stripper
    silently stops working and the retry is primed again — exactly the live bug."""

    assert _ANTI_REPEAT_BLOCK_RE.search(_CONTRACT), (
        "the regex no longer matches the contract this module injects"
    )


# ── 4. a briefing's follow-ups are read against what it listed ─────────────
@pytest.fixture()
def briefing_open(tmp_path, monkeypatch):
    """A news briefing just shown, with its stories open for a follow-up."""
    from eli.runtime import pending_proposal as pp
    monkeypatch.setattr(pp, "_path", lambda: tmp_path / "pending_proposal.json")
    pp.set_pending_proposal("", listed={
        "action": "NEWS_FETCH",
        "items": [{"title": "Hubble images a newborn star system"},
                  {"title": "Sleep study links late nights to memory loss"}],
        "follow_up": {"action": "NEWS_FETCH", "arg": "topic",
                      "args": {"mode": "fetch_and_show", "sources": ["all"]}},
    }, from_reply=True)
    return pp


def test_a_report_asked_for_after_a_briefing_is_a_report(briefing_open):
    """The transcript case: "Good.. morning report?" right after a report was rewritten into
    CHAT. A question naming no listed story is not a follow-up on one."""
    from eli.execution.router_enhanced import route
    r = route("Good.. morning report?")
    assert r["action"] == "MORNING_REPORT", r
    assert (r.get("meta") or {}).get("matched_by") != "pending_proposal.follow_up"


def test_a_bare_pick_after_a_briefing_follows_that_story_up(briefing_open):
    """What the old rule was built for still works, from what was listed rather than from
    any short message."""
    from eli.execution.router_enhanced import route
    r = route("the first one")
    assert r["action"] == "NEWS_FETCH"
    assert r["args"]["topic"] == "Hubble images a newborn star system"
    assert r["meta"]["matched_by"] == "pending_proposal.follow_up"


# ── 6. ELI's own output must not be recycled as fact ────────────────────────
def test_chat_history_labels_elis_own_lines_as_unverified():
    """A single hallucination became permanent truth.

    ELI invented "you're in the Simulation Lab, the Branch Tree is humming". That turn
    landed in conversation_turns; on the next session the memory agent retrieved it and
    ELI restated it verbatim as established fact. `memories` held ZERO rows for those
    terms and the world state's room was None — recall of its own prior turn was the
    entire mechanism. memory.py already filters reflections and assistant_insight out
    of recall for this exact reason; raw chat history bypassed that filter.
    """
    import inspect
    from eli.kernel import engine

    from pathlib import Path as _P
    src = inspect.getsource(engine) + "".join(
        f.read_text(encoding="utf-8") for f in sorted(_P(engine.__file__).parent.glob("stages/*.py")))
    i = src.index("Active chat history (chronological")
    header = src[i:i + 400]
    assert "your own" in header.lower(), "ELI's lines are not marked as its own"
    assert "not as verified facts" in header.lower() or "NOT as verified" in header, (
        "the history block still presents ELI's assertions as evidence"
    )


def test_the_retry_is_capped_at_two_generations():
    """Observed live: 20.1s for one reply (8.4 + 7.9 + 2.9). The handler logged
    'serving it' while silently generating a third time, because the retry was passed
    allow_retry=True and raised again."""
    import inspect
    from eli.kernel import engine

    src = inspect.getsource(engine.CognitiveEngine._yield_with_anti_repeat_guard)
    i = src.index("_RepeatDetected")
    tail = src[i:]
    # One primary stream_factory call + one retry — no recursive third pass.
    calls = tail.count("stream_factory(")
    assert calls <= 2, f"the repeat handler regenerates {calls} times, expected at most 2"
    assert "allow_retry=False" in tail, "the retry can still raise and trigger a third pass"


def test_keyword_recall_excludes_elis_own_turns_by_default():
    """The structural fix, after the prompt-level one failed.

    v2.1.74 labelled chat history as "your own previous statements — NOT verified
    facts". It did not hold: ELI restated a fabrication about "stress tests on your
    sleep schedule and the quantum model of your morning routine" as history on a
    later day. An instruction to a model is not a filter — the same lesson as the
    anti-repeat contract that only asked.

    `search_conversations` is keyword recall across ALL history and had no role
    filter, so ELI's own assertions came back as evidence. Its sibling search already
    did `AND role = 'user'`; this one never got it.
    """
    import inspect
    from eli.memory.memory import Memory

    assert "include_assistant" in str(inspect.signature(Memory.search_conversations))


def test_assistant_turns_remain_reachable_when_explicitly_requested():
    """"What did you tell me about X?" is legitimate — the filter is a default, not a
    ban."""
    import inspect
    from eli.memory.memory import Memory
    sig = inspect.signature(Memory.search_conversations)
    assert sig.parameters["include_assistant"].default is False


def _seed_past_session(mem, marker):
    """A realistic past conversation ending on a user turn.

    Shape matters. The block drops a trailing assistant turn and then a trailing user
    turn (so the model does not regurgitate the live prompt), which silently ate a
    2-row fixture entirely and made an earlier version of this test pass against code
    with the guard REMOVED. Five turns survive that trim with the fabrication inside.
    """
    conn = mem._get_connection()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(conversation_turns)")]
    base = time.time() - 86400  # yesterday — a different session
    convo = [
        ("user", "I was working on the solar hydrogen model today."),
        ("assistant", "That sounds like steady progress on the electrolyser side."),
        ("user", "yeah, model 06 is still the baseline."),
        ("assistant", marker),
        ("user", "what does the grant timeline look like?"),
    ]
    for n, (role, content) in enumerate(convo):
        ts = base + n * 60
        row = {"timestamp": ts, "ts": ts, "session_id": "PAST_SESSION",
               "user_id": "probe_user", "role": role, "content": content}
        use = [k for k in row if k in cols]
        conn.execute(
            f"INSERT INTO conversation_turns ({','.join(use)}) "
            f"VALUES ({','.join('?' * len(use))})", [row[k] for k in use])
    conn.commit()


def test_the_agent_does_not_serve_elis_own_fabrication_back_as_history():
    """Behaviour lock, driving the SHIPPED agent — not its source.

    ELI invented that the user was "stuck in a simulation lab with a Branch Tree and a
    Development Console". The memory agent then pulled recent turns ACROSS sessions
    (get_recent_conversation with no session_id) and headed them "use this when asked
    about past topics", so the invention came back on later days as established fact.

    Verified by A/B against the live agent: with the guard removed the block renders 4
    turns and the fabrication IS present; with it, 2 turns, user's words only. The
    legitimate user turn must still come back — this is a filter, not an amputation of
    cross-session recall.
    """
    from eli.memory import get_memory
    from eli.cognition.agent_bus import BusMemoryAgent

    marker = ("You're stuck in the Simulation Lab with a Branch Tree and a Development "
              "Console, and I've been running stress tests on your sleep schedule.")
    mem = get_memory()
    _seed_past_session(mem, marker)

    res = BusMemoryAgent().run("what did we talk about earlier?", {}, "LIVE_SESSION", "probe_user")
    ctx = str((getattr(res, "data", {}) or {}).get("memory_context") or "")

    assert not (getattr(res, "data", {}) or {}).get("skipped"), (
        "the memory agent skipped — this fixture no longer exercises the recall path"
    )
    assert "solar hydrogen" in ctx, (
        "the user's own past turn is missing — cross-session recall has been broken, "
        "not filtered"
    )
    assert "Simulation Lab" not in ctx and "stress tests" not in ctx, (
        "ELI's own fabrication came back as history"
    )


def test_cross_session_recall_carries_only_the_users_words():
    """Third attempt at this, after two failed ones.

    The memory agent calls get_recent_conversation WITHOUT a session_id ("full
    history") and rendered it under "use this when asked about past topics" — so ELI's
    own fabrications became authoritative history. Recycled verbatim across four days.

    Attempt 1 (v2.1.74): label the block "not verified facts" — an instruction is not a
    filter; it did not hold.
    Attempt 2 (v2.1.75): filter search_conversations — real, but the wrong path;
    keyword recall was never the leak.
    This: withhold ELI's turns from the cross-session block entirely. Within-session
    continuity is unaffected (engine.py's block is session-scoped) and the anti-repeat
    guard fetches assistant turns directly.
    """
    import inspect
    from eli.cognition import agent_bus

    src = inspect.getsource(agent_bus)
    i = src.index("use when asked about past topics")
    block = src[max(0, i - 2500):i]
    assert 'str(t.get("role") or "").lower() != "user"' in block, (
        "cross-session recall can still surface ELI's own statements as history"
    )


# ── 7. the same reply re-presented is a repeat ──────────────────────────────
def test_a_reply_reformatted_as_a_list_is_still_a_repeat():
    """Live: told the news was not what was asked for, ELI served the same stories again as a
    bulleted list under bold headings. It opened differently, so the opening and head-to-head tests passed it."""
    from eli.kernel.engine import _is_repeat_of_recent
    prior = ("The harbour council has opened an independent review into the ferry collision to establish what "
             "happened before and after the impact [Coast News — 15:49]. Ada Brennan received a suspended "
             "eight-month sentence after admitting the warehouse fire [City Desk — 14:16]. The region is projected "
             "to miss its target for new rail lines by an estimated twelve kilometres [Transport Desk — 06:59].")
    again = ("Here are the latest updates:\n\n*   **Ferry Review:** The harbour council opened an independent "
             "review into the ferry collision to establish what happened before and after the impact "
             "[Coast News — 15:49].\n*   **Brennan Sentenced:** Ada Brennan received a suspended eight-month "
             "sentence after admitting the warehouse fire [City Desk — 14:16].")
    assert _is_repeat_of_recent(again, [prior])
    fresh = ("Got it, my mistake: you were telling me what's new with you, the show you're watching. "
             "How far into the season are you?")
    assert not _is_repeat_of_recent(fresh, [prior])
    quoting_one = ("You're right, I listed the review into the ferry collision to establish what happened "
                   "before and after the impact. That was the news, not what you meant, and I should have "
                   "asked about the show instead. So how is the season going so far for you?")
    assert not _is_repeat_of_recent(quoting_one, [prior])
