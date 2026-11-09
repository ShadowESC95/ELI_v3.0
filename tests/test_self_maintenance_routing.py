"""Self-maintenance routing and packaged-build behaviour."""
from __future__ import annotations

from eli.execution.router_enhanced import route


def test_casual_code_change_notice_routes_chat_not_self_report():
    out = route("do you notice any changes in your code recently?")
    assert out["action"] != "SELF_REPORT"


def test_technical_git_update_still_self_report():
    out = route("what updates were made recently to your code?")
    assert out["action"] == "SELF_REPORT"


def test_fix_your_code_routes_self_patch():
    out = route("can you fix your code?")
    assert out["action"] == "SELF_PATCH"


def test_fix_the_errors_routes_self_patch():
    out = route("fix the errors")
    assert out["action"] == "SELF_PATCH"


def test_fix_those_issues_routes_self_patch():
    out = route("fix those issues")
    assert out["action"] == "SELF_PATCH"


def test_self_fix_typo_routes_self_patch():
    out = route("well do self fixx")
    assert out["action"] == "SELF_PATCH"


def test_close_app_empty_args_not_actionable():
    from eli.runtime.failure_taxonomy import classify, is_actionable

    tags = classify("missing app name", "CLOSE_APP {}")
    assert tags["category"] == "user_input"
    assert not is_actionable(tags["category"])


def test_deterministic_patch_already_applied():
    from eli.runtime.deterministic_failure_patches import propose_deterministic_patch

    patch = propose_deterministic_patch({
        "error": "Missing topic for document generation",
        "command": 'CREATE_DOCUMENT {"name": "new agent"}',
    })
    assert patch is not None
    assert patch.get("already_applied") is True


def test_a_request_to_fix_the_users_own_text_is_not_a_self_patch():
    """Both ran ELI's patch cycle on its own source, unconfirmed."""
    for said in ("can you fix the errors in this paragraph", "please fix the issues with my cv",
                 "apply a nicotine patch"):
        assert route(said)["action"] != "SELF_PATCH", said
    assert route("apply the patch")["action"] == "SELF_PATCH"
    assert route("please fix the bugs in your router")["action"] == "SELF_PATCH"


def test_questions_about_the_world_do_not_reach_elis_own_code_tools():
    """A crash, memory and sleep, a react app and speakers got AUTOPILOT_DEBUG, CODEBASE_GRAPH,
    GENERATE_TESTS and LORA_TRAIN of ELI's own code and model."""
    own_tools = {"AUTOPILOT_DEBUG", "CODEBASE_GRAPH", "GENERATE_TESTS", "LORA_TRAIN", "LORA_STATUS"}
    for said in ("why did the plane crash?", "how does memory relate to sleep", "write unit tests for my react app",
                 "fine-tune the eq on my speakers", "can you train my dog"):
        assert route(said)["action"] not in own_tools, said
    for said, action in (("debug this error: ValueError x in eli/core/paths.py", "AUTOPILOT_DEBUG"),
                         ("how does the router reach the executor", "CODEBASE_GRAPH"),
                         ("show the codebase graph", "CODEBASE_GRAPH"), ("generate tests for your code", "GENERATE_TESTS"),
                         ("fine-tune yourself", "LORA_TRAIN"), ("train a lora", "LORA_TRAIN")):
        assert route(said)["action"] == action, said


def test_advice_asked_for_is_not_elis_own_proposals():
    for said in ("i want to repaint my kitchen, any suggestions?", "do you have any ideas for me for dinner"):
        assert route(said)["action"] != "GET_PROPOSALS", said
    for said in ("do you have any proposals for me", "any suggestions?", "what are you proposing"):
        assert route(said)["action"] == "GET_PROPOSALS", said


def test_a_question_about_what_was_done_starts_nothing():
    """"did you inspect any of those files?" started a lint of ELI's core (EXAMINE_CODE) nobody asked for."""
    for asked in ("did you inspect any of those files?", "have you run the tests?"):
        assert route(asked)["action"] not in ("EXAMINE_CODE", "FILE_AUDIT", "RUN_TESTS", "SELF_TEST", "TEST_REVIEW"), asked
    assert route("did you add it to my calendar?")["action"] == "LIST_EVENTS"   # reading the state answers it
    assert route("inspect those files")["action"] == "EXAMINE_CODE"
