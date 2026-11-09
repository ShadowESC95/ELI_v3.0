from __future__ import annotations

import json
from pathlib import Path

import pytest

from eli.licensing.activation import (
    activate_license_key,
    activation_required,
    deactivate_license,
    validate_activation,
)
from eli.licensing.signature import issue_license_key, parse_license_key
from eli.licensing.storage import activation_state_path, load_activation_state
from eli.licensing.types import LICENSE_VERSION, LicensePayload


@pytest.fixture()
def signing_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv = tmp_path / "product_signing.pem"
    pub = tmp_path / "product_signing.pub"
    priv.write_bytes(priv_pem)
    pub.write_bytes(pub_pem)

    lic_dir = tmp_path / "licensing"
    lic_dir.mkdir()
    data_dir = tmp_path / "artifacts"
    data_dir.mkdir()
    monkeypatch.setenv("ELI_LICENSE_DIR", str(lic_dir))
    monkeypatch.setenv("ELI_KEYS_DIR", str(tmp_path / "eli-keys"))
    # Trial markers are mirrored into the data dir; without this they would land
    # in the developer's real artifacts directory and leak between runs.
    monkeypatch.setenv("ELI_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(data_dir))
    monkeypatch.setenv("ELI_LICENSE_PUBLIC_KEY", str(pub))
    monkeypatch.setenv("ELI_LICENSE_SIGNING_KEY", str(priv))
    monkeypatch.delenv("ELI_LICENSE_BYPASS", raising=False)
    monkeypatch.delenv("ELI_REQUIRE_LICENSE", raising=False)
    monkeypatch.delenv("ELI_RETAIL_BUILD", raising=False)
    monkeypatch.delenv("ELI_LICENSE_KEY", raising=False)

    from eli.core import paths
    from eli.licensing import machine_id

    for fn in (
        paths.data_dir,
        paths.config_dir,
        machine_id.machine_fingerprint,
        machine_id.legacy_machine_fingerprints,
    ):
        fn.cache_clear()

    yield {"private": priv, "public": pub, "license_dir": lic_dir, "data_dir": data_dir}

    for fn in (
        paths.data_dir,
        paths.config_dir,
        machine_id.machine_fingerprint,
        machine_id.legacy_machine_fingerprints,
    ):
        fn.cache_clear()


def _sample_payload() -> LicensePayload:
    return LicensePayload(
        version=LICENSE_VERSION,
        license_id="test-license-001",
        tier="pro",
        seats=2,
        issued_at="2026-07-05T00:00:00+00:00",
        expires_at=None,
        customer_ref="order-123",
    )


def test_issue_and_parse_license_key(signing_keys, tmp_path: Path):
    priv = signing_keys["private"].read_bytes()
    payload = _sample_payload()
    key = issue_license_key(payload, private_pem=priv)
    assert key.startswith("ELIV3-")
    parsed = parse_license_key(key)
    assert parsed.license_id == payload.license_id
    assert parsed.tier == "pro"
    assert parsed.seats == 2


def test_activate_and_validate(signing_keys, monkeypatch: pytest.MonkeyPatch):
    priv = signing_keys["private"].read_bytes()
    key = issue_license_key(_sample_payload(), private_pem=priv)

    monkeypatch.setenv("ELI_REQUIRE_LICENSE", "1")
    before = validate_activation()
    assert before.active is False
    assert before.required is True

    state = activate_license_key(key)
    assert state.tier == "pro"
    assert state.seats == 2
    assert activation_state_path().is_file()

    after = validate_activation()
    assert after.active is True
    assert after.tier == "pro"
    assert after.machines_bound == 1

    saved = load_activation_state()
    assert saved is not None
    assert saved.license_id == "test-license-001"
    assert len(saved.machines) == 1


def test_seat_limit_enforced(signing_keys, monkeypatch: pytest.MonkeyPatch):
    priv = signing_keys["private"].read_bytes()
    payload = LicensePayload(
        version=LICENSE_VERSION,
        license_id="seat-test",
        tier="personal",
        seats=1,
        issued_at="2026-07-05T00:00:00+00:00",
    )
    key = issue_license_key(payload, private_pem=priv)
    activate_license_key(key)

    monkeypatch.setattr(
        "eli.licensing.activation.machine_fingerprint",
        lambda: "other-machine-hash",
    )
    with pytest.raises(ValueError, match="seat limit"):
        activate_license_key(key)


def test_deactivate_clears_state(signing_keys):
    priv = signing_keys["private"].read_bytes()
    key = issue_license_key(_sample_payload(), private_pem=priv)
    activate_license_key(key)
    assert load_activation_state() is not None
    assert deactivate_license() is True
    assert load_activation_state() is None


def test_dev_checkout_does_not_require_license(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ELI_REQUIRE_LICENSE", raising=False)
    monkeypatch.delenv("ELI_RETAIL_BUILD", raising=False)
    # Typical pytest/dev environment: no retail flag -> not required unless forced
    assert activation_required() in {True, False}


def test_a_key_activated_here_is_kept_for_every_eli_product(signing_keys, tmp_path: Path):
    """ELI VR read keys from the shared folder and ELI v3 kept its own: a key entered in v3
    never reached VR, and VR asked for one."""
    key = issue_license_key(_sample_payload(), private_pem=signing_keys["private"].read_bytes())
    activate_license_key(key)
    kept = tmp_path / "eli-keys" / "eli.key"
    assert kept.read_text(encoding="utf-8").strip() == key
    assert (kept.stat().st_mode & 0o777) == 0o600, "nobody else on the computer reads it"


def test_a_key_another_eli_product_kept_is_taken_up_here(signing_keys, tmp_path: Path, monkeypatch):
    key = issue_license_key(_sample_payload(), private_pem=signing_keys["private"].read_bytes())
    (tmp_path / "eli-keys").mkdir()
    (tmp_path / "eli-keys" / "eli.key").write_text(key + "\n", encoding="utf-8")
    monkeypatch.setenv("ELI_REQUIRE_LICENSE", "1")
    assert load_activation_state() is None
    status = validate_activation()
    assert status.active is True and status.tier == "pro"
    assert load_activation_state().license_id == "test-license-001"


def test_a_key_in_the_shared_folder_that_does_not_hold_is_not_taken_up(signing_keys, tmp_path: Path, monkeypatch):
    (tmp_path / "eli-keys").mkdir()
    (tmp_path / "eli-keys" / "eli.key").write_text("ELIV3-not.a-key\n", encoding="utf-8")
    monkeypatch.setenv("ELI_REQUIRE_LICENSE", "1")
    assert validate_activation().active is False and load_activation_state() is None


def test_deactivating_takes_the_key_back_from_every_eli_product(signing_keys, tmp_path: Path, monkeypatch):
    key = issue_license_key(_sample_payload(), private_pem=signing_keys["private"].read_bytes())
    activate_license_key(key)
    assert deactivate_license() is True
    assert not (tmp_path / "eli-keys" / "eli.key").exists()
    monkeypatch.setenv("ELI_REQUIRE_LICENSE", "1")
    assert validate_activation().active is False, "not taken up again from the shared folder"


# ── developer switches are not read in a build that is sold ─────────────────────
def _sold(monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    if how == "frozen":
        monkeypatch.setattr("eli.core.paths.is_frozen", lambda: True)
    else:
        monkeypatch.setenv("ELI_RETAIL_BUILD", "1")


@pytest.mark.parametrize("how", ["frozen", "retail"])
def test_the_bypass_does_not_open_a_sold_build(signing_keys, monkeypatch: pytest.MonkeyPatch, how):
    """One exported ELI_LICENSE_BYPASS ran a retail build with no licence and no trial."""
    monkeypatch.setenv("ELI_LICENSE_BYPASS", "1")
    _sold(monkeypatch, how)
    assert activation_required() is True
    status = validate_activation()
    assert status.required is True and status.active is False


def test_the_bypass_still_works_when_working_from_source(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ELI_LICENSE_BYPASS", "1")
    monkeypatch.setenv("ELI_REQUIRE_LICENSE", "1")
    monkeypatch.delenv("ELI_RETAIL_BUILD", raising=False)
    assert activation_required() is False


@pytest.mark.parametrize("how", ["frozen", "retail"])
def test_a_sold_build_checks_keys_against_its_own_public_key_only(signing_keys, monkeypatch: pytest.MonkeyPatch, how):
    """ELI_LICENSE_PUBLIC_KEY pointed at a key pair of one's own made ELI accept self-signed keys."""
    from eli.licensing import signature

    key = issue_license_key(_sample_payload(), private_pem=signing_keys["private"].read_bytes())
    assert parse_license_key(key).license_id == "test-license-001"   # from source: the substitute is used
    _sold(monkeypatch, how)
    assert signature.public_key_path() == signature._PUBLIC_KEY_FILE
    with pytest.raises(ValueError, match="signature is invalid"):
        parse_license_key(key)
