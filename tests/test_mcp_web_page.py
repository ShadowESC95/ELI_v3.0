"""The dedicated MCP page on ELI's own web server.

Follows the same clean-lane convention as tests/test_api_server.py: the suite conftest
mocks `pydantic` session-wide, which makes the real FastAPI app un-importable in-process,
so this module skips under the mocked full-suite run and does the real work when invoked
with `--noconftest`:

    .venv/bin/python -m pytest tests/test_mcp_web_page.py --noconftest
"""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
    import api.server as srv
    _WEB_OK = True
except Exception as _e:  # mocked-pydantic conflict inside the full suite
    pytest.skip(
        f"web server not importable in-process ({type(_e).__name__}: {_e}); "
        f"run this file with `--noconftest` for real deps",
        allow_module_level=True,
    )


def _client():
    return TestClient(srv.app, base_url="http://127.0.0.1")



def test_mcp_page_is_served():
    r = _client().get("/mcp")
    assert r.status_code == 200
    body = r.text
    assert "Model Context Protocol" in body
    assert "ELI as a server" in body


def test_mcp_page_is_theme_aware_like_the_rest_of_the_ui():
    body = _client().get("/mcp").text
    assert "eli_theme" in body and 'data-theme' in body


def test_mcp_page_is_linked_from_the_chat_ui():
    """A page nobody can find is not attached to anything."""
    assert 'href="/mcp"' in _client().get("/").text


def test_mcp_json_endpoints_sit_behind_the_same_token_gate():
    c = _client()
    for path in ("/v1/mcp/status", "/v1/mcp/tools"):
        assert c.get(path).status_code == 401, f"{path} must not be open to a LAN caller"


def test_mcp_json_endpoints_answer_when_authenticated(monkeypatch):
    monkeypatch.setenv("ELI_API_TOKEN", "testtok")
    c = _client()
    h = {"Authorization": "Bearer testtok"}
    st = c.get("/v1/mcp/status", headers=h)
    assert st.status_code == 200 and "servers" in st.json()
    tl = c.get("/v1/mcp/tools", headers=h)
    assert tl.status_code == 200 and "tools" in tl.json()


def test_mcp_status_endpoint_survives_a_broken_client(monkeypatch):
    """The page must degrade to a reported error, never a 500."""
    monkeypatch.setenv("ELI_API_TOKEN", "testtok")
    import eli.integrations.mcp.client as mc
    def boom(*a, **k):
        raise RuntimeError("config exploded")
    monkeypatch.setattr(mc, "status", boom)
    r = _client().get("/v1/mcp/status", headers={"Authorization": "Bearer testtok"})
    assert r.status_code == 200
    assert "config exploded" in r.json().get("error", "")
