"""Trial lifecycle, stable machine identity, and entry-point flag reachability."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eli.licensing import trial as T
from eli.licensing.activation import activate_license_key, validate_activation
from eli.licensing.signature import issue_license_key
from eli.licensing.storage import (
    activation_state_path,
    load_activation_state,
    save_activation_state,
)
from eli.licensing.types import LICENSE_VERSION, ActivationState, LicensePayload, MachineBinding


def _clear_caches() -> None:
    """Path and fingerprint helpers are lru_cached; env changes need a reset."""
    from eli.core import paths
    from eli.licensing import machine_id

    for fn in (
        paths.data_dir,
        paths.config_dir,
        paths.cache_dir,
        machine_id.machine_fingerprint,
        machine_id.legacy_machine_fingerprints,
    ):
        try:
            fn.cache_clear()
        except AttributeError:
            pass


@pytest.fixture()
def retail_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated retail build: own license dir, own data dir (trial markers)."""
    lic = tmp_path / "licensing"
    data = tmp_path / "artifacts"
    lic.mkdir()
    data.mkdir()
    monkeypatch.setenv("ELI_LICENSE_DIR", str(lic))
    monkeypatch.setenv("ELI_DATA_DIR", str(data))
    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(data))
    monkeypatch.setenv("ELI_REQUIRE_LICENSE", "1")
    monkeypatch.setenv("ELI_NO_GUI_PROMPT", "1")
    for name in ("ELI_LICENSE_BYPASS", "ELI_LICENSE_KEY", "ELI_TRIAL_DAYS", "ELI_TRIAL_DISABLED"):
        monkeypatch.delenv(name, raising=False)
    _clear_caches()
    yield {"license_dir": lic, "data_dir": data, "tmp": tmp_path}
    _clear_caches()


@pytest.fixture()
def signing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    priv = tmp_path / "signing.pem"
    pub = tmp_path / "signing.pub"
    priv.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    pub.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv("ELI_LICENSE_PUBLIC_KEY", str(pub))
    monkeypatch.setenv("ELI_LICENSE_SIGNING_KEY", str(priv))
    return priv.read_bytes()


def _paid_key(private_pem: bytes, *, tier: str = "personal", seats: int = 1) -> str:
    return issue_license_key(
        LicensePayload(
            version=LICENSE_VERSION,
            license_id=f"paid-{uuid.uuid4().hex[:8]}",
            tier=tier,
            seats=seats,
            issued_at="2026-07-30T00:00:00+00:00",
        ),
        private_pem=private_pem,
    )


def _backdate_trial(days_past: int = 1) -> None:
    """Expire the trial by rewriting the marker with a valid signature."""
    rec = T.load_trial_record()
    assert rec is not None
    end = datetime.now(timezone.utc) - timedelta(days=days_past)
    rec.started_at = (end - timedelta(days=7)).replace(microsecond=0).isoformat()
    rec.expires_at = end.replace(microsecond=0).isoformat()
    T.save_trial_record(rec)


# ── Stable machine identity ─────────────────────────────────────────────────

def test_fingerprint_ignores_nic_changes(retail_env, monkeypatch: pytest.MonkeyPatch):
    """The v1 scheme moved with uuid.getnode(); v2 must not."""
    from eli.licensing import machine_id

    machine_id.machine_fingerprint.cache_clear()
    monkeypatch.setattr(uuid, "getnode", lambda: 0x001122334455)
    first = machine_id.machine_fingerprint()

    machine_id.machine_fingerprint.cache_clear()
    monkeypatch.setattr(uuid, "getnode", lambda: 0xAABBCCDDEEFF)
    second = machine_id.machine_fingerprint()

    assert first == second


def test_fingerprint_ignores_hostname_changes(retail_env, monkeypatch: pytest.MonkeyPatch):
    import platform

    from eli.licensing import machine_id

    machine_id.machine_fingerprint.cache_clear()
    monkeypatch.setattr(platform, "node", lambda: "before-rename")
    first = machine_id.machine_fingerprint()

    machine_id.machine_fingerprint.cache_clear()
    monkeypatch.setattr(platform, "node", lambda: "after-rename")
    assert machine_id.machine_fingerprint() == first


def test_legacy_binding_is_migrated_not_rejected(retail_env, signing):
    """Pre-stabilisation activations must keep working and be rewritten."""
    from eli.licensing.machine_id import legacy_machine_fingerprints, machine_fingerprint

    legacy = legacy_machine_fingerprints()
    if not legacy:
        pytest.skip("no legacy fingerprint derivable on this host")

    from eli.licensing.signature import parse_license_key

    key = _paid_key(signing, tier="pro", seats=1)
    state = ActivationState(
        version=1,
        activated_at="2026-07-01T00:00:00+00:00",
        license_id=parse_license_key(key).license_id,
        tier="pro",
        seats=1,
        expires_at=None,
        key_fingerprint="deadbeef",
        machines=[
            MachineBinding(id_hash=legacy[0], label="old-host", bound_at="2026-07-01T00:00:00+00:00")
        ],
        source="offline",
        license_key=key,
    )
    save_activation_state(state)

    status = validate_activation()
    assert status.active is True, status.message

    migrated = load_activation_state()
    assert migrated is not None
    assert [m.id_hash for m in migrated.machines] == [machine_fingerprint()]


# ── Trial grant / uniqueness ────────────────────────────────────────────────

def test_trial_duration_defaults_to_seven_days(retail_env):
    assert T.trial_duration_days() == 7


def test_trial_granted_once_with_unique_identity(retail_env):
    assert T.trial_state() == "available"

    state = T.grant_trial()
    assert state is not None
    assert state.tier == "trial"
    assert state.seats == 1
    assert state.source == T.TRIAL_SOURCE
    assert state.license_id.startswith("trial-")
    assert (state.customer_ref or "").startswith("ELI-TRIAL-")

    assert T.trial_state() == "active"
    assert T.trial_days_remaining() == 7
    assert validate_activation().active is True

    # Second call must not mint another trial.
    assert T.grant_trial() is None


def test_two_installs_get_different_trial_identities(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    seen = set()
    for n in range(2):
        lic = tmp_path / f"lic{n}"
        data = tmp_path / f"data{n}"
        lic.mkdir()
        data.mkdir()
        monkeypatch.setenv("ELI_LICENSE_DIR", str(lic))
        monkeypatch.setenv("ELI_DATA_DIR", str(data))
        monkeypatch.setenv("ELI_REQUIRE_LICENSE", "1")
        _clear_caches()
        state = T.grant_trial()
        assert state is not None
        seen.add((state.license_id, state.customer_ref))
    _clear_caches()
    assert len(seen) == 2


# ── Anti-reset behaviour ────────────────────────────────────────────────────

def test_deleting_activation_state_does_not_restart_trial(retail_env):
    first = T.grant_trial()
    assert first is not None
    _backdate_trial(days_past=0)  # normalise, still valid
    original = T.load_trial_record()
    assert original is not None

    activation_state_path().unlink()
    assert T.trial_state() == "active"

    restored = T.ensure_trial_activation()
    assert restored is not None
    assert restored.license_id == first.license_id
    after = T.load_trial_record()
    assert after is not None
    assert after.expires_at == original.expires_at


def test_deleting_one_marker_does_not_restart_trial(retail_env):
    T.grant_trial()
    markers = T.marker_paths()
    assert len(markers) >= 2, "need two independent markers for this guarantee"
    markers[0].unlink()
    assert T.trial_state() == "active"
    assert T.grant_trial() is None


def test_forged_activation_expiry_does_not_extend_trial(retail_env):
    T.grant_trial()
    _backdate_trial()
    assert validate_activation().active is False

    state = load_activation_state()
    assert state is not None
    state.expires_at = "2099-01-01T00:00:00+00:00"
    save_activation_state(state)

    status = validate_activation()
    assert status.active is False
    assert status.message == "trial expired"


def test_forged_marker_signature_is_rejected(retail_env):
    T.grant_trial()
    for path in T.marker_paths():
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["expires_at"] = "2099-01-01T00:00:00+00:00"
        path.write_text(json.dumps(raw), encoding="utf-8")
    # Every marker now fails its HMAC, so none is trusted: the trial is not live.
    assert T.load_trial_record() is None
    assert validate_activation().active is False


def test_clock_rollback_does_not_reopen_trial(retail_env, monkeypatch: pytest.MonkeyPatch):
    T.grant_trial()
    rec = T.load_trial_record()
    assert rec is not None
    # Marker start date sits far in the future relative to "now".
    future = datetime.now(timezone.utc) + timedelta(days=400)
    rec.started_at = future.replace(microsecond=0).isoformat()
    rec.expires_at = (future + timedelta(days=7)).replace(microsecond=0).isoformat()
    T.save_trial_record(rec)
    assert T.trial_state() == "expired"


# ── Expiry -> revocation -> purchase ───────────────────────────────────────

def test_expired_trial_is_revoked_permanently(retail_env):
    T.grant_trial()
    _backdate_trial()

    status = validate_activation()
    assert status.active is False
    assert status.trial == "expired"

    assert T.revoke_trial() is True
    assert T.trial_state() == "revoked"
    assert activation_state_path().is_file() is False

    # Revocation is terminal: no new trial on this machine.
    assert T.grant_trial() is None
    assert T.ensure_trial_activation() is None
    assert validate_activation().active is False


def test_gate_grants_then_revokes_and_demands_purchase(retail_env):
    from eli.licensing.activation import LicenseRequiredError
    from eli.licensing.launch_gate import ensure_license_or_exit

    # First launch: trial granted, app runs.
    ensure_license_or_exit(allow_gui_prompt=False)
    assert T.trial_state() == "active"

    # Trial runs out: gate revokes it and refuses to launch.
    _backdate_trial()
    with pytest.raises(LicenseRequiredError) as excinfo:
        ensure_license_or_exit(allow_gui_prompt=False)
    assert "trial has ended" in str(excinfo.value)
    assert T.trial_state() == "revoked"


def test_revoked_trial_does_not_block_paid_key(retail_env, signing):
    T.grant_trial()
    _backdate_trial()
    T.revoke_trial()

    activate_license_key(_paid_key(signing))
    status = validate_activation()
    assert status.active is True
    assert status.tier == "personal"


def test_paid_key_supersedes_running_trial_without_force(retail_env, signing):
    T.grant_trial()
    # No force_rebind: buying mid-trial must simply work.
    state = activate_license_key(_paid_key(signing))
    assert state.tier == "personal"
    assert state.source == "offline"
    assert validate_activation().active is True


def test_paid_activation_is_not_clobbered_by_trial_restore(retail_env, signing):
    T.grant_trial()
    activate_license_key(_paid_key(signing))
    assert T.ensure_trial_activation() is None
    state = load_activation_state()
    assert state is not None
    assert state.tier == "personal"


def test_force_rebind_repairs_a_one_seat_stale_binding(retail_env, signing):
    """The documented repair must work when re-activating the *same* license.

    A binding left by a vanished NIC cannot be attributed to this host, so on a
    one-seat license the seat is spent and the holder is locked out. --force has
    to clear it even though the license id is unchanged.
    """
    key = _paid_key(signing, tier="personal", seats=1)
    state = activate_license_key(key)

    state.machines = [
        MachineBinding(id_hash="de" * 32, label="vanished-nic", bound_at="2026-07-01T00:00:00+00:00")
    ]
    save_activation_state(state)
    assert validate_activation().active is False

    with pytest.raises(ValueError, match="seat limit"):
        activate_license_key(key)

    repaired = activate_license_key(key, force_rebind=True)
    assert len(repaired.machines) == 1
    assert validate_activation().active is True


# ── activation.json is not signed: what it says is not a licence ────────────

def _rewrite_activation(**changes) -> None:
    path = activation_state_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    for name, value in changes.items():
        if value is _DROP:
            raw.pop(name, None)
        else:
            raw[name] = value
    path.write_text(json.dumps(raw), encoding="utf-8")


_DROP = object()


@pytest.mark.parametrize("source", ["offline", _DROP], ids=["relabelled", "source-removed"])
def test_relabelling_the_trial_in_the_activation_file_keeps_it_the_trial(retail_env, monkeypatch, source):
    """Changing "source" from "trial" and clearing the end date made a trial a permanent licence."""
    monkeypatch.setenv("ELI_KEYS_DIR", str(retail_env["tmp"] / "eli-keys"))
    T.grant_trial()
    _rewrite_activation(source=source, expires_at=None, tier="enterprise")
    status = validate_activation()
    assert (status.tier, status.trial) == ("trial", "active")
    assert status.expires_at == T.load_trial_record().expires_at
    _backdate_trial()
    assert validate_activation().active is False


def _keyless_activation(license_id: str = "made-by-hand") -> None:
    from eli.licensing.machine_id import machine_fingerprint, machine_label

    save_activation_state(ActivationState(
        license_id=license_id, tier="enterprise", seats=9, source="offline",
        activated_at="2026-09-01T00:00:00+00:00",
        machines=[MachineBinding(id_hash=machine_fingerprint(), label=machine_label(),
                                 bound_at="2026-09-01T00:00:00+00:00")]))


def test_an_activation_with_no_key_is_not_a_licence(retail_env, monkeypatch):
    from eli.licensing.activation import LicenseRequiredError
    from eli.licensing.launch_gate import ensure_license_or_exit

    monkeypatch.setenv("ELI_KEYS_DIR", str(retail_env["tmp"] / "eli-keys"))
    _keyless_activation()
    status = validate_activation()
    assert status.required and not status.active
    assert "no license key" in status.message
    assert status.trial is None, "the key is asked for, not a trial started over it"
    with pytest.raises(LicenseRequiredError, match="license key"):
        ensure_license_or_exit(allow_gui_prompt=False)


def test_entering_the_key_replaces_an_activation_that_has_none(retail_env, signing, monkeypatch):
    monkeypatch.setenv("ELI_KEYS_DIR", str(retail_env["tmp"] / "eli-keys"))
    _keyless_activation()
    state = activate_license_key(_paid_key(signing))   # no --force
    assert state.license_key and validate_activation().active is True


@pytest.mark.parametrize("how", ["frozen", "retail"])
def test_a_sold_build_ignores_the_trial_length_and_machine_switches(retail_env, monkeypatch, how):
    """ELI_TRIAL_DAYS gave a trial of any length, and each new ELI_MACHINE_ID a fresh one."""
    from eli.licensing.machine_id import machine_fingerprint

    days, fp = T.trial_duration_days(), machine_fingerprint()
    monkeypatch.setenv("ELI_TRIAL_DAYS", str(days * 50))
    monkeypatch.setenv("ELI_MACHINE_ID", "another-computer")
    _clear_caches()
    assert T.trial_duration_days() == days * 50 and machine_fingerprint() != fp   # from source
    if how == "frozen":
        monkeypatch.setattr("eli.core.paths.is_frozen", lambda: True)
    else:
        monkeypatch.setenv("ELI_RETAIL_BUILD", "1")
    _clear_caches()
    assert T.trial_duration_days() == days and machine_fingerprint() == fp


# ── Entry-point wiring ─────────────────────────────────────────────────────

def test_license_flags_are_handled_before_any_gate(retail_env, signing, capsys):
    from eli.licensing.flags import handle_license_flags, has_license_flag

    assert has_license_flag(["--license-status"]) is True
    assert has_license_flag(["--headless"]) is False

    # Unactivated retail build: status must still be reportable (rc 2, not a gate).
    assert handle_license_flags(["--license-status"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["required"] is True
    assert payload["trial"]["state"] == "available"

    assert handle_license_flags(["--start-trial"]) == 0
    assert T.trial_state() == "active"

    assert handle_license_flags(["--activate-key", _paid_key(signing)]) == 0
    assert handle_license_flags(["--license-status"]) == 0

    assert handle_license_flags(["--deactivate-license"]) == 0
    assert handle_license_flags(["--headless"]) is None


def test_frozen_entry_handles_license_flags_first():
    """eli_entry.py must consult the flags before dispatching to the GUI gate."""
    src = Path("packaging/pyinstaller/eli_entry.py").read_text(encoding="utf-8")
    entry = src.index('if __name__ == "__main__":')
    flags_at = src.index("handle_license_flags", entry)
    gui_at = src.index("from eli.gui.app import main", entry)
    assert flags_at < gui_at


def test_created_qapplication_is_retained(monkeypatch: pytest.MonkeyPatch):
    """A QApplication built for the dialog must be kept referenced.

    Qt objects die with their last Python reference. Dropping it meant the app
    was collected as soon as the helper returned, and the next widget aborted
    with "Must construct a QApplication before a QWidget" — so the activation
    dialog could never be shown.
    """
    from eli.gui import license_dialog

    created: list[object] = []

    class FakeApp:
        def __init__(self, argv):
            created.append(self)

        @staticmethod
        def instance():
            return None

    monkeypatch.setattr(license_dialog, "_OWNED_APP", None, raising=False)
    app = license_dialog._ensure_app({"QApplication": FakeApp})

    assert created and app is created[0]
    assert license_dialog._OWNED_APP is app, "created QApplication was not retained"


def test_gui_prompt_does_not_require_a_tty(monkeypatch: pytest.MonkeyPatch):
    """A desktop launch has no tty; the dialog must still be offered."""
    import sys

    from eli.licensing import launch_gate

    monkeypatch.delenv("ELI_NO_GUI_PROMPT", raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(launch_gate.sys, "platform", "linux")
    assert launch_gate.gui_prompt_available() is True

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert launch_gate.gui_prompt_available() is False


_SERVE = """
import importlib.util, sys, types
spec = importlib.util.spec_from_file_location("eli_entry", sys.argv[1])
entry = importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)
sys.frozen = True
served = []
api = types.ModuleType("api"); api.__path__ = []
server = types.ModuleType("api.server"); server.main = lambda: served.append(1) or 0
sys.modules.update({"api": api, "api.server": server})
print("RESULT", entry._serve(), len(served))
"""


@pytest.mark.parametrize("trial,expect", [("revoked", (1, 0)), ("available", (0, 1))])
def test_the_frozen_server_is_behind_the_licence_gate(retail_env, trial, expect):
    """`ELI --server` (and the "ELI Server" menu entry) served the whole engine with no licence."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "ELI_NO_GUI_PROMPT": "1",
           "ELI_KEYS_DIR": str(retail_env["tmp"] / "eli-keys"),
           "XDG_DATA_HOME": str(retail_env["tmp"] / "xdg")}
    env.pop("ELI_REQUIRE_LICENSE", None)    # frozen alone has to be enough
    if trial == "revoked":
        env["ELI_TRIAL_DISABLED"] = "1"
    entry = Path("packaging/pyinstaller/eli_entry.py").resolve()
    out = subprocess.run([sys.executable, "-c", _SERVE, str(entry)], env=env, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=120)
    result = [line for line in out.stdout.splitlines() if line.startswith("RESULT")]
    assert result, out.stderr[-1500:]
    assert tuple(int(x) for x in result[-1].split()[1:]) == expect, out.stderr[-1500:]


def test_the_mcp_server_is_behind_the_licence_gate(retail_env, monkeypatch):
    import sys

    served = []
    monkeypatch.setenv("ELI_TRIAL_DISABLED", "1")
    monkeypatch.setenv("ELI_KEYS_DIR", str(retail_env["tmp"] / "eli-keys"))
    monkeypatch.setattr("eli.integrations.mcp.server.main", lambda: served.append(1) or 0)
    monkeypatch.setattr(sys, "argv", ["eli", "--mcp-server"])
    from eli.__main__ import main

    assert main() == 1 and not served


def test_the_web_server_run_on_its_own_is_behind_the_licence_gate(retail_env, monkeypatch):
    """`python -m api.server` and eli_serve.sh start api.server.main() directly, past every gate."""
    import sys
    pytest.importorskip("fastapi")
    try:
        import api.server as srv
    except Exception as e:  # the suite's mocked pydantic cannot build the real server
        pytest.skip(f"web server not importable here: {e}")
    served = []
    monkeypatch.setenv("ELI_TRIAL_DISABLED", "1")
    monkeypatch.setenv("ELI_KEYS_DIR", str(retail_env["tmp"] / "eli-keys"))
    monkeypatch.setattr(srv.uvicorn, "run", lambda *a, **k: served.append(a))
    monkeypatch.setattr(srv, "_open_browser_async", lambda url: served.append(url) or False)
    monkeypatch.setattr(sys, "argv", ["api.server"])
    assert srv.main() == 1 and served == []


@pytest.mark.parametrize("trial,expect", [("revoked", (1, 0)), ("available", (0, 1))])
def test_the_mcp_script_on_its_own_is_behind_the_licence_gate(retail_env, monkeypatch, trial, expect):
    """The `eli-mcp` console script enters eli.integrations.mcp.server.main() directly."""
    import sys
    from eli.integrations.mcp import server as mcp
    served = []
    monkeypatch.setenv("ELI_KEYS_DIR", str(retail_env["tmp"] / "eli-keys"))
    monkeypatch.setenv("XDG_DATA_HOME", str(retail_env["tmp"] / "xdg"))
    if trial == "revoked":
        monkeypatch.setenv("ELI_TRIAL_DISABLED", "1")
    monkeypatch.setattr(sys, "stdout", sys.stdout)          # main() points stdout at stderr; put back after
    monkeypatch.setattr(mcp.os, "dup", lambda fd: (_ for _ in ()).throw(OSError("not in a test")))
    monkeypatch.setattr(mcp, "serve", lambda stdout=None: served.append(1) or 0)
    assert (mcp.main(), len(served)) == expect
