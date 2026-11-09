"""The orchestrator's grounding escalation read the last 8 conversation turns
with no user filter, so on the shared API engine one user's escalation could
be primed with another user's messages. It now reads the current user's turns
from any of their sessions, the same scope as the orchestrator's episodic
block."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from eli.cognition.orchestrator import AgentOrchestrator


def test_escalation_reads_only_the_current_users_turns():
    memory = MagicMock()
    memory.get_recent_conversation.return_value = [{"role": "user", "content": "hi"}]
    engine = SimpleNamespace(memory=memory, user_id="alice", session_id="s-1",
                             _crisis_steering=None)
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch.engine = engine
    wm = SimpleNamespace(trace={}, bus_result=None)

    with patch("eli.runtime.grounding_escalation.escalate", return_value=None) as esc:
        orch._maybe_grounding_escalate(wm, "what's new", {"action": "CHAT"},
                                       stream=False, reasoning_mode="quick")

    memory.get_recent_conversation.assert_called_once()
    _, kwargs = memory.get_recent_conversation.call_args
    assert kwargs.get("user_id") == "alice"
    assert "session_id" not in kwargs  # any of alice's sessions
    assert esc.call_args.kwargs["recent_turns"] == [{"role": "user", "content": "hi"}]


def _orch(**engine):
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    engine.setdefault("_store_assistant_turn", lambda t: None)
    orch.engine = SimpleNamespace(memory=MagicMock(), user_id="alice", session_id="s-1", _crisis_steering=None,
                                  _in_orchestrator=True, **engine)
    return orch


def test_a_message_about_eli_brings_the_record_and_its_measured_accuracy(monkeypatch):
    """'How accurate are you?' had only the turn rows, no figures, and the claim check then dropped any
    figure ELI gave. The test for 'about ELI' is self_claims.about_eli (its wording, or what ELI just did)."""
    from eli.cognition import self_claims, epistemics
    from eli.kernel import request_context as rc
    asked = []
    monkeypatch.setattr(self_claims, "about_eli",
                        lambda text, mem=None, user_id="": asked.append((text, user_id)) or True, raising=False)
    monkeypatch.setattr(self_claims, "turn_record_lines", lambda mem, user_id="": ["- 10:00 you asked: x; ran CHAT"])
    monkeypatch.setattr(epistemics, "measured_lines",
                        lambda **k: ["Measured accuracy (settled outcomes ...):", "- replies to you: 40 settled, 85% proved right"])
    facts = {"request_id": "r1"}
    token = rc.turn_facts_var.set(facts)
    try:
        wm = SimpleNamespace(assembled_context="older context")
        assert _orch()._add_own_record(wm, "how accurate are you?") is True
    finally:
        rc.turn_facts_var.reset(token)
    assert asked == [("how accurate are you?", "alice")]
    assert "85% proved right" in wm.assembled_context and "you asked: x" in wm.assembled_context
    assert "85% proved right" in facts["_record"], "the figures are what the claim check holds the reply to"
    monkeypatch.setattr(self_claims, "about_eli", lambda *a, **k: False, raising=False)
    assert _orch()._add_own_record(SimpleNamespace(assembled_context=""), "play some jazz") is False


def test_an_escalation_answer_is_held_to_the_record_before_it_is_shown(monkeypatch):
    seen = {}

    def govern(text, is_grounded=False, evidence=None, **k):
        seen.update(text=text, evidence=evidence, grounded=is_grounded)
        return "held: " + text
    monkeypatch.setattr("eli.cognition.output_governor.govern_output", govern)
    orch = _orch()
    wm = SimpleNamespace(trace={}, bus_result=None, assembled_context="the context it was made from")
    with patch("eli.runtime.grounding_escalation.escalate", return_value={"response": "the answer", "meta": {}}):
        out = orch._maybe_grounding_escalate(wm, "what's new", {"action": "CHAT"}, stream=False, reasoning_mode="quick")
    assert out["response"] == "held: the answer" and seen["grounded"] is True
    assert "the context it was made from" in seen["evidence"]



def test_an_escalation_answer_is_stored_as_it_was_shown(monkeypatch):
    """The escalation stored its text before the engine checked the stream, and the turn then
    counted as stored: the copy kept was not the one on the screen. It is stored when the turn
    ends, from what was shown."""
    from eli.kernel.engine import CognitiveEngine
    from eli.kernel import request_context as rc
    from eli.kernel.stages import memory_commit
    stored = []
    monkeypatch.setattr("eli.cognition.output_governor.govern_output", lambda text, **k: text)
    orch = _orch(_store_assistant_turn=lambda t: stored.append(("early", t)),
                 _yield_text_chunks=lambda text, chunk_size=12: iter([text]))
    wm = SimpleNamespace(trace={}, bus_result=None, assembled_context="")
    with patch("eli.runtime.grounding_escalation.escalate",
               return_value={"response": "The tide is high at noon. I checked it twice.", "meta": {}}):
        stream = orch._maybe_grounding_escalate(wm, "when is high tide", {"action": "CHAT"},
                                                stream=True, reasoning_mode="quick")
    assert stored == [], "stored before it was shown"
    eng = CognitiveEngine()
    monkeypatch.setattr(memory_commit.MemoryCommitMixin, "_store_assistant_turn",
                        lambda self, t: stored.append(("shown", t)) or rc.note_turn_fact("assistant_stored", True))
    monkeypatch.setattr(eng, "_process_impl", lambda *a, **k: (p for p in stream))
    shown = "".join(eng.process("when is high tide", stream=True))
    assert stored == [("shown", shown)]
