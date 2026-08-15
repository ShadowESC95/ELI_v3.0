"""Personal-memory questions must reach the personal-memory route AND carry the
memory that was retrieved for them.

Regression cover for a session where the user asked what ELI knew about them and
was told "I know your name is alex. That is the entirety of my knowledge base
regarding you" — on a turn where the memory agent had already retrieved 10,858
characters. Two independent defects combined:

  1. the phrase "- as a person," matched an UNANCHORED alternative in the
     "who are *you*" identity regex, rewriting the route to SELF_REPORT;
  2. the compact grounded-synthesis path synthesised from the executor payload
     alone (~183 chars) and returned before the full-evidence assembly, so the
     retrieved memory was dropped. The model obeyed its evidence-only contract
     and correctly reported knowing nothing.

No GGUF is used here.
"""
from __future__ import annotations

from unittest.mock import patch

from eli.runtime.control_contracts import route_control_text
from eli.runtime.personal_memory_clean_response import _extract_fact


# The verbatim question from the session log, typos and all.
USER_QUESTION = ("What do you know about me, eli ? My name, preferrences, music, "
                 "style, tone- as a person, who am i ?")


# ── 1. Routing ───────────────────────────────────────────────────────────────

def test_user_question_is_not_hijacked_to_self_report():
    assert route_control_text(USER_QUESTION, "PERSONAL_MEMORY_SUMMARY") == "PERSONAL_MEMORY_SUMMARY"


def test_user_question_routes_to_personal_memory_from_chat():
    assert route_control_text(USER_QUESTION, "CHAT") == "PERSONAL_MEMORY_SUMMARY"


def test_bare_about_me_still_routes_to_personal_memory():
    assert route_control_text("what do you know about me", "CHAT") == "PERSONAL_MEMORY_SUMMARY"


def test_eli_identity_questions_still_reach_self_report():
    """The narrowing must not cost ELI its own identity route."""
    for q in ("who are you",
              "who are you as a person",
              "tell me about yourself",
              "you, as a person, what are you",
              "how has your persona evolved"):
        assert route_control_text(q, "CHAT") == "SELF_REPORT", q


# ── 2. Evidence assembly ─────────────────────────────────────────────────────

def _capture_prompt(engine, **kwargs) -> str:
    seen = {}

    def _fake_chat(prompt, **kw):
        seen["prompt"] = prompt
        return "ok"

    with patch("eli.cognition.gguf_inference.chat_completion", side_effect=_fake_chat):
        engine._compact_grounded_synthesis(**kwargs)
    return seen.get("prompt", "")


def test_retrieved_memory_reaches_the_synthesis_prompt():
    from eli.kernel.engine import CognitiveEngine
    engine = CognitiveEngine.__new__(CognitiveEngine)   # no __init__: no model load
    prompt = _capture_prompt(
        engine,
        user_input=USER_QUESTION,
        evidence="I'm ELI. Context window: 8192 tokens. You're alex.",
        action="PERSONAL_MEMORY_SUMMARY",
        mode="self_consistency",
        memory_context="User's favourite rapper is Immortal Technique.",
    )
    assert "Immortal Technique" in prompt, "retrieved memory was dropped from the prompt"
    assert "RETRIEVED MEMORY" in prompt


def test_non_personal_actions_keep_the_tight_single_evidence_prompt():
    """Runtime/audit answers must not gain a memory block — their prompts are kept
    small on purpose (an oversized prompt here previously overflowed n_ctx)."""
    from eli.kernel.engine import CognitiveEngine
    engine = CognitiveEngine.__new__(CognitiveEngine)
    prompt = _capture_prompt(
        engine,
        user_input="what is my gpu doing",
        evidence="GPU status: RTX 2060 SUPER, 55% util.",
        action="GPU_STATUS",
        mode="chain_of_thought",
        memory_context="User's favourite rapper is Immortal Technique.",
    )
    assert "Immortal Technique" not in prompt
    assert "RETRIEVED MEMORY" not in prompt


def test_empty_memory_context_keeps_the_single_evidence_prompt():
    from eli.kernel.engine import CognitiveEngine
    engine = CognitiveEngine.__new__(CognitiveEngine)
    prompt = _capture_prompt(
        engine,
        user_input=USER_QUESTION,
        evidence="You're alex.",
        action="PERSONAL_MEMORY_SUMMARY",
        mode="quick",
        memory_context="   ",
    )
    assert "RETRIEVED MEMORY" not in prompt


# ── 3. Read-boundary normalisation ───────────────────────────────────────────

def test_first_person_statements_become_readable_facts():
    """The writer stores the user's own words; the readback patterns required a
    literal 'User ' prefix that nothing ever produced."""
    cases = {
        "my favourite rapper is Immortal Technique": "favourite rapper",
        "I prefer dark themes and terse answers": "prefers",
        "i use arch linux and neovim": "uses",
        "I am a security researcher": "is a security researcher",
        "i love hip hop and documentaries": "loves",
        "my name is Alex": "name is Alex",
    }
    for raw, expected in cases.items():
        got = _extract_fact(raw)
        assert got and expected.lower() in got.lower(), f"{raw!r} -> {got!r}"


def test_already_third_person_facts_still_extract():
    assert _extract_fact("User prefers concise answers") == "User prefers concise answers."


def test_log_noise_is_still_rejected():
    for noise in ("ERROR: traceback module not found",
                  "[PROACTIVE] Daemon started - continuous learning active"):
        assert _extract_fact(noise) is None, noise
