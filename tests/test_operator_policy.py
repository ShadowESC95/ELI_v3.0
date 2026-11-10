"""Operator policy — the autonomy-level governance gate.

Controls how much autonomy ELI has (proposal_only / operator_supervised /
goal_driven / observe_only). set_policy_mode must reject unknown modes (fail-safe)
and persist valid ones; load_policy always returns a well-formed record. This is an
audit-relevant control surface. File-backed; each test keeps it in a data folder of its own.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eli.execution import operator_policy as op


@pytest.fixture(autouse=True)
def _own_data_folder(tmp_path, monkeypatch):
    from eli.core import paths
    monkeypatch.setenv("ELI_DATA_DIR", str(tmp_path / "data"))
    paths.data_dir.cache_clear()
    yield
    paths.data_dir.cache_clear()


def test_the_level_is_kept_in_elis_data_folder_never_the_installs_tree(tmp_path):
    """The level was kept under the install's tree (project root/artifacts/runtime) wherever the data
    folder was: a run with its own data folder wrote it into the checkout, and the suite left an
    empty artifacts/runtime there each time."""
    from eli.core import paths
    rec = op.set_policy_mode("observe_only")
    kept = Path(rec["path"])
    assert kept == tmp_path / "data" / "runtime" / "operator_policy.json" and kept.is_file()
    assert paths.project_root().resolve() not in kept.resolve().parents
    assert op.load_policy()["mode"] == "observe_only" and op.load_policy()["path"] == str(kept)


def test_valid_modes_are_defined():
    assert {"proposal_only", "operator_supervised", "goal_driven", "observe_only"} <= op.VALID_POLICY_MODES


def test_load_policy_is_well_formed():
    pol = op.load_policy()
    assert isinstance(pol, dict)
    assert pol.get("mode") in op.VALID_POLICY_MODES
    assert "path" in pol


def test_set_valid_mode_persists():
    rec = op.set_policy_mode("observe_only", actor="tester", reason="unit test")
    assert rec["ok"] is True and rec["mode"] == "observe_only"
    assert rec["actor"] == "tester" and rec["reason"] == "unit test"
    # Round-trips through the store.
    assert op.load_policy()["mode"] == "observe_only"


def test_set_invalid_mode_rejected_and_no_write():
    op.set_policy_mode("goal_driven")           # a known-good baseline
    bad = op.set_policy_mode("full_send_yolo")   # not a valid mode
    assert bad["ok"] is False and "invalid mode" in bad["error"]
    assert "valid" in bad
    # The rejected mode must NOT have overwritten the baseline.
    assert op.load_policy()["mode"] == "goal_driven"


def test_mode_switch_round_trip():
    for mode in ("proposal_only", "operator_supervised", "goal_driven", "observe_only"):
        op.set_policy_mode(mode)
        assert op.load_policy()["mode"] == mode
