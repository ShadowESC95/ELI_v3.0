from eli.core.paths import get_paths, project_root, artifacts_dir, config_dir

def test_project_root():
    root = project_root()
    assert (root / "eli").exists()
    assert (root / "README.md").exists()

def test_artifacts_dir(monkeypatch):
    """With nothing overriding it, a checkout keeps its state in its own artifacts/ folder."""
    from eli.core import paths
    monkeypatch.delenv("ELI_DATA_DIR", raising=False)
    paths.data_dir.cache_clear()
    try:
        art = artifacts_dir()
        assert art.parent == project_root()
        assert art.name == "artifacts"
    finally:
        paths.data_dir.cache_clear()

def test_config_dir():
    cfg = config_dir()
    assert cfg.exists() or cfg.parent.exists()

def test_get_paths():
    paths = get_paths()
    assert paths.project_root == project_root()
    assert paths.artifacts_dir == artifacts_dir()
    assert paths.user_db.suffix == ".sqlite3"
