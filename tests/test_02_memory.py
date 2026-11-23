"""
test_02_memory.py
=================
Tests for eli.memory — the full memory subsystem.
"""


# (removed test_memory_working_memory_loadable — eli.memory.working_memory was deleted)


def test_memory_db_paths_exists():
    # The memory layer exposes the resolve_db_paths() helper. Verify the memory-layer API resolves DB paths.
    from eli.memory import resolve_db_paths
    paths = resolve_db_paths()
    assert paths is not None
    assert any(getattr(paths, a, None) for a in ("user_db", "agent_db", "memory_db"))
