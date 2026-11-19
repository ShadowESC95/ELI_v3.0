"""SELF_UPDATE must refresh live overlays + manifest, not narrate a generic plan."""
from __future__ import annotations

from unittest.mock import patch

from eli.runtime import control_contracts as cc


def test_self_update_compact_answer_lists_refreshed_surfaces():
    evidence = {
        "ok": True,
        "report": {
            "ok": True,
            "paths": {"project_root": "/eli", "user_db": "/eli/user.sqlite3"},
            "changed": {
                "overlays": {"ok": True},
                "world_model_runtime": True,
                "capability_manifest": {"total": 225},
            },
            "errors": [],
        },
    }
    text = cc.compact_evidence_answer("SELF_UPDATE", evidence)
    assert "Self-update result:" in text
    assert "capability_manifest" in text
    assert "world_model_runtime_refreshed: True" in text


def test_refresh_all_overlays_updates_manifest(monkeypatch):
    from eli.runtime import self_model_refresh as smr

    monkeypatch.setattr(
        "eli.cognition.persona_updater.update_persona_overlay",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        "eli.cognition.persona_updater.update_user_profile_overlay",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        "eli.cognition.user_info_builder.maybe_refresh_user_info",
        lambda reason="manual": {"ok": True},
    )
    monkeypatch.setattr(
        "eli.tools.registry.capability_updater.update_capability_manifest",
        lambda: {"total": 225},
    )

    out = smr.refresh_all_overlays_nonfatal(reason="test")
    assert out.get("capability_manifest") == {"total": 225}
    assert out.get("ok") is True


def test_what_eli_holds_about_its_owner_is_written_whole_and_for_the_owner_only(tmp_path, monkeypatch):
    """The user-info files were written by a private copy of an atomic write, with default
    permissions: readable by every account on the machine."""
    import os
    import stat
    import pytest
    from eli.cognition import user_info_builder as builder
    meta = tmp_path / "user_info.meta.json"
    monkeypatch.setattr(builder, "_paths", lambda: {"meta": meta})
    builder._save_meta({"dirty": True, "dirty_reasons": ["a new fact"]})
    assert builder._load_meta()["dirty_reasons"] == ["a new fact"]
    assert [p.name for p in tmp_path.iterdir()] == [meta.name], "no scratch file is left beside it"
    if os.name != "posix":
        pytest.skip("permission bits are POSIX")
    assert stat.S_IMODE(meta.stat().st_mode) == 0o600
