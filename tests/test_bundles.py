"""Bundles: separate ELI applications opened on ELI v3's core in place of its window."""

import json
import os
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
    monkeypatch.setattr(bundles, "_search_folders", lambda: [])   # not this machine's desktop
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


def test_a_build_that_needs_no_licence_starts_every_bundle(monkeypatch):
    """Product keys are checked where ELI v3's own key is (retail builds); a source
    checkout starts official bundles without one. tests/test_licensing_products.py
    covers the retail side."""
    for name in ("ELI_REQUIRE_LICENSE", "ELI_RETAIL_BUILD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ELI_LICENSE_BYPASS", "1")
    ok, why = bundles.entitled(Bundle(id="legal", title="ELI Legal", launch="a:b"))
    assert ok and "not required" in why


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
    """Opened from ELI's window: that window's own process serves it, so the model loaded there answers."""
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


def test_a_port_held_by_another_program_is_not_taken_for_eli_and_eli_serves_beside_it(monkeypatch):
    """With ELI v2 open on the usual port, a bundle started from v3 opened without ELI at all."""
    from eli.runtime import server_util

    monkeypatch.setattr(server_util, "effective_api_port", lambda: 18999)
    monkeypatch.setattr(server_util, "port_in_use", lambda port: port <= 19001)
    started = []
    monkeypatch.setattr(core_server, "running", lambda port: port in started)
    from types import SimpleNamespace
    monkeypatch.setattr(core_server, "_start", lambda port: started.append(port) or SimpleNamespace(should_exit=False))
    monkeypatch.delenv("ELI_API_PORT", raising=False)
    with core_server.serving() as port:
        assert port == 19002 and started == [19002], "the held port is left alone; the next free one is used"
        assert os.environ["ELI_API_PORT"] == "19002"
    monkeypatch.setattr(server_util, "port_in_use", lambda port: True)
    started.clear()
    with core_server.serving() as port:
        assert port is None and started == [], "no free port at all: the bundle opens and says ELI is not there"


def test_only_a_server_that_says_it_is_eli_v3_is_taken_for_this_eli(monkeypatch):
    """ELI v2 answers on the same port with the same health check: v3 took it for itself and
    talked to v2 ("ELI is already running")."""
    from eli.runtime import server_util

    monkeypatch.setattr(server_util, "port_in_use", lambda port: True)
    answers = {"/health": {"status": "healthy"},
               "/api": {"service": "ELI Cognitive OS Agent", "version": "1.0.0"}}
    monkeypatch.setattr(server_util, "_http_json",
                        lambda url, headers=None: next((v for k, v in answers.items() if url.endswith(k)), None))
    seen = server_util.probe_eli_server(18999)
    assert seen["in_use"] and not seen["eli_running"], "a server that does not say it is ELI v3 is another program"
    answers["/api"] = {"service": "ELI Cognitive OS Agent", "product": "eli_v2", "version": "2.5.34"}
    assert not server_util.probe_eli_server(18999)["eli_running"]
    answers["/api"] = {"service": "ELI Cognitive OS Agent", "product": server_util.PRODUCT, "version": "3.0.145"}
    assert server_util.probe_eli_server(18999)["eli_running"]


def test_a_running_eli_v3_says_where_it_serves_and_takes_it_back(tmp_path, monkeypatch):
    """ELI VR started on its own looked only at the usual port; with v3 moved off it, VR found
    nothing (or ELI v2)."""
    from eli.core import paths
    from eli.runtime import server_util

    monkeypatch.setattr(paths, "user_root", lambda: tmp_path)
    server_util.announce_port(19002)
    note = json.loads((tmp_path / "server.json").read_text(encoding="utf-8"))
    assert note == {"product": server_util.PRODUCT, "port": 19002, "pid": os.getpid()}
    server_util.withdraw_port()
    assert not (tmp_path / "server.json").exists()
    (tmp_path / "server.json").write_text(json.dumps({"product": "eli_v3", "port": 1, "pid": os.getpid() + 1}))
    server_util.withdraw_port()
    assert (tmp_path / "server.json").exists(), "another process's note is left alone"


def test_the_api_names_which_eli_it_is():
    import asyncio
    from api.server import api_info
    from eli.runtime import server_util

    info = asyncio.run(api_info())
    assert info["product"] == server_util.PRODUCT and info["version"]


def test_eli_vr_has_an_edition_pack_like_the_others():
    from eli.editions import discover_editions

    packs = discover_editions()
    assert {"legal", "vr"} <= set(packs)
    vr = packs["vr"]
    assert vr.label == "ELI VR" and vr.policy_module == "eli.editions.vr.policy"
    import importlib

    policy = importlib.import_module(vr.policy_module)
    assert policy.blocked_actions() == set() and policy.output_disclaimer() is None


def test_eli_vr_on_the_desktop_is_found_without_being_registered(isolated, monkeypatch):
    """Registering was a command typed by hand, so v3 had no way in to a copy on the desktop."""
    import os
    desktop = isolated / "Desktop"
    desktop.mkdir()
    old, new = desktop / "ELI_VR-0.9.1-x86_64.AppImage", desktop / "ELI_VR-0.9.2-x86_64.AppImage"
    for i, path in enumerate((old, new)):
        path.write_text("")
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
    monkeypatch.setattr(bundles, "_search_folders", lambda: [desktop])
    found = bundles.discover()
    assert found["vr"].kind == "external" and found["vr"].command == (str(new),), "the newest copy"
    assert "server" in found["vr"].extra["needs"]


def test_a_copy_pointed_to_by_hand_is_put_on_the_list(isolated):
    program = isolated / "ELI-VR.x86_64"
    program.write_text("")
    written = bundles.register_program("vr", program)
    assert written.parent == isolated / "registry"
    found = bundles.discover()
    assert found["vr"].command == (str(program),) and found["vr"].title == "ELI VR"


def test_every_official_bundle_is_listed_with_what_it_takes_here(isolated, monkeypatch):
    """ELI VR, ELI Legal and the bundles to come are upgrades to ELI v3: a person starting their own
    ELI v3 is told which they have, which need a key, which to get, and which are coming."""
    from eli.licensing.products import OFFICIAL_BUNDLES
    listed = {o.id: o for o in bundles.offers()}
    assert set(OFFICIAL_BUNDLES) <= set(listed)
    assert listed["vr"].state == "not_installed" and listed["legal"].state == "not_installed"
    assert all(listed[p].state == "coming" for p in OFFICIAL_BUNDLES if p not in bundles.bundle_apps())
    (isolated / "ELI-VR.x86_64").write_text("")
    bundles.register_program("vr", isolated / "ELI-VR.x86_64")
    monkeypatch.setattr(bundles, "entitled", lambda b: (False, "no ELI VR licence on this machine"))
    assert bundles.offers()[[o.id for o in bundles.offers()].index("vr")].state == "needs_key"
    monkeypatch.setattr(bundles, "entitled", lambda b: (True, ""))
    assert {o.id: o for o in bundles.offers()}["vr"].state == "ready"


def test_the_menu_and_the_start_up_choice_say_what_each_bundle_needs(isolated, monkeypatch):
    """Hidden until something had registered, the menu gave no way in to ELI VR at all."""
    from eli.bundles.chooser import entries
    from eli.gui.main_window._common import QApplication, QMenu, QWidget
    from eli.gui.main_window._mixins.tabs import TabsMixin
    QApplication.instance() or QApplication([])

    class Window(QWidget, TabsMixin):
        pass

    window = Window()
    window._bundles_menu = QMenu()
    window._fill_bundles_menu()
    menu = {a.text(): a.isEnabled() for a in window._bundles_menu.actions()}
    assert menu.get("Get ELI VR…") is True and menu.get("Get ELI Legal…") is True
    assert menu.get("ELI Healthcare: coming") is False and window._bundles_menu.menuAction().isVisible()
    shown = {bundle_id: (text, can) for text, can, bundle_id in entries(bundles.offers())}
    assert shown["vr"] == ("ELI VR — not installed", False) and shown["healthcare"][1] is False

    (isolated / "ELI-VR.x86_64").write_text("")
    bundles.register_program("vr", isolated / "ELI-VR.x86_64")
    monkeypatch.setattr(bundles, "entitled", lambda b: (True, ""))
    window._fill_bundles_menu()
    menu = {a.text() for a in window._bundles_menu.actions()}
    assert "Open ELI VR in its own window" in menu and "Get ELI VR…" not in menu
    assert {b: c for _t, c, b in entries(bundles.offers())}["vr"] is True


def test_a_bundle_that_brings_a_body_is_expected_until_it_closes(isolated, monkeypatch):
    """ELI VR draws on the card the model loads onto: while it is on its way the model load waits for
    it (api/server.py) and the plan keeps its room (eli/core/load_plan.py)."""
    program = isolated / "eli-vr"
    program.write_text("#!/bin/sh\nexit 0\n")
    registry = isolated / "registry"
    registry.mkdir()
    (registry / "vr.json").write_text(json.dumps({"id": "vr", "edition": "vr", "command": [str(program)], "needs": ["server"]}))
    (registry / "rooms.json").write_text(json.dumps({"id": "rooms", "command": [str(program)], "brings": ["body"]}))
    (registry / "plain.json").write_text(json.dumps({"id": "plain", "command": [str(program)]}))
    _served_on(monkeypatch, 18999)
    seen = []
    monkeypatch.setattr(bundles.subprocess, "call", lambda cmd, env=None: seen.append(bundles.body_expected()) or 0)
    assert not bundles.body_expected()
    assert bundles.launch("vr") == 0 and bundles.launch("rooms") == 0 and bundles.launch("plain") == 0
    assert seen == [True, True, False]
    assert not bundles.body_expected(), "the expectation goes with the bundle"


def test_the_model_waits_for_a_body_on_its_way_and_not_for_one_that_is_not(monkeypatch):
    from eli.world import embodiment
    monkeypatch.setattr(embodiment, "present", lambda: [])
    assert bundles.wait_for_body() is False, "nothing expected: nothing to wait for"
    with bundles._bodies_lock:
        bundles._bodies_expected.add("vr")
    try:
        ticks = []

        def _present():
            ticks.append(1)
            return [{"surface": "eli_vr"}] if len(ticks) >= 3 else []

        monkeypatch.setattr(embodiment, "present", _present)
        assert bundles.wait_for_body(poll_seconds=0.0) is True and len(ticks) == 3
    finally:
        with bundles._bodies_lock:
            bundles._bodies_expected.discard("vr")


def test_eli_legal_found_by_looking_is_the_highest_version_and_never_its_setup_program(
    isolated, monkeypatch
):
    """A setup program in Downloads is not the application, and the most recently touched copy
    is not necessarily the newest release."""
    import os
    downloads = isolated / "Downloads"
    downloads.mkdir()
    setup = downloads / "ELI_Legal-0.9.0-windows-x64-setup.exe"
    old = downloads / "ELI_Legal-0.6.1-linux-x86_64.AppImage"
    new = downloads / "ELI_Legal-0.7.0-linux-x86_64.AppImage"
    for i, path in enumerate((new, setup, old)):  # the old copy was touched last
        path.write_text("")
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
    monkeypatch.setattr(bundles, "_search_folders", lambda: [downloads])
    found = bundles.discover()["legal"]
    assert found.command == (str(new),) and found.version == "0.7.0"


def test_a_newer_copy_is_preferred_to_an_older_package_or_registration(isolated, monkeypatch):
    """An old copy must never hide a new release: the installed package, preferred because it can
    use ELI's own model, and a registration, preferred to a copy found by looking, both give way
    to a newer version, and only to a newer one."""
    _fake_package(
        isolated,
        monkeypatch,
        """
        def describe():
            return {"id": "legal", "title": "ELI Legal", "version": "0.6.1", "launch": "fakebundle:launch"}
    """,
    )
    desktop = isolated / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(bundles, "_search_folders", lambda: [desktop])
    older = desktop / "ELI_Legal-0.5.0-linux-x86_64.AppImage"
    older.write_text("")
    assert bundles.discover()["legal"].kind == "in-process", "an older copy does not win"
    newer = desktop / "ELI_Legal-0.7.0-linux-x86_64.AppImage"
    newer.write_text("")
    assert bundles.discover()["legal"].command == (str(newer),)
    monkeypatch.delenv("ELI_EXTRA_BUNDLES")
    registry = isolated / "registry"
    registry.mkdir()
    registered = isolated / "eli-legal-gui"
    registered.write_text("")
    record = {"id": "legal", "version": "0.8.0", "command": [str(registered)]}
    (registry / "legal.json").write_text(json.dumps(record))
    assert bundles.discover()["legal"].command == (str(registered),), "a newer registration stays"
    (registry / "legal.json").write_text(json.dumps({**record, "version": "0.6.0"}))
    assert bundles.discover()["legal"].command == (str(newer),)


def test_a_macos_application_found_by_looking_is_started_by_its_own_program(isolated, monkeypatch):
    import plistlib
    apps = isolated / "Applications"
    program = apps / "ELI Legal.app" / "Contents" / "MacOS" / "eli-legal-gui"
    program.parent.mkdir(parents=True)
    program.write_text("")
    with (apps / "ELI Legal.app" / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleExecutable": "eli-legal-gui"}, fh)
    monkeypatch.setattr(bundles, "_search_folders", lambda: [apps])
    assert bundles.discover()["legal"].command == (str(program),)


def test_a_downloaded_program_is_marked_runnable_and_one_that_cannot_start_is_told(isolated, monkeypatch, capsys):
    """ELI VR downloaded through a browser has no execute bit: starting it raised PermissionError out
    of launch() and ELI v3 died with a traceback. A program the user owns is marked runnable for them
    (what their file manager's "Allow executing" does) and started; one that still cannot start is
    told in words, with no traceback."""
    import os
    import stat as _stat
    program = isolated / "ELI_VR-9.9.9-x86_64.AppImage"
    program.write_text("#!/bin/sh\nexit 0\n")
    program.chmod(0o644)
    registry = isolated / "registry"
    registry.mkdir()
    (registry / "vr.json").write_text(json.dumps({"id": "vr", "edition": "vr", "command": [str(program)]}))
    _served_on(monkeypatch, 18999)
    assert bundles.launch("vr") == 0, "it ran"
    assert program.stat().st_mode & _stat.S_IXUSR
    if os.name != "nt":
        program.chmod(0o644)
        monkeypatch.setattr(bundles.subprocess, "call", lambda *a, **k: (_ for _ in ()).throw(OSError(8, "Exec format error")))
        assert bundles.launch("vr") == 4
        assert "cannot start" in capsys.readouterr().err
