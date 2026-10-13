"""Bundles: separate ELI applications opened on ELI v3's core in place of its window."""

import json
import sys
import textwrap

import pytest

from eli import bundles
from eli.bundles import Bundle, chooser, core_server


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


def _served_on(monkeypatch, port):
    """ELI's local server, as far as bundles can tell: answering on this port, or absent."""
    import contextlib

    asked = []

    @contextlib.contextmanager
    def serving():
        asked.append(1)
        yield port

    monkeypatch.setattr(core_server, "serving", serving)
    return asked


def test_a_program_that_needs_the_server_is_started_with_it_and_told_the_port(
    isolated, monkeypatch
):
    program = isolated / "eli-rooms"
    program.write_text("#!/bin/sh\nexit 0\n")
    registry = isolated / "registry"
    registry.mkdir()
    (registry / "rooms.json").write_text(
        json.dumps({"id": "rooms", "command": [str(program)], "needs": ["server"]})
    )
    (registry / "plain.json").write_text(
        json.dumps({"id": "plain", "command": [str(program)]})
    )
    asked = _served_on(monkeypatch, 18999)
    ran = []
    monkeypatch.setattr(
        bundles.subprocess,
        "call",
        lambda cmd, env=None: ran.append((cmd, (env or {}).get("ELI_API_PORT"))) or 0,
    )
    assert bundles.launch("rooms", ["--flat"]) == 0
    assert ran == [([str(program), "--flat"], "18999")] and asked == [1]
    assert bundles.launch("plain") == 0
    assert ran[-1] == ([str(program)], None) and asked == [1], (
        "a bundle that did not ask never starts the server"
    )


def test_an_installed_bundle_that_needs_the_server_is_handed_the_port(
    isolated, monkeypatch
):
    _fake_package(
        isolated,
        monkeypatch,
        """
        SEEN = []
        def describe():
            return {"id": "rooms", "launch": "fakebundle:launch", "needs": ["server"]}
        def launch(argv, *, host):
            SEEN.append(host.get("api_port"))
            return 0
    """,
    )
    _served_on(monkeypatch, 18999)
    assert bundles.launch("rooms") == 0
    import fakebundle

    assert fakebundle.SEEN == [18999]
    _served_on(monkeypatch, None)
    assert bundles.launch("rooms") == 0 and fakebundle.SEEN == [18999, None], (
        "with no server to be had the bundle still opens"
    )


def test_beside_a_running_eli_a_bundle_is_served_by_that_eli(isolated, monkeypatch):
    """Opened from ELI's window: his own process serves it, so the model loaded there answers."""
    program = isolated / "eli-rooms"
    program.write_text("")
    registry = isolated / "registry"
    registry.mkdir()
    for name, extra in (("rooms", {"needs": ["server"]}), ("plain", {})):
        (registry / f"{name}.json").write_text(
            json.dumps({"id": name, "command": [str(program)], **extra})
        )

    class Server:
        should_exit = False

    class Process:
        def wait(self):
            return 0

    server = Server()
    asked = []
    monkeypatch.setattr(core_server, "start", lambda: asked.append(1) or (18999, server))
    started = []
    monkeypatch.setattr(
        bundles.subprocess,
        "Popen",
        lambda cmd, env=None, **kw: started.append((cmd, (env or {}).get("ELI_API_PORT"))) or Process(),
    )
    monkeypatch.delenv("ELI_API_PORT", raising=False)
    bundles.spawn("plain")
    assert started[-1] == ([str(program)], None) and asked == [], "no server for one that did not ask"
    bundles.spawn("rooms")
    assert started[-1] == ([str(program)], "18999") and asked == [1]
    import time

    for _ in range(50):
        if server.should_exit:
            break
        time.sleep(0.02)
    assert server.should_exit, "a server started for the bundle goes when the bundle closes"


def test_the_server_already_running_is_used_and_left_alone(monkeypatch):
    from eli.runtime import server_util

    monkeypatch.setattr(server_util, "effective_api_port", lambda: 18999)
    monkeypatch.setattr(core_server, "running", lambda port: True)
    monkeypatch.setattr(
        core_server, "_start", lambda port: pytest.fail("one is already answering")
    )
    with core_server.serving() as port:
        assert port == 18999


def test_a_server_started_for_a_bundle_is_stopped_when_it_closes(monkeypatch):
    from eli.runtime import server_util

    class Server:
        should_exit = False

    server = Server()
    up = []
    monkeypatch.setattr(server_util, "effective_api_port", lambda: 18999)
    monkeypatch.setattr(server_util, "port_in_use", lambda port: False)
    monkeypatch.setattr(core_server, "running", lambda port: bool(up))
    monkeypatch.setattr(core_server, "_start", lambda port: up.append(port) or server)
    with core_server.serving() as port:
        assert port == 18999 and up == [18999] and not server.should_exit
    assert server.should_exit


def test_a_port_held_by_another_program_is_not_taken_for_eli(monkeypatch):
    from eli.runtime import server_util

    monkeypatch.setattr(server_util, "effective_api_port", lambda: 18999)
    monkeypatch.setattr(server_util, "port_in_use", lambda port: True)
    monkeypatch.setattr(core_server, "running", lambda port: False)
    monkeypatch.setattr(
        core_server, "_start", lambda port: pytest.fail("the port is not free")
    )
    with core_server.serving() as port:
        assert port is None


def test_eli_vr_has_an_edition_pack_like_the_others():
    from eli.editions import discover_editions

    packs = discover_editions()
    assert {"legal", "vr"} <= set(packs)
    vr = packs["vr"]
    assert vr.label == "ELI VR" and vr.policy_module == "eli.editions.vr.policy"
    import importlib

    policy = importlib.import_module(vr.policy_module)
    assert policy.blocked_actions() == set() and policy.output_disclaimer() is None
