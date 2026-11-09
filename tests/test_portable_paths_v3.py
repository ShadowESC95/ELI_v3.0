from pathlib import Path

from eli.core.portable_paths import make_portable_path_value, project_root, resolve_path_value


def test_a_path_in_another_eli_checkout_stays_where_it_is():
    """Another ELI product's model is that product's file: v3 reads it where it is. It used to
    become 'models/foo.gguf' and was then looked for in v3's own folder, where it is not."""
    root = Path("/home/user/Desktop/ELI_v3.0")
    raw = "/home/user/Desktop/ELI_MKXI-main_MAY_NEWEST/models/foo.gguf"
    assert make_portable_path_value(raw, root) == raw


def test_models_kept_in_any_folder_the_user_chose_keep_their_full_path():
    root = Path("/home/user/Desktop/ELI_v3.0")
    for raw in ("/home/user/.lmstudio/models/org/x.gguf",
                "/home/user/.ollama/models/blobs/sha256-abc",
                "/home/user/AI/models/x.gguf",
                "/home/user/eli_stuff/x.gguf",
                "/mnt/disk2/config/llm/eli/x.gguf"):
        assert make_portable_path_value(raw, root) == raw


def test_strip_v3_absolute_path():
    root = Path("/home/user/Desktop/ELI_v3.0")
    raw = "/home/user/Desktop/ELI_v3.0/artifacts/db/user.sqlite3"
    assert make_portable_path_value(raw, root) == "artifacts/db/user.sqlite3"


def test_a_sibling_folder_that_starts_with_the_root_name_is_not_inside_it():
    root = Path("/home/user/Desktop/ELI_v3.0")
    raw = "/home/user/Desktop/ELI_v3.0-old/models/x.gguf"
    assert make_portable_path_value(raw, root) == raw


def test_resolve_relative_against_detected_root():
    root = project_root()
    rel = "artifacts/db/user.sqlite3"
    resolved = resolve_path_value(rel, root)
    assert resolved.endswith("artifacts/db/user.sqlite3")
    assert Path(resolved).is_absolute()


def test_the_scrub_script_makes_only_this_installs_paths_relative(tmp_path):
    """scripts/scrub_portable_paths.py promised a v2 → v3 rewrite, and its text branch replaced any
    path with another version's or checkout's folder name in it by this install's root."""
    import importlib.util
    import json
    from pathlib import Path as _P
    script = _P(__file__).resolve().parents[1] / "scripts" / "scrub_portable_paths.py"
    spec = importlib.util.spec_from_file_location("scrub_portable_paths_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path / "an-install"
    (root / "models").mkdir(parents=True)
    other = "/home/someone/ELI_v2.0/models/b.gguf"
    text = tmp_path / "notes.log"
    text.write_text(f"loaded {root}/models/a.gguf and {other}\n", encoding="utf-8")
    assert mod.scrub_file(text, root) is True
    assert text.read_text(encoding="utf-8") == f"loaded models/a.gguf and {other}\n"
    js = tmp_path / "state.json"
    js.write_text(json.dumps({"model_path": str(root / "models" / "a.gguf"), "note": other}), encoding="utf-8")
    mod.scrub_file(js, root)
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["model_path"] == "models/a.gguf" and data["note"] == other
    assert "v2 →" not in (mod.__doc__ or "") and "ELI_MKXI" not in script.read_text(encoding="utf-8")
