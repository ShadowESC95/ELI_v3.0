from __future__ import annotations

import pytest


def test_stage_order_is_the_legacy_order():
    """The stages are a first-match-wins chain lifted out of route() in place.
    Reordering them silently changes which guard shadows which, so the order is
    part of the contract, not an implementation detail."""
    from eli.execution.routing.intents import INTENT_STAGES

    assert [name for name, _ in INTENT_STAGES] == [
        # early group — before the inline code-generation block
        "self_report",
        "runtime_voice",
        "input_agents",
        "world_games",
        # tail group — after it
        "documents",
        "system_settings",
        "app_launch",
        "shell_ops",
        "world_models",
        "capture_input",
        "session_ops",
    ]


def test_every_stage_has_the_same_signature():
    import inspect

    from eli.execution.routing.intents import INTENT_STAGES

    for name, fn in INTENT_STAGES:
        params = list(inspect.signature(fn).parameters)
        assert params == ["text", "raw", "low", "text_l"], f"{name} takes {params}"


def test_stages_pass_by_returning_none():
    """A stage that matches nothing must return None so the next one runs."""
    from eli.execution.routing.intents import INTENT_STAGES

    for name, fn in INTENT_STAGES:
        assert fn("zzzq", "zzzq", "zzzq", "zzzq") is None, \
            f"{name} claimed a nonsense input"


@pytest.mark.parametrize("stage_name,text,action", [
    ("self_report", "what did you update recently", "SELF_REPORT"),
    ("runtime_voice", "list voices", "LIST_VOICES"),
    ("input_agents", "double click", "GAZE_CLICK"),
    ("documents", "what is the weather", "GET_WEATHER"),
    ("system_settings", "open settings", "OPEN_SYSTEM_SETTINGS"),
    ("app_launch", "open spotify", "OPEN_APP"),
    ("shell_ops", "grep foo bar.txt", "SHELL_EXEC"),
    ("world_games", "set up a catch game in your room", "PLAY_GAME"),
    ("world_models", "fetch a model of a pine tree", "FETCH_MODEL"),
    ("capture_input", "note: buy milk", "WRITE_NOTE"),
    ("session_ops", "help", "HELP"),
])
def test_each_stage_is_reachable_and_owns_its_intent(stage_name, text, action):
    """Every stage is actually reached through route() — an unreachable stage
    would mean an earlier guard swallowed its whole family."""
    import eli.execution.router_enhanced as R
    from eli.execution.routing.intents import INTENT_STAGES

    stage = dict(INTENT_STAGES)[stage_name]
    direct = stage(text, text, text.lower(), text.lower())
    assert direct is not None, f"{stage_name} did not claim {text!r}"
    assert direct["action"] == action

    via_route = R._ROUTE_CORE(text)
    assert via_route["action"] == action


def test_intents_do_not_import_the_router_back():
    """The stages are leaves: importing router_enhanced from them would make the
    package a cycle and defeat the extraction."""
    import ast
    from pathlib import Path

    import eli.execution.routing.intents as pkg

    for path in Path(pkg.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
            elif isinstance(node, ast.Import):
                mod = ",".join(a.name for a in node.names)
            if mod and "router_enhanced" in mod:
                pytest.fail(f"{path.name} imports the router back: {mod}")


def test_route_none_sentinel_is_distinct_from_passing():
    """A stage returning None means "not mine, try the next one". A stage
    returning ROUTE_NONE means "routing ends here and route() returns None" —
    which is what a bare `return None` did while these guards were inline.
    Collapsing the two silently upgraded unrouted turns into a confident CHAT."""
    from eli.execution.routing.intents import ROUTE_NONE

    assert ROUTE_NONE is not None
    assert not ROUTE_NONE          # falsy, but distinguishable by identity


def test_topic_without_a_verb_stays_unrouted():
    """Regression: 'voice' names a subject but asks for nothing. route() must
    return None so the priority pipeline applies its own fallback, rather than
    claiming the turn with route()'s higher-confidence CHAT."""
    import eli.execution.router_enhanced as R

    for text in ("voice", "please accent", "voice now"):
        assert R._ROUTE_CORE(text) is None, text
