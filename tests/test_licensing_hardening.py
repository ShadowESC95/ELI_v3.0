"""The simple ways round ELI v3's licence do not work: an installed copy of any kind needs a
licence or the trial; the trial survives redirected folders, deleted records and a clock set
back; edited code refuses to start; keys from the store, keys for one machine, withdrawn keys
and keys including bundles released later are all read as ELI Legal and the store read them."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eli.licensing import activation, integrity
from eli.licensing import trial as T
from tests.test_licensing_trial import _clear_caches, retail_env, signing  # noqa: F401


def _installed(monkeypatch):
    """Not the developer's checkout: an installed copy (a .deb, a wheel, a portable folder)."""
    monkeypatch.setattr(activation, "developer_checkout", lambda: False)


def test_an_installed_copy_of_any_kind_needs_a_licence_and_the_bypass_is_ignored(monkeypatch):
    monkeypatch.setenv("ELI_LICENSE_BYPASS", "1")
    _installed(monkeypatch)
    assert activation.activation_required() is True
    monkeypatch.setattr(activation, "developer_checkout", lambda: True)
    assert activation.activation_required() is False, "the developer's checkout, when told"
    monkeypatch.delenv("ELI_LICENSE_BYPASS")
    assert activation.activation_required() is True, "and even there only when told"


def test_redirecting_the_folders_does_not_restart_the_trial(retail_env, monkeypatch, tmp_path):  # noqa: F811
    assert T.grant_trial() is not None
    T.revoke_trial(reason="expired")
    for name, folder in (("ELI_LICENSE_DIR", "a"), ("ELI_DATA_DIR", "b"), ("ELI_CONFIG_DIR", "c")):
        monkeypatch.setenv(name, str(tmp_path / "elsewhere" / folder))
    _clear_caches()
    assert T.trial_state() == "revoked", "the fixed records still say so"


def test_deleting_every_record_once_eli_has_been_used_starts_no_trial(retail_env, monkeypatch, tmp_path):  # noqa: F811
    db = tmp_path / "licence-data" / "db"
    db.mkdir(parents=True)
    (db / "user.sqlite3").write_bytes(b"conversations")
    assert T.trial_state() == "expired"
    assert T.grant_trial() is None


def test_a_clock_set_back_pauses_the_trial_without_ending_it(retail_env, monkeypatch):  # noqa: F811
    assert T.grant_trial() is not None
    rec = T.load_trial_record()
    rec.last_seen = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat()
    T.save_trial_record(rec)
    assert T.trial_state() == "clock_behind"
    rec.last_seen = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    T.save_trial_record(rec)
    assert T.trial_state() == "active"


def _store_key(signing_pem: bytes, **fields):
    """A key as the store makes it: signed by an issuer key the offline key certified."""
    from eli.licensing._sdk.issuing import certify_issuer, issue, load_private_key, new_signing_key

    master = load_private_key(signing_pem)
    private, public = new_signing_key()
    now = datetime.now(timezone.utc)
    cert = certify_issuer(master, public, issuer_id="shop-test", not_before=now - timedelta(days=1),
                          not_after=now + timedelta(days=30))
    from eli.licensing._sdk.issuing import public_pem_of

    return issue(fields.pop("product", "eli"), fields.pop("tier", "pro"), private, certificate=cert,
                 public_key=public_pem_of(master), **fields)


def test_a_key_from_the_store_activates_and_one_for_another_machine_does_not(retail_env, signing):  # noqa: F811
    from eli.licensing.signature import parse_license_key

    key, _ = _store_key(signing, product="eli", tier="pro", major_version=3)
    payload = parse_license_key(key)
    assert payload.issuer_id == "shop-test"
    assert activation.activate_license_key(key).tier == "pro"
    other, _ = _store_key(signing, product="eli", tier="trial", major_version=3, machine="0" * 64)
    with pytest.raises(ValueError, match="another machine"):
        activation.activate_license_key(other, force_rebind=True)


def test_a_withdrawn_key_stops_working(retail_env, signing, monkeypatch, tmp_path):  # noqa: F811
    from eli.licensing._sdk.issuing import load_private_key, sign_revocations

    monkeypatch.setenv("ELI_KEYS_DIR", str(tmp_path / "keys"))
    key, lic = _store_key(signing, product="eli", tier="pro", major_version=3)
    activation.activate_license_key(key)
    assert activation.validate_activation().active
    keys = tmp_path / "keys"
    keys.mkdir(exist_ok=True)
    (keys / "revocations.list").write_text(
        sign_revocations(load_private_key(signing), serial=1, licences=[lic.license_id]) + "\n")
    status = activation.validate_activation()
    assert not status.active and "withdrawn" in status.message


def test_a_key_including_a_bundle_released_later_still_opens_eli(retail_env, signing):  # noqa: F811
    from eli.licensing._sdk.issuing import licence_body, load_private_key, public_pem_of, sign_key
    from eli.licensing.signature import parse_license_key

    master = load_private_key(signing)
    body = licence_body("eli", "pro", major_version=3, includes={"legal": 1})
    body["includes"]["medical"] = 1
    key = sign_key(body, master, public_key=public_pem_of(master))
    assert parse_license_key(key).includes == {"legal": 1, "medical": 1}
    assert activation.activate_license_key(key).tier == "pro"


def _tree(root: Path) -> None:
    for rel in integrity.COVERED:
        path = root / rel
        if rel.endswith(".py"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {rel}\n")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "gate.py").write_text("def allowed():\n    return check()\n")


def test_an_edited_or_incomplete_installed_copy_refuses_to_start(monkeypatch, tmp_path):
    from eli.licensing._sdk import integrity as sdk_integrity
    from eli.licensing._sdk.issuing import new_signing_key
    from eli.licensing.launch_gate import ensure_license_or_exit

    root = tmp_path / "site-packages"
    _tree(root)
    private, public = new_signing_key()
    monkeypatch.setattr(sdk_integrity, "release_public_key", lambda: _pub(public))
    monkeypatch.setattr(integrity, "package_dir", lambda: root / "eli")
    monkeypatch.setattr(integrity, "checked", lambda: True)
    monkeypatch.setattr(integrity, "_version", lambda: "3.0.174")
    integrity.problems.cache_clear()
    assert "manifest is missing" in integrity.refusal()

    manifest = sdk_integrity.build_manifest(root, list(integrity.COVERED), product="eli", version="3.0.174")
    (root / "eli" / integrity.MANIFEST).write_text(sdk_integrity.sign_manifest(manifest, private))
    integrity.problems.cache_clear()
    assert integrity.refusal() == ""

    (root / "eli" / "licensing" / "gate.py").write_text("def allowed():\n    return True\n")
    integrity.problems.cache_clear()
    assert "eli/licensing/gate.py" in integrity.refusal()
    _installed(monkeypatch)
    status = activation.validate_activation()
    assert not status.active and "altered" in status.message
    from eli.licensing.activation import LicenseRequiredError

    with pytest.raises(LicenseRequiredError, match="altered"):
        ensure_license_or_exit(allow_gui_prompt=False)


def _pub(pem: bytes):
    from eli.licensing._sdk.keys import load_public_key

    return load_public_key(pem)
