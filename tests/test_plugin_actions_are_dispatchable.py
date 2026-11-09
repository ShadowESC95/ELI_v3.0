"""An installed, enabled plugin's own actions are actually callable.

The marketplace stopped one move short of useful. A plugin could be downloaded,
checksum-verified, scanned by eleven engines, consented to per capability, written
to disk, enabled and imported — and then nothing could invoke it:

  * `Plugin.register()` published actions into `capability_registry`, but that is a
    CATALOGUE. `list_capabilities` backs the docs and compatibility surfaces;
    nothing ever looked a `handler` back up and called it.
  * Registration was driven by `base.load_plugins()`, which enumerates the
    `eli.plugins` **source package** — so a plugin installed into the user's
    plugins directory was never among the ones registered in the first place.
  * And registration ran once at engine start, so nothing installed mid-session
    could work before a restart.

MCP servers were never affected: they have a real dispatch path through
`client.call_tool`. This is the Python-plugin half.
"""
from __future__ import annotations

import json

import pytest


PLUGIN_SRC = '''
from eli.plugins.base.base import Plugin


class GreeterPlugin(Plugin):
    name = "greeter"
    description = "Greets."

    def __init__(self):
        self.actions = {"hello": self.hello, "shout": self.shout}
        super().__init__()

    def hello(self, args):
        who = (args or {}).get("who") or "world"
        return {"ok": True, "content": f"hello {who}"}

    def shout(self, args):
        return "SHOUTING"
'''

SECOND_SRC = '''
from eli.plugins.base.base import Plugin


class RivalPlugin(Plugin):
    name = "rival"
    description = "Also greets."

    def __init__(self):
        self.actions = {"hello": self.hello}
        super().__init__()

    def hello(self, args):
        return {"ok": True, "content": "rival hello"}
'''


@pytest.fixture()
def installed(tmp_path, monkeypatch):
    """A plugins directory holding one enabled plugin, as install() would leave it."""
    import eli.plugins.manager as M

    pdir = tmp_path / "plugins"
    (pdir / "greeter").mkdir(parents=True)
    (pdir / "greeter" / "plugin.py").write_text(PLUGIN_SRC, encoding="utf-8")
    (pdir / "greeter" / "eli_plugin.json").write_text(json.dumps({
        "id": "greeter", "name": "Greeter", "version": "1.0.0",
        "description": "Greets.", "author": "t", "license": "MIT",
        "permissions": []}), encoding="utf-8")

    state = {"enabled": ["greeter"], "disabled": []}
    monkeypatch.setattr(M, "_plugins_dir", lambda: pdir)
    monkeypatch.setattr(M, "_load_state", lambda: dict(state))
    monkeypatch.setattr(M, "_save_state", lambda s: state.update(s))

    mgr = M.PluginManager() if hasattr(M, "PluginManager") else M.get_manager()
    mgr._state = state
    mgr.enable("greeter")
    return mgr, pdir, state


def test_plugin_action_is_dispatchable(installed):
    mgr, _, _ = installed
    res = mgr.dispatch("GREETER_HELLO", {"who": "eli"})
    assert res is not None, "an enabled plugin's action must resolve"
    assert res["ok"] is True
    assert res["content"] == "hello eli"
    assert res["plugin"] == "greeter"


def test_bare_action_name_resolves_when_unambiguous(installed):
    mgr, _, _ = installed
    res = mgr.dispatch("HELLO", {})
    assert res is not None and res["content"] == "hello world"


def test_non_dict_return_is_normalised(installed):
    """A plugin author returning a plain string must not break the caller."""
    mgr, _, _ = installed
    res = mgr.dispatch("GREETER_SHOUT", {})
    assert res["ok"] is True
    assert res["content"] == "SHOUTING" and res["response"] == "SHOUTING"


def test_unknown_action_returns_none_not_an_error(installed):
    """None means 'no plugin claims this', which is what lets the executor fall
    through to its own 'unsupported action' answer instead of masking it."""
    mgr, _, _ = installed
    assert mgr.dispatch("NOT_A_PLUGIN_ACTION", {}) is None


def test_disabled_plugin_is_not_dispatchable(installed):
    mgr, _, state = installed
    state["enabled"] = []
    assert mgr.dispatch("GREETER_HELLO", {}) is None


def test_a_raising_plugin_is_reported_not_propagated(installed, monkeypatch):
    mgr, _, _ = installed
    inst = mgr._loaded["greeter"]

    # Plugin.execute binds handlers as methods (`__get__`), so a stub needs self.
    def boom(self, args):
        raise RuntimeError("kaboom")
    monkeypatch.setitem(inst.actions, "hello", boom)

    res = mgr.dispatch("GREETER_HELLO", {})
    assert res["ok"] is False
    assert "kaboom" in res["content"]


def test_ambiguous_bare_name_is_reported_not_guessed(installed, monkeypatch):
    """Silently picking one of two plugins that both define HELLO is how one
    plugin ends up shadowing another."""
    import eli.plugins.manager as M
    mgr, pdir, state = installed

    (pdir / "rival").mkdir()
    (pdir / "rival" / "plugin.py").write_text(SECOND_SRC, encoding="utf-8")
    (pdir / "rival" / "eli_plugin.json").write_text(json.dumps({
        "id": "rival", "name": "Rival", "version": "1.0.0", "description": "Also greets.",
        "author": "t", "license": "MIT", "permissions": []}), encoding="utf-8")
    state["enabled"] = ["greeter", "rival"]
    mgr._state = state
    mgr.enable("rival")

    res = mgr.dispatch("HELLO", {})
    assert res is not None and res["ok"] is False
    assert "more than one" in res["content"]

    # …but the fully-qualified names still work.
    assert mgr.dispatch("GREETER_HELLO", {})["content"] == "hello world"
    assert mgr.dispatch("RIVAL_HELLO", {})["content"] == "rival hello"


def test_media_plugin_is_dispatchable(monkeypatch):
    """MediaPlugin didn't inherit from Plugin, so PluginManager's issubclass check
    never found it — it loaded fine but was silently unregistered. Covers the real
    eli/plugins/media/plugin.py, not a synthetic fixture. _load_plugin_from_file
    execs it under a fresh module name (plugins.media.plugin, not
    eli.plugins.media.plugin), so the MPRIS backend has to be patched at its own
    module rather than on media/plugin.py's `_b` — both resolve the same object."""
    import eli.plugins.manager as M
    from eli.plugins.media import plugin as media_plugin_mod
    from eli.integrations.mpris import playerctl_backend

    monkeypatch.setattr(playerctl_backend, "play", lambda player=None: {"ok": True, "content": "playing"})
    monkeypatch.setattr(M, "_load_state", lambda: {"enabled": ["media"], "disabled": []})

    mgr = M.PluginManager()
    mgr._load_plugin_from_file("media", media_plugin_mod.__file__)
    assert isinstance(mgr._loaded["media"], media_plugin_mod.Plugin)

    res = mgr.dispatch("MEDIA_PLAY", {"player": "spotify"})
    assert res is not None and res["ok"] is True and res["content"] == "playing"


def test_a_plugin_cannot_shadow_a_builtin_action():
    """Dispatch is the LAST stop in the executor. A listing declaring SHUTDOWN or
    SEND_EMAIL must not take that verb over for the whole assistant."""
    import inspect
    from eli.execution import executor_enhanced

    src = inspect.getsource(executor_enhanced._execute_impl)
    tail = src[-2000:]
    assert "get_manager().dispatch" in tail, (
        "plugin dispatch must sit at the end of _execute_impl, after every "
        "built-in handler has had its chance to claim the action")


def test_installing_a_built_in_uses_the_shipped_plugin_and_uninstalling_keeps_it(tmp_path, monkeypatch):
    """A built-in with no download got a generated stub whose every action answered ok "executed
    (stub)", and it replaced the real plugin in memory; uninstalling deleted ELI's own plugin folder."""
    import inspect

    import eli.plugins.manager as M

    pdir = tmp_path / "plugins"
    state = {"enabled": [], "disabled": [], "installed": {}}
    monkeypatch.setattr(M, "_plugins_dir", lambda: pdir)
    monkeypatch.setattr(M, "_load_state", lambda: dict(state))
    monkeypatch.setattr(M, "_save_state", lambda s: state.update(s))
    shipped = M._bundled_plugins_dir() / "pomodoro" / "plugin.py"
    before = shipped.read_bytes()

    mgr = M.PluginManager()
    res = mgr.install("pomodoro")
    assert res["ok"], res
    assert not pdir.exists() or not any(pdir.rglob("plugin.py"))
    loaded = mgr._loaded["pomodoro"]
    assert inspect.getsourcefile(type(loaded)) == str(shipped)
    assert "stub" not in str(loaded.actions["status"]({}))

    assert mgr.uninstall("pomodoro")["ok"]
    assert shipped.read_bytes() == before


def test_installing_an_id_that_does_not_ship_never_downloads_or_runs_it(tmp_path, monkeypatch):
    """manager.install fetched a registry entry's 'source' over raw urllib, pip-installed its
    packages with no consent and no network check, and exec'd the module at once with no manifest,
    checksum, scan or consent. Now it goes through the marketplace (which needs consent) or nowhere."""
    import subprocess
    import urllib.request

    import eli.plugins.manager as M
    import eli.plugins.marketplace as MK

    pdir = tmp_path / "plugins"
    state = {"enabled": [], "disabled": [], "installed": {}}
    monkeypatch.setattr(M, "_plugins_dir", lambda: pdir)
    monkeypatch.setattr(M, "_load_state", lambda: dict(state))
    monkeypatch.setattr(M, "_save_state", lambda s: state.update(s))
    touched = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: touched.append("download"))
    monkeypatch.setattr(subprocess, "check_call", lambda *a, **k: touched.append("pip"))
    monkeypatch.setattr(MK, "search", lambda q, **k: [])
    entry = {"id": "stranger", "name": "Stranger", "version": "1.0.0", "source": "https://example.invalid/p.py",
             "pip": ["somepkg"], "actions": ["X"]}
    mgr = M.PluginManager()
    monkeypatch.setattr(mgr, "get_registry_entry", lambda pid: entry if pid == "stranger" else None)
    monkeypatch.setattr(mgr, "list_available", lambda: [entry])

    res = mgr.install("stranger")
    assert res["ok"] is False
    assert touched == [], "nothing may be downloaded or pip-installed"
    assert not (pdir / "stranger").exists() and "stranger" not in mgr._loaded


def test_a_listed_plugin_goes_through_the_marketplace_and_its_consent(tmp_path, monkeypatch):
    import eli.plugins.manager as M
    import eli.plugins.marketplace as MK
    state = {"enabled": [], "disabled": [], "installed": {}}
    monkeypatch.setattr(M, "_load_state", lambda: dict(state))
    monkeypatch.setattr(M, "_save_state", lambda s: state.update(s))
    listing = {"id": "stranger", "name": "Stranger", "registry_label": "a registry"}
    seen = {}
    monkeypatch.setattr(MK, "search", lambda q, **k: [listing])
    monkeypatch.setattr(MK, "install", lambda l, confirm=None, **k: seen.update(listing=l, confirm=confirm)
                        or {"ok": False, "problems": ["needs your explicit confirmation"]})
    res = M.PluginManager().install("stranger")
    assert seen["listing"] is listing and seen["confirm"] is None
    assert res["ok"] is False and "confirmation" in res["error"]


def test_a_shipped_plugin_is_never_pip_installed(tmp_path, monkeypatch):
    """A built-in whose package is missing is reported, not installed from PyPI in the background."""
    import subprocess

    import eli.plugins.manager as M
    state = {"enabled": [], "disabled": [], "installed": {}}
    monkeypatch.setattr(M, "_plugins_dir", lambda: tmp_path / "plugins")
    monkeypatch.setattr(M, "_load_state", lambda: dict(state))
    monkeypatch.setattr(M, "_save_state", lambda s: state.update(s))
    ran = []
    monkeypatch.setattr(subprocess, "check_call", lambda *a, **k: ran.append(a))
    mgr = M.PluginManager()
    entry = dict(mgr.get_registry_entry("pomodoro"), pip=["a_package_that_is_not_installed_xyz"])
    monkeypatch.setattr(mgr, "get_registry_entry", lambda pid: entry if pid == "pomodoro" else None)
    res = mgr.install("pomodoro")
    assert res["ok"] is False and "a_package_that_is_not_installed_xyz" in res["error"]
    assert ran == [] and "pomodoro" not in state["enabled"]
