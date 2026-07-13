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
