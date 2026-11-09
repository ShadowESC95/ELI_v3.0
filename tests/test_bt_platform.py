"""Cross-platform Bluetooth platform helpers."""
import sys

import pytest

import eli.runtime.bt_platform as bp


def test_platform_kind_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert bp.platform_kind() == "linux"


def test_valid_mac_rejects_zero():
    assert not bp._valid_mac("00:00:00:00:00:00")
    assert bp._valid_mac("AA:BB:CC:DD:EE:FF")


def test_recovery_hint_linux_dynamic(monkeypatch):
    monkeypatch.setattr(bp, "platform_kind", lambda: "linux")
    monkeypatch.setattr(bp, "list_adapters", lambda: [
        bp.BtAdapter(id="hci0", state="down", source="kernel"),
        bp.BtAdapter(id="hci2", state="down", source="kernel"),
    ])
    hint = bp.recovery_hint()
    assert "hci0" in hint and "hci2" in hint
    assert "hciconfig" in hint


def test_recovery_hint_macos(monkeypatch):
    monkeypatch.setattr(bp, "platform_kind", lambda: "darwin")
    monkeypatch.setattr(bp.shutil, "which", lambda t: None)
    assert "System Settings" in bp.recovery_hint([])


def test_recovery_hint_windows(monkeypatch):
    monkeypatch.setattr(bp, "platform_kind", lambda: "windows")
    assert "Settings" in bp.recovery_hint([])


def test_linux_pick_controller_prefers_valid_mac():
    adapters = [bp.BtAdapter(id="hci0", address="AA:BB:CC:DD:EE:FF", bus="usb", bluez=True)]
    pick = bp._linux_pick_controller(["00:00:00:00:00:00", "AA:BB:CC:DD:EE:FF"], adapters)
    assert pick == "AA:BB:CC:DD:EE:FF"


def test_linux_default_hci_id_uses_busctl_not_hardcoded(monkeypatch):
    monkeypatch.setattr(bp, "_linux_bluez_default_controller_mac", lambda: "AA:BB:CC:DD:EE:FF")
    monkeypatch.setattr(bp, "_linux_hci_for_controller_mac", lambda m: "hci2" if m == "AA:BB:CC:DD:EE:FF" else "")
    assert bp._linux_default_hci_id() == "hci2"


def test_linux_device_dbus_path_prefers_active_hci(monkeypatch):
    tree = "└─ /org/bluez/hci2/dev_AA_BB_CC_DD_EE_FF\n"
    monkeypatch.setattr(bp, "_sh", lambda args, timeout=10: (0, tree))
    monkeypatch.setattr(bp, "_linux_default_hci_id", lambda: "hci2")
    path = bp._linux_device_dbus_path("AA:BB:CC:DD:EE:FF")
    assert path == "/org/bluez/hci2/dev_AA_BB_CC_DD_EE_FF"


def test_linux_hci_down_skips_zero_mac_ghosts(monkeypatch):
    monkeypatch.setattr(bp, "_linux_kernel_adapters", lambda: [
        bp.BtAdapter(id="hci1", address="00:00:00:00:00:00", state="down", source="kernel"),
        bp.BtAdapter(id="hci2", address="AA:BB:CC:DD:EE:FF", state="down", source="kernel"),
    ])
    assert bp._linux_hci_down() == ["hci2"]


def test_ensure_radio_delegates_by_platform(monkeypatch):
    monkeypatch.setattr(bp, "platform_kind", lambda: "darwin")
    monkeypatch.setattr(bp, "_darwin_ensure_radio", lambda: (True, "default"))
    ok, msg = bp.ensure_radio()
    assert ok and msg == "default"


def test_an_adapter_with_no_address_file_is_read_without_a_traceback(monkeypatch, tmp_path, caplog):
    """Linux 7 gives hci devices no `address` file. Each look at the adapters logged
    FileNotFoundError with its traceback, twenty times in one short session."""
    import logging

    from eli.runtime import bt_platform as bt

    (tmp_path / "hci0").mkdir()
    monkeypatch.setattr(bt.glob, "glob", lambda pattern: [str(tmp_path / "hci0")])
    monkeypatch.setattr(bt.shutil, "which", lambda name: "/usr/bin/hciconfig" if name == "hciconfig" else None)
    monkeypatch.setattr(bt, "_sh", lambda cmd, timeout=4: (0, "hci0:\tType: Primary  Bus: USB\n"
                                                             "\tBD Address: AA:BB:CC:DD:EE:FF  ACL MTU: 1021:8\n\tUP RUNNING\n"))
    with caplog.at_level(logging.DEBUG):
        found = bt._linux_kernel_adapters()
    assert [(a.id, a.address, a.state) for a in found] == [("hci0", "AA:BB:CC:DD:EE:FF", "up")]
    assert not [r for r in caplog.records if r.exc_info], "nothing went wrong: nothing to trace"


# ── Windows: an address is a value, never part of the script; success is the status Windows gives ──

def _windows(monkeypatch):
    ran = []
    monkeypatch.setattr(bp, "platform_kind", lambda: "windows")
    monkeypatch.setattr(bp.shutil, "which", lambda name: f"C:/Windows/{name}.exe" if name == "powershell" else None)

    def fake_sh(args, timeout=25.0, env=None):
        ran.append((args, dict(env or {})))
        return 0, fake_sh.answer
    fake_sh.answer = "STATUS|Paired"
    monkeypatch.setattr(bp, "_sh", fake_sh)
    return ran, fake_sh


@pytest.mark.parametrize("bad", ["00') ; Start-Process notepad ; ('", "AA:BB:CC:DD:EE:FF'; calc; '", "not-a-mac"])
def test_an_address_that_is_not_one_never_reaches_powershell(monkeypatch, bad):
    """A quote in the address closed the script's string and ran what followed as the ELI user."""
    ran, _ = _windows(monkeypatch)
    ok, said = bp.windows_bt_action(bad, "pair")
    assert ok is False and "not a Bluetooth address" in said
    assert bp.device_known(bad) is False and bp.device_info(bad).get("name", "") == ""
    assert ran == [], "PowerShell ran for an address that is not one"


def test_the_address_goes_to_powershell_as_a_value(monkeypatch):
    ran, _ = _windows(monkeypatch)
    ok, _said = bp.windows_bt_action("aa:bb:cc:dd:ee:ff", "pair")
    assert ok is True
    (args, env), = ran
    assert env["ELI_BT_ADDR"] == "AABBCCDDEEFF"
    assert "AABBCCDDEEFF" not in args[-1], "the address was pasted into the script"
    assert args[0].endswith("powershell.exe"), "WinRT needs Windows PowerShell"


@pytest.mark.parametrize("status,ok", [("Paired", True), ("AlreadyPaired", True), ("RejectedByHandler", False),
                                       ("AuthenticationTimeout", False), ("PairingCanceled", False)])
def test_pairing_is_judged_by_the_status_windows_reports(monkeypatch, status, ok):
    _, fake = _windows(monkeypatch)
    fake.answer = f"STATUS|{status}"
    assert bp.windows_bt_action("AA:BB:CC:DD:EE:FF", "pair")[0] is ok


@pytest.mark.parametrize("action", ["connect", "disconnect", "trust"])
def test_what_windows_cannot_do_is_refused_not_reported_done(monkeypatch, action):
    """'Disconnect' printed the word 'Disconnected' and ELI said it had disconnected the headset."""
    ran, _ = _windows(monkeypatch)
    ok, said = bp.windows_bt_action("AA:BB:CC:DD:EE:FF", action)
    assert ok is False and "not supported on Windows" in said and ran == []


def test_the_radio_snapshot_changes_nothing(monkeypatch):
    ran = []
    monkeypatch.setattr(bp, "platform_kind", lambda: "linux")
    monkeypatch.setattr(bp, "list_adapters", lambda: [bp.BtAdapter(id="hci0", state="down", source="kernel")])
    monkeypatch.setattr(bp.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(bp, "_sh", lambda args, timeout=25.0, env=None: ran.append(args) or (0, "Powered: no"))
    status = bp.radio_status()
    assert status["powered"] is False
    assert all(a[:2] in (["bluetoothctl", "show"], ["bluetoothctl", "list"]) for a in ran), ran
