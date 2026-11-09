import pytest

from eli.cognition.output_governor import (
    govern_output,
    normalize_assistant_text,
    repair_local_persona_drift,
    strip_fabricated_diagnostic_shell,
    strip_generic_ai_identity_drift,
)
from eli.kernel import request_context as rc

# What the governor used to serve as ELI's answer. None of these is ever a reply.
_GOVERNOR_NOTES = ("Wrong frame", "Persona drift detected", "drifted into generic model-speak",
                   "got the timing wrong", "should be regenerated")


@pytest.fixture()
def turn():
    facts = {"user_input": "a question"}
    token = rc.turn_facts_var.set(facts)
    yield facts
    rc.turn_facts_var.reset(token)


def _no_note(text):
    assert not any(n.lower() in str(text).lower() for n in _GOVERNOR_NOTES), text


def test_open_head_repair_frame_asks_the_model_again(turn):
    text = normalize_assistant_text(
        "I said I performed open head surgery on you.",
        "I'm an adaptive intelligence system. I cannot remember any surgical procedure in this context.",
    )
    _no_note(text)
    assert "cannot remember any surgical procedure" not in text
    # the reply misread the frame: the engine asks the model again, told what not to say
    short = turn["reply_fell_short"]
    assert "I cannot remember any surgical procedure in this context." in short["dropped"]


def test_generic_surgical_disclaimer_does_not_leave_fragment():
    text = normalize_assistant_text(
        "What do you recall?",
        "I'm an adaptive intelligence system. I cannot remember any surgical procedure in this context.",
    )

    assert text == "I'm an adaptive intelligence system."
    assert "surgical procedure" not in text
    assert "in this context" not in text


def test_engine_visible_response_governance_uses_output_governor(turn):
    from eli.kernel.engine import CognitiveEngine

    engine = object.__new__(CognitiveEngine)
    text = engine._govern_visible_response(
        "I performed open head surgery on you.",
        "I'm an adaptive intelligence system. I cannot remember any surgical procedure in this context.",
        memory_context="local repair memory",
        is_grounded=True,
    )

    _no_note(text)
    assert "cannot remember any surgical procedure" not in text
    assert turn.get("reply_fell_short")


@pytest.mark.parametrize("reply,left", [
    # a disclaimer goes as a whole sentence, never as a phrase cut out of one
    ("I don't have emotions in the way humans do. That song is still a banger though.",
     "That song is still a banger though."),
    ("I do not have personal memories, beyond what is programmed. Your train leaves at nine.",
     "Your train leaves at nine."),
])
def test_a_disclaimer_goes_as_a_whole_sentence(reply, left):
    out = repair_local_persona_drift(reply)
    assert out == left
    assert "in the way humans do" not in out


@pytest.mark.parametrize("reply", [
    "I don't have emotions in the way humans do.",
    "I do not have personal memories.",
])
def test_a_reply_that_was_only_a_disclaimer_is_asked_for_again(reply, turn):
    out = repair_local_persona_drift(reply)
    _no_note(out)
    assert out == reply                        # the model's own words until it says it again
    assert turn["reply_fell_short"]["dropped"] == [reply]


@pytest.mark.parametrize("reply", [
    "As an AI, I do not have opinions.",
    "As an AI language model, I can't. Sorry.",
])
def test_a_generic_ai_opening_with_nothing_after_it_is_asked_for_again(reply, turn):
    out = strip_generic_ai_identity_drift(reply)
    _no_note(out)
    assert turn["reply_fell_short"]["dropped"]
    _no_note(govern_output(reply))


def test_a_generic_ai_opening_goes_and_the_answer_stays(turn):
    assert strip_generic_ai_identity_drift("As an AI, I have no view on it. The film runs 132 minutes.") == \
        "The film runs 132 minutes."
    assert "reply_fell_short" not in turn


def test_an_invented_shell_block_alone_is_not_answered_with_an_apology(turn):
    reply = "```bash\njournalctl -u eli --since today\n```"
    out = strip_fabricated_diagnostic_shell(reply)
    _no_note(out)
    assert turn["reply_fell_short"]["dropped"]
