from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from eli.licensing.types import LICENSE_VERSION, VALID_TIERS, LicensePayload

_KEY_DIR = Path(__file__).resolve().parent / "keys"
_PUBLIC_KEY_FILE = _KEY_DIR / "product_signing.pub"
_KEY_RE = re.compile(r"^ELIV3-[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + pad)


def public_key_path() -> Path:
    override = __import__("os").environ.get("ELI_LICENSE_PUBLIC_KEY")
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
    if payload.tier not in VALID_TIERS:
        raise ValueError(f"invalid tier: {payload.tier}")
    if payload.seats < 1:
        raise ValueError("seats must be >= 1")
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
    if not _verify_payload_bytes(body, sig, public):
        raise ValueError("license key signature is invalid")
    raw = json.loads(body.decode("utf-8"))
    payload = LicensePayload.from_dict(raw)
    if payload.version != LICENSE_VERSION:
        raise ValueError(f"unsupported license version: {payload.version}")
    if payload.tier not in VALID_TIERS:
        raise ValueError(f"invalid tier in license: {payload.tier}")
    return payload


def key_fingerprint(key: str) -> str:
    import hashlib

    return hashlib.sha256(normalize_license_key(key).encode("utf-8")).hexdigest()[:16]
