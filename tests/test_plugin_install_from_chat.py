"""PLUGIN_INSTALL from chat installs only what ships with ELI or a verified marketplace listing. A
registry entry with a download is never fetched and switched on from a chat message, and a loose
name match is offered, not installed."""
from __future__ import annotations

import pytest


class _Manager:
    def __init__(self, entries):
        self.entries = {e["id"]: e for e in entries}
        self.installed = []

    def get_registry_entry(self, pid):
        return self.entries.get(pid)

    def search(self, query):
        return [e for e in self.entries.values() if query.split()[0] in e["description"]]

    def install(self, pid):
        self.installed.append(pid)
        return {"ok": True, "content": f"installed {pid}", "response": f"installed {pid}"}


@pytest.fixture
def manager(monkeypatch):
    from eli.execution.effectors import plugins
    import eli.plugins.manager as pm
    mgr = _Manager([
        {"id": "pomodoro", "name": "Pomodoro", "description": "focus timer"},
        {"id": "remote_tool", "name": "Remote", "description": "weather widget",
         "source": "https://example.invalid/plugin.py"},
    ])
    monkeypatch.setattr(pm, "get_manager", lambda: mgr)
    monkeypatch.setattr(plugins, "_eli_marketplace_install", lambda q: None)
    return mgr


def _install(query):
    from eli.execution.effectors.plugins import _h_plugin_install
    return _h_plugin_install("PLUGIN_INSTALL", {"query": query})


def test_a_built_in_named_exactly_is_installed(manager):
    assert _install("pomodoro")["ok"] is True
    assert manager.installed == ["pomodoro"]


def test_a_download_is_not_installed_from_chat(manager):
    out = _install("remote_tool")
    assert out["ok"] is False and out["error"] == "unverified_download"
    assert manager.installed == []


def test_a_loose_match_is_offered_not_installed(manager):
    out = _install("focus")
    assert manager.installed == []
    assert out["asks_user"] is True
    assert out["choices"][0]["command"] == "install plugin pomodoro"


def test_a_loose_match_on_a_download_is_refused(manager):
    out = _install("weather")
    assert out["ok"] is False and manager.installed == []
