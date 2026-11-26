"""Licence keys name a product: ELI v3 itself, or an official bundle sold separately.

What must hold:
  * an ELI v3 key opens ELI v3 and nothing else; a bundle key opens its bundle and not ELI v3;
  * keys issued before products existed still read as ELI v3 keys;
  * builds that predate products can never misread a bundle key as an ELI v3 licence;
  * a key covers one major version line (pre-1.0 releases belong to 1.x);
  * the grant comes from the signed key, not from the editable activation file.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from eli.bundles import Bundle, entitled
from eli.licensing import products
from eli.licensing.activation import (
    activate_license_key,
    deactivate_license,
    product_status,
    validate_activation,
)
from eli.licensing.signature import issue_license_key, parse_license_key
from eli.licensing.storage import activation_state_path, load_activation_state
from eli.licensing.types import (
    LICENSE_VERSION,
    PRODUCT_LICENSE_VERSION,
    LicensePayload,
)


@pytest.fixture()
def keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    priv = tmp_path / "product_signing.pem"
    pub = tmp_path / "product_signing.pub"
    priv.write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    pub.write_bytes(private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    lic_dir = tmp_path / "licensing"
    data_dir = tmp_path / "artifacts"
    lic_dir.mkdir()
    data_dir.mkdir()
    monkeypatch.setenv("ELI_LICENSE_DIR", str(lic_dir))
    monkeypatch.setenv("ELI_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(data_dir))
    monkeypatch.setenv("ELI_LICENSE_PUBLIC_KEY", str(pub))
    monkeypatch.setenv("ELI_LICENSE_SIGNING_KEY", str(priv))
    # Retail behaviour: activation is required, as in a shipped build.
    monkeypatch.setenv("ELI_REQUIRE_LICENSE", "1")
    for name in ("ELI_LICENSE_BYPASS", "ELI_RETAIL_BUILD", "ELI_LICENSE_KEY"):
        monkeypatch.delenv(name, raising=False)

    from eli.core import paths
    from eli.licensing import machine_id

    caches = (paths.data_dir, paths.config_dir, machine_id.machine_fingerprint,
              machine_id.legacy_machine_fingerprints)
    for fn in caches:
        fn.cache_clear()
    yield {"private": private_key, "pem": priv.read_bytes(), "dir": lic_dir}
    for fn in caches:
        fn.cache_clear()


def _key(keys, product: str = "eli", *, major: int | None = None, license_id: str = "",
         tier: str = "pro", seats: int = 2, includes: dict | None = None) -> str:
    payload = LicensePayload(
        version=LICENSE_VERSION,  # issue_license_key sets the real format version
        license_id=license_id or f"{product}-licence",
        tier=tier,
        seats=seats,
        issued_at="2026-10-14T00:00:00+00:00",
        product=product,
        major_version=major if major is not None else products.default_major(product),
        includes=dict(includes or {}),
    )
    return issue_license_key(payload, private_pem=keys["pem"])


def _hand_signed(keys, body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    sig = keys["private"].sign(raw)
    enc = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"ELIV3-{enc(raw)}.{enc(sig)}"


def _body(key: str) -> dict:
    part = key[6:].split(".", 1)[0]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


# ── key format ─────────────────────────────────────────────────────────────────

def test_an_eli_v3_key_keeps_the_format_every_v3_build_reads(keys):
    key = _key(keys)
    assert _body(key)["version"] == LICENSE_VERSION
    parsed = parse_license_key(key)
    assert (parsed.product, parsed.major_version) == ("eli", 3)


def test_a_key_issued_before_products_existed_is_an_eli_v3_key(keys):
    old = _hand_signed(keys, {
        "version": 1, "license_id": "old", "tier": "personal", "seats": 1,
        "issued_at": "2026-07-05T00:00:00+00:00", "expires_at": None, "customer_ref": None,
    })
    parsed = parse_license_key(old)
    assert (parsed.product, parsed.major_version) == ("eli", 3)


def test_a_bundle_key_uses_a_format_older_builds_refuse(keys):
    # Builds from before products ignore the product field, so a bundle key in the
    # old format would open ELI v3 for someone who bought only the bundle.
    assert _body(_key(keys, "legal"))["version"] == PRODUCT_LICENSE_VERSION


def test_an_old_format_key_naming_a_bundle_is_refused(keys):
    forged = _hand_signed(keys, {
        "version": 1, "license_id": "x", "tier": "pro", "seats": 1,
        "issued_at": "2026-10-14T00:00:00+00:00", "product": "legal", "major_version": 1,
    })
    with pytest.raises(ValueError, match="does not match its product"):
        parse_license_key(forged)


def test_a_key_for_an_unknown_product_is_neither_issued_nor_accepted(keys):
    with pytest.raises(ValueError, match="unknown product"):
        _key(keys, "nonesuch")
    foreign = _hand_signed(keys, {
        "version": 2, "license_id": "x", "tier": "pro", "seats": 1,
        "issued_at": "2026-10-14T00:00:00+00:00", "product": "nonesuch", "major_version": 1,
    })
    with pytest.raises(ValueError, match="does not know"):
        parse_license_key(foreign)


# ── one key per product ──────────────────────────────────────────────────────────

def test_each_product_is_activated_apart(keys):
    activate_license_key(_key(keys, "eli"))
    activate_license_key(_key(keys, "legal", tier="enterprise", seats=5))
    assert activation_state_path("eli").is_file()
    assert activation_state_path("legal").is_file()
    assert activation_state_path("eli") != activation_state_path("legal")
    assert validate_activation().tier == "pro"
    assert product_status("legal").tier == "enterprise"
    assert deactivate_license(product="legal") is True
    assert validate_activation().active is True, "removing a bundle leaves ELI v3 alone"


def test_a_bundle_key_does_not_open_eli_v3(keys, monkeypatch):
    activate_license_key(_key(keys, "legal"))
    assert validate_activation().active is False
    monkeypatch.setenv("ELI_LICENSE_KEY", _key(keys, "legal"))
    status = validate_activation()
    assert status.active is False and "ELI Legal, not ELI v3" in status.message


def test_an_eli_v3_key_does_not_open_a_bundle(keys):
    activate_license_key(_key(keys, "eli"))
    ok, why = entitled(Bundle(id="legal", title="ELI Legal", launch="a:b", version="0.6.1"))
    assert not ok and "separate product" in why


def test_a_bundle_opens_with_its_own_key(keys):
    legal = Bundle(id="legal", title="ELI Legal", launch="a:b", version="0.6.1", edition="legal")
    assert entitled(legal)[0] is False
    activate_license_key(_key(keys, "legal"))
    assert entitled(legal) == (True, "ELI Legal license active")


def test_relabelling_an_official_bundles_edition_does_not_dodge_its_key(keys):
    relabelled = Bundle(id="legal", title="ELI Legal", launch="a:b", edition="general")
    assert entitled(relabelled)[0] is False


def test_another_publishers_bundle_needs_no_key_of_ours(keys):
    ok, why = entitled(Bundle(id="acme-notes", title="Acme Notes", launch="a:b"))
    assert ok and "publisher" in why


# ── major version lines ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("version,line", [
    ("0.6.1", 1), ("1.0.0", 1), ("v2.3", 2), ("10.1", 10), ("", None), ("dev", None),
])
def test_releases_before_1_0_belong_to_the_1_x_line(version, line):
    assert products.major_line(version) == line


def test_a_bundle_key_covers_its_major_line_only(keys):
    activate_license_key(_key(keys, "vr", major=1))
    assert product_status("vr", version="0.9.2").active
    assert product_status("vr", version="1.7.0").active
    later = product_status("vr", version="2.0.0")
    assert not later.active and "covers ELI VR 1.x" in later.message


def test_eli_v3_refuses_a_key_for_an_earlier_major_version(keys):
    with pytest.raises(ValueError, match="ELI v2"):
        activate_license_key(_key(keys, "eli", major=2))
    activate_license_key(_key(keys, "eli", major=4))  # a v4 buyer may run v3
    assert validate_activation().active


# ── the signed key decides, not the file ──────────────────────────────────────────

def _edit_state(which: str, /, **changes) -> None:
    path = activation_state_path(which)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(changes)
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_editing_the_activation_file_does_not_change_the_grant(keys):
    activate_license_key(_key(keys, "eli", tier="personal", seats=1))
    _edit_state("eli", tier="enterprise", seats=99, expires_at=None)
    status = validate_activation()
    assert status.active and (status.tier, status.seats) == ("personal", 1)


def test_a_stored_key_that_no_longer_verifies_is_refused(keys):
    activate_license_key(_key(keys, "legal"))
    _edit_state("legal", license_key="ELIV3-bogus.bogus")
    status = product_status("legal")
    assert not status.active and "no longer verifies" in status.message


def test_a_stored_key_from_another_licence_is_refused(keys):
    activate_license_key(_key(keys, "eli", license_id="mine"))
    _edit_state("eli", license_key=_key(keys, "eli", license_id="someone-elses"))
    status = validate_activation()
    assert not status.active and "does not belong" in status.message


def test_an_activation_without_its_key_holds_only_once_the_key_is_found(keys, tmp_path, monkeypatch):
    """Nothing in activation.json is signed, so with its key gone there is nothing to read the
    grant from: the key kept for every ELI product is taken up, and with none it is asked for."""
    from eli.licensing.storage import unshare_key

    monkeypatch.setenv("ELI_KEYS_DIR", str(tmp_path / "eli-keys"))
    key = _key(keys, "eli")
    activate_license_key(key)
    _edit_state("eli", license_key="")
    assert validate_activation().active and load_activation_state().license_key == key
    _edit_state("eli", license_key="")
    unshare_key("eli")
    status = validate_activation()
    assert not status.active and "no license key" in status.message


def test_a_file_for_another_product_is_not_read_as_this_one(keys):
    activate_license_key(_key(keys, "legal"))
    _edit_state("legal", product="vr")
    assert load_activation_state("legal") is None


def test_status_lists_the_bundles_this_machine_holds(keys):
    from eli.licensing.flags import status_payload

    activate_license_key(_key(keys, "eli"))
    activate_license_key(_key(keys, "legal"))
    payload = status_payload()
    assert payload["active"] is True
    assert payload["products"]["legal"]["active"] is True
    assert "vr" not in payload["products"]


# ── bundled sales: an ELI v3 key that includes bundles ──────────────────────────

LEGAL = Bundle(id="legal", title="ELI Legal", launch="a:b", version="0.6.1")
VR = Bundle(id="vr", title="ELI VR", launch="a:b", version="0.9.2")


def test_a_v3_key_can_include_a_bundle(keys):
    activate_license_key(_key(keys, "eli", includes={"legal": 1}))
    ok, why = entitled(LEGAL)
    assert ok and "included in your ELI v3 license" in why
    assert entitled(VR)[0] is False, "only the bundles the key names are included"


def test_an_included_bundle_covers_the_line_the_key_names(keys):
    activate_license_key(_key(keys, "eli", includes={"legal": 1}))
    later = Bundle(id="legal", title="ELI Legal", launch="a:b", version="2.0.0")
    ok, why = entitled(later)
    assert not ok and "includes ELI Legal 1.x" in why


def test_a_bundles_own_key_and_an_inclusion_both_work(keys):
    activate_license_key(_key(keys, "eli", includes={"legal": 1}))
    activate_license_key(_key(keys, "vr"))
    assert entitled(LEGAL)[0] and entitled(VR)[0]


def test_inclusions_are_read_from_the_signed_key_not_the_file(keys):
    activate_license_key(_key(keys, "eli"))
    _edit_state("eli", includes={"legal": 1})          # not a field the key grants
    assert entitled(LEGAL)[0] is False


def test_a_key_set_in_the_environment_can_include_a_bundle(keys, monkeypatch):
    monkeypatch.setenv("ELI_LICENSE_KEY", _key(keys, "eli", includes={"vr": 1}))
    assert entitled(VR)[0] is True


def test_only_a_v3_key_includes_and_only_official_bundles(keys):
    with pytest.raises(ValueError, match="only an ELI v3 key"):
        _key(keys, "legal", includes={"vr": 1})
    with pytest.raises(ValueError, match="is not a bundle"):
        _key(keys, "eli", includes={"Not a bundle!": 1})
    # A bundle released after this build is allowed: the key still opens everything else.
    assert parse_license_key(_key(keys, "eli", includes={"nonesuch": 1})).includes == {"nonesuch": 1}
    forged = _hand_signed(keys, {
        "version": 2, "license_id": "x", "tier": "pro", "seats": 1, "product": "legal",
        "major_version": 1, "issued_at": "2026-10-14T00:00:00+00:00", "includes": {"vr": 1},
    })
    with pytest.raises(ValueError, match="only an ELI v3 key"):
        parse_license_key(forged)


def test_inclusions_are_given_as_bundle_and_line():
    assert products.parse_includes(["legal", "VR:2", " "]) == {"legal": 1, "vr": 2}
    assert products.parse_includes(None) == {}


def test_status_shows_a_bundle_the_v3_key_includes(keys):
    from eli.licensing.flags import status_payload

    activate_license_key(_key(keys, "eli", includes={"legal": 1}))
    included = status_payload()["products"]["legal"]
    assert included["active"] is True and included["activation_file"] is None
