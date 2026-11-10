"""Registration + contract invariants for every effector-registry action."""
import pytest

import eli.execution.executor_enhanced as EX
from eli.execution.effectors.registry import registered_actions, get

REG = sorted(registered_actions())


def test_registry_populated_min_count():
    assert len(REG) >= 118


@pytest.mark.parametrize("action", REG)
def test_registered_action_is_callable(action):
    assert callable(get(action))


@pytest.mark.parametrize("action", REG)
def test_registered_action_is_supported(action):
    assert action in EX.SUPPORTED_ACTIONS


def test_registry_lookup_case_insensitive():
    assert get("date") is get("DATE") is get("  Date ")


def test_every_registered_action_in_supported_surface():
    missing = [a for a in REG if a not in EX.SUPPORTED_ACTIONS]
    assert not missing, f"registry actions absent from SUPPORTED_ACTIONS: {missing}"


def test_unknown_action_returns_none():
    assert get("NO_SUCH_ACTION_XYZ") is None


def test_no_duplicate_family_owners():
    seen = {}
    for a in REG:
        fn = get(a)
        seen.setdefault(id(fn), []).append(a)
    for fn_id, actions in seen.items():
        assert len(actions) <= 13, f"one handler owns too many actions: {actions}"


# ── one declaration per action ─────────────────────────────────────────────────────────────────
@pytest.fixture()
def a_new_action():
    """Registered for the test and taken out of the registry again afterwards."""
    from eli.execution.effectors import registry
    names = ("A_TEST_ONLY_REPORT", "A_TEST_ONLY_STEP")
    yield names
    for name in names:
        registry._HANDLERS.pop(name, None)
        registry._DECLARED.pop(name, None)
        registry.CATALOGUE.pop(name, None)
    registry._VERSION[0] += 1


def test_a_new_action_is_declared_once_and_every_list_reads_it(a_new_action, monkeypatch):
    """Adding an action meant writing it into SUPPORTED_ACTIONS, the catalogue, the engine's and the
    fast path's verbatim sets, the resolver's internal set and MCP's open set, each by hand (the yes
    to a fix candidate was added to six). One register() says it now, and each of them reads it."""
    from eli.cognition.llm_intent import _catalogue, _INTERNAL_ACTIONS
    from eli.execution.effectors import registry
    from eli.integrations.mcp import server as mcp
    from eli.kernel.stages.fast_actions import _DIRECT_FINAL_ACTIONS, _DIRECT_RESULT_ACTIONS, _shown_as_is
    from eli.tools.registry.capabilities_doc import _C

    report, step = a_new_action
    handler = lambda a, args: {"ok": True, "action": a, "content": "done", "response": "done"}
    registry.register(report, handler, about=("System status & info", "A report made for this test", ["“test report”"]),
                      shown="always", direct=True, open=True)
    registry.register(step, handler, offered=False)

    assert report in EX.SUPPORTED_ACTIONS and step in EX.SUPPORTED_ACTIONS
    assert _C[report] == ("System status & info", "A report made for this test", ["“test report”"])
    assert report in _DIRECT_RESULT_ACTIONS and report in _DIRECT_FINAL_ACTIONS
    assert _shown_as_is(report, "chain_of_thought") and not _shown_as_is(step, "quick")
    assert step in _INTERNAL_ACTIONS and step not in _catalogue() and report in _catalogue()
    monkeypatch.setenv("ELI_MCP_ALLOW_CONTROL", "0")
    assert report in mcp._exposed_actions() and step not in mcp._exposed_actions()
    assert mcp._describe(report)["description"] == "A report made for this test"
    assert EX.execute(report, {})["content"] == "done"


def test_an_action_is_described_in_one_place():
    from eli.execution.effectors import registry
    registry.declare("A_TEST_ONLY_TWICE", shown="quick")
    try:
        registry.declare("A_TEST_ONLY_TWICE", shown="quick")            # the same again is no second say
        with pytest.raises(ValueError):
            registry.declare("A_TEST_ONLY_TWICE", shown="always")
        with pytest.raises(TypeError):
            registry.declare("A_TEST_ONLY_TWICE", verbatim=True)
    finally:
        registry._DECLARED.pop("A_TEST_ONLY_TWICE", None)


def test_the_executor_lists_only_the_actions_no_effector_registers():
    """SUPPORTED_ACTIONS listed every registered action by hand as well: a second place to add one."""
    assert not set(EX.SUPPORTED_ACTIONS.own()) & set(REG)
    assert set(EX.SUPPORTED_ACTIONS) == set(EX.SUPPORTED_ACTIONS.own()) | set(REG)


def test_the_manifest_scan_finds_an_action_registered_in_any_form(tmp_path):
    """The manifest read registrations from the effector files with two patterns, and 14 registered
    actions (a loop over a tuple, a lambda, a helper) read as neither supported nor dispatched."""
    from eli.runtime.capability_sync import CapabilitySync
    found = CapabilitySync().discover()
    assert all(found[a]["in_supported_list"] and found[a]["in_dispatch"] for a in REG)
    assert {a for a, v in found.items() if v.get("in_supported_list")} == set(EX.SUPPORTED_ACTIONS)
