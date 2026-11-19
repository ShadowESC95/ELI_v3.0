"""ELI must be told, in the prompt, not to repeat what it has already said.

Three live loops, three different phrasings — "still glitchy, still running on the same
old code", then "post-breakfast haze and Puffin Cove on repeat" — each re-asking
"How are you?" after the user had answered, and continuing even after being told to
stop. Purging the text doesn't help: the mechanism just latches onto a new phrase.

The output-side echo guard cannot fire on the streaming path (it lives in finalize,
which streaming never reaches), so the contract has to be stated in the prompt, where
it applies to every path. This tests the block the engine puts ahead of the persona handoff.
"""
from __future__ import annotations

from eli.kernel.engine import CognitiveEngine

LOOP = ("I'm standing by, still got that post-breakfast haze and Puffin Cove "
        "on repeat. Everything's fine. How are you?")


class _NoStore:
    """No stored conversation: only the turns the test hands over are read."""
    def get_recent_conversation(self, **_kw):
        return []


class _Engine:
    """The engine's own contract builder and reply collector, with nothing else of the engine."""
    memory = _NoStore()
    _collect_recent_assistant_replies = CognitiveEngine._collect_recent_assistant_replies
    _inject_anti_repeat_contract = CognitiveEngine._inject_anti_repeat_contract


def _build_contract(conversations):
    return _Engine()._inject_anti_repeat_contract("what happens next?", "", recent_turns=conversations)


def test_the_retry_can_find_the_contract_to_take_it_out():
    """The retry strips this block by its wording (guards._ANTI_REPEAT_BLOCK_RE): the two must agree."""
    from eli.kernel.stages.guards import _ANTI_REPEAT_BLOCK_RE
    c = _build_contract([{"role": "assistant", "content": LOOP}])
    assert _ANTI_REPEAT_BLOCK_RE.search(c)
    assert _Engine()._inject_anti_repeat_contract("what happens next?", c, recent_turns=[
        {"role": "assistant", "content": LOOP}]) == c, "a brief that already holds it gets no second one"


def test_contract_is_emitted_when_eli_has_spoken():
    convo = [{"role": "assistant", "content": LOOP}, {"role": "user", "content": "yo"}]
    c = _build_contract(convo)
    assert "ALREADY SAID" in c
    assert "post-breakfast haze" in c          # the exact line is quoted back
    assert "re-ask a question" in c            # covers the repeated "How are you?"


def test_contract_absent_on_a_fresh_conversation():
    assert _build_contract([{"role": "user", "content": "yo"}]) == ""
    assert _build_contract([]) == ""


def test_contract_quotes_at_most_three_and_truncates():
    convo = [{"role": "assistant", "content": f"{LOOP} #{i}"} for i in range(6)]
    c = _build_contract(convo)
    assert c.count("  - ") == 3               # bounded, so it can't bloat the prompt
    for line in [l for l in c.splitlines() if l.startswith("  - ")]:
        assert len(line) <= 226


def test_user_turns_are_not_quoted_back_as_elis_own():
    convo = [{"role": "user", "content": "I am tired and had breakfast"},
             {"role": "assistant", "content": LOOP}]
    c = _build_contract(convo)
    assert "I am tired" not in c
