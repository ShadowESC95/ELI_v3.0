"""memory_reset — the factory-reset / fresh-slate file+DB operations. Real sqlite &
json, all isolated to tmp. These are load-bearing for privacy (wiping personal data).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from eli.core import memory_reset as mr


def test_counts():
    t = {"db": [Path("a"), Path("b")], "json": [Path("c")]}
    assert mr.counts(t) == {"db": 2, "json": 1}


def test_clear_db_wipes_rows_keeps_schema(tmp_path):
    p = tmp_path / "m.sqlite3"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY, text TEXT)")
    c.executemany("INSERT INTO facts(text) VALUES(?)", [("a",), ("b",), ("c",)])
    c.commit(); c.close()
    assert mr.clear_db(p) == 3                       # 3 rows cleared
    c = sqlite3.connect(str(p))
    assert c.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0     # rows gone
    assert c.execute("SELECT name FROM sqlite_master WHERE name='facts'").fetchone()  # schema kept
    c.close()


def test_clear_db_keep_tables(tmp_path):
    p = tmp_path / "m.sqlite3"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY, t TEXT)")
    c.execute("CREATE TABLE habits(id INTEGER PRIMARY KEY, t TEXT)")
    c.execute("INSERT INTO facts(t) VALUES('x')")
    c.execute("INSERT INTO habits(t) VALUES('keep')")
    c.commit(); c.close()
    mr.clear_db(p, keep_tables={"habits"})
    c = sqlite3.connect(str(p))
    assert c.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0     # wiped
    assert c.execute("SELECT COUNT(*) FROM habits").fetchone()[0] == 1    # kept
    c.close()


def test_scrub_json_names(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"user_name": "Alex", "nested": {"name": "Bob", "keep": "ok"}}),
                 encoding="utf-8")
    assert mr.scrub_json_names(p) == 2               # user_name + nested.name
    d = json.loads(p.read_text())
    assert d["user_name"] == "" and d["nested"]["name"] == "" and d["nested"]["keep"] == "ok"


def test_clear_settings_name(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"user_name": "Alex", "theme": "dark"}), encoding="utf-8")
    assert mr.clear_settings_name(p) is True
    assert json.loads(p.read_text())["user_name"] == ""
    assert mr.clear_settings_name(p) is False


def test_clear_settings_identity_clears_device_labels(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({
        "user_name": "Alex",
        "device_custom_names": {"bt:AA:BB:CC:DD:EE:FF": "Kitchen"},
        "audio_output_aliases": {"sink1": "Office"},
        "bluetooth_display_name": "Pocket-Phone",
    }), encoding="utf-8")
    assert mr.clear_settings_identity(p) is True
    d = json.loads(p.read_text())
    assert d["user_name"] == "" and d["device_custom_names"] == {}
    assert d["audio_output_aliases"] == {} and d["bluetooth_display_name"] == ""


def test_reset_persona_overlay_writes_template(tmp_path, monkeypatch):
    tpl = tmp_path / "persona.auto.template.txt"
    tpl.write_text("# blank overlay\n", encoding="utf-8")
    dest = tmp_path / "persona.auto.txt"
    monkeypatch.setattr(mr, "_BLANK_PERSONA_TEMPLATE", tpl)

    def _write(text):
        dest.write_text(text, encoding="utf-8")
        return text

    monkeypatch.setattr("eli.cognition.persona.write_auto_persona", _write)
    assert mr.reset_persona_overlay(tpl) is True
    assert "blank overlay" in dest.read_text(encoding="utf-8")        # nothing left to clear


# ── the backup is whole, and nothing is wiped without it ─────────────────────

def _wal_db_with_an_open_writer(path):
    """A database in WAL mode whose last commit is still in the -wal file, its writer still open,
    as ELI's memory holds it while the GUI runs the reset."""
    w = sqlite3.connect(str(path))
    w.execute("PRAGMA journal_mode=WAL")
    w.execute("PRAGMA wal_autocheckpoint=0")
    w.execute("CREATE TABLE memories(id INTEGER PRIMARY KEY, text TEXT)")
    w.execute("INSERT INTO memories(text) VALUES ('the plant needs water on mondays')")
    w.commit()
    assert Path(str(path) + "-wal").stat().st_size > 0
    return w


def test_the_backup_holds_the_commits_still_in_the_wal(tmp_path):
    base = tmp_path / "data"
    (base / "db").mkdir(parents=True)
    db = base / "db" / "user.sqlite3"
    writer = _wal_db_with_an_open_writer(db)
    try:
        bk = mr.backup({"dbs": [db]}, base)
        c = sqlite3.connect(str(bk / "db" / "user.sqlite3"))
        try:
            assert c.execute("SELECT text FROM memories").fetchall() == [("the plant needs water on mondays",)]
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            c.close()
    finally:
        writer.close()


def _sandbox(tmp_path, monkeypatch):
    base, config = tmp_path / "data", tmp_path / "config"
    (base / "db").mkdir(parents=True)
    config.mkdir()
    monkeypatch.setattr(mr, "bases", lambda: (base, config))
    monkeypatch.setattr(mr, "reset_persona_overlay", lambda template=None: True)
    return base, config


def test_a_failed_backup_wipes_nothing(tmp_path, monkeypatch):
    """A failed copy was logged at debug and the wipe went ahead, the summary pointing at the
    incomplete backup and the dialog saying everything was backed up first."""
    base, _config = _sandbox(tmp_path, monkeypatch)
    db = base / "db" / "user.sqlite3"
    writer = _wal_db_with_an_open_writer(db)
    writer.close()
    note = base / "notes" / "note.md"
    note.parent.mkdir()
    note.write_text("the plant needs water on mondays", encoding="utf-8")

    base.chmod(0o500)                # the backup folder cannot be made: a real failure, not a stub
    try:
        s = mr.run_reset(rebuild=False)
    finally:
        base.chmod(0o700)
    c = sqlite3.connect(str(db))
    try:
        assert c.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1, "wiped without a backup"
    finally:
        c.close()
    assert note.exists()
    assert s["ok"] is False and s["wiped"] is False and any(e.startswith("backup") for e in s["errors"])


def test_a_reset_leaves_none_of_the_user_s_words_and_keeps_the_installation(tmp_path, monkeypatch):
    """discover() listed the stores to wipe and missed the incidents, auto goals, the attention
    queue, notes, documents, the proactive queue, the world journal, logs and a backup copy of the
    user database. Now everything goes except the installation and the machine's measurements."""
    base, config = _sandbox(tmp_path, monkeypatch)
    marker = "QUOKKA-5521"
    db = base / "db" / "user.sqlite3"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE memories(id INTEGER PRIMARY KEY, text TEXT)")
    c.execute("INSERT INTO memories(text) VALUES (?)", (f"play {marker} on repeat",))
    c.commit()
    c.close()
    personal = ["incidents/20261108.jsonl", "runtime/goals.json", "runtime/attention_queue.jsonl",
                "notes/shopping.md", "documents/report.txt", "proactive/proposal_queue.jsonl",
                "world/journal/eli_world_journal.md", "world/events.jsonl", "logs/eli.log",
                "conversations/conversation_1.json", "conversations/archive/20261027.jsonl",
                "vectors/meta.json", "db/user.sqlite3.pre_policy.bak", "runtime/users/u1/user_profile.json",
                "state.json", "runtime/world_model.json", "image_engine/outputs/job.json", "training/candidates.jsonl"]
    for rel in personal:
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(f'{{"said": "{marker}"}}', encoding="utf-8")
    kept = ["models/gguf/base/model.gguf", "voices/voice.onnx", "plugins/p/plugin.py", "bundles/pack.json",
            "runtime/gpu_headroom.json", "runtime_hardware_profile.json", ".eli_trial_state.json"]
    for rel in kept:
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text("machine", encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({"user_name": "Alex", "theme": "dark"}), encoding="utf-8")

    s = mr.run_reset(rebuild=False)
    assert s["ok"] and s["wiped"], s["errors"]
    bk = Path(s["backup"])
    survivors = [p for p in base.rglob("*") if p.is_file() and bk not in p.parents
                 and marker.encode() in p.read_bytes()]
    assert survivors == [], f"the reset left the user's words in {survivors}"
    for rel in kept:
        assert (base / rel).read_text(encoding="utf-8") == "machine", rel
    c = sqlite3.connect(str(db))
    try:
        assert c.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0          # schema kept, rows gone
    finally:
        c.close()
    assert json.loads((config / "settings.json").read_text())["user_name"] == ""
    for rel in personal:                                   # moved into the backup, not lost
        assert marker in (bk / rel).read_text(encoding="utf-8"), rel
    c = sqlite3.connect(str(bk / "db" / "user.sqlite3"))
    try:
        assert c.execute("SELECT text FROM memories").fetchall() == [(f"play {marker} on repeat",)]
    finally:
        c.close()


def test_a_reset_that_wiped_nothing_is_shown_as_a_failure(tmp_path, monkeypatch):
    """A backup that failed left everything in place, and the dialog still said "Memory cleared"."""
    pytest = __import__("pytest")
    pytest.importorskip("PySide6")
    from eli.gui.main_window._mixins.features import reset_outcome
    base, _config = _sandbox(tmp_path, monkeypatch)
    _wal_db_with_an_open_writer(base / "db" / "user.sqlite3").close()     # something the backup must copy
    base.chmod(0o500)                # the backup folder cannot be made: nothing is wiped
    try:
        s = mr.run_reset(rebuild=False)
    finally:
        base.chmod(0o700)
    wiped, title, text = reset_outcome(s, name_kept=False)
    assert wiped is False and title == "Memory not cleared"
    assert text.startswith("Nothing was wiped: backup") and s["backup_incomplete"] in text
    wiped, title, text = reset_outcome({"wiped": True, "ok": True, "db_rows": 4, "files_removed": 3}, name_kept=True)
    assert wiped and title == "Memory cleared" and "3 other file(s)" in text and "Name kept" in text


def _clear_memory_tool():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "tools" / "clear_memory.py"
    spec = importlib.util.spec_from_file_location("eli_clear_memory_tool", path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def test_the_reset_tool_lists_the_other_files_and_says_when_nothing_was_wiped(tmp_path, monkeypatch, capsys):
    """tools/clear_memory.py left out the notes, documents and logs it removes, and after a failed
    backup printed "✓ wiped 0 DB rows" as if it had worked."""
    base, _config = _sandbox(tmp_path, monkeypatch)
    _wal_db_with_an_open_writer(base / "db" / "user.sqlite3").close()     # something the backup must copy
    (base / "notes").mkdir()
    (base / "notes" / "shopping.md").write_text("bread", encoding="utf-8")
    tool = _clear_memory_tool()
    assert tool.main(["--dry-run"]) == 0
    plan = capsys.readouterr().out
    assert "1  other files" in plan
    base.chmod(0o500)                # the backup folder cannot be made: nothing is wiped
    try:
        rc = tool.main(["--yes"])
    finally:
        base.chmod(0o700)
    out = capsys.readouterr().out
    assert rc == 2 and "nothing was wiped: backup" in out and "✓ wiped" not in out
    assert (base / "notes" / "shopping.md").exists()


def test_paired_devices_and_the_gaze_calibration_survive_a_reset(tmp_path, monkeypatch):
    """Clear Memory unpaired every device, dropped their automations and the home mesh, and lost the
    gaze calibration: those are this machine's set-up, not memory."""
    base, _config = _sandbox(tmp_path, monkeypatch)
    kept = ["devices/registry.json", "devices/mesh.json", "devices/creds/a-lamp.json", "eli_ar_avatar/state.json"]
    for rel in kept:
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text("set up here", encoding="utf-8")
    (base / "notes").mkdir()
    (base / "notes" / "n.md").write_text("a note", encoding="utf-8")
    s = mr.run_reset(rebuild=False)
    assert s["wiped"], s["errors"]
    for rel in kept:
        assert (base / rel).read_text(encoding="utf-8") == "set up here", rel
    assert not (base / "notes" / "n.md").exists()
