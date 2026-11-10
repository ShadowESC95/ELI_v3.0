"""A captured preferred name must not depend on persona.auto.txt's "User Preferences"
section — persona_updater.update_auto_sections() rebuilds that file from a fixed section
set that never includes user preferences, silently erasing anything written there."""
import sqlite3

import pytest

from eli.runtime.profile_extractor import _insert_user_pattern, ensure_profile_tables


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "user.sqlite3"
    ensure_profile_tables(path)
    con = sqlite3.connect(str(path))
    yield con
    con.close()


def test_preferred_name_is_stored_as_a_single_valued_pattern(db):
    cur = db.cursor()
    assert _insert_user_pattern(cur, "identity.preferred_name", "User prefers to be called Alex.",
                                provenance="user_explicit")
    db.commit()
    rows = db.execute(
        "SELECT pattern_data FROM user_patterns WHERE lower(pattern_type)=lower(?)",
        ("identity.preferred_name",),
    ).fetchall()
    assert rows and "Alex" in rows[0][0]


def test_a_persona_auto_txt_rebuild_does_not_touch_it(db, tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_PERSONA_AUTO_PATH", str(tmp_path / "persona.auto.txt"))
    cur = db.cursor()
    _insert_user_pattern(cur, "identity.preferred_name", "User prefers to be called Alex.",
                         provenance="user_explicit")
    db.commit()

    # Simulate persona_updater's real section set — it never includes "User Preferences"
    # (persona_updater.py:418-436) — this is the exact rebuild that used to wipe the name.
    from eli.cognition.persona import update_auto_sections
    update_auto_sections({"Runtime Persona Notes": "some other content"})

    rows = db.execute(
        "SELECT pattern_data FROM user_patterns WHERE lower(pattern_type)=lower(?)",
        ("identity.preferred_name",),
    ).fetchall()
    assert rows and "Alex" in rows[0][0]


def test_correcting_the_name_supersedes_not_accumulates(db):
    cur = db.cursor()
    _insert_user_pattern(cur, "identity.preferred_name", "User prefers to be called Alex.",
                         provenance="user_explicit")
    db.commit()
    _insert_user_pattern(cur, "identity.preferred_name", "User prefers to be called James.",
                         provenance="user_explicit")
    db.commit()
    rows = db.execute(
        "SELECT pattern_data FROM user_patterns WHERE lower(pattern_type)=lower(?)",
        ("identity.preferred_name",),
    ).fetchall()
    assert len(rows) == 1
    assert "James" in rows[0][0]


# ── what the persona overlay carries, and how often it is rewritten ──────────

def test_the_overlay_never_carries_the_owners_complaints_word_for_word():
    """'Investigate failure: <the owner's sentence> -> anomaly (rejected): ...' rows and the raw
    failure errors were rendered into the persona loaded into every prompt."""
    from eli.cognition import persona_updater as pu
    said = "why do you keep opening the wrong tide chart"
    improvements = [{"description": f"Investigate failure: {said} -> anomaly (rejected): misroute", "category": "runtime"},
                    {"description": "Anomaly read on a reply", "category": "anomaly"},
                    {"description": "Cache the station list instead of fetching it each time", "category": "runtime"}]
    failures = [{"user_input": said, "error": f"anomaly (rejected): {said}", "command": "", "occurrence_count": 1},
                {"user_input": said, "error": "no player for that site", "command": "MEDIA_CONTROL pause",
                 "occurrence_count": 3}]
    shown = "\n".join(pu._improvement_lines(improvements) + pu._failure_lines(failures))
    assert said not in shown and "anomaly" not in shown.lower()
    assert "Cache the station list" in shown and "MEDIA_CONTROL failed 3x" in shown


def test_the_profile_is_written_only_when_something_in_it_changed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from eli.cognition import persona_updater as pu
    from eli.kernel import state
    db = tmp_path / "user.sqlite3"
    ensure_profile_tables(db)
    con = sqlite3.connect(str(db))
    _insert_user_pattern(con.cursor(), "preference.detail", "User prefers in-depth answers.")
    _insert_user_pattern(con.cursor(), "identity.fact", "Their cat is called Biscuit.")
    con.commit()
    con.close()
    before = state.get_user_name()
    state.set_user_name("Robin")
    writes = []
    real = state.update_user_profile
    monkeypatch.setattr(state, "update_user_profile", lambda u=None, **k: writes.append(dict(u or {})) or real(u, **k))
    try:
        mem = SimpleNamespace(db_path=str(db))
        assert pu.update_user_profile_overlay(mem)["updated"] == ["preferences"]
        assert pu.update_user_profile_overlay(mem)["updated"] == [], "the same data is not written again"
        assert len(writes) == 1 and "name" not in writes[0]
        assert state.get_user_name() == "Robin", "a name is never taken from an identity line"
    finally:
        state.set_user_name(before)


def test_a_chosen_style_is_kept_in_the_profile_alone_and_writes_no_other_setting(monkeypatch):
    """The whole load_settings() view went back to save_settings: every default and this session's
    environment overrides were written into the file as if chosen. And the tone, the user's own, was
    copied into settings.json, which every caller shares, where the window read it: it is kept in the
    user's profile alone, and a copy the file held is cleared, that key and nothing else."""
    from eli.kernel import state
    import eli.core.runtime_settings as rs
    saved, profiles = [], []
    on_disk = {"theme": "a-default", "n_ctx": 12288, "communication_style": "an older tone"}
    monkeypatch.setattr(state, "load_user_profile", lambda user_id=None: {})
    monkeypatch.setattr(state, "save_user_profile", lambda profile, user_id=None: profiles.append(dict(profile)) or profile)
    monkeypatch.setattr(state, "_is_active_user", lambda user_id=None: True)
    monkeypatch.setattr(rs, "load_settings", lambda *a, **k: dict(on_disk))
    monkeypatch.setattr(rs, "save_settings", lambda delta: saved.append(dict(delta)) or on_disk.update(delta))
    monkeypatch.setattr("eli.cognition.persona_updater.update_persona_overlay", lambda *a, **k: None)
    assert state.set_communication_style("short and plain") == "short and plain"
    assert profiles == [{"communication_style": "short and plain"}]
    assert saved == [{"communication_style": ""}]
    state.set_communication_style("warmer")
    assert saved == [{"communication_style": ""}], "with no copy there, nothing is written"


def test_a_name_is_kept_in_the_profile_alone_and_never_written_into_settings_by_hand(monkeypatch, tmp_path):
    """set_user_name copied the active user's name into settings.json, writing the file by hand past
    the settings lock, and the window read that copy before the profile. The name is the profile's
    alone; a copy left in settings.json is cleared through the settings' own writer."""
    import json
    from eli.kernel import state
    import eli.core.runtime_settings as rs
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"theme": "dark", "user_name": "Robin"}), encoding="utf-8")
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(settings))
    profile = {}
    monkeypatch.setattr(state, "load_user_profile", lambda user_id=None: dict(profile))
    monkeypatch.setattr(state, "save_user_profile", lambda p, user_id=None: profile.update(p) or p)
    monkeypatch.setattr(state, "_is_active_user", lambda user_id=None: True)
    monkeypatch.setattr(state, "sync_identity_to_world_model", lambda **k: None)
    through = []
    real_save = rs.save_settings
    monkeypatch.setattr(rs, "save_settings", lambda delta: through.append(dict(delta)) or real_save(delta))
    assert state.set_user_name("Sky") == "Sky" and profile["name"] == "Sky"
    on_disk = json.loads(settings.read_text(encoding="utf-8"))
    assert on_disk.get("user_name") == "" and on_disk["theme"] == "dark" and through == [{"user_name": ""}]
