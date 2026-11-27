"""Keys bought to be activated: ELI v3 activates them with the licence store once and keeps the key
the store makes for this computer; a full licence and no internet are explained to the buyer;
deactivating gives the seat back before the key is forgotten. The store is stood in for: nothing
here goes online."""
from __future__ import annotations

import pytest

from eli.licensing import activation as act
from eli.licensing._sdk import store as sdk_store
from eli.licensing._sdk.issuing import activated_body, licence_body, sign_key
from eli.licensing._sdk.keys import read_key
from eli.licensing.machine_id import machine_fingerprint
from eli.licensing.signature import parse_license_key
from eli.licensing.storage import load_activation_state
from tests.test_licensing_activation import signing_keys  # noqa: F401  (the fixture)


def _bought(keys, product="eli", seats=1, **fields):
    return sign_key(licence_body(product, "pro", seats=seats, activate=True, license_id="L-2026-000001", **fields),
                    keys["private"].read_bytes(), public_key=keys["public"].read_bytes())


@pytest.fixture()
def store(signing_keys, monkeypatch):  # noqa: F811
    """A licence store that counts computers as the real one does, signing with the test key."""
    seats: dict[str, set[str]] = {}
    calls = {"activate": 0, "deactivate": 0, "online": True}

    def activate(key, machine, **_kw):
        calls["activate"] += 1
        if not calls["online"]:
            raise sdk_store.StoreError("the store could not be reached", code="offline")
        lic = read_key(key, signing_keys["public"].read_bytes())
        on = seats.setdefault(lic.license_id, set())
        if machine not in on and len(on) >= lic.seats:
            raise sdk_store.StoreError("every seat is in use", code="seats_full",
                                       detail={"seats": lic.seats, "used": len(on)})
        on.add(machine)
        return sign_key(activated_body(lic, machine, "A-0001"), signing_keys["private"].read_bytes(),
                        public_key=signing_keys["public"].read_bytes())

    def deactivate(key, **_kw):
        calls["deactivate"] += 1
        if not calls["online"]:
            raise sdk_store.StoreError("the store could not be reached", code="offline")
        lic = read_key(key, signing_keys["public"].read_bytes())
        seats.get(lic.license_id, set()).discard(lic.machine)
        return {"used": 0}

    monkeypatch.setattr(sdk_store, "activate", activate)
    monkeypatch.setattr(sdk_store, "deactivate", deactivate)
    return {"keys": signing_keys, "seats": seats, "calls": calls}


def test_a_bought_key_is_read_and_opens_nothing_by_itself(signing_keys):  # noqa: F811
    payload = parse_license_key(_bought(signing_keys))
    assert payload.needs_activation and payload.version == 3 and not payload.machine


def test_activating_keeps_this_computers_own_key(store):
    state = act.activate_license_key(_bought(store["keys"]))
    kept = parse_license_key(state.license_key)
    assert (kept.version, kept.machine, kept.activation) == (1, machine_fingerprint(), "A-0001")
    assert act.validate_activation().active
    assert store["seats"]["L-2026-000001"] == {machine_fingerprint()}
    act.activate_license_key(_bought(store["keys"]))  # entering it again uses no other seat
    assert len(store["seats"]["L-2026-000001"]) == 1


def test_a_full_licence_is_explained(store):
    store["seats"]["L-2026-000001"] = {"f" * 64}  # its one seat is in use elsewhere
    with pytest.raises(ValueError) as err:
        act.activate_license_key(_bought(store["keys"]))
    assert "already in use on 1 of its 1 computer" in str(err.value) and "/activations" in str(err.value)
    assert load_activation_state() is None


def test_no_internet_says_how_to_activate_from_another_device(store):
    store["calls"]["online"] = False
    with pytest.raises(ValueError) as err:
        act.activate_license_key(_bought(store["keys"]))
    assert "/activate" in str(err.value) and act.activation_code() in str(err.value)


def test_deactivating_frees_the_seat_before_the_key_goes(store):
    act.activate_license_key(_bought(store["keys"]))
    store["calls"]["online"] = False
    with pytest.raises(ValueError, match="could not be reached"):
        act.deactivate_license()
    assert load_activation_state() is not None  # kept: its seat is still taken
    store["calls"]["online"] = True
    assert act.deactivate_license() and load_activation_state() is None
    assert store["seats"]["L-2026-000001"] == set()


def test_a_bought_key_in_the_environment_is_not_a_licence(store, monkeypatch):
    monkeypatch.setenv("ELI_LICENSE_KEY", _bought(store["keys"]))
    status = act.validate_activation()
    assert not status.active and "not been activated on this computer" in status.message


def test_a_bought_key_naming_a_computer_is_refused(signing_keys):  # noqa: F811
    body = {**licence_body("eli", "pro", activate=True), "machine": "a" * 64}
    import json

    from eli.licensing._sdk._encoding import b64url_encode
    from eli.licensing._sdk.issuing import load_private_key

    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    key = f"ELIV3-{b64url_encode(raw)}.{b64url_encode(load_private_key(signing_keys['private'].read_bytes()).sign(raw))}"
    with pytest.raises(ValueError, match="names no computer"):
        parse_license_key(key)


def test_a_bought_key_in_the_shared_folder_is_never_activated_in_passing(store, tmp_path):
    from eli.licensing.storage import shared_keys_dir

    folder = shared_keys_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "eli.key").write_text(_bought(store["keys"]) + "\n")
    assert act._adopt_shared_key("eli") is None and store["calls"]["activate"] == 0
