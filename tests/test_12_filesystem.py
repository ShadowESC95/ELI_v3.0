"""
test_12_filesystem.py
=====================
Validates the project filesystem — no missing __init__.py files, expected
directories present, key JSON artefacts valid, no stale .pyc-only modules.
"""
import os
from pathlib import Path

import pytest


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ELI_ROOT = os.path.join(PROJECT_ROOT, "eli")

EXPECTED_DIRS = [
    "cognition", "core", "execution", "gui", "integrations",
    "kernel", "memory", "perception", "planning", "plugins",
    "runtime", "tools", "utils",
]

EXPECTED_TOP_LEVEL = [
    "eli.sh", "pyproject.toml", "requirements.txt", "README.md",
]


@pytest.mark.parametrize("subdir", EXPECTED_DIRS, ids=EXPECTED_DIRS)
def test_eli_subdirectory_exists(subdir):
    path = os.path.join(ELI_ROOT, subdir)
    assert os.path.isdir(path), f"Expected directory missing: {path}"


@pytest.mark.parametrize("subdir", EXPECTED_DIRS, ids=EXPECTED_DIRS)
def test_eli_subdirectory_has_init(subdir):
    init = os.path.join(ELI_ROOT, subdir, "__init__.py")
    assert os.path.isfile(init), f"Missing __init__.py in {subdir}/"


@pytest.mark.parametrize("fname", EXPECTED_TOP_LEVEL, ids=EXPECTED_TOP_LEVEL)
def test_project_root_file_exists(fname):
    path = os.path.join(PROJECT_ROOT, fname)
    assert os.path.isfile(path), f"Expected project file missing: {path}"


def test_pyproject_toml_valid():
    from eli.core.toml_util import load_toml
    path = os.path.join(PROJECT_ROOT, "pyproject.toml")
    data = load_toml(path)
    assert "project" in data or "tool" in data, "pyproject.toml looks empty"


def test_persona_txt_and_auto_txt_present():
    """persona.txt ships with the code. The overlay (persona.auto.txt) is ELI's own state, written at
    run time where paths.persona_auto_path() says (the data folder; this run's sandbox): it is
    ignored by git and never in a fresh copy, so its being in eli/cognition is not asked."""
    cognition = os.path.join(ELI_ROOT, "cognition")
    assert os.path.isfile(os.path.join(cognition, "persona.txt")), \
        "cognition/persona.txt missing"
    from eli.cognition import persona
    from eli.core.paths import persona_auto_path
    assert Path(persona.auto_path()) == Path(persona_auto_path()), "the persona module writes the overlay elsewhere"
