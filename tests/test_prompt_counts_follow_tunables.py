"""The orchestrator's recent-turn and verified-fact counts come from the tunables.

It imported a `prompt_count` this edition never had, inside a try, so every turn quietly used
fixed 8 and 12 whatever the user set.
"""
from eli.cognition import orchestrator
from eli.core import cognition_tunables as ct


def test_quick_mode_keeps_a_lean_prompt_and_deeper_modes_get_the_setting():
    snap = dict(ct.snapshot(), **{"cog.mem_recent_turns": 30, "cog.mem_semantic_shown": 20})
    assert ct.prompt_count("cog.mem_recent_turns", "quick", snap) == 8
    assert ct.prompt_count("cog.mem_recent_turns", "tree_of_thoughts", snap) == 30
    assert ct.prompt_count("cog.mem_semantic_shown", "tree_of_thoughts", snap) == 20


def test_the_orchestrator_reads_them(monkeypatch):
    monkeypatch.setattr(ct, "snapshot", lambda: {"cog.mem_recent_turns": 30, "cog.mem_semantic_shown": 20})
    assert orchestrator._recent_turns_limit("tree_of_thoughts") == 30
    assert orchestrator._verified_shown_limit("tree_of_thoughts") == 20
