"""Portable packages must ship offline GUI bootstrap requirements."""
from __future__ import annotations

from pathlib import Path


def test_portable_bootstrap_requirements_exist():
    root = Path(__file__).resolve().parents[1]
    req = root / "requirements-portable-bootstrap.txt"
    assert req.is_file()
    text = req.read_text(encoding="utf-8").lower()
    assert "pyside6" in text


def test_eli_setup_insists_on_venv_before_gui():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "eli_setup.sh").read_text(encoding="utf-8")
    assert "_ensure_ready_for_gui" in script
    assert "install.sh" in script
    assert 'pip install --user' not in script


def test_windows_install_bootstraps_gui_in_venv():
    root = Path(__file__).resolve().parents[1]
    ps1 = (root / "install.ps1").read_text(encoding="utf-8")
    assert "requirements-portable-bootstrap.txt" in ps1
    assert "qt_compat import QApplication" in ps1
    assert 'pip install --user' not in ps1.lower()


def test_windows_setup_bat_uses_venv_python():
    root = Path(__file__).resolve().parents[1]
    bat = (root / "build_packages.sh").read_text(encoding="utf-8")
    assert '.venv\\Scripts\\python.exe" -m eli.setup' in bat
    assert "install.ps1" in bat


def test_eli_setup_applies_hardware_policy():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "eli_setup.sh").read_text(encoding="utf-8")
    assert "eli.setup.hardware_policy" in script
    assert "ELI_INSTALL_CPU_ONLY" in script


def test_one_click_and_install_eli_redirect():
    root = Path(__file__).resolve().parents[1]
    for name in ("eli_one_click_setup.sh", "install_eli.sh"):
        src = (root / "scripts" / name).read_text(encoding="utf-8")
        assert "eli_setup.sh" in src
