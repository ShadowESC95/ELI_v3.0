"""Web-server (api/server.py) test suite.

The FastAPI server + dashboard PWA had zero automated coverage; this exercises the
security-critical auth/RBAC layer deeply (pure-function branches + live gate via
TestClient), the static/PWA routes, the read endpoints, and the engine-backed
execute/chat paths with the engine mocked so no GGUF model is loaded.

Auth model under test (see api/server.py):
  * TestClient's socket peer is "testclient" → NON-loopback, so the token gate is
    genuinely exercised (a same-machine loopback caller would bypass it).
  * Single-operator mode (RBAC off) is forced in a fixture so auth is deterministic
    regardless of any user store on the box.
"""
from __future__ import annotations

import time

import pytest

# The suite conftest mocks `pydantic` session-wide (to keep the cognition-engine
# tests light). That single mock makes the real FastAPI web server impossible to
# import in-process — which is exactly why api/server.py measured 0% coverage. This
# module therefore runs on its own CLEAN lane (real deps, coverage-visible):
#
#     .venv/bin/python -m pytest tests/test_api_server.py --noconftest \
#         --cov=api.server --cov-report=term-missing
#
# Under the normal (mocked) full-suite run it detects the un-importable server and
# SKIPS — so it can never break the main suite. The assertions below are the real
# thing; they just need real fastapi/pydantic to execute.
try:
    from fastapi.testclient import TestClient
    import api.server as srv
    from api.server import app
    _WEB_OK = True
except Exception as _e:  # metaclass/mocked-pydantic conflict inside the full suite
    pytest.skip(
        f"web server not importable in-process ({type(_e).__name__}: {_e}); "
        f"run this file with `--noconftest` for real deps + coverage",
        allow_module_level=True,
    )


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _single_operator(monkeypatch):
    """Force single-operator (RBAC off) so token/tokenless auth is deterministic."""
    import eli.runtime.api_users as api_users
    monkeypatch.setattr(api_users, "rbac_enabled", lambda: False, raising=False)


@pytest.fixture
def tokenless_client(monkeypatch):
    """Owner/tokenless mode: no ELI_API_TOKEN, tokenless bind opted in → every
    request authenticates as the operator/admin."""
    monkeypatch.setenv("ELI_API_ALLOW_TOKENLESS", "1")
    monkeypatch.delenv("ELI_API_TOKEN", raising=False)
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def secured():
    """LAN mode: a token is configured, tokenless is OFF → non-loopback callers
    (TestClient) must present the bearer token. Returns (client, token)."""
    token = "s3cr3t-test-token-abc123"

    def _factory(monkeypatch):
        monkeypatch.delenv("ELI_API_ALLOW_TOKENLESS", raising=False)
        monkeypatch.setenv("ELI_API_TOKEN", token)
        return TestClient(app, base_url="http://127.0.0.1"), token

    return _factory


# --------------------------------------------------------------------------- #
# Pure auth helpers — every branch
# --------------------------------------------------------------------------- #
def test_bearer_parsing():
    assert srv._bearer("Bearer abc123") == "abc123"
    assert srv._bearer("bearer  spaced ") == "spaced"
    assert srv._bearer("Basic abc") == ""
    assert srv._bearer("") == ""
    assert srv._bearer(None) == ""


def test_rank_hierarchy():
    assert srv._rank("admin") > srv._rank("member") > srv._rank("viewer")
    assert srv._rank("nonsense") == 0          # unknown role → least privilege
    assert srv._rank("") == 0
    assert srv._rank(None) == 0


class _FakeReq:
    def __init__(self, host):
        self.client = type("C", (), {"host": host})() if host is not None else None


def test_is_loopback_client():
    assert srv._is_loopback_client(_FakeReq("127.0.0.1")) is True
    assert srv._is_loopback_client(_FakeReq("::1")) is True
    assert srv._is_loopback_client(_FakeReq("localhost")) is True
    assert srv._is_loopback_client(_FakeReq("10.0.0.5")) is False
    assert srv._is_loopback_client(_FakeReq("192.168.1.20")) is False
    assert srv._is_loopback_client(_FakeReq(None)) is False


def test_tokenless_allowed_env(monkeypatch):
    for val in ("1", "true", "YES", "on"):
        monkeypatch.setenv("ELI_API_ALLOW_TOKENLESS", val)
        assert srv._tokenless_allowed() is True
    monkeypatch.setenv("ELI_API_ALLOW_TOKENLESS", "0")
    assert srv._tokenless_allowed() is False
    monkeypatch.delenv("ELI_API_ALLOW_TOKENLESS", raising=False)
    assert srv._tokenless_allowed() is False


def test_api_token_env_live(monkeypatch):
    monkeypatch.setenv("ELI_API_TOKEN", "  padded-token  ")
    assert srv._api_token() == "padded-token"
    monkeypatch.delenv("ELI_API_TOKEN", raising=False)
    assert srv._api_token() == ""


def test_resolve_principal_tokenless_owner(monkeypatch):
    monkeypatch.delenv("ELI_API_TOKEN", raising=False)
    monkeypatch.setenv("ELI_API_ALLOW_TOKENLESS", "1")
    p = srv._resolve_principal("", loopback=False)
    assert p is not None and p.role == "admin"


def test_resolve_principal_correct_token(monkeypatch):
    monkeypatch.delenv("ELI_API_ALLOW_TOKENLESS", raising=False)
    monkeypatch.setenv("ELI_API_TOKEN", "right")
    assert srv._resolve_principal("Bearer right", loopback=False).role == "admin"


def test_resolve_principal_wrong_token_fails_closed(monkeypatch):
    monkeypatch.delenv("ELI_API_ALLOW_TOKENLESS", raising=False)
    monkeypatch.setenv("ELI_API_TOKEN", "right")
    assert srv._resolve_principal("Bearer wrong", loopback=False) is None
    # ...but a same-machine (loopback) caller with no token is still the owner.
    assert srv._resolve_principal("", loopback=True).role == "admin"


def test_resolve_principal_no_config_no_tokenless_denies(monkeypatch):
    monkeypatch.delenv("ELI_API_TOKEN", raising=False)
    monkeypatch.delenv("ELI_API_ALLOW_TOKENLESS", raising=False)
    # No token configured, not tokenless, not loopback → fail closed.
    assert srv._resolve_principal("", loopback=False) is None


def test_loopback_admin_opt_out(monkeypatch):
    """ELI_LOOPBACK_ADMIN=0 — hardened shared-host / reverse-proxy deployments."""
    monkeypatch.delenv("ELI_API_TOKEN", raising=False)
    monkeypatch.delenv("ELI_API_ALLOW_TOKENLESS", raising=False)
    monkeypatch.setenv("ELI_LOOPBACK_ADMIN", "0")
    assert srv._resolve_principal("", loopback=True) is None
    monkeypatch.setenv("ELI_API_TOKEN", "secret")
    assert srv._resolve_principal("Bearer secret", loopback=True).role == "admin"
    # ...and with a token configured, a loopback caller without it is not the owner either
    assert srv._resolve_principal("", loopback=True) is None


# --------------------------------------------------------------------------- #
# Only ELI's own pages and native clients are served
# --------------------------------------------------------------------------- #
def test_a_page_whose_name_was_pointed_at_this_machine_is_refused(monkeypatch):
    """DNS rebinding: a site's own name re-pointed at 127.0.0.1 makes its page same-origin with
    this server, and tokenless loopback is the owner. The Host it sends gives it away."""
    monkeypatch.setenv("ELI_API_ALLOW_TOKENLESS", "1")
    monkeypatch.delenv("ELI_API_TOKEN", raising=False)
    monkeypatch.delenv("ELI_API_ALLOWED_HOSTS", raising=False)
    rebound = TestClient(app, base_url="http://rebound.example:8081")
    for path in ("/v1/me", "/v1/admin/overview", "/v1/settings", "/"):
        assert rebound.get(path).status_code == 403, path
    assert rebound.post("/v1/devices/discover").status_code == 403


def test_this_servers_own_addresses_and_listed_names_are_served(monkeypatch):
    import socket
    monkeypatch.setenv("ELI_API_ALLOW_TOKENLESS", "1")
    monkeypatch.delenv("ELI_API_TOKEN", raising=False)
    own = socket.gethostname().split(".")[0].lower()
    for base in ("http://127.0.0.1:8081", "http://localhost:8081",
                 "http://192.0.2.10:8081", f"http://{own}.local:8081"):
        assert TestClient(app, base_url=base).get("/v1/me").status_code == 200, base
    # (the test client cannot take a bracketed IPv6 address as its base)
    assert srv._names_this_server(srv._split_host("[::1]:8081")[0])
    assert TestClient(app, base_url="http://eli.home.arpa").get("/v1/me").status_code == 403
    monkeypatch.setenv("ELI_API_ALLOWED_HOSTS", "eli.home.arpa, other.example")
    assert TestClient(app, base_url="http://eli.home.arpa").get("/v1/me").status_code == 200


def test_another_sites_page_cannot_act_through_the_owners_browser(tokenless_client, monkeypatch):
    calls = []

    class _Engine:
        def process(self, message, source="", stream=False, **kw):
            calls.append(message)
            return {"response": "ok"}
    monkeypatch.setattr(srv, "get_engine", lambda: _Engine())
    # a cross-site form post or fetch: the browser names the page's origin
    for origin in ("http://evil.example", "null", "http://127.0.0.1:3000"):
        r = tokenless_client.post("/v1/chat", json={"message": "hi"}, headers={"Origin": origin})
        assert r.status_code == 403, origin
    # a cross-site request that names no origin still carries Sec-Fetch-Site
    assert tokenless_client.get("/v1/me", headers={"Sec-Fetch-Site": "cross-site",
                                                   "Sec-Fetch-Mode": "no-cors"}).status_code == 403
    assert tokenless_client.get("/v1/me", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert calls == [], "a refused request never reaches the engine"
    # ELI's own page: same origin, or no Origin at all (a native client)
    assert tokenless_client.post("/v1/chat", json={"message": "hi"},
                                 headers={"Origin": "http://127.0.0.1",
                                          "Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert tokenless_client.post("/v1/chat", json={"message": "hi"}).status_code == 200
    # a link on another site may open the page itself, but never call /v1 by navigating
    nav = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
    assert tokenless_client.get("/", headers=nav).status_code == 200
    assert tokenless_client.get("/v1/me", headers=nav).status_code == 403


# The test client joins a WebSocket path onto ws://testserver whatever its base_url is.
_OWN_WS = "ws://127.0.0.1:8081"


def test_a_web_page_cannot_open_the_world_feed(tokenless_client):
    """WebSockets are outside the browser's same-origin policy: any page could open the feed and
    read the memories, agenda and thoughts in it. A browser always names the page's Origin."""
    from starlette.websockets import WebSocketDisconnect
    for origin in ("http://evil.example", "null"):
        with pytest.raises(WebSocketDisconnect) as closed:
            with tokenless_client.websocket_connect(_OWN_WS + "/v1/world/ws", headers={"Origin": origin}):
                pass
        assert closed.value.code == 1008, origin


def test_a_native_client_reaches_the_world_feed_gate(monkeypatch):
    """ELI VR sends no Origin: it is let through to the feed's own token gate, which decides."""
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.delenv("ELI_API_ALLOW_TOKENLESS", raising=False)
    monkeypatch.setenv("ELI_API_TOKEN", "vr-test-token")
    client = TestClient(app, base_url="http://127.0.0.1")
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(_OWN_WS + "/v1/world/ws"):
            pass
    assert closed.value.code == 4401   # the feed's own refusal, not the site guard's


def test_a_sentence_the_world_answered_is_a_turn_of_the_owners_conversation(tokenless_client, monkeypatch):
    """A build or a game set in ELI VR was kept as an action row only: "what did I ask you in VR?"
    found nothing. Its sentence and ELI's answer are kept where the chat path keeps its turns: the
    engine's memory, under the same person and session as a chat turn from VR."""
    stored = []

    class _Memory:
        def add_conversation_turn(self, role, content, session_id=None, user_id=None):
            stored.append((role, content, session_id, user_id))

    class _Engine:
        memory = _Memory()
        user_id = "the-owner-id"
        session_id = "the-desktop-session"

    from eli.world import composer, games
    import eli.runtime.evidence_ledger as evidence_ledger
    monkeypatch.setattr("api.server.get_engine", lambda *a, **k: _Engine(), raising=False)
    monkeypatch.setattr(evidence_ledger, "record_event", lambda *a, **k: 1)
    monkeypatch.setattr(games, "wants_stop", lambda text: False)
    monkeypatch.setattr(games, "wants_game", lambda text: False)
    monkeypatch.setattr(composer, "wants_building", lambda text, scene: True)
    monkeypatch.setattr(composer, "compose", lambda text, scene: {
        "kind": "build", "text": "A lamp, by the window.", "room": "workshop",
        "results": [{"op": "create", "name": "Lamp", "ok": True}], "refused": []})
    r = tokenless_client.post("/v1/world/say", json={"text": "build a lamp by the window", "session_id": "eli-vr-7"})
    assert r.status_code == 200 and r.json()["text"] == "A lamp, by the window."
    assert stored == [("user", "build a lamp by the window", "eli-vr-7", "the-owner-id"),
                      ("assistant", "A lamp, by the window.", "eli-vr-7", "the-owner-id")]
    stored.clear()
    tokenless_client.post("/v1/world/say", json={"text": "build a lamp by the window"})
    assert {(row[2], row[3]) for row in stored} == {("the-desktop-session", "the-owner-id")}, \
        "with no session named, the engine's own, as a chat turn from VR gets"


# ── the three clients, as the window starts the server: its own page, ELI VR, the phone ──────────
_SAVED_TOKEN = "the-saved-stable-token"


def _as_the_window_starts_it(monkeypatch):
    """The window's "this computer" and "phone" starts both set the saved token and never tokenless."""
    monkeypatch.setenv("ELI_API_TOKEN", _SAVED_TOKEN)
    monkeypatch.delenv("ELI_API_ALLOW_TOKENLESS", raising=False)
    monkeypatch.delenv("ELI_LOOPBACK_ADMIN", raising=False)


def test_the_desktop_page_eli_vr_and_the_phone_all_still_connect(monkeypatch):
    """The window now starts the server with its saved token, and the server refuses other sites' pages:
    neither may shut out the owner's own page, ELI VR (loopback, no token, a native socket with no
    Origin) or the phone (the LAN, with the token)."""
    _as_the_window_starts_it(monkeypatch)
    own = "http://127.0.0.1:8081"
    page = TestClient(app, base_url=own, client=("127.0.0.1", 50001))
    assert page.get("/", headers={"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate"}).status_code == 200
    asked = page.get("/v1/world/protocol", headers={"Authorization": f"Bearer {_SAVED_TOKEN}", "Origin": own,
                                                     "Sec-Fetch-Site": "same-origin"})
    assert asked.status_code == 200, asked.text
    vr = TestClient(app, base_url=own, client=("127.0.0.1", 50002))
    assert vr.get("/v1/world/protocol").status_code == 200, "ELI VR's tokenless loopback call was refused"
    with vr.websocket_connect(_OWN_WS + "/v1/world/ws") as ws:
        assert ws.receive_json()["type"]
    lan = "http://192.168.1.20:8081"
    phone = TestClient(app, base_url=lan, client=("192.168.1.31", 50003))
    assert phone.get("/v1/world/protocol", headers={"Authorization": f"Bearer {_SAVED_TOKEN}", "Origin": lan,
                                                     "Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert phone.get("/v1/world/protocol", headers={"Origin": lan}).status_code == 401, "the LAN needs the token"
    with phone.websocket_connect(f"ws://192.168.1.20:8081/v1/world/ws?token={_SAVED_TOKEN}",
                                 headers={"Origin": lan}) as ws:
        assert ws.receive_json()["type"]


def test_the_link_the_server_opens_on_this_computer_carries_its_token(monkeypatch, capsys):
    """Started with a token for this computer (the window passes --token), `python -m api.server`
    still opened its own page with no token in the link: with ELI_LOOPBACK_ADMIN=0 that page could
    do nothing. Its link carries the token, as the window's does."""
    import sys
    opened = []
    for name in ("ELI_API_HOST", "ELI_API_PORT", "ELI_API_TOKEN", "ELI_API_SCHEME", "ELI_API_ALLOW_TOKENLESS",
                 "ELI_API_HTTPS", "ELI_API_HTTPS_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(srv.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(srv, "_open_browser_async", lambda url: opened.append(url) or True)
    # the server keeps its console in a file from here on; started for real, it held the rest of the
    # run's output and test_console_log's own file was never opened
    monkeypatch.setattr("eli.utils.log.keep_console_log", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["api.server", "--port", "8099", "--token", _SAVED_TOKEN])
    srv.main()
    assert opened == [f"http://127.0.0.1:8099/#token={_SAVED_TOKEN}"]
    monkeypatch.delenv("ELI_API_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["api.server", "--port", "8099"])
    opened.clear()
    srv.main()
    assert opened == ["http://127.0.0.1:8099/"], "no token, no token in the link"


def test_started_on_its_own_the_server_moves_off_a_port_another_program_holds(monkeypatch, capsys):
    """`ELI --server` and `python -m api.server` kept a copy of the port rule without the step the
    window and the bundles take, and failed to bind when ELI v2 or anything else held the port."""
    import sys
    from eli.runtime import server_util
    assert not hasattr(srv, "effective_api_port"), "the port rule has one owner (server_util)"
    for name in ("ELI_API_HOST", "ELI_API_PORT", "ELI_API_TOKEN", "ELI_API_SCHEME", "ELI_API_ALLOW_TOKENLESS",
                 "ELI_API_HTTPS", "ELI_API_HTTPS_PORT"):
        monkeypatch.delenv(name, raising=False)
    served = []
    monkeypatch.setattr(srv.uvicorn, "run", lambda *a, **k: served.append(k.get("port")))
    monkeypatch.setattr(srv, "_open_browser_async", lambda url: True)
    monkeypatch.setattr("eli.utils.log.keep_console_log", lambda *a, **k: None)
    monkeypatch.setattr(server_util, "effective_api_port", lambda: 18099)
    monkeypatch.setattr(server_util, "free_port_after", lambda port: port + 3)
    monkeypatch.setattr(sys, "argv", ["api.server"])
    monkeypatch.setattr(server_util, "probe_eli_server", lambda port=None: {"in_use": True, "eli_running": False})
    srv.main()
    monkeypatch.setattr(server_util, "probe_eli_server", lambda port=None: {"in_use": False, "eli_running": False})
    srv.main()
    # this ELI already there: it is not started a second time on another port
    monkeypatch.setattr(server_util, "probe_eli_server", lambda port=None: {"in_use": True, "eli_running": True})
    srv.main()
    assert served == [18102, 18099, 18099]
    monkeypatch.delenv("ELI_API_PORT", raising=False)


def test_the_two_model_lists_are_two_handlers_and_reply_text_has_one_reader():
    """Both handlers were named list_models, so the name meant the second; and the server kept
    its own copy of the engine's field order for a reply."""
    from eli.kernel.engine import _visible_reply_text
    assert srv.list_models()["object"] == "list" and srv.list_installed_models is not srv.list_models
    for result in ({"response": " a "}, {"content": "b", "text": "c"}, {"text": " c"}, {}, " plain ", ""):
        assert srv._extract_response_text(result) == _visible_reply_text(result).strip()
    assert srv._extract_response_text(iter([{"response": "one "}, "two", {"token": " three"}])) == "one two three"
    assert srv._extract_response_text(None) == ""


def test_the_mesh_timings_have_one_owner(tokenless_client):
    """The request model and the page each carried the mesh's 5 s / 18 s figures; a save with the
    box empty wrote the page's figure over the one kept."""
    from eli.runtime import home_mesh
    fields = srv.MeshConfig.model_fields
    assert fields["heartbeat_interval_sec"].default is None and fields["failover_after_sec"].default is None
    before = home_mesh.load_config()
    try:
        home_mesh.save_config({**before, "failover_after_sec": 31.0})
        r = tokenless_client.post("/v1/home/mesh/config", json={"failover_after_sec": None, "node_name": "desk"})
        assert r.status_code == 200 and home_mesh.load_config()["failover_after_sec"] == 31.0
    finally:
        home_mesh.save_config(before)
    page = tokenless_client.get("/").text
    assert "failover_after_sec||18" not in page and ".value)||18" not in page


def test_the_page_draws_the_controls_each_driver_declares(tokenless_client):
    """The page kept its own list of what each driver can do, which had drifted from the drivers'
    (no volume keys for a Fire TV)."""
    from eli.runtime import device_drivers
    rows = {row["name"]: row for row in tokenless_client.get("/v1/devices/drivers").json()["drivers"]}
    for name, row in rows.items():
        assert row["capabilities"] == device_drivers.get_driver(name).capabilities({})
    assert "volume_up" in rows["firetv"]["capabilities"]
    assert "DRIVER_CAPS" not in tokenless_client.get("/").text


# --------------------------------------------------------------------------- #
# Static / PWA routes (no auth)
# --------------------------------------------------------------------------- #
def test_health_is_tokenless(monkeypatch):
    # Health must answer even in fully-secured LAN mode (used by the PWA self-heal).
    monkeypatch.delenv("ELI_API_ALLOW_TOKENLESS", raising=False)
    monkeypatch.setenv("ELI_API_TOKEN", "x")
    r = TestClient(app, base_url="http://127.0.0.1").get("/health")
    assert r.status_code == 200 and r.json()["status"] == "healthy"


def test_root_serves_html(tokenless_client):
    r = tokenless_client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_manifest_and_service_worker(tokenless_client):
    m = tokenless_client.get("/manifest.webmanifest")
    assert m.status_code == 200 and "name" in m.json()
    sw = tokenless_client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]


def test_api_descriptor(tokenless_client):
    r = tokenless_client.get("/api")
    assert r.status_code == 200 and isinstance(r.json(), dict)


# --------------------------------------------------------------------------- #
# Auth gating through the real dependency graph
# --------------------------------------------------------------------------- #
def test_protected_endpoint_401_without_token(secured, monkeypatch):
    client, _ = secured(monkeypatch)
    assert client.get("/v1/capabilities").status_code == 401


def test_protected_endpoint_200_with_token(secured, monkeypatch):
    client, token = secured(monkeypatch)
    r = client.get("/v1/capabilities", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_protected_endpoint_401_with_wrong_token(secured, monkeypatch):
    client, _ = secured(monkeypatch)
    r = client.get("/v1/capabilities", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Read endpoints (tokenless owner)
# --------------------------------------------------------------------------- #
def test_whoami(tokenless_client):
    r = tokenless_client.get("/v1/me")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["role"] == "admin"


def test_capabilities_lists_actions(tokenless_client):
    r = tokenless_client.get("/v1/capabilities")
    assert r.status_code == 200


def test_models_endpoint(tokenless_client):
    r = tokenless_client.get("/v1/models/installed")
    assert r.status_code == 200


def test_net_status(tokenless_client):
    r = tokenless_client.get("/v1/net")
    assert r.status_code == 200
    assert "enabled" in r.json() or "online" in r.json() or isinstance(r.json(), dict)


def test_net_egress_tail(tokenless_client):
    r = tokenless_client.get("/v1/net/egress")
    assert r.status_code == 200


def test_system_vitals(tokenless_client):
    r = tokenless_client.get("/v1/system")
    assert r.status_code == 200 and isinstance(r.json(), dict)


# --------------------------------------------------------------------------- #
# Engine-backed paths — engine + executor mocked, no model loaded
# --------------------------------------------------------------------------- #
def test_execute_action_mocked(tokenless_client, monkeypatch):
    import eli.execution.executor_enhanced as ex
    monkeypatch.setattr(
        ex, "execute", lambda action, args=None: {"ok": True, "content": "did it"}
    )
    r = tokenless_client.post("/v1/execute", json={"action": "SCREENSHOT", "args": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["result"]["content"] == "did it"


def test_execute_reports_failure(tokenless_client, monkeypatch):
    import eli.execution.executor_enhanced as ex
    monkeypatch.setattr(ex, "execute", lambda action, args=None: {"ok": False, "error": "nope"})
    r = tokenless_client.post("/v1/execute", json={"action": "OPEN_APP", "args": {"name": "x"}})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_chat_mocked_engine(tokenless_client, monkeypatch):
    class _FakeEngine:
        def process(self, message, source="", stream=False, **kw):
            return {"response": f"echo: {message}", "content": f"echo: {message}"}

    monkeypatch.setattr(srv, "get_engine", lambda: _FakeEngine())
    r = tokenless_client.post("/v1/chat", json={"message": "hello there", "user_id": "u1"})
    assert r.status_code == 200
    assert "echo: hello there" in r.json()["response"]


def test_chat_runs_the_turn_as_the_caller(tokenless_client, monkeypatch):
    """One engine serves every caller: who a turn belongs to has to reach it per call. With one
    operator that is the owner whatever label a client sends ('web-…' from the browser, nothing
    from the phone or ELI VR): the same person as the desktop, so his agenda and what he said are
    one record on every surface. None hands the turn the engine's own owner id."""
    seen = {}

    class _Engine:
        def process(self, message, **kwargs):
            seen.clear()
            seen.update(kwargs)
            return {"response": "ok"}

    monkeypatch.setattr("eli.kernel.engine.get_engine", lambda *a, **k: _Engine())
    monkeypatch.setattr("api.server.get_engine", lambda *a, **k: _Engine(), raising=False)
    for label in ("u1", "web-abc123", "default", None):
        body = {"message": "hello there", "session_id": "s1"}
        if label:
            body["user_id"] = label
        r = tokenless_client.post("/v1/chat", json=body)
        assert r.status_code == 200
        assert seen["session_id"] == "s1" and seen["user_id"] is None, label


def test_a_member_under_rbac_keeps_their_own_turns(monkeypatch):
    import eli.runtime.api_users as api_users
    monkeypatch.setattr(api_users, "rbac_enabled", lambda: True, raising=False)
    assert srv._turn_user(srv.Principal("bob", "member")) == "bob"
    assert srv._turn_user(srv.Principal("operator", "admin")) is None   # the machine's owner


# How long a test waits for a stream's own thread to give the model lock back. It is a bound
# on a wait that ends at the stream's next frame (a few milliseconds here), not a pace.
_LOCK_WAIT_S = 10.0


def _drive_stream_and_leave(path: str, body: dict, text_marker: bytes) -> list:
    """Run one streamed request through the ASGI app the way uvicorn does (ASGI spec 2.3, so a
    client that goes is an http.disconnect), and leave as soon as the first text frame arrives:
    the web chat's Stop, a closed phone app, lost Wi-Fi."""
    import asyncio
    import json as _json

    frames = []

    async def _run():
        left = asyncio.Event()
        asked = [False]

        async def receive():
            if not asked[0]:
                asked[0] = True
                return {"type": "http.request", "body": _json.dumps(body).encode(), "more_body": False}
            await left.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                frames.append(message["body"])
                if text_marker in message["body"]:
                    left.set()

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                 "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"host", b"127.0.0.1:8081"), (b"content-type", b"application/json")],
                 "client": ("127.0.0.1", 50123), "server": ("127.0.0.1", 8081)}
        await srv.app(scope, receive, send)

    asyncio.run(_run())
    return frames


class _NextCaller:
    """Another thread that wants the model next (the desktop's reply, an embedding). It is started
    before the stream so it is alive all along: a thread started later can be handed the ident of
    the dead pool thread that holds the lock, and an RLock would then take it for its owner."""

    def __init__(self, lock):
        import threading
        self._lock, self._go, self._got = lock, threading.Event(), []
        self._t = threading.Thread(target=self._take)
        self._t.start()

    def _take(self):
        self._go.wait()
        ok = self._lock.acquire(timeout=_LOCK_WAIT_S)
        self._got.append(ok)
        if ok:
            self._lock.release()

    def gets_the_lock(self) -> bool:
        self._go.set()
        self._t.join()
        return self._got == [True]


def _locked_reply(lock, owners: list, pieces: int):
    """A reply that holds the model's call lock across its pieces and gives it back in its
    finally, the way gguf_inference's stream does (a release from another thread is swallowed
    there too)."""
    import threading

    def _gen():
        lock.acquire()
        owners.append(threading.get_ident())
        try:
            for i in range(pieces):
                time.sleep(0.005)
                yield f"piece{i} "
        finally:
            owners.append(threading.get_ident())
            try:
                lock.release()
            except RuntimeError:
                pass
    return _gen()


def test_a_stream_the_client_leaves_gives_the_model_lock_back(monkeypatch):
    """Stop in the web chat used to leave the model's lock held by a pool thread, and every later
    reply, embedding or ELI VR turn waited for it for ever."""
    import threading
    monkeypatch.setenv("ELI_API_ALLOW_TOKENLESS", "1")
    lock, owners = threading.RLock(), []

    class _Engine:
        def process(self, message, source="", stream=False, **kw):
            return _locked_reply(lock, owners, pieces=400)
    monkeypatch.setattr(srv, "get_engine", lambda: _Engine())
    next_caller = _NextCaller(lock)
    frames = _drive_stream_and_leave("/v1/chat/stream", {"message": "a long answer please"}, b'"delta"')
    assert any(b"delta" in f for f in frames)
    assert next_caller.gets_the_lock(), "the stream's lock is still held after the client left"
    assert len(owners) == 2 and owners[0] == owners[1], "taken and given back on one thread"


def test_a_completions_stream_the_client_leaves_gives_the_model_lock_back(monkeypatch):
    import threading
    monkeypatch.setenv("ELI_API_ALLOW_TOKENLESS", "1")
    lock, owners = threading.RLock(), []
    monkeypatch.setattr(srv, "_model_reply",
                        lambda prompt, system="": _locked_reply(lock, owners, pieces=400))
    next_caller = _NextCaller(lock)
    frames = _drive_stream_and_leave("/v1/chat/completions",
                                     {"stream": True, "messages": [{"role": "user", "content": "hi"}]},
                                     b'"content"')
    assert any(b"content" in f for f in frames)
    assert next_caller.gets_the_lock()
    assert len(owners) == 2 and owners[0] == owners[1]


def test_a_stream_read_to_the_end_is_whole_and_in_order(tokenless_client, monkeypatch):
    class _Engine:
        def process(self, message, source="", stream=False, **kw):
            return iter(["one ", "two ", "three"])
    monkeypatch.setattr(srv, "get_engine", lambda: _Engine())
    r = tokenless_client.post("/v1/chat/stream", json={"message": "count", "session_id": "s9"})
    import json as _json
    events = [_json.loads(line[5:]) for line in r.text.split("\n\n") if line.startswith("data:")]
    assert events[0] == {"session_id": "s9"}
    assert "".join(e.get("delta", "") for e in events) == "one two three"
    assert events[-1] == {"done": True}


def test_completions_are_a_model_call_never_a_turn_of_the_owners_conversation(tokenless_client, monkeypatch):
    """A third-party client's system prompt and transcript are not the owner talking: they are
    not stored, mined for his name, routed to actions, or used to settle ELI's last reply."""
    turns, asked = [], []

    class _Engine:
        def process(self, message, **kw):
            turns.append(message)
            return {"response": "from the engine"}
    monkeypatch.setattr(srv, "get_engine", lambda: _Engine())

    def _reply(prompt, system=""):
        asked.append((prompt, system))
        yield "from "
        yield "the model"
    monkeypatch.setattr(srv, "_model_reply", _reply)
    msgs = [{"role": "system", "content": "You are a code reviewer."},
            {"role": "user", "content": "my name is Sam, review this"}]
    r = tokenless_client.post("/v1/chat/completions", json={"messages": msgs})
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "from the model"
    assert asked == [("my name is Sam, review this", "You are a code reviewer.")]
    s = tokenless_client.post("/v1/chat/completions", json={"messages": msgs, "stream": True})
    assert "the model" in s.text and s.text.rstrip().endswith("data: [DONE]")
    assert turns == [], "nothing a completions client sends reaches the conversation engine"


def test_a_research_answer_is_a_model_call_never_a_turn(tokenless_client, monkeypatch):
    """The passages are documents: they are never stored as something the owner said."""
    turns, asked = [], []

    class _Engine:
        def process(self, message, **kw):
            turns.append(message)
            return {"response": "from the engine"}
    monkeypatch.setattr(srv, "get_engine", lambda: _Engine())
    monkeypatch.setattr(srv, "_model_reply", lambda prompt, system="": asked.append(prompt) or iter(["cited [a.txt]"]))
    import eli.runtime.research_corpus as rc
    monkeypatch.setattr(rc, "query", lambda corpus, question, k=6, user="": {
        "ok": True, "hits": [{"source": "a.txt", "text": "a passage", "score": 0.9}]})
    r = tokenless_client.post("/v1/research/query", json={"corpus": "c", "question": "what is in it?"})
    assert r.status_code == 200 and r.json()["answer"] == "cited [a.txt]"
    assert asked and "a passage" in asked[0] and "what is in it?" in asked[0]
    assert turns == []


def test_chat_requires_auth(secured, monkeypatch):
    client, _ = secured(monkeypatch)
    # /v1/chat is token-gated; no token → 401 before the engine is ever touched.
    assert client.post("/v1/chat", json={"message": "hi"}).status_code == 401


# --------------------------------------------------------------------------- #
# Read-endpoint contract: every GET surface degrades gracefully — it returns a
# structured JSON body (200), never an unhandled 500, even when its subsystem is
# unavailable. That "never crash the dashboard" guarantee matters for an audited
# service, so it's asserted per endpoint (which also executes each handler).
# --------------------------------------------------------------------------- #
_READ_ENDPOINTS = [
    "/v1/system", "/v1/net", "/v1/net/egress", "/v1/audit", "/v1/settings",
    "/v1/ui/prefs", "/v1/cognition/orchestration", "/v1/autonomy/goals", "/v1/tasks",
    "/v1/home/state", "/v1/home/automations", "/v1/home/scenes", "/v1/home/sun",
    "/v1/home/suggestions", "/v1/devices", "/v1/devices/rooms", "/v1/devices/drivers",
    "/v1/media", "/v1/research/corpora",
    "/v1/connect",
]
# Note: /v1/research/documents and /v1/research/activity require a `corpus_id` query
# param (a bare GET correctly returns 422), so they're not in the no-arg read set.


@pytest.mark.parametrize("path", _READ_ENDPOINTS)
def test_read_endpoint_returns_structured_json(tokenless_client, path):
    r = tokenless_client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    body = r.json()
    assert isinstance(body, dict), f"{path} did not return a JSON object"


@pytest.mark.parametrize("path", _READ_ENDPOINTS)
def test_read_endpoint_is_token_gated(secured, monkeypatch, path):
    # Every dashboard read requires auth in secured (LAN) mode. /v1/ui/prefs is the
    # one deliberately-open UI-preferences read (no principal), so it's exempt.
    if path == "/v1/ui/prefs":
        pytest.skip("ui/prefs is intentionally open (no principal)")
    client, _ = secured(monkeypatch)
    assert client.get(path).status_code == 401, f"{path} served unauthenticated"


# --------------------------------------------------------------------------- #
# Mutating endpoints must reject unauthenticated callers (fail-closed).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("method,path,body", [
    ("post", "/v1/tasks", {"request": "x", "kind": "reflection", "when_spec": "tonight"}),
    ("post", "/v1/devices/control", {"device_id": "x", "command": "on"}),
    ("post", "/v1/ui/prefs", {"prefs": {}}),
    ("post", "/v1/settings", {"values": {}}),
    ("post", "/v1/net", {"enabled": False}),
])
def test_mutating_endpoint_fails_closed(secured, monkeypatch, method, path, body):
    client, _ = secured(monkeypatch)
    r = getattr(client, method)(path, json=body)
    assert r.status_code == 401, f"{method.upper()} {path} served unauthenticated ({r.status_code})"


def test_status_endpoint_shape(tokenless_client):
    r = tokenless_client.get("/v1/status/default")
    assert r.status_code == 200
    body = r.json()
    assert {"status", "version", "model", "uptime", "user_id"} <= set(body)


def test_net_egress_respects_limit(tokenless_client):
    r = tokenless_client.get("/v1/net/egress?limit=3")
    assert r.status_code == 200 and r.json().get("ok") is True


def test_list_models_endpoint(tokenless_client):
    r = tokenless_client.get("/v1/models/installed")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and isinstance(d["models"], list)
    assert all({"path", "name", "size_gb"} <= set(m) for m in d["models"])


def test_switch_model_rejects_unknown_path(tokenless_client):
    # Unknown path is rejected WITHOUT reloading anything (a dropdown can't strand it).
    r = tokenless_client.post("/v1/model", json={"path": "models/nope-not-real.gguf"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False and d["error"] == "unknown_model"


def test_switch_model_empty_path_rejected(tokenless_client):
    r = tokenless_client.post("/v1/model", json={"path": ""})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_settings_get_shape(tokenless_client):
    r = tokenless_client.get("/v1/settings")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and isinstance(d.get("schema"), list) and d["schema"]
    assert "values" in d


def test_home_mesh_config_partial_update_preserves_other_fields(tokenless_client, monkeypatch):
    """POST /v1/home/mesh/config with only ONE field must not wipe the others.
    A full model_dump() splatted every default (node_name="", …) over the saved
    config — editing just the role blanked the node name. exclude_unset fixes it."""
    from eli.runtime import home_mesh
    home_mesh.save_config({**home_mesh.load_config(), "role": "off",
                           "node_name": "My Hub", "primary_url": "http://hub:9137"})
    try:
        r = tokenless_client.post("/v1/home/mesh/config", json={"role": "primary"})
        assert r.status_code == 200 and r.json().get("ok")
        cfg = home_mesh.load_config()
        assert cfg["role"] == "primary"
        assert cfg["node_name"] == "My Hub", "node_name must survive a role-only update"
        assert cfg["primary_url"] == "http://hub:9137", "primary_url must survive too"
    finally:
        home_mesh.save_config({**home_mesh.load_config(), "role": "off",
                               "node_name": "Eli · Home", "primary_url": ""})


# --------------------------------------------------------------------------- #
# Values in the dashboard's inline handlers cannot become script
# --------------------------------------------------------------------------- #
def test_no_inline_handler_splices_a_value_the_html_way():
    """HTML-escaping is no protection inside an onclick's JavaScript (the page decodes &#39; back
    to a quote first), and JSON.stringify inside a double-quoted attribute ends the attribute.
    Every value spliced into a handler goes through jsq()."""
    import re
    for name in ("_WEB_UI", "_MCP_PAGE"):
        page = getattr(srv, name)
        assert "/*ELI_ESCAPES*/" not in page and "function jsq(" in page, name
        handlers = re.findall(r'\bon[a-z]+="[^"]*"', page)
        assert handlers or name == "_MCP_PAGE"
        for h in handlers:
            # a choice between two fixed words ('on' / 'off') is no value from outside
            h = re.sub(r"\\''\+\(\w+\?'\w*':'\w*'\)\+'\\'", "", h)
            assert "\\''+" not in h and "+'\\'" not in h, h
            assert "JSON.stringify(" not in h, h


def test_jsq_turns_any_device_id_into_a_literal_that_cannot_end_anything():
    """Run the page's own escapes: whatever a broker names a device, the handler gets that exact
    text as a string and the attribute and the tag stay whole."""
    import json as _json
    import shutil
    import subprocess
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("no JavaScript engine on this machine to run the page's escapes")
    hostile = ["x');alert(document.domain);('", 'a" onmouseover="alert(1)', "</script><b>",
               "dev:a b&amp;c", "line\nbreak\u2028", "caf\u00e9 \U0001f600", "\\';x//", ""]
    script = srv._PAGE_ESCAPES + """
      const ids = JSON.parse(process.argv[1]); const out = [];
      for (const id of ids) {
        const lit = jsq(id);
        out.push({ok: !/["&<>\\n\\r]/.test(lit), back: eval('(function(v){return v;})(' + lit + ')'),
                  html: esc(id)});
      }
      // ASCII only on the way back: a console code page (Windows) turns U+2028 into a space
      console.log(JSON.stringify(out).replace(/[\u007f-\uffff]/g,
                  c => "\\\\u" + c.charCodeAt(0).toString(16).padStart(4, "0")));"""
    # no limit of its own: a cold node start on a CI runner (virus scan on first launch) outlasted the
    # lock wait this borrowed; the job's own time limit still stops a real hang
    res = subprocess.run([node, "-e", script, _json.dumps(hostile)], capture_output=True, text=True,
                         encoding="utf-8")
    assert res.returncode == 0, res.stderr
    for value, got in zip(hostile, _json.loads(res.stdout)):
        assert got["ok"], value          # nothing that ends the attribute or the tag
        assert got["back"] == value      # and the handler receives exactly the id
        assert "'" not in got["html"] and '"' not in got["html"]


def test_accepting_a_suggestion_automates_what_was_suggested(tokenless_client, monkeypatch):
    """The page sent every suggestion as command "on" at the hour, and the model defaulted the command
    to "on": a lamp switched off at 23:40 most nights became an automation turning it on at 23:00."""
    from types import SimpleNamespace
    added = []
    monkeypatch.setattr(srv, "_device_server", lambda: SimpleNamespace(
        add_automation=lambda **k: added.append(k) or {"ok": True}))
    body = {"device": "lamp-1", "command": "off", "value": None, "hour": 23, "minute": 40, "name": "Lamp"}
    assert tokenless_client.post("/v1/home/suggestions/accept", json=body).status_code == 200
    assert added == [{"device": "lamp-1", "command": "off", "time_str": "23:40", "value": None, "name": "Lamp"}]
    no_command = {k: v for k, v in body.items() if k != "command"}
    assert tokenless_client.post("/v1/home/suggestions/accept", json=no_command).status_code == 422
    page = tokenless_client.get("/").text
    assert "command:x.command" in page and "minute:x.minute" in page and "command:'on'" not in page


# ── every store in this module's own folder, with or without the suite conftest ─────────────────
# On the --noconftest lane (CI's API lane, where these tests run with real fastapi) nothing set the
# stores aside: they wrote the checkout's databases, world, settings and licence activation, took
# up the key every ELI product shares from ~/.config/eli-keys and wrote it back there, and read the
# owner's ~/.eli_user_id. The module keeps every store in a folder of its own (tests/_state_env.py).
from tests import _state_env


@pytest.fixture(scope="module", autouse=True)
def _every_store_in_this_modules_own_folder(tmp_path_factory):
    with _state_env.own_state(tmp_path_factory.mktemp("eli_state")) as held:
        yield held


def test_zz_no_test_here_reads_or_writes_this_machines_own_records(_every_store_in_this_modules_own_folder):
    held = _every_store_in_this_modules_own_folder
    assert not _state_env.outside(held.root)
    assert not held.opened_elsewhere, sorted(set(held.opened_elsewhere))[:20]
