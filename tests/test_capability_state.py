"""Phase 3 of the identity/provenance plan (2026-10-02): capability_manifest.json
was fully wiped and rebuilt from a fresh AST scan on every regen (capability_
updater.py) — any field not re-derivable from that scan + evidence_ledger was
lost every time, which is why `active` had always been a hardcoded True with
no real detection: there was nowhere to persist a real answer between regens.

eli.runtime.capability_state is that place — one row per action, updated
incrementally, never wiped wholesale. Mirrors test_orchestrator_audit_ledger.
py's style: every call passes an explicit db_path to a throwaway file.
"""
import os
import tempfile

import pytest

from eli.runtime import capability_state as cs


@pytest.fixture()
def db():
    return os.path.join(tempfile.mkdtemp(prefix="eli_cap_state_"), "cap.sqlite3")


def test_unknown_action_returns_none_not_a_fabricated_default(db):
    assert cs.get_state("NEVER_SEEN", db_path=db) is None


def test_touch_discovered_persists_and_is_idempotent(db):
    cs.touch_discovered("OPEN_APP", now=100.0, db_path=db)
    first = cs.get_state("OPEN_APP", db_path=db)
    assert first["discovered_at"] == 100.0

    cs.touch_discovered("OPEN_APP", now=999.0, db_path=db)
    second = cs.get_state("OPEN_APP", db_path=db)
    assert second["discovered_at"] == 100.0, "a second discovery must not overwrite the first"


def test_upsert_merges_fields_without_wiping_others(db):
    """The actual bug being fixed: capability_manifest.json's full-wipe-and-
    rebuild destroyed exactly this kind of persistent signal every regen."""
    cs.upsert_state("OPEN_APP", db_path=db, reachable=True)
    cs.upsert_state("OPEN_APP", db_path=db, device_available=True)

    state = cs.get_state("OPEN_APP", db_path=db)
    assert state["reachable"] is True
    assert state["device_available"] is True  # not wiped by the second call


def test_upsert_rejects_an_unknown_field(db):
    with pytest.raises(ValueError):
        cs.upsert_state("OPEN_APP", db_path=db, made_up_field=1)


def test_regenerating_state_does_not_reset_what_is_already_known(db):
    """A later 'regen' (touch_discovered/upsert_state again, exactly as capability_updater.py does
    on every run) must not null out what was already established about the action."""
    cs.upsert_state("OPEN_APP", db_path=db, reachable=True, dependency_ready=False, now=1000.0)
    before = cs.get_state("OPEN_APP", db_path=db)

    # Simulate a later capability_updater.py run touching this same action.
    cs.touch_discovered("OPEN_APP", db_path=db)
    cs.upsert_state("OPEN_APP", db_path=db, permission_state="not_applicable")

    after = cs.get_state("OPEN_APP", db_path=db)
    assert after["reachable"] is True and before["reachable"] is True
    assert after["dependency_ready"] is False
    assert after["permission_state"] == "not_applicable"
