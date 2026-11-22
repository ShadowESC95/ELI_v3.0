"""CLAIM: every download can put the licence in front of its user.

ELI v3 is proprietary, so the terms have to
be reachable from each shape it ships in — not just the Windows installer, which
is the only wrapper with a licence pane of its own. These pin the surfaces:

  * `LICENSE` / `NOTICE` / `THIRD_PARTY_NOTICES.md` exist and name the licence
  * the frozen bundles carry them (ELI.spec data manifest)
  * the dmg and AppImage stage them into the artifact
  * the Windows installer shows one at install time
  * `--license` resolves the text at runtime, on every platform
"""
from __future__ import annotations

import pytest

from . import _helpers as H

REPO = H.REPO

DOCS = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")


@pytest.mark.parametrize("name", DOCS)
def test_license_doc_present(name):
    path = REPO / name
    assert path.is_file(), f"{name} missing from the repo root"
    assert path.stat().st_size > 200, f"{name} is suspiciously short"


def test_license_names_the_terms():
    text = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert "PROPRIETARY COMMERCIAL SOFTWARE" in text


@pytest.mark.parametrize("name", DOCS + ("models/MODEL_LICENSES.md",))
def test_frozen_bundle_ships_license(name):
    """ELI.spec's data manifest must carry the terms into exe/zip/dmg/AppImage."""
    spec = (REPO / "ELI.spec").read_text(encoding="utf-8")
    assert f'"{name}"' in spec, f"{name} not in the ELI.spec data manifest"


@pytest.mark.parametrize("script,label", [
    ("packaging/macos/build-dmg.sh", "dmg"),
    ("packaging/linux/build-appimage-pyinstaller.sh", "AppImage"),
])
def test_artifact_stages_license(script, label):
    """The dmg/AppImage have no installer step, so they stage the docs directly."""
    text = (REPO / script).read_text(encoding="utf-8")
    assert "LICENSE" in text, f"the {label} build does not stage LICENSE"
    assert "THIRD_PARTY_NOTICES.md" in text, \
        f"the {label} build does not stage THIRD_PARTY_NOTICES.md"


def test_windows_installer_shows_license():
    iss = (REPO / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
    assert "LicenseFile=" in iss, "Inno installer has no licence pane"
    assert "AppCopyright=" in iss, "Inno installer declares no copyright"


def test_license_flag_wired_into_both_entry_points():
    src = (REPO / "eli" / "__main__.py").read_text(encoding="utf-8")
    frozen = (REPO / "packaging" / "pyinstaller" / "eli_entry.py").read_text(encoding="utf-8")
    for name, text in (("eli/__main__.py", src), ("eli_entry.py", frozen)):
        assert "--license" in text, f"{name} does not handle --license"
        assert "license_info" in text, f"{name} does not call the licence printer"


def test_license_resolves_at_runtime():
    from eli.runtime.license_info import license_path, license_text

    assert license_path() is not None, "LICENSE not resolvable from a source checkout"
    text = license_text()
    assert "PROPRIETARY COMMERCIAL SOFTWARE" in text
    assert "Jason Fitzgibbon Bridgeman" in text


def test_who_made_eli_is_read_from_the_licence_it_ships_with():
    """Asked who made it, the model invented a company; the licence names the maker."""
    from eli.runtime.license_info import license_text, made_by
    maker = made_by()
    assert maker and maker in license_text() and "rights" not in maker.lower()


def test_an_option_this_program_does_not_take_starts_nothing(monkeypatch, capsys):
    """`python -m eli --bundel legal` (a typo, or another program's option) opened ELI v3's window as if
    nothing had been asked. An --option the command does not read is refused before anything starts;
    every one it reads still works, and what follows --bundle <id> is the bundle's own."""
    import re
    import sys
    import eli.__main__ as cli
    from eli.licensing.flags import LICENSE_FLAGS
    started = []
    monkeypatch.setattr("eli.gui.app.main", lambda: started.append("window") or 0)
    monkeypatch.setattr("eli.bundles.launch", lambda bundle, argv: started.append((bundle, list(argv))) or 0)
    monkeypatch.setattr("eli.licensing.launch_gate.launch_allowed", lambda **k: True)
    monkeypatch.setattr("eli.core.init_data.bootstrap_once", lambda: None)
    monkeypatch.setattr("eli.utils.log.keep_console_log", lambda *a, **k: None)

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["eli", *args])
        return cli.main()

    assert run("--bundel", "legal") == 2 and not started
    assert "does not take --bundel" in capsys.readouterr().err
    assert run("--help") == 0 and "--no-bundles" in capsys.readouterr().out and not started
    assert run("--bundle", "legal", "--its-own-option") == 0 and started == [("legal", ["--its-own-option"])]

    # the options the command's path reads (eli/__main__.py, the licence flags, the window's start, the
    # bundle chooser) are the ones it takes
    read = set(LICENSE_FLAGS)
    for name in ("eli/__main__.py", "eli/licensing/flags.py", "eli/gui/app.py", "eli/bundles/chooser.py"):
        text = (REPO / name).read_text(encoding="utf-8")
        read |= set(re.findall(r'"(--[a-z-]+)"\s+(?:not\s+)?in\s+(?:args|argv|sys\.argv)\b', text))
    assert {"--setup", "--choose", "--no-bundles", "--bundle", "--headless"} <= read
    assert cli._unknown_options(sorted(read)) == []
