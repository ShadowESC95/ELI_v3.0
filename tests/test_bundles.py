"""Bundles: separate ELI applications opened on ELI v3's core in place of its window."""

import json
import sys
import textwrap

import pytest

from eli import bundles
from eli.bundles import Bundle, chooser


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """No installed packages, no real registry folders: only what each test provides."""
    monkeypatch.setattr(bundles, "entry_points", lambda group=None: [])
    monkeypatch.setattr(bundles, "registry_dirs", lambda: [tmp_path / "registry"])
    monkeypatch.delenv("ELI_EXTRA_BUNDLES", raising=False)
    return tmp_path


def _fake_package(tmp_path, monkeypatch, body):
    (tmp_path / "fakebundle.py").write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("fakebundle", None)
    monkeypatch.setenv("ELI_EXTRA_BUNDLES", "fakebundle:describe")


def test_an_in_process_bundle_is_found_and_launched_with_its_host(
    isolated, monkeypatch
):
    _fake_package(
        isolated,
        monkeypatch,
        """
        CALLS = []
        def describe():
            return {"contract": 1, "id": "Demo", "title": "ELI Demo", "launch": "fakebundle:launch"}
        def launch(argv, *, host):
            CALLS.append((argv, host["name"]))
            return 7
    """,
    )
    found = bundles.discover()
    assert list(found) == ["demo"] and found["demo"].kind == "in-process"
    assert bundles.launch("DEMO", ["--x"]) == 7
    import fakebundle

    assert fakebundle.CALLS == [(["--x"], "ELI v3")]


def test_a_separately_installed_bundle_registers_with_a_file_and_runs_as_its_own_program(
    isolated, monkeypatch
):
    program = isolated / "eli-demo"
    program.write_text("#!/bin/sh\nexit 0\n")
    program.chmod(0o755)
    registry = isolated / "registry"
    registry.mkdir()
    (registry / "demo.json").write_text(
        json.dumps({"id": "demo", "title": "ELI Demo", "command": [str(program)]})
    )
    (registry / "gone.json").write_text(
        json.dumps({"id": "gone", "command": [str(isolated / "removed")]})
    )
    (registry / "broken.json").write_text("{ not json")
    found = bundles.discover()
    assert list(found) == ["demo"], (
        "a removed program or an unreadable file is skipped, never fatal"
    )
    ran = []
    monkeypatch.setattr(bundles.subprocess, "call", lambda cmd: ran.append(cmd) or 0)
    assert bundles.launch("demo", ["--choose"]) == 0 and ran == [
        [str(program), "--choose"]
    ]


def test_an_installed_package_is_preferred_to_a_registration_of_the_same_bundle(
    isolated, monkeypatch
):
    _fake_package(
        isolated,
        monkeypatch,
        """
        def describe():
            return {"id": "demo", "title": "ELI Demo", "launch": "fakebundle:launch"}
    """,
    )
    program = isolated / "eli-demo"
    program.write_text("")
    registry = isolated / "registry"
    registry.mkdir()
    (registry / "demo.json").write_text(
        json.dumps({"id": "demo", "command": [str(program)]})
    )
    assert bundles.discover()["demo"].kind == "in-process"


def test_descriptions_are_checked():
    with pytest.raises(ValueError):
        Bundle.from_description(
            {"id": "x", "contract": 99, "launch": "a:b"}, origin="t"
        )
    with pytest.raises(ValueError):
        Bundle.from_description({"id": "bad id!", "launch": "a:b"}, origin="t")
    with pytest.raises(ValueError):
        Bundle.from_description({"id": "x"}, origin="t")
    with pytest.raises(ValueError):
        Bundle.from_description({"id": "x"}, origin="t", kind="external")


def test_an_unknown_bundle_is_refused_plainly(isolated, capsys):
    assert bundles.launch("nothing") == 2
    assert "No bundle 'nothing' is installed" in capsys.readouterr().err


def test_every_bundle_is_allowed_until_licences_carry_editions():
    ok, why = bundles.entitled(Bundle(id="legal", title="ELI Legal", launch="a:b"))
    assert ok and "do not yet carry editions" in why


def test_the_start_up_choice_follows_what_was_remembered(monkeypatch):
    found = {"legal": Bundle(id="legal", title="ELI Legal", launch="a:b")}
    monkeypatch.setattr(chooser, "discover", lambda: found)
    asked = []
    monkeypatch.setattr(chooser, "choose", lambda b: asked.append(1) or "legal")
    monkeypatch.setattr(
        chooser, "_settings", lambda: {"startup_target": "bundle:legal"}
    )
    assert chooser.target_at_startup([]) == "legal" and not asked
    monkeypatch.setattr(chooser, "_settings", lambda: {"startup_target": "eli"})
    assert chooser.target_at_startup([]) is None and not asked
    assert chooser.target_at_startup(["--choose"]) == "legal" and asked == [1]
    assert chooser.target_at_startup(["--no-bundles"]) is None
    monkeypatch.setattr(chooser, "_settings", dict)
    monkeypatch.setattr(chooser, "choose", lambda b: "eli")
    assert chooser.target_at_startup([]) is None
    monkeypatch.setattr(chooser, "discover", dict)
    monkeypatch.setattr(
        chooser,
        "choose",
        lambda b: pytest.fail("no question when nothing is installed"),
    )
    assert chooser.target_at_startup([]) is None
