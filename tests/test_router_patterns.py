"""Extended router pattern tests — ~200 tests covering all route branches."""
from __future__ import annotations

import pytest
from eli.execution.router_enhanced import route


def action(text: str) -> str:
    return (route(text).get("action") or "").upper()


# ── Math / factual routing ────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "What is 2 + 2?",
    "Calculate the square root of 144",
    "What is pi?",
    "Convert 100 Fahrenheit to Celsius",
    "What is 1024 in binary?",
    "What's the integral of x^2?",
    "Solve: 3x + 5 = 20",
    "What year did WW2 end?",
    "Who wrote Hamlet?",
    "What is the speed of light?",
    "How far is the moon from Earth?",
    "What is the boiling point of water?",
])
def test_factual_queries(text):
    result = route(text)
    assert isinstance(result, dict)
    a = (result.get("action") or "").upper()
    assert a in ("CHAT", "FACTUAL", "COGNITIVE_CHAT"), f"'{a}' for: {text}"


# ── Identity / self-awareness queries ────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Who are you?",
    "What are you?",
    "What is your name?",
    "Introduce yourself",
    "Tell me about yourself",
    "What are you capable of?",
    "What can you do?",
    "What are your limitations?",
    "Are you an AI?",
    "Are you sentient?",
])
def test_identity_queries(text):
    result = route(text)
    assert isinstance(result, dict)
    a = (result.get("action") or "").upper()
    assert a in ("CHAT", "SELF_REPORT", "COGNITIVE_CHAT", "MEMORY_RECALL",
                 "USER_IDENTITY_SUMMARY", "LIST_CAPABILITIES"), f"'{a}' for: {text}"


# ── Cognition/pipeline queries ────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "How does your cognition pipeline work?",
    "What agents are you running?",
    "Show your cognition status",
    "What is your pipeline doing?",
    "Describe your architecture",
])
def test_cognition_queries(text):
    result = route(text)
    assert isinstance(result, dict)
    a = (result.get("action") or "").upper()
    assert a in ("CHAT", "COGNITION_STATUS", "RUNTIME_STATUS",
                 "COGNITIVE_CHAT", "SELF_REPORT",
                 "EXPLAIN_COGNITION_RUNTIME", "LIST_CAPABILITIES"), f"'{a}' for: {text}"


# ── Long-form queries ─────────────────────────────────────────────────────

LONG_QUERIES = [
    "I'm building a distributed machine learning system with PyTorch and I need to understand the best practices for gradient synchronization across multiple GPUs. Can you explain the different approaches?",
    "Can you explain the differences between supervised, unsupervised, and reinforcement learning, including when to use each approach and what kinds of problems they're best suited for?",
    "I have a Python application that's running slowly. I've already tried basic profiling and found the bottleneck is in database queries. What are the best approaches for optimizing SQLite queries in Python?",
    "What are the main philosophical differences between the utilitarian and deontological approaches to ethics, and how might these apply to real-world AI systems development?",
]

@pytest.mark.parametrize("text", LONG_QUERIES)
def test_long_query_routing(text):
    result = route(text)
    assert isinstance(result, dict)
    a = (result.get("action") or "").upper()
    assert a in ("CHAT", "FACTUAL", "COGNITIVE_CHAT", "MEMORY_RECALL"), \
        f"'{a}' for long query"


# ── Visual content-questions gather a real glance (not CHAT guessing) ────────
# Regression: a question about what's ON the screen must route to a real vision
# pass, never fall through to CHAT where the model fabricates an answer
# (user-reported: ELI invented a Star Trek season/episode it never looked at).
@pytest.mark.parametrize("text", [
    "what season, or episode is currently on the screen ?",
    "can you see star trek on the screen?",
    "which app is on my display?",
    "who is on screen right now",
    "what is on the screen",
    "tell me what's on my monitor",
])
def test_screen_content_questions_route_to_vision(text):
    assert action(text) == "SCREEN_READ_ANALYZE"


def test_screen_content_question_passes_user_question_to_vision():
    # The user's own question is handed to the VL model with an anti-fabrication
    # instruction so it answers from pixels (or admits it can't tell).
    r = route("what season or episode is on the screen?")
    assert r.get("action") == "SCREEN_READ_ANALYZE"
    prompt = (r.get("args") or {}).get("prompt", "")
    assert "season or episode" in prompt.lower()
    assert "never guess" in prompt.lower() or "cannot tell" in prompt.lower()


@pytest.mark.parametrize("text", [
    "turn on the screen",
    "i saw it on the screen earlier",
    "what is going on",
])
def test_non_visual_screen_phrases_do_not_route_to_vision(text):
    assert action(text) != "SCREEN_READ_ANALYZE"


# ── EXAMINE_CODE vs GUI_RUNTIME_AUDIT disambiguation (regression) ────────────
# A GUI/runtime audit-with-proof request must NOT be stolen by the EXAMINE_CODE
# guard (which runs in an earlier stage). Both are part of ELI's self-inspection
# / self-upgrade surface and must keep their own routes.
@pytest.mark.parametrize("text", [
    "scan the gui runtime wiring and prove every hook with actual file-read evidence",
    "audit your gui file in full and prove you read it",
])
def test_runtime_audit_proof_not_stolen_by_examine_code(text):
    assert action(text) == "GUI_RUNTIME_AUDIT"


@pytest.mark.parametrize("text", [
    "examine eli/memory/memory.py for errors",
    "review the codebase for bugs",
    "scan eli/runtime/scheduled_tasks.py for issues",
    "inspect eli.kernel.engine for mistakes",
])
def test_code_examination_still_routes_to_examine_code(text):
    assert action(text) == "EXAMINE_CODE"


def test_hardcoded_provenance_routes_explain_last_response():
    assert action("is that hardcoded?") == "EXPLAIN_LAST_RESPONSE"
    assert action("was that response deterministic?") == "EXPLAIN_LAST_RESPONSE"


def test_fix_recall_routes_examine_code():
    assert action("how did you fix that file/LOC?") == "EXAMINE_CODE"
    assert action("which line did you change?") == "EXAMINE_CODE"


# ── a report on ELI answers a question about ELI ──────────────────────────

@pytest.mark.parametrize("text", ["what is heart failure?", "what are common failures of hard drives"])
def test_failures_of_other_things_are_not_elis_failure_log(text):
    assert action(text) != "EXPLAIN_FAILURE_LOG"


@pytest.mark.parametrize("text", ["what are the failures", "show the failure log", "show me the last 20 failures",
                                  "what failed?", "list executor failures"])
def test_elis_own_failures_still_get_the_log(text):
    assert action(text) == "EXPLAIN_FAILURE_LOG"


def test_a_prior_claim_question_is_not_turned_into_the_log_by_a_count():
    """`not X and A or B` let "\\d+ failures" fire whatever X said; a count of another thing's failures
    is not ELI's log either."""
    assert action("my car had 3 failures this year, is that normal?") != "EXPLAIN_FAILURE_LOG"


@pytest.mark.parametrize("text", ["how many days until the 4th of May?", "what day of the week is the 4th of may?",
                                  "the loan may default next month"])
def test_a_date_in_may_is_answered_not_met_with_a_fixed_reply(text):
    """The voice contract returned a fixed Star Wars line (shown as silence) for any of these."""
    out = route(text)
    assert out["action"] != "NOOP" and "may_fourth" not in str((out.get("meta") or {}).get("matched_by"))


@pytest.mark.parametrize("text", ["who's the best internet provider in my area", "any good reddit threads about python"])
def test_a_provider_or_threads_of_the_world_is_not_runtime_status(text):
    assert action(text) != "RUNTIME_STATUS"


@pytest.mark.parametrize("text", ["what model are you running", "how many threads are you using", "runtime status"])
def test_runtime_questions_still_get_runtime_status(text):
    assert action(text) == "RUNTIME_STATUS"


def test_the_coding_agents_language_is_read_by_the_one_reader():
    """A bare word list read "go through" as the Go language."""
    got = route("use the coding agent to go through the list and sort it")
    assert got["action"] == "CODE_SOLVE" and got["args"]["language"] != "go"
    assert route("implement quicksort in go and make sure it passes tests")["args"]["language"] == "go"


def test_a_file_is_replaced_only_when_the_request_says_so():
    assert "overwrite" not in route("create a file called notes.txt with hello")["args"]
    assert route("overwrite the file notes.txt with hello")["args"].get("overwrite") is True


def test_a_diff_of_two_named_files_names_them():
    got = route("show me the changes between a.py and b.py")
    assert got["action"] == "SHOW_DIFF" and got["args"].get("paths") == ["a.py", "b.py"]
    assert route("show the diff")["args"] == {}


def test_a_diagnostic_report_needs_the_question_to_be_about_eli(monkeypatch):
    """"how many memories can a human brain hold" got ELI's memory count and "which model has the biggest
    context window" its runtime report."""
    from eli.cognition import self_claims
    monkeypatch.setattr(self_claims, "about_eli", lambda text, **k: False, raising=False)
    for asked in ("how many memories can a human brain hold", "which model has the biggest context window",
                  "what's the best model for coding", "how much ram does a raspberry pi have"):
        assert action(asked) not in ("MEMORY_STATUS", "RUNTIME_STATUS", "EXPLAIN_COGNITION_RUNTIME"), asked
    assert action("how many memories do you have") == "MEMORY_STATUS"
    assert action("what is your context window") == "EXPLAIN_COGNITION_RUNTIME"
    assert action("what model is loaded") == "RUNTIME_STATUS"
    # ELI's own doing, as the reader of the record says, counts too
    monkeypatch.setattr(self_claims, "about_eli", lambda text, **k: True, raising=False)
    assert action("how many memories are there") == "MEMORY_STATUS"


def test_a_correction_lead_stands_in_front_of_the_instruction():
    """"i said play X" was read as a statement about what was said, and the play never ran."""
    got = route("i said play golden hour by jvke")
    assert got["action"] == "PLAY_MEDIA" and got["args"]["query"].lower() == "golden hour by jvke"
    assert route("no, i meant open the downloads folder")["action"] in ("OPEN_FILE_SYSTEM", "OPEN_APP")
    assert action("i said it's raining") == "CHAT"
    from eli.cognition.llm_intent import is_plain_statement
    assert not is_plain_statement("i said play golden hour by jvke")


def test_the_routers_pipeline_trace_is_a_log_of_this_install_not_a_file_in_its_tree():
    """It was appended to <install>/ops/reports/..., so a test or an eval run from a checkout wrote there."""
    from eli.core.paths import logs_dir, project_root
    from eli.execution import router_enhanced as router
    trace = logs_dir() / "pipeline_visibility" / "runtime_pipeline_trace.jsonl"
    old = project_root() / "ops" / "reports" / "pipeline_visibility" / "runtime_pipeline_trace.jsonl"
    before = old.stat().st_mtime_ns if old.exists() else None
    router._eli_pipeline_trace("test_stage", note="a line for this test")
    assert '"test_stage"' in trace.read_text(encoding="utf-8").splitlines()[-1]
    assert (old.stat().st_mtime_ns if old.exists() else None) == before
