"""Behaviour locks for MQTT device setup.

Reported: "the mqtt on the server is not advanced enough, and not
connecting/searching properly, and it needs to be simple to use for average
user, one click etc."

What was actually there:
  * DeviceServer.connect() returned "no MQTT broker configured (set mqtt_host)"
    whenever the user had not typed a hostname into settings by hand. That is
    a dead end for anyone who does not know what MQTT is.
  * suggest_local_hosts() existed but nothing in the codebase ever called it.
  * There was no mDNS discovery at all, despite zeroconf already being a
    pinned dependency - so a broker advertising itself on the LAN was invisible.
  * There was no single entry point that goes from nothing to working.
"""
import inspect


from eli.runtime import mqtt_setup
from eli.runtime import device_server


# ── discovery ──────────────────────────────────────────────────────────────


def test_mdns_discovery_never_raises_without_zeroconf(monkeypatch):
    """Discovery is best-effort: a missing or broken zeroconf must degrade to
    'found nothing', never take the setup flow down with it."""
    import builtins
    real_import = builtins.__import__

    def _no_zeroconf(name, *a, **k):
        if name == "zeroconf":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_zeroconf)
    assert mqtt_setup.discover_brokers_mdns(timeout=0.1) == []


def test_autodetect_probes_rather_than_assuming(monkeypatch):
    """An open port is not a broker. A candidate only counts once a broker has
    actually accepted a connection."""
    monkeypatch.setattr(mqtt_setup, "discover_brokers_mdns", lambda timeout=2.0: [])
    probed = []

    def _probe(*, host, port=1883, username="", password="", tls=False, timeout=5.0):
        probed.append(host)
        return {"ok": host == "mqtt.local", "host": host, "port": port}

    monkeypatch.setattr(mqtt_setup, "probe_broker_connection", _probe)
    res = mqtt_setup.autodetect_broker(timeout=0.1)
    assert res["ok"] is True
    assert res["broker"]["host"] == "mqtt.local"
    assert probed, "nothing was probed"


def test_autodetect_prefers_mdns_over_guesswork(monkeypatch):
    monkeypatch.setattr(mqtt_setup, "discover_brokers_mdns",
                        lambda timeout=2.0: [{"host": "10.0.0.5", "port": 1883,
                                              "name": "hub", "tls": False,
                                              "source": "mdns"}])
    monkeypatch.setattr(mqtt_setup, "probe_broker_connection",
                        lambda **kw: {"ok": True, "host": kw["host"], "port": kw["port"]})
    res = mqtt_setup.autodetect_broker(timeout=0.1)
    assert res["broker"]["host"] == "10.0.0.5"
    assert res["broker"]["source"] == "mdns"


def test_autodetect_reports_what_it_tried(monkeypatch):
    """A silent 'not found' gives the user nothing to act on."""
    monkeypatch.setattr(mqtt_setup, "discover_brokers_mdns", lambda timeout=2.0: [])
    monkeypatch.setattr(mqtt_setup, "probe_broker_connection",
                        lambda **kw: {"ok": False, "error": "refused"})
    res = mqtt_setup.autodetect_broker(timeout=0.1)
    assert res["ok"] is False
    assert res["tried"], "no record of what was attempted"
    assert all("host" in t for t in res["tried"])


# ── one-click ──────────────────────────────────────────────────────────────


def test_one_click_without_a_broker_returns_the_install_step(monkeypatch):
    """When there is genuinely no broker, 'install one' IS the single next
    step, so the guide has to come back with the failure."""
    monkeypatch.setattr(mqtt_setup, "autodetect_broker",
                        lambda timeout=2.0: {"ok": False, "error": "none", "tried": []})
    res = mqtt_setup.one_click_setup(timeout=0.1)
    assert res["ok"] is False
    assert res["found"] is False
    assert res.get("guide"), "no install guide offered"
    assert "broker" in str(res.get("content", "")).lower()


def test_one_click_explains_mqtt_in_plain_language(monkeypatch):
    """The audience is someone who does not know the word 'broker'."""
    monkeypatch.setattr(mqtt_setup, "autodetect_broker",
                        lambda timeout=2.0: {"ok": False, "error": "none", "tried": []})
    text = str(mqtt_setup.one_click_setup(timeout=0.1).get("content", ""))
    assert "smart devices" in text.lower(), "no plain-language explanation"


# ── connect() must configure itself ────────────────────────────────────────


def test_connect_autodetect_can_be_turned_off():
    """Callers that manage configuration themselves must be able to opt out."""
    sig = inspect.signature(device_server.DeviceServer.connect)
    assert "autodetect" in sig.parameters
    assert sig.parameters["autodetect"].default is True


def test_the_broker_search_runs_the_one_mdns_sweep(monkeypatch):
    """It had its own copy of the zeroconf sweep beside the device discovery's."""
    from eli.runtime import device_server
    asked = {}

    def sweep(timeout, found, errors, *, services=None, resolve_ms=None):
        asked.update(timeout=timeout, services=tuple(services), resolve_ms=resolve_ms)
        found.extend([
            {"name": "broker-one.extra", "service": "_mqtt._tcp.local.", "host": "192.0.2.10", "port": 1883},
            {"name": "broker-one.extra", "service": "_mqtt._tcp.local.", "host": "192.0.2.10", "port": 1883},
            {"name": "broker-two", "service": "_secure-mqtt._tcp.local.", "host": "192.0.2.11", "port": 8883},
            {"name": "no-address", "service": "_mqtt._tcp.local.", "host": "", "port": 1883},
            {"name": "no-port", "service": "_mqtt._tcp.local.", "host": "192.0.2.12", "port": None},
        ])

    monkeypatch.setattr(device_server, "_mdns_discover", sweep)
    assert mqtt_setup.discover_brokers_mdns(timeout=2.5) == [
        {"host": "192.0.2.10", "port": 1883, "name": "broker-one", "tls": False, "source": "mdns"},
        {"host": "192.0.2.11", "port": 8883, "name": "broker-two", "tls": True, "source": "mdns"},
        {"host": "192.0.2.12", "port": 1883, "name": "no-port", "tls": False, "source": "mdns"},
    ]
    assert asked == {"timeout": 2.5, "resolve_ms": 2500,
                     "services": ("_mqtt._tcp.local.", "_secure-mqtt._tcp.local.")}
