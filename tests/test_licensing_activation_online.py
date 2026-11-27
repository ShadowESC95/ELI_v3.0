"""Licensing never goes online: a licence covers a number of computers, each with its own key made by
the seller from that computer's activation code. A licence entered as if it were a key says how to
get this computer's key; deactivating removes the key here; nothing is ever sent anywhere."""
from __future__ import annotations

import json
import socket

import pytest

from eli.licensing import activation as act
from eli.licensing._sdk._encoding import b64url_encode
from eli.licensing._sdk.issuing import activated_body, licence_body, load_private_key, sign_key
from eli.licensing._sdk.keys import read_key
from eli.licensing.machine_id import machine_fingerprint
from eli.licensing.signature import parse_license_key
from eli.licensing.storage import load_activation_state
from tests.test_licensing_activation import signing_keys  # noqa: F401  (the fixture)


def _licence(keys, product="eli", seats=1):
    """The licence itself (format 3): what the seller keeps, for no computer yet."""
    return sign_key(licence_body(product, "pro", seats=seats, activate=True, license_id="K-2026-000001"),
                    keys["private"].read_bytes(), public_key=keys["public"].read_bytes())


def _computer_key(keys, machine, product="eli"):
    """This computer's key, as the seller makes it from the activation code."""
    lic = read_key(_licence(keys, product), keys["public"].read_bytes())
    body = activated_body(lic, machine, "H-0001")
    body.pop("activation")
    return sign_key(body, keys["private"].read_bytes(), public_key=keys["public"].read_bytes())


@pytest.fixture()
def no_network(monkeypatch):
    """Any attempt to open a connection fails the test."""
    def refuse(*_a, **_kw):
        raise AssertionError("licensing tried to go online")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_a_licence_says_how_to_get_this_computers_key(signing_keys, no_network):  # noqa: F811
    with pytest.raises(ValueError) as err:
        act.activate_license_key(_licence(signing_keys))
    assert "not this computer's key" in str(err.value) and act.activation_code() in str(err.value)
    assert load_activation_state() is None


def test_this_computers_key_activates_with_nothing_sent(signing_keys, no_network):  # noqa: F811
    state = act.activate_license_key(_computer_key(signing_keys, machine_fingerprint()))
    kept = parse_license_key(state.license_key)
    assert (kept.version, kept.machine, kept.license_id) == (1, machine_fingerprint(), "K-2026-000001")
    assert act.validate_activation().active


def test_another_computers_key_does_not(signing_keys, no_network):  # noqa: F811
    with pytest.raises(ValueError, match="another machine"):
        act.activate_license_key(_computer_key(signing_keys, "f" * 64))


def test_deactivating_removes_the_key_here_with_nothing_sent(signing_keys, no_network):  # noqa: F811
    act.activate_license_key(_computer_key(signing_keys, machine_fingerprint()))
    assert act.deactivate_license() and load_activation_state() is None


def test_a_licence_in_the_environment_is_not_a_licence(signing_keys, monkeypatch, no_network):  # noqa: F811
    monkeypatch.setenv("ELI_LICENSE_KEY", _licence(signing_keys))
    status = act.validate_activation()
    assert not status.active and "not this computer's key" in status.message


def test_a_licence_in_the_shared_folder_is_never_taken_up(signing_keys, no_network):  # noqa: F811
    from eli.licensing.storage import shared_keys_dir

    folder = shared_keys_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "eli.key").write_text(_licence(signing_keys) + "\n")
    assert act._adopt_shared_key("eli") is None


def test_a_licence_naming_a_computer_is_refused(signing_keys):  # noqa: F811
    body = {**licence_body("eli", "pro", activate=True), "machine": "a" * 64}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    signature = load_private_key(signing_keys["private"].read_bytes()).sign(raw)
    with pytest.raises(ValueError, match="names no computer"):
        parse_license_key(f"ELIV3-{b64url_encode(raw)}.{b64url_encode(signature)}")


def test_the_purchase_link_is_the_website():
    from eli.licensing.launch_gate import PURCHASE_URL

    assert PURCHASE_URL == "https://geteli.tech"
