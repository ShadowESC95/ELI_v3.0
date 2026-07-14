"""Shared loaders for the claims-verification suite.

The claims suite examines the PROJECT vs its CLAIMS — the capability manifest, the
executor's action surface, the blueprint documents, and the module tree — and
asserts they actually line up. Everything is derived from real artifacts so the
test count scales with the project and the assertions are grounded, not invented.
"""
from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


@lru_cache(maxsize=1)
def manifest() -> dict:
    return json.loads((REPO / "capability_manifest.json").read_text(encoding="utf-8"))


def capabilities() -> list:
    return list(manifest().get("capabilities", []))


@lru_cache(maxsize=1)
def supported_actions() -> tuple:
    from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
    return tuple(sorted(SUPPORTED_ACTIONS))


@lru_cache(maxsize=1)
def _effector_registered_actions() -> frozenset:
    import importlib
    import pkgutil
    try:
        from eli.execution import effectors
        from eli.execution.effectors import registry
    except Exception:
        return frozenset()
    for _finder, _name, _ispkg in pkgutil.iter_modules(effectors.__path__):
        if _name in ("registry", "_base"):
            continue
        try:
            importlib.import_module(f"eli.execution.effectors.{_name}")
        except Exception:
            continue
    return frozenset(registry.registered_actions())


def executor_action_branches() -> frozenset:
    from eli.tools.registry.capability_updater import extract_executor_actions
    if_chain = extract_executor_actions(REPO / "eli" / "execution" / "executor_enhanced.py")
    return frozenset(if_chain) | _effector_registered_actions()


@lru_cache(maxsize=1)
def eli_py_files() -> tuple:
    return tuple(sorted(
        p for p in (REPO / "eli").rglob("*.py")
        if "__pycache__" not in p.parts
    ))


def rel(p: Path) -> str:
    return str(p.relative_to(REPO))


_EXAMPLE_DOCS = {"NEXT_SESSION.md", "EXTENDING_ELI.md"}


@lru_cache(maxsize=1)
def blueprint_files() -> tuple:
    # These docs deliberately reference files not in THIS tree, so they are
    # excluded from claims that verify blueprints against the current code:
    # NEXT_SESSION.md is the forward-work handoff (future modules), and
    # EXTENDING_ELI.md is a how-to guide with illustrative example paths
    # (e.g. effectors/display_control.py) a user would create themselves.
    return tuple(sorted(p for p in (REPO / "blueprints").glob("*.md")
                        if p.name not in _EXAMPLE_DOCS))


_FILE_RE = re.compile(r"`([a-zA-Z0-9_][a-zA-Z0-9_./\-]*\.py)`")
_MOD_RE = re.compile(r"`(eli\.[a-z0-9_]+(?:\.[a-z0-9_]+)+)`")
_TOP_DIRS = ("eli/", "tools/", "tests/", "blueprints/", "scripts/", "config/")


@lru_cache(maxsize=1)
def blueprint_file_refs() -> tuple:
    """Path-like `*.py` references in the blueprints (must contain a '/' and start
    under a known top dir — bare filenames are too ambiguous to verify)."""
    refs = set()
    for f in blueprint_files():
        for m in _FILE_RE.findall(f.read_text(encoding="utf-8", errors="ignore")):
            if "/" in m and m.startswith(_TOP_DIRS):
                refs.add(m)
    return tuple(sorted(refs))


@lru_cache(maxsize=1)
def blueprint_module_refs() -> tuple:
    """Dotted `eli.x.y` module references in the blueprints."""
    refs = set()
    for f in blueprint_files():
        for m in _MOD_RE.findall(f.read_text(encoding="utf-8", errors="ignore")):
            refs.add(m)
    return tuple(sorted(refs))
