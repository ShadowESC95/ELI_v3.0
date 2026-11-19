"""
test_01_core.py
===============
Tests for eli.core — config, paths, hardware_profile, runtime_settings.
"""
import importlib


# ── Module attribute presence ──────────────────────────────────────────────────

def test_core_config_has_config_class():
    mod = importlib.import_module("eli.core.config")
    attrs = dir(mod)
    hits = [a for a in attrs if "config" in a.lower() or "Config" in a]
    assert hits, f"eli.core.config exposes no Config-like symbol. Got: {attrs}"


def test_core_paths_exports_paths():
    mod = importlib.import_module("eli.core.paths")
    attrs = dir(mod)
    path_attrs = [a for a in attrs if "path" in a.lower() or "dir" in a.lower() or "PATH" in a]
    assert path_attrs, f"eli.core.paths exposes no path symbols. Got: {attrs}"


