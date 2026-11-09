"""A failed action's replacement, proposed by the model, is admitted the way a resolved intent is.

_eli_tool_failure_replan asked the model for an alternative and ran it with the model's own args
after checking only that the name was a supported action, so a failed close could turn into a shell
command or a keystroke the user never asked for. Now only an action that does the same deed by other
means (action_outcomes.same_deed) is tried, on the request's own args.
"""
import pytest

from eli.kernel.engine import CognitiveEngine

# action_outcomes.same_deed's groups, as a stub, so this file tests the engine's admission alone
_SAME = ({"OPEN_APP", "OPEN_URL", "FOCUS_APP"}, {"WEB_SEARCH", "NEWS_FETCH"})


@pytest.fixture
def replan(monkeypatch):
    ran = []
    eng = CognitiveEngine.__new__(CognitiveEngine)
    eng._gguf_available = True
    proposal = {}
    import eli.cognition.gguf_inference as gi
    import eli.execution.executor_enhanced as ex
    import eli.cognition.action_trust as trust
    import eli.runtime.action_outcomes as ao
    monkeypatch.setattr(gi, "generate_json", lambda *a, **k: dict(proposal))
    monkeypatch.setattr(ex, "execute", lambda action, args: ran.append((action, args)) or {"ok": True})
    monkeypatch.setattr(ao, "same_deed", lambda a, b: a != b and any(a in g and b in g for g in _SAME),
                        raising=False)

    def run(said, failed, args, alt, trusted=True, result=None):
        ran.clear()
        proposal.clear()
        proposal.update(alt)
        monkeypatch.setattr(trust, "may_choose_actions", lambda: trusted)
        eng._eli_tool_failure_replan(said, failed, dict(args), dict(result or {"ok": False, "error": "it failed"}))
        return list(ran)
    return run


def test_a_replacement_that_is_not_the_same_deed_is_not_run(replan):
    assert replan("close the music player", "CLOSE_APP", {"name": "player"},
                  {"action": "SHELL_EXEC", "args": {"command": "pkill player"}}) == []
    assert replan("close the music player", "CLOSE_APP", {"name": "player"},
                  {"action": "KEYBOARD", "args": {"keys": "alt+f4"}}) == []


def test_the_same_deed_runs_on_the_requests_own_args_not_the_models(replan):
    assert replan("show me the latest news about the comet", "NEWS_FETCH", {"topic": "comet"},
                  {"action": "WEB_SEARCH", "args": {"query": "something else entirely"}}) == [
        ("WEB_SEARCH", {"topic": "comet"})]


def test_nothing_is_run_while_the_model_may_not_choose_actions(replan):
    assert replan("show me the latest news about the comet", "NEWS_FETCH", {"topic": "comet"},
                  {"action": "WEB_SEARCH", "args": {}}, trusted=False) == []


def test_a_result_that_already_went_another_way_is_not_replanned(replan):
    assert replan("show me the latest news about the comet", "NEWS_FETCH", {"topic": "comet"},
                  {"action": "WEB_SEARCH", "args": {}},
                  result={"ok": False, "error": "x", "instead_of": {"action": "NEWS_FETCH"}}) == []
