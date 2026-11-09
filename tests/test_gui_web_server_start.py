"""The window's web server asks for its token on this computer too (eli/gui/main_window/_mixins/
chrome_io.py, _eli_server_auth).

"Start (this computer)" ran the API with ELI_API_ALLOW_TOKENLESS=1 on 127.0.0.1. Any page the
browser opens can reach 127.0.0.1, so a page whose name re-resolves there could drive ELI's whole
API as the operator. The link the window shows now carries the token, as the LAN link does.
"""
from __future__ import annotations

import os
import pathlib

import pytest


@pytest.fixture
def auth(monkeypatch):
    import api.api_token as tokens
    from eli.gui.main_window._mixins.chrome_io import ChromeIoMixin
    monkeypatch.setattr(tokens, "get_stable_token", lambda: "a-saved-token")
    monkeypatch.setenv("ELI_API_ALLOW_TOKENLESS", "1")          # what an earlier start left
    for name in ("ELI_API_TOKEN", "ELI_API_HOST"):
        monkeypatch.delenv(name, raising=False)
    return ChromeIoMixin._eli_server_auth


def test_this_computer_only_still_needs_the_token(auth):
    assert auth(False) == ("127.0.0.1", "a-saved-token")
    assert os.environ["ELI_API_TOKEN"] == "a-saved-token"
    assert "ELI_API_ALLOW_TOKENLESS" not in os.environ
    assert os.environ["ELI_API_HOST"] == "127.0.0.1"


def test_the_lan_server_uses_the_same_saved_token(auth):
    assert auth(True) == ("0.0.0.0", "a-saved-token")
    assert "ELI_API_ALLOW_TOKENLESS" not in os.environ


def test_no_start_path_turns_the_token_off():
    src = (pathlib.Path(__file__).resolve().parents[1] / "eli" / "gui" / "main_window" / "_mixins"
           / "chrome_io.py").read_text(encoding="utf-8")
    assert 'ELI_API_ALLOW_TOKENLESS"] = "1"' not in src
    assert 'url = f"http://127.0.0.1:{port}/#token={token}"' in src, "the link carries the token"
