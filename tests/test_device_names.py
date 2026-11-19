"""Tests for persistent custom device names."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eli.runtime import device_names as dn


def test_sink_key_uses_mac():
    sid = "bluez_output.XX_B9_08_A1_EF_CD_23.1"
    assert dn.sink_key(sid) == "sink:B9:08:A1:EF:CD:23"
    assert dn.mac_from_sink_id(sid) == "B9:08:A1:EF:CD:23"


def test_bt_and_registry_keys():
    assert dn.bt_key("b9:08:a1:ef:cd:23") == "bt:B9:08:A1:EF:CD:23"
    assert dn.registry_key("living_room_light") == "dev:living_room_light"


def test_save_and_load_custom_name(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"device_custom_names": {}}), encoding="utf-8")

    def _load():
        return json.loads(settings_file.read_text(encoding="utf-8"))

    def _save(data):
        settings_file.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr("eli.core.runtime_settings.load_settings", _load)
    monkeypatch.setattr("eli.core.runtime_settings.save_settings", _save)

    res = dn.save_custom_name("bt:B9:08:A1:EF:CD:23", "Reflex")
    assert res["ok"] is True
    names = dn.load_custom_names()
    assert names["bt:B9:08:A1:EF:CD:23"] == "Reflex"

    row = {"name": "Reflex-Pro", "host": "B9:08:A1:EF:CD:23"}
    dn.apply_name(row, "bt:B9:08:A1:EF:CD:23", "Reflex-Pro")
    assert row["display_name"] == "Reflex"
    assert "reflex" in row["voice_names"]

    match = dn.match_bluetooth_name("reflex", [{"name": "Reflex-Pro", "host": "B9:08:A1:EF:CD:23"}])
    assert match is not None

    dn.save_custom_name("bt:B9:08:A1:EF:CD:23", "")
    assert "bt:B9:08:A1:EF:CD:23" not in dn.load_custom_names()


def test_a_name_is_saved_without_the_rest_of_the_settings_view(monkeypatch):
    """The whole load_settings() view handed back wrote every default and one-session override into
    the file; only the names change here."""
    view = {"device_custom_names": {}, "temperature": 0.7, "n_ctx": 2048, "audio_output_aliases": {}}
    saved = []
    monkeypatch.setattr("eli.core.runtime_settings.load_settings", lambda: dict(view))
    monkeypatch.setattr("eli.core.runtime_settings.save_settings", lambda d: saved.append(dict(d)))
    assert dn.save_custom_name("sink:B9:08:A1:EF:CD:23", "Desk speaker")["ok"] is True
    assert saved and set(saved[0]) <= {"device_custom_names", "audio_output_aliases"}
    assert saved[0]["device_custom_names"] == {"sink:B9:08:A1:EF:CD:23": "Desk speaker"}


def test_one_list_of_kind_words_and_one_token_builder_for_every_device():
    """The Bluetooth resolver and the name matcher each kept a list of kind words ("soundbar" and
    "airpods" in one only, "music" in the other only), and audio outputs had a copy of the token builder."""
    from eli.runtime import device_server, local_connectivity
    for word in ("headphones", "headset", "earbuds", "earphones", "buds", "airpods", "speaker", "speakers",
                 "soundbar", "bluetooth", "device", "it", "music"):
        assert word in dn.GENERIC_DEVICE_WORDS, word
    assert not hasattr(local_connectivity, "_voice_names_for_sink")
    assert "_GENERIC = {" not in __import__("inspect").getsource(device_server.bluetooth_control_by_name)
    # a kind word inside a longer name is not what picks the device
    devices = [{"name": "Lounge Soundbar", "host": "AA:BB:CC:00:00:01"},
               {"name": "Kitchen JBL", "host": "AA:BB:CC:00:00:02"}]
    assert dn.match_bluetooth_name("kitchen soundbar", devices)["name"] == "Kitchen JBL"
    assert dn.match_bluetooth_name("soundbar", devices) is None      # said alone: the caller's nearest-device rule
    # the tokens an output answers to: its name, the words of it, its number
    assert dn._voice_tokens("Desk speaker", "alsa_output.usb", 2) == [
        "desk speaker", "desk", "speaker", "alsa_output.usb", "device 2", "device2"]
    assert dn._voice_tokens("", "alsa_output.usb", 1) == ["alsa_output.usb", "device 1", "device1"]
