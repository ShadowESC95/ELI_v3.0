"""Ported from v2: the cross-session recall leak (see the docstrings below)."""
import time


def _seed_past_session(mem, marker):
    """A realistic past conversation ending on a user turn.

    Shape matters. The block drops a trailing assistant turn and then a trailing user
    turn (so the model does not regurgitate the live prompt), which silently ate a
    2-row fixture entirely and made an earlier version of this test pass against code
    with the guard REMOVED. Five turns survive that trim with the fabrication inside.
    """
    import sqlite3
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
    assert "their words only" in src[i - 200:i + 200]
