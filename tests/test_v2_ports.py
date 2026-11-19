"""Surfaces ported from v2 that v3 was missing.

Each of these had its machinery already present in v3 and only the reachable
surface absent — the same half-port shape that left "be comedic" doing nothing.
They are pinned here because absence is silent: nothing errors, the feature is
just unreachable.
"""
from __future__ import annotations



# ── Ollama auto-start ────────────────────────────────────────────────────────


def test_autostart_is_skippable(monkeypatch):
    from eli.integrations.ollama import client
    monkeypatch.setenv("ELI_OLLAMA_AUTOSTART", "0")
    monkeypatch.setattr(client, "is_running", lambda: False)
    monkeypatch.setattr(client, "_autostart_tried", False, raising=False)
    assert client.ensure_server_running() is False


def test_autostart_refuses_a_remote_host(monkeypatch):
    """A remote Ollama is the owner's to start, not ours."""
    from eli.integrations.ollama import client
    monkeypatch.delenv("ELI_OLLAMA_AUTOSTART", raising=False)
    monkeypatch.setattr(client, "is_running", lambda: False)
    monkeypatch.setattr(client, "_autostart_tried", False, raising=False)
    monkeypatch.setattr(client, "candidate_hosts", lambda *a, **k: ["http://192.168.1.50:11434"])
    assert client.ensure_server_running() is False


# ── Licence terms ────────────────────────────────────────────────────────────
def test_license_terms_are_printable_and_distinct_from_activation():
    from eli.runtime.license_info import eula_path, license_path, license_text
    assert license_path() is not None
    assert eula_path() is not None, "the EULA must ship beside LICENSE"
    text = license_text()
    assert "PROPRIETARY COMMERCIAL SOFTWARE" in text
    assert "ELI End User Licence Agreement" in text and "## 3. Licence grant" in text, \
        "--license must print the EULA as well as the notice"


def test_license_flag_does_not_collide_with_license_status():
    """--license (terms) and --license-status (activation) must stay separate."""
    from eli.licensing.flags import LICENSE_FLAGS
    assert "--license" not in LICENSE_FLAGS, \
        "--license must fall through to the terms printer, not the activation handler"
    assert "--license-status" in LICENSE_FLAGS
