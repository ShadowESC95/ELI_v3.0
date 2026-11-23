"""CLAIM: every action documented in blueprints/capabilities_and_actions.md (the
curated reference) is a real, routable/plugin-backed manifest capability.

Examines the public capability reference against the live manifest.
"""
from __future__ import annotations

import pytest

from . import _helpers as H

from eli.tools.registry.capabilities_doc import _C as CURATED  # action -> (cat, desc, [phrases])

_ACTIONS = sorted(CURATED.keys())
_CAP_BY_ACTION = {c["action"]: c for c in H.capabilities()}


@pytest.mark.parametrize("action", _ACTIONS, ids=_ACTIONS)
def test_documented_action_in_manifest(action):
    assert action in _CAP_BY_ACTION, f"{action} is documented but not in the manifest"


@pytest.mark.parametrize("action", _ACTIONS, ids=_ACTIONS)
def test_documented_action_is_reachable(action):
    cap = _CAP_BY_ACTION[action]
    assert cap.get("routable") or cap.get("in_supported_list") or cap.get("plugin"), (
        f"{action} is documented but neither routable, supported, nor plugin-backed")


# ── regenerating the table ──────────────────────────────────────────────────────
def test_a_test_never_rewrites_the_checkouts_blueprint():
    """Every engine start regenerates the table; a test that built an engine rewrote
    blueprints/capabilities_and_actions.md in the checkout."""
    from pathlib import Path
    from eli.tools.registry import capabilities_doc as cd

    before = cd.OUT.stat().st_mtime_ns if cd.OUT.exists() else None
    written = Path(cd.generate_capabilities_doc(H.manifest_path())["path"])
    assert written != cd.OUT and written.is_file()
    assert (cd.OUT.stat().st_mtime_ns if cd.OUT.exists() else None) == before


def test_a_new_date_over_an_unchanged_table_is_not_written(tmp_path, monkeypatch):
    import re
    from eli.tools.registry import capabilities_doc as cd

    target = tmp_path / "capabilities_and_actions.md"
    monkeypatch.setenv("ELI_CAPABILITIES_DOC", str(target))
    cd.generate_capabilities_doc(H.manifest_path())
    dated_earlier = re.sub(r"Auto-generated \d{4}-\d{2}-\d{2}", "Auto-generated 2000-01-01",
                           target.read_text(encoding="utf-8"))
    target.write_text(dated_earlier, encoding="utf-8")
    cd.generate_capabilities_doc(H.manifest_path())
    assert target.read_text(encoding="utf-8") == dated_earlier, "rewritten for its date alone"
    target.write_text(dated_earlier.replace("| `", "| `CHANGED_", 1), encoding="utf-8")
    cd.generate_capabilities_doc(H.manifest_path())
    assert "CHANGED_" not in target.read_text(encoding="utf-8"), "a real change was not written"


def test_an_engine_start_never_writes_the_blueprint_even_with_no_sandbox_named(monkeypatch):
    """Every engine start rewrote blueprints/capabilities_and_actions.md, so a test or an eval run from a
    checkout without ELI_CAPABILITIES_DOC did too. The start writes this install's own copy (its data
    folder); the blueprint is written when asked for by name."""
    from pathlib import Path
    from eli.core.paths import data_dir
    from eli.tools.registry import capabilities_doc as cd

    monkeypatch.delenv("ELI_CAPABILITIES_DOC", raising=False)
    before = cd.OUT.stat().st_mtime_ns if cd.OUT.exists() else None
    written = Path(cd.generate_capabilities_doc(H.manifest_path())["path"])
    assert data_dir() in written.parents and written.is_file()
    assert (cd.OUT.stat().st_mtime_ns if cd.OUT.exists() else None) == before
