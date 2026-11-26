from __future__ import annotations

import base64
import dataclasses
import json
import re
from pathlib import Path

from eli.licensing.products import OFFICIAL_BUNDLES, PLATFORM, is_product, product_label
from eli.licensing.types import (
    SUPPORTED_LICENSE_VERSIONS,
    VALID_TIERS,
    LicensePayload,
    key_format_version,
)

_KEY_DIR = Path(__file__).resolve().parent / "keys"
_PUBLIC_KEY_FILE = _KEY_DIR / "product_signing.pub"
_KEY_RE = re.compile(r"^ELIV3-[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + pad)


def public_key_path() -> Path:
    """The key every licence is checked against. A substitute (ELI_LICENSE_PUBLIC_KEY, for the
    developer walkthrough with a key pair of one's own) is read only when working from source:
    in a sold build it would make ELI accept keys anyone can sign."""
    from eli.licensing.activation import developer_override

    override = developer_override("ELI_LICENSE_PUBLIC_KEY")
    if override:
        return Path(override).expanduser().resolve()
    return _PUBLIC_KEY_FILE


def load_public_key_pem() -> bytes | None:
    path = public_key_path()
    if not path.is_file():
        return None
    return path.read_bytes()


def load_private_key_pem() -> bytes | None:
    override = __import__("os").environ.get("ELI_LICENSE_SIGNING_KEY")
    if not override:
        return None
    path = Path(override).expanduser().resolve()
    if not path.is_file():
        return None
    return path.read_bytes()


def _sign_payload_bytes(payload_bytes: bytes, private_pem: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("signing key must be Ed25519")
    return key.sign(payload_bytes)


def _verify_payload_bytes(payload_bytes: bytes, signature: bytes, public_pem: bytes) -> bool:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = serialization.load_pem_public_key(public_pem)
    if not isinstance(key, Ed25519PublicKey):
        return False
    try:
        key.verify(signature, payload_bytes)
        return True
    except Exception:
        return False


def normalize_license_key(raw: str) -> str:
    key = (raw or "").strip()
    if key.upper().startswith("ELIV3-"):
        return "ELIV3-" + key[6:].strip()
    return key


def issue_license_key(payload: LicensePayload, *, private_pem: bytes | None = None) -> str:
    """Sign a key. Its format version is set from its product, whatever the caller passed."""
    if payload.tier not in VALID_TIERS:
        raise ValueError(f"invalid tier: {payload.tier}")
    if payload.seats < 1:
        raise ValueError("seats must be >= 1")
    if not is_product(payload.product):
        raise ValueError(
            f"unknown product: {payload.product!r} (add it to eli/licensing/products.py first)"
        )
    if payload.major_version < 1:
        raise ValueError("major_version must be >= 1")
    _check_includes(payload)
    payload = dataclasses.replace(payload, version=key_format_version(payload.product))
    private = private_pem or load_private_key_pem()
    if not private:
        raise RuntimeError(
            "no signing key: set ELI_LICENSE_SIGNING_KEY to your private PEM path"
        )
    body = json.dumps(payload.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = _sign_payload_bytes(body, private)
    token = f"ELIV3-{_b64url_encode(body)}.{_b64url_encode(sig)}"
    return token


def parse_license_key(key: str) -> LicensePayload:
    normalized = normalize_license_key(key)
    if not _KEY_RE.match(normalized):
        raise ValueError("license key format is invalid")
    body_b64, sig_b64 = normalized[6:].split(".", 1)
    body = _b64url_decode(body_b64)
    sig = _b64url_decode(sig_b64)
    public = load_public_key_pem()
    if not public:
        raise RuntimeError(
            "no product signing public key installed "
            f"(expected {public_key_path()})"
        )
    issuer_id = ""
    if _verify_payload_bytes(body, sig, public):
        raw = json.loads(body.decode("utf-8"))
        if isinstance(raw, dict) and raw.get("issuer"):
            raise ValueError("a key signed by ELI's own key carries no issuer certificate")
    else:
        # A key from the store: signed by an issuer key that ELI's own key certified, with the
        # certificate inside it. Read exactly as every ELI product reads it (eli-sdk).
        try:
            raw = json.loads(body.decode("utf-8"))
        except ValueError:
            raw = None
        if not (isinstance(raw, dict) and raw.get("issuer")):
            raise ValueError("license key signature is invalid")
        from eli.licensing._sdk.errors import InvalidKey
        from eli.licensing._sdk.keys import read_key

        try:
            issuer_id = read_key(normalized, public).issuer_id
        except InvalidKey as exc:
            raise ValueError(f"license key signature is invalid ({exc})") from None
    payload = LicensePayload.from_dict(raw)
    payload.issuer_id = issuer_id
    if payload.version not in SUPPORTED_LICENSE_VERSIONS:
        raise ValueError(f"unsupported license version: {payload.version}")
    if payload.tier not in VALID_TIERS:
        raise ValueError(f"invalid tier in license: {payload.tier}")
    if not is_product(payload.product):
        raise ValueError(
            f"this key is for {payload.product!r}, a product this version of ELI does not "
            "know — update ELI and try again"
        )
    if payload.version != key_format_version(payload.product):
        # Only a hand-built key gets here: a version-1 key naming a bundle is exactly
        # what older builds would misread as an ELI v3 licence.
        raise ValueError("license key format does not match its product")
    _check_includes(payload)
    return payload


def _check_includes(payload: LicensePayload) -> None:
    """Only an ELI v3 key includes bundles, and only official ones, each for a real version line."""
    if not payload.includes:
        return
    if payload.product != PLATFORM:
        raise ValueError("only an ELI v3 key can include bundles")
    for product, line in payload.includes.items():
        if product not in OFFICIAL_BUNDLES:
            raise ValueError(f"included product {product!r} is not an official bundle")
        if int(line) < 1:
            raise ValueError(f"included {product!r} needs a version line of 1 or more")


def require_product(payload: LicensePayload, product: str = PLATFORM) -> LicensePayload:
    """Refuse a key for a different product, saying which product it is for."""
    if payload.product != product:
        raise ValueError(
            f"this key is for {product_label(payload.product)}, not {product_label(product)}"
        )
    return payload


def key_fingerprint(key: str) -> str:
    import hashlib

    return hashlib.sha256(normalize_license_key(key).encode("utf-8")).hexdigest()[:16]
