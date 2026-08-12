"""Ported from v2: the cross-session recall leak (see the docstring below)."""


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
