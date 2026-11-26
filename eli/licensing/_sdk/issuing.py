"""Making licence keys, issuer certificates and revocation lists.

This module holds no key. Whoever issues passes the private key in: ELI's own signing key, kept
offline, or an *issuer* key that the offline key has certified. An issuer key is what an online
shop holds. If that machine is ever broken into, the certificate is withdrawn and a new issuer
certified; the offline key, and every key a customer already holds, are untouched.

Every key made here is checked with :func:`eli_sdk.read_key` before it is returned, so nothing
issued can be something the products would refuse.

Tokens
------
- a key:          ``ELIV3-<body>.<signature>`` (see docs/keys.md)
- a certificate:  ``ELICERT-<body>.<signature>``, signed by ELI's offline key
- a revocation:   ``ELIREV-<body>.<signature>``, signed by ELI's key or by a certified issuer
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ._encoding import b64url_encode, canonical_json
from .errors import InvalidKey
from .keys import PREFIX, License, format_for, read_key
from .products import TIER_SEATS, TRIAL_DAYS, default_major, is_product, parse_includes

CERT_PREFIX = "ELICERT-"
REVOCATION_PREFIX = "ELIREV-"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


# ── signing keys ─────────────────────────────────────────────────────────────

def load_private_key(source: Any, password: bytes | str | None = None):
    """An Ed25519 private key from PEM text or bytes, a path to a PEM file, raw 32 bytes, or a
    key object. A PEM protected by a passphrase needs it."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if isinstance(source, Ed25519PrivateKey):
        return source
    if isinstance(source, (bytes, bytearray)) and len(source) == 32:
        return Ed25519PrivateKey.from_private_bytes(bytes(source))
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    elif isinstance(source, str) and "-----BEGIN" in source:
        data = source.encode("ascii")
    else:
        data = Path(source).expanduser().read_bytes()
    secret = password.encode("utf-8") if isinstance(password, str) else password
    try:
        key = serialization.load_pem_private_key(data, password=secret)
    except (TypeError, ValueError) as exc:
        raise InvalidKey(f"not a usable private key ({exc})") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise InvalidKey("the private key is not an Ed25519 key")
    return key


def new_signing_key(password: bytes | str | None = None) -> tuple[bytes, bytes]:
    """A new Ed25519 key pair as (private PEM, public PEM). With a passphrase the private PEM is
    encrypted with it."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    secret = password.encode("utf-8") if isinstance(password, str) else password
    encryption = (serialization.BestAvailableEncryption(secret) if secret
                  else serialization.NoEncryption())
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                encryption)
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, public


def public_pem_of(private_key: Any) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return load_private_key(private_key).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def _raw_public(public_key: Any) -> bytes:
    from cryptography.hazmat.primitives import serialization

    from .keys import load_public_key

    return load_public_key(public_key).public_bytes(serialization.Encoding.Raw,
                                                    serialization.PublicFormat.Raw)


def _token(prefix: str, body: Mapping[str, Any], private_key: Any) -> str:
    raw = canonical_json(dict(body))
    signature = load_private_key(private_key).sign(raw)
    return f"{prefix}{b64url_encode(raw)}.{b64url_encode(signature)}"


# ── issuer certificates ──────────────────────────────────────────────────────

def certify_issuer(master_key: Any, issuer_public_key: Any, *, issuer_id: str,
                   not_after: datetime | str, not_before: datetime | str | None = None,
                   products: Iterable[str] = ("*",), may_revoke: bool = True) -> str:
    """ELI's offline key vouches for an issuer key: keys it signs between ``not_before`` and
    ``not_after``, for these products, are ELI keys. Returns the certificate token."""
    products = sorted({str(p).lower() for p in products}) or ["*"]
    for product in products:
        if product != "*" and not is_product(product):
            raise InvalidKey(f"unknown product {product!r}")
    if not str(issuer_id or "").strip():
        raise InvalidKey("an issuer needs an id")
    start = not_before or _now()
    body = {
        "type": "eli-issuer",
        "issuer_id": str(issuer_id).strip(),
        "public_key": b64url_encode(_raw_public(issuer_public_key)),
        "not_before": start if isinstance(start, str) else _iso(start),
        "not_after": not_after if isinstance(not_after, str) else _iso(not_after),
        "products": products,
        "may_revoke": bool(may_revoke),
    }
    token = _token(CERT_PREFIX, body, master_key)
    from .keys import read_certificate

    read_certificate(token, public_pem_of(master_key))  # refuse anything unreadable
    return token


# ── keys ─────────────────────────────────────────────────────────────────────

def licence_body(product: str, tier: str, *, seats: int | None = None,
                 major_version: int | None = None, includes: Mapping[str, int] | Iterable[str] | None = None,
                 expires_at: datetime | str | None = None, days: int | None = None,
                 customer_ref: str | None = None, license_id: str | None = None,
                 machine: str | None = None, issued_at: datetime | str | None = None) -> dict[str, Any]:
    """The contents of a key, with every default the products assume filled in.

    A trial ends after ``TRIAL_DAYS`` unless told otherwise; paid keys never end unless given
    ``days`` or ``expires_at``. ``machine`` binds the key to one machine's fingerprint.
    """
    product = str(product or "").lower()
    tier = str(tier or "").lower()
    issued = issued_at or _now()
    issued_dt = (issued if isinstance(issued, datetime)
                 else datetime.fromisoformat(str(issued).replace("Z", "+00:00")))
    if isinstance(includes, Mapping):
        inc = {str(k).lower(): int(v) for k, v in includes.items()}
    else:
        inc = parse_includes(includes)
    if expires_at is None and days is None and tier == "trial":
        days = TRIAL_DAYS
    ends = expires_at if expires_at is not None else (
        issued_dt + timedelta(days=int(days)) if days is not None else None)
    body: dict[str, Any] = {
        "version": format_for(product),
        "license_id": license_id or f"lic_{uuid.uuid4().hex}",
        "tier": tier,
        "seats": int(seats if seats is not None else TIER_SEATS.get(tier, 1)),
        "issued_at": issued if isinstance(issued, str) else _iso(issued),
        "expires_at": None if ends is None else (ends if isinstance(ends, str) else _iso(ends)),
        "customer_ref": customer_ref,
        "product": product,
        "major_version": int(major_version if major_version is not None else default_major(product)),
    }
    if inc:
        body["includes"] = inc
    if machine:
        body["machine"] = str(machine).lower()
    return body


def sign_key(body: Mapping[str, Any], private_key: Any, *, certificate: str | None = None,
             public_key: Any = None) -> str:
    """Sign a key's contents with ELI's key, or with an issuer key and its certificate.

    The key is read back before it is returned (against ``public_key``, ELI's own by default),
    so a key the products would refuse is never handed out.
    """
    fields = dict(body)
    fields.pop("issuer", None)
    if certificate:
        fields["issuer"] = certificate
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = load_private_key(private_key).sign(raw)
    key = f"{PREFIX}{b64url_encode(raw)}.{b64url_encode(signature)}"
    read_key(key, public_key)
    return key


def issue(product: str, tier: str, private_key: Any, *, certificate: str | None = None,
          public_key: Any = None, **fields: Any) -> tuple[str, License]:
    """Make a key in one step: (the key, what it grants)."""
    key = sign_key(licence_body(product, tier, **fields), private_key, certificate=certificate,
                   public_key=public_key)
    return key, read_key(key, public_key)


# ── revocation ───────────────────────────────────────────────────────────────

def sign_revocations(private_key: Any, *, serial: int, licences: Iterable[str] = (),
                     issuers: Iterable[str] = (), certificate: str | None = None,
                     issued_at: datetime | str | None = None) -> str:
    """A list of licences (and, from ELI's own key only, issuers) that no product may honour.

    A later list from the same signer has a higher ``serial`` and replaces the earlier one, so
    each list is complete, not a delta. An issuer's list withdraws only keys that issuer made.
    """
    if certificate and list(issuers):
        raise InvalidKey("only ELI's own key withdraws an issuer")
    when = issued_at or _now()
    body: dict[str, Any] = {
        "type": "eli-revocations",
        "serial": int(serial),
        "issued_at": when if isinstance(when, str) else _iso(when),
        "licences": sorted({str(x) for x in licences}),
        "issuers": sorted({str(x) for x in issuers}),
    }
    if certificate:
        body["issuer"] = certificate
    return _token(REVOCATION_PREFIX, body, private_key)
