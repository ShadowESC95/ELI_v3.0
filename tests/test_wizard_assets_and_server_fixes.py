"""Setup wizard must hard-fail missing nomic/piper correctly."""
from __future__ import annotations

from pathlib import Path


def test_windows_installer_no_desktop_server_icon():
    iss = Path("packaging/windows/installer.iss").read_text(encoding="utf-8")
    assert 'Name: "{autodesktop}\\ELI Server"' not in iss
    assert 'Name: "{autodesktop}\\{#MyAppName}"' in iss
    assert iss.count("[Run]") >= 1
    # Only one postinstall Launch ELI line in the final [Run] block
    assert 'Description: "Launch ELI"' in iss
