"""CLAIM: every code path/module the blueprints reference actually exists.

Parses path-like `*.py` and dotted `eli.x.y` references out of local blueprint
markdown (not shipped in git — PDFs only). Skips when no local *.md present.
"""
from __future__ import annotations

import importlib.util

import pytest

from . import _helpers as H

pytestmark = pytest.mark.skipif(
    not H.blueprint_files(),
    reason="blueprints/*.md are local-only (git ships PDFs); run capabilities_doc locally",
)

_FILE_REFS = H.blueprint_file_refs()
_MOD_REFS = H.blueprint_module_refs()


@pytest.mark.parametrize("ref", _FILE_REFS, ids=list(_FILE_REFS))
def test_blueprint_file_reference_exists(ref):
    assert (H.REPO / ref).exists(), f"blueprints reference a non-existent file: {ref}"


def _resolves(dotted: str) -> bool:
    try:
        if importlib.util.find_spec(dotted) is not None:
            return True
    except (ImportError, AttributeError, ValueError):
        pass
    return False


@pytest.mark.parametrize("ref", _MOD_REFS, ids=list(_MOD_REFS))
def test_blueprint_module_reference_importable(ref):
    # Either the ref is a module, or it's `module.symbol` whose module resolves.
    ok = _resolves(ref) or ("." in ref and _resolves(ref.rsplit(".", 1)[0]))
    assert ok, f"blueprints reference an unresolvable module/symbol: {ref}"


def test_a_per_user_folder_the_blueprints_name_is_this_versions_own():
    """The uninstall guide told a v3 user to delete ~/.local/share/ELI_v2, another product's
    memories, and left v3's own data in place."""
    import re
    from eli.core import paths

    shapes = re.compile(r"(?:\.local/share/|LOCALAPPDATA%?\\|Application Support/)(ELI_v\d+)")
    wrong = [f"{p.name}: {m.group(0)}" for p in H.blueprint_files()
             for m in shapes.finditer(p.read_text(encoding="utf-8")) if m.group(1) != paths._APP_NAME]
    assert not wrong, wrong
