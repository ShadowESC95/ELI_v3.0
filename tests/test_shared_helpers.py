"""Helpers that replaced copy-pasted versions in many modules."""
from pathlib import Path

from eli.core.paths import canonical_root, path_get
from eli.utils.jsonio import read_json_dict, read_jsonl_dicts


def test_canonical_root_prefers_the_real_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_PROJECT_ROOT", str(tmp_path))
    assert canonical_root(Path("/nonexistent/fallback")) == tmp_path.resolve()


def test_canonical_root_falls_back_when_resolution_fails(monkeypatch):
    import eli.core.paths as P
    monkeypatch.setattr(P, "project_root", lambda: (_ for _ in ()).throw(RuntimeError("no root")))
    assert canonical_root(Path("/fallback")) == Path("/fallback")


def test_path_get_reads_dicts_and_objects():
    class Paths:
        user_db = "u.sqlite3"
    assert path_get({"user_db": "d"}, "user_db") == "d"
    assert path_get(Paths(), "user_db") == "u.sqlite3"
    assert path_get(None, "user_db", "x") == "x" and path_get({}, "user_db", "x") == "x"


def test_read_json_dict_is_tolerant(tmp_path):
    good = tmp_path / "g.json"; good.write_text('{"a": 1}')
    assert read_json_dict(good) == {"a": 1}
    for name, body in (("list.json", "[1]"), ("bad.json", "{oops"), ("empty.json", "")):
        p = tmp_path / name; p.write_text(body)
        assert read_json_dict(p) == {}
    assert read_json_dict(tmp_path / "missing.json") == {}


def test_read_jsonl_dicts_skips_blank_and_bad_lines(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text('{"a": 1}\n\nnot json\n[1,2]\n{"b": 2}\n')
    assert read_jsonl_dicts(p) == [{"a": 1}, {"b": 2}]
    assert read_jsonl_dicts(tmp_path / "missing.jsonl") == []


def test_small_state_files_are_written_through_a_scratch_file_of_their_own(tmp_path, monkeypatch):
    """Each of these wrote through one fixed '<name>.tmp' beside the file, so two writers at once
    shared it. They go through core.secure_io now: a scratch file per write, owner-only."""
    import json
    import os
    from eli.cognition import agent_trust
    from eli.plugins import integrity
    from eli.runtime import version_record
    from eli.world import models

    trust, seen, manifest, publishers = (tmp_path / n for n in (
        "trusted_agents.json", "versions_seen.json", "manifest.json", "publishers.json"))
    monkeypatch.setenv("ELI_AGENT_TRUST_FILE", str(trust))
    monkeypatch.setattr(version_record, "_seen_path", lambda: seen)
    monkeypatch.setattr(models, "_manifest_path", lambda: manifest)
    monkeypatch.setattr(integrity, "_publishers_path", lambda: publishers)
    old_scratch = [trust.with_suffix(".json.tmp"), seen.with_suffix(".tmp"), manifest.with_suffix(".tmp"),
                   publishers.with_suffix(".json.tmp")]
    for scratch in old_scratch:
        scratch.write_text("another writer's half-written file", encoding="utf-8")

    agent_trust._save({"version": 1, "agents": {}})
    version_record.note_seen("9.8.7", now=5.0)
    models._write_manifest({"chair": {"file": "chair.glb"}})
    integrity._save_publishers({"p1": {"name": "Example"}})

    assert json.loads(trust.read_text(encoding="utf-8"))["agents"] == {}
    assert json.loads(seen.read_text(encoding="utf-8")) == {"9.8.7": 5.0}
    assert json.loads(manifest.read_text(encoding="utf-8")) == {"chair": {"file": "chair.glb"}}
    assert json.loads(publishers.read_text(encoding="utf-8"))["publishers"] == {"p1": {"name": "Example"}}
    for scratch in old_scratch:
        assert scratch.read_text(encoding="utf-8") == "another writer's half-written file", scratch.name
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        p.name for p in (trust, seen, manifest, publishers, *old_scratch)), "no scratch file is left behind"
    if os.name == "posix":
        for written in (trust, seen, manifest, publishers):
            assert written.stat().st_mode & 0o777 == 0o600, written.name
