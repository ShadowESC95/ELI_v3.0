"""The effector registry owns every action it registers.

v3 moved action handlers out of executor_enhanced._execute_impl into eli/execution/effectors/,
then v2 ports copied inline branches back over them. Because the registry was consulted last, the
inline copies won and the effector modules (with their v3-only fixes) became unreachable: 158 of
186 registered actions were shadowed, including self-improve's background propose mode.

These tests keep the two from overlapping again. A port that brings back an inline branch for a
registered action fails here; move its changes into the effector module instead.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

EXECUTOR = Path(__file__).resolve().parents[1] / "eli" / "execution" / "executor_enhanced.py"


def _execute_impl():
    tree = ast.parse(EXECUTOR.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_execute_impl")


def _dispatched(test):
    """Action names an `if` compares the action variable against."""
    names, on_action = set(), False
    for node in ast.walk(test):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id in ("a", "action", "_a"):
            on_action = True
            for comp in node.comparators:
                for k in ast.walk(comp):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]+", k.value):
                        names.add(k.value)
    return names if on_action else set()


def _inline_actions():
    found = {}
    for node in ast.walk(_execute_impl()):
        if isinstance(node, ast.If):
            for name in _dispatched(node.test):
                found.setdefault(name, node.lineno)
    return found


def test_no_action_is_handled_both_inline_and_by_an_effector():
    from eli.execution.effectors import load_all
    from eli.execution.effectors.registry import registered_actions

    load_all()
    both = sorted(set(_inline_actions()) & registered_actions())
    assert not both, (
        f"{len(both)} registered action(s) also have an inline branch in _execute_impl: {both}. "
        "Move the change into the action's effector module and delete the inline branch."
    )


def test_registry_is_consulted_before_any_inline_handler():
    fn = _execute_impl()
    lookup = next(
        (n.lineno for n in ast.walk(fn)
         if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_effector_get"),
        None,
    )
    assert lookup is not None, "_execute_impl no longer consults the effector registry"
    returning = [
        n.lineno for n in fn.body
        if isinstance(n, ast.If) and _dispatched(n.test)
        and any(isinstance(s, ast.Return) for s in ast.walk(n))
    ]
    assert returning and lookup < min(returning), (
        "the effector lookup must come before the first inline action branch, or an inline copy "
        "shadows the effector again"
    )
