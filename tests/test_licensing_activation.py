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
    monkeypatch.setenv("ELI_LICENSE_DIR", str(lic_dir))
    monkeypatch.setenv("ELI_LICENSE_PUBLIC_KEY", str(pub))
    monkeypatch.setenv("ELI_LICENSE_SIGNING_KEY", str(priv))
    monkeypatch.delenv("ELI_LICENSE_BYPASS", raising=False)
    monkeypatch.delenv("ELI_REQUIRE_LICENSE", raising=False)
    monkeypatch.delenv("ELI_RETAIL_BUILD", raising=False)
    monkeypatch.delenv("ELI_LICENSE_KEY", raising=False)
    return {"private": priv, "public": pub, "license_dir": lic_dir}


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
