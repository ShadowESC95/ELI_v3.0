from __future__ import annotations

import pytest


def test_stage_order_is_the_legacy_order():
    """The stages are a first-match-wins chain lifted out of route() in place.
    Reordering them silently changes which guard shadows which, so the order is
    part of the contract, not an implementation detail."""
    from eli.execution.routing.intents import INTENT_STAGES

    assert [name for name, _ in INTENT_STAGES] == [
        "documents",
        "system_settings",
        "app_launch",
        "shell_ops",
        "capture_input",
        "session_ops",
    ]


def test_every_stage_has_the_same_signature():
    import inspect

    from eli.execution.routing.intents import INTENT_STAGES

    for name, fn in INTENT_STAGES:
        params = list(inspect.signature(fn).parameters)
        assert params == ["raw", "low", "text_l"], f"{name} takes {params}"


def test_stages_pass_by_returning_none():
    """A stage that matches nothing must return None so the next one runs."""
    from eli.execution.routing.intents import INTENT_STAGES

    for name, fn in INTENT_STAGES:
        assert fn("zzzq", "zzzq", "zzzq") is None, f"{name} claimed a nonsense input"


@pytest.mark.parametrize("stage_name,text,action", [
    ("documents", "what is the weather", "GET_WEATHER"),
    ("system_settings", "open settings", "OPEN_SYSTEM_SETTINGS"),
    ("app_launch", "open spotify", "OPEN_APP"),
    ("shell_ops", "grep foo bar.txt", "SHELL_EXEC"),
    ("capture_input", "note: buy milk", "WRITE_NOTE"),
    ("session_ops", "help", "HELP"),
])
def test_each_stage_is_reachable_and_owns_its_intent(stage_name, text, action):
    """Every stage is actually reached through route() — an unreachable stage
    would mean an earlier guard swallowed its whole family."""
    import eli.execution.router_enhanced as R
    from eli.execution.routing.intents import INTENT_STAGES

    stage = dict(INTENT_STAGES)[stage_name]
    direct = stage(text, text.lower(), text.lower())
    assert direct is not None, f"{stage_name} did not claim {text!r}"
    assert direct["action"] == action

    via_route = R._ROUTE_CORE(text)
    assert via_route["action"] == action


def test_route_delegates_its_tail_to_the_stage_list():
    """route() must consult INTENT_STAGES rather than carrying inline copies."""
    import inspect

    import eli.execution.router_enhanced as R

    src = inspect.getsource(R._ROUTE_CORE)
    assert "INTENT_STAGES" in src


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
