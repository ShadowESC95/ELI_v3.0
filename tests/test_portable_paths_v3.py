from pathlib import Path

from eli.core.portable_paths import make_portable_path_value, project_root, resolve_path_value


def test_strip_v2_absolute_path():
    root = Path("/home/user/Desktop/Eli_v3")
    raw = "/home/user/Desktop/ELI_MKXI-main_MAY_NEWEST/models/foo.gguf"
    assert make_portable_path_value(raw, root) == "models/foo.gguf"


def test_strip_v3_absolute_path():
    root = Path("/home/user/Desktop/Eli_v3")
    raw = "/home/user/Desktop/Eli_v3/artifacts/db/user.sqlite3"
    assert make_portable_path_value(raw, root) == "artifacts/db/user.sqlite3"


def test_resolve_relative_against_detected_root():
    root = project_root()
    rel = "artifacts/db/user.sqlite3"
    resolved = resolve_path_value(rel, root)
    assert resolved.endswith("artifacts/db/user.sqlite3")
    assert Path(resolved).is_absolute()
