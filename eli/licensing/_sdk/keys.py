"""Reading ELI licence keys and deciding what they grant.

Verification only. Keys are issued with a private signing key that never leaves its owner; this
module needs nothing but the matching public key, and works offline.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._encoding import b64url_decode
from .errors import InvalidKey, NeedsActivation
from .products import (
    LEGACY_MAJOR,
    PLATFORM,
    is_product,
    major_line,
    product_label,
    valid_id,
)
from .versions import Version

PREFIX = "ELIV3-"
PLATFORM_FORMAT = 1
PRODUCT_FORMAT = 2
# A key to be activated, for any product: what was bought, for no computer yet. It opens nothing
# until the store binds it to one computer (eli_sdk.activation), which counts the computers it is
# on against its seats. Readers from before it refuse it outright ("unsupported key format 3"),
# so it cannot be passed round to copies that would not count.
ACTIVATION_FORMAT = 3
TIERS = frozenset({"trial", "personal", "pro", "enterprise"})

_SHAPE = re.compile(r"^ELIV3-[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def format_for(product: str) -> int:
    """A key for ELI itself is format 1, which every ELI v3 build reads. A bundle's key is
    format 2, which builds from before bundles had keys refuse instead of misreading."""
    return PLATFORM_FORMAT if product == PLATFORM else PRODUCT_FORMAT


def normalize(key: str) -> str:
    text = str(key or "").strip()
    if text.upper().startswith(PREFIX):
        return PREFIX + text[len(PREFIX):].strip()
    return text


def fingerprint(key: str) -> str:
    return hashlib.sha256(normalize(key).encode("utf-8")).hexdigest()[:16]


def load_public_key(source: Any):
    """An Ed25519 public key from PEM text or bytes, a path to a PEM file, or a key object."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if isinstance(source, Ed25519PublicKey):
        return source
    if isinstance(source, bytes):
        data = source
    elif isinstance(source, str) and "-----BEGIN" in source:
        data = source.encode("ascii")
    else:
        data = Path(source).expanduser().read_bytes()
    try:
        key = serialization.load_pem_public_key(data)
    except ValueError as exc:
        raise InvalidKey(f"not a PEM public key ({exc})") from None
    if not isinstance(key, Ed25519PublicKey):
        raise InvalidKey("the public key is not an Ed25519 key")
    return key


def eli_public_key():
    """ELI's own verification key. Every ELI product checks its keys against it."""
    from importlib.resources import files

    return load_public_key(files(__package__ or "eli_sdk").joinpath("eli.pub").read_bytes())


def _when(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class License:
    """What one key grants."""

    license_id: str
    tier: str
    seats: int
    issued_at: str
    product: str = PLATFORM
    major_version: int = LEGACY_MAJOR
    includes: Mapping[str, int] = field(default_factory=dict)
    expires_at: str | None = None
    customer_ref: str | None = None
    format: int = PLATFORM_FORMAT
    fingerprint: str = ""
    # Who signed it: "" for ELI's own key, otherwise the id of the certified issuer.
    issuer_id: str = ""
    # The machine it is bound to (machine.fingerprint()), or "" for a key any machine may use.
    machine: str = ""
    # The store's id for this computer's activation of the licence ("" for any other key): a
    # computer released from a licence is withdrawn by it, without withdrawing the licence.
    activation: str = ""

    @property
    def needs_activation(self) -> bool:
        """A key bought to be activated: it opens nothing until the store binds it to a computer."""
        return self.format == ACTIVATION_FORMAT

    @property
    def label(self) -> str:
        return product_label(self.product)

    @property
    def perpetual(self) -> bool:
        return not self.expires_at

    @property
    def trial(self) -> bool:
        return self.tier == "trial"

    def expired(self, now: datetime | None = None) -> bool:
        ends = _when(self.expires_at)
        return ends is not None and (now or datetime.now(timezone.utc)) > ends

    def bound_elsewhere(self, machine: str | None) -> bool:
        """True when the key names a machine and it is not this one."""
        return bool(self.machine) and machine is not None and self.machine != str(machine).lower()

    def as_dict(self) -> dict[str, Any]:
        return {
            "license_id": self.license_id, "tier": self.tier, "seats": self.seats,
            "issued_at": self.issued_at, "product": self.product,
            "major_version": self.major_version, "includes": dict(self.includes),
            "expires_at": self.expires_at, "customer_ref": self.customer_ref,
            "format": self.format, "fingerprint": self.fingerprint,
            "issuer_id": self.issuer_id, "machine": self.machine,
            "activation": self.activation, "needs_activation": self.needs_activation,
        }


@dataclass(frozen=True)
class IssuerCertificate:
    """ELI's offline key vouching for an issuer key (eli_sdk.issuing.certify_issuer)."""

    issuer_id: str
    public_key: bytes
    not_before: str
    not_after: str
    products: tuple[str, ...]
    may_revoke: bool

    def covers(self, product: str) -> bool:
        return "*" in self.products or product in self.products

    def valid_at(self, moment: Any) -> bool:
        when, start, end = _when(moment), _when(self.not_before), _when(self.not_after)
        return when is not None and start is not None and end is not None and start <= when <= end


def _split(token: str, prefix: str, what: str) -> tuple[bytes, bytes]:
    text = str(token or "").strip()
    if not text.upper().startswith(prefix) or text.count(".") != 1:
        raise InvalidKey(f"not an ELI {what}")
    body_part, sig_part = text[len(prefix):].split(".", 1)
    try:
        return b64url_decode(body_part), b64url_decode(sig_part)
    except ValueError:
        raise InvalidKey(f"the {what} is not valid base64") from None


def _signed_by(verifier: Any, signature: bytes, body: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature

    try:
        verifier.verify(signature, body)
        return True
    except InvalidSignature:
        return False


def _json_object(body: bytes, what: str) -> dict[str, Any]:
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise InvalidKey(f"the {what}'s contents are not JSON") from None
    if not isinstance(raw, dict):
        raise InvalidKey(f"the {what}'s contents are not an object")
    return raw


def read_certificate(token: str, public_key: Any = None) -> IssuerCertificate:
    """Verify an issuer certificate against ELI's key (or the one given). Raises InvalidKey."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    body, signature = _split(token, "ELICERT-", "issuer certificate")
    verifier = eli_public_key() if public_key is None else load_public_key(public_key)
    if not _signed_by(verifier, signature, body):
        raise InvalidKey("the issuer certificate is not signed by ELI")
    raw = _json_object(body, "issuer certificate")
    if raw.get("type") != "eli-issuer":
        raise InvalidKey("not an issuer certificate")
    try:
        cert = IssuerCertificate(
            issuer_id=str(raw["issuer_id"]),
            public_key=b64url_decode(str(raw["public_key"])),
            not_before=str(raw["not_before"]),
            not_after=str(raw["not_after"]),
            products=tuple(str(p).lower() for p in raw.get("products") or ["*"]),
            may_revoke=bool(raw.get("may_revoke", False)),
        )
        Ed25519PublicKey.from_public_bytes(cert.public_key)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidKey(f"the issuer certificate is incomplete ({exc})") from None
    if _when(cert.not_before) is None or _when(cert.not_after) is None:
        raise InvalidKey("the issuer certificate's dates are unreadable")
    return cert


def _issuer_verifier(cert: IssuerCertificate):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    return Ed25519PublicKey.from_public_bytes(cert.public_key)


def read_key(key: str, public_key: Any = None) -> License:
    """Verify a key and return what it grants. Raises InvalidKey.

    A key is signed by ELI's own key, or by an issuer key that ELI's key has certified: then
    the certificate travels inside the key, and the key must have been issued while the
    certificate was valid, for a product it covers. Checked against ELI's own key unless
    another is given.
    """
    text = normalize(key)
    if not _SHAPE.match(text):
        raise InvalidKey("not an ELI licence key")
    body_part, sig_part = text[len(PREFIX):].split(".", 1)
    try:
        body, signature = b64url_decode(body_part), b64url_decode(sig_part)
    except ValueError:
        raise InvalidKey("the key is not valid base64") from None
    try:
        root = eli_public_key() if public_key is None else load_public_key(public_key)
    except InvalidKey:
        raise
    except Exception as exc:  # unreadable key file
        raise InvalidKey(f"the public key cannot be read ({exc})") from None

    if _signed_by(root, signature, body):
        raw = _json_object(body, "key")
        if raw.get("issuer"):
            raise InvalidKey("a key signed by ELI's own key carries no issuer certificate")
        return _license(raw, fingerprint(text))

    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raw = None
    certificate = raw.get("issuer") if isinstance(raw, dict) else None
    if not certificate:
        raise InvalidKey("the key's signature is invalid")
    cert = read_certificate(str(certificate), root)
    if not _signed_by(_issuer_verifier(cert), signature, body):
        raise InvalidKey("the key's signature is invalid")
    lic = _license(raw, fingerprint(text), issuer_id=cert.issuer_id)
    if not cert.covers(lic.product):
        raise InvalidKey(f"issuer {cert.issuer_id!r} is not certified for {lic.label}")
    if not cert.valid_at(lic.issued_at):
        raise InvalidKey(f"the key was issued outside issuer {cert.issuer_id!r}'s certificate")
    return lic


def _license(raw: dict[str, Any], key_print: str, *, issuer_id: str = "") -> License:
    try:
        lic = License(
            license_id=str(raw["license_id"]),
            tier=str(raw["tier"]).lower(),
            seats=max(1, int(raw.get("seats", 1))),
            issued_at=str(raw["issued_at"]),
            product=str(raw.get("product") or PLATFORM).lower(),
            major_version=int(raw.get("major_version") or LEGACY_MAJOR),
            includes={str(k).lower(): int(v) for k, v in dict(raw.get("includes") or {}).items()},
            expires_at=raw.get("expires_at"),
            customer_ref=raw.get("customer_ref"),
            format=int(raw.get("version", PLATFORM_FORMAT)),
            fingerprint=key_print,
            issuer_id=issuer_id,
            machine=str(raw.get("machine") or "").lower(),
            activation=str(raw.get("activation") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidKey(f"the key's contents are incomplete ({exc})") from None
    if lic.format not in (PLATFORM_FORMAT, PRODUCT_FORMAT, ACTIVATION_FORMAT):
        raise InvalidKey(f"unsupported key format {lic.format}")
    if lic.tier not in TIERS:
        raise InvalidKey(f"unknown tier {lic.tier!r}")
    if not is_product(lic.product):
        raise InvalidKey(f"this key is for {lic.product!r}, a product this SDK does not know; "
                         "a newer SDK may")
    if lic.format != format_for(lic.product) and not lic.needs_activation:
        raise InvalidKey("the key's format does not match its product")
    if lic.needs_activation and (lic.machine or lic.activation):
        raise InvalidKey("a key to be activated names no computer")
    if lic.activation and not lic.machine:
        raise InvalidKey("an activation names the computer it is for")
    if lic.major_version < 1:
        raise InvalidKey("the key names no version line")
    if lic.trial and not lic.expires_at:
        raise InvalidKey("a trial key must end")
    if lic.expires_at and _when(lic.expires_at) is None:
        raise InvalidKey("the key's end date is unreadable")
    check_includes(lic.product, lic.includes)
    return lic


def check_includes(product: str, includes: Mapping[str, int]) -> None:
    """Only an ELI v3 key includes bundles, each named by a valid id with a real version line.
    A bundle this SDK does not know yet (released later) is allowed: it grants nothing here,
    and the key still opens everything else it covers."""
    if not includes:
        return
    if product != PLATFORM:
        raise InvalidKey("only an ELI v3 key can include bundles")
    for name, line in includes.items():
        if name == PLATFORM or not valid_id(name):
            raise InvalidKey(f"included product {name!r} is not a bundle")
        if int(line) < 1:
            raise InvalidKey(f"included {name!r} needs a version line of 1 or more")


@dataclass(frozen=True)
class Entitlement:
    product: str
    granted: bool
    reason: str
    license: License | None = None
    included: bool = False


def entitlement(product: str, licenses: Iterable[License], *, version: str | Version | None = None,
                now: datetime | None = None, revocations: Any = None,
                machine: str | None = None) -> Entitlement:
    """Whether the licences cover a product, at a version if one is given.

    A product is covered by a key of its own, or, for a bundle, by an ELI v3 key that includes
    it. A key covers its version line and every earlier one: whoever bought 4.x may run 3.x.
    A licence that has been withdrawn (``revocations``) covers nothing, and nor does a key bound
    to a machine other than ``machine``. A paid key is preferred to a trial.
    """
    product = str(product or "").lower()
    label = product_label(product)
    line = major_line(version) if version is not None else None
    held = sorted(licenses, key=lambda lic: lic.trial)  # paid keys first
    reason = f"no {label} licence"

    def unusable(lic: License) -> str:
        withdrawn = revocations.why(lic) if revocations is not None else ""
        if withdrawn:
            return withdrawn
        if lic.needs_activation:
            return (f"the {lic.label} key has not been activated on this computer: enter it in "
                    f"{lic.label} (activating needs the internet once)")
        if lic.bound_elsewhere(machine):
            return f"this {lic.label} key is for another machine"
        return ""

    for lic in held:
        if lic.product != product:
            continue
        problem = unusable(lic)
        if problem:
            reason = problem
        elif lic.expired(now):
            reason = (f"the {label} trial has expired" if lic.trial
                      else f"the {label} licence has expired")
        elif line is not None and lic.major_version < line:
            reason = (f"your {label} licence covers {label} {lic.major_version}.x; "
                      f"{version} needs an upgrade")
        else:
            return Entitlement(product, True, f"{label} trial" if lic.trial else f"{label} licence",
                               lic)

    if product != PLATFORM:
        for lic in held:
            if lic.product != PLATFORM or product not in lic.includes:
                continue
            covered = int(lic.includes[product])
            problem = unusable(lic)
            if problem:
                reason = problem
            elif lic.expired(now):
                reason = "the ELI v3 licence that includes it has expired"
            elif line is not None and covered < line:
                reason = (f"your ELI v3 licence includes {label} {covered}.x; "
                          f"{version} needs an upgrade")
            else:
                return Entitlement(product, True, f"{label} is included in your ELI v3 licence",
                                   lic, included=True)
    return Entitlement(product, False, reason)


def shared_directory() -> Path:
    """Where every ELI product keeps the keys it has been given, so a key activated in one is
    seen by the others. ELI_KEYS_DIR overrides it."""
    override = os.environ.get("ELI_KEYS_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "ELI Keys"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ELI Keys"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "eli-keys"


REVOCATIONS_FILE = "revocations.list"


class KeyStore:
    """Licence keys kept as files, one per product, in a folder, with the revocation lists the
    products have learnt of."""

    def __init__(self, directory: str | os.PathLike[str]):
        self.directory = Path(directory).expanduser()

    @classmethod
    def shared(cls) -> KeyStore:
        """The folder every ELI product reads and writes (shared_directory)."""
        return cls(shared_directory())

    def _path(self, product: str) -> Path:
        return self.directory / f"{product}.key"

    def _write(self, path: Path, text: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".key-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def add(self, key: str, public_key: Any = None, *, machine: str | None = None,
            now: datetime | None = None) -> License:
        """Verify a key and keep it, replacing any key held for the same product.

        Refused: a key that has been withdrawn, one bound to another machine, a trial key
        offered over a paid key that still works (it would take the paid key's place), and a key
        bought to be activated (NeedsActivation: eli_sdk.activation.activate keeps the key the
        store makes of it for this computer instead).
        """
        lic = read_key(key, public_key)
        if lic.needs_activation:
            raise NeedsActivation(f"this {lic.label} key must be activated first: enter it in {lic.label} "
                                  "(activating needs the internet once)")
        if lic.machine and machine is None:
            from .machine import fingerprint as this_machine

            machine = this_machine()
        if lic.bound_elsewhere(machine):
            raise InvalidKey(f"this {lic.label} key is for another machine")
        withdrawn = self.revocations(public_key).why(lic)
        if withdrawn:
            raise InvalidKey(withdrawn)
        if lic.trial:
            current = self._held(lic.product, public_key)
            if current is not None and not current.trial and not current.expired(now):
                raise InvalidKey(f"this machine already holds a paid {lic.label} licence")
        self._write(self._path(lic.product), normalize(key) + "\n")
        return lic

    def _held(self, product: str, public_key: Any = None) -> License | None:
        path = self._path(product)
        try:
            return read_key(path.read_text(encoding="utf-8"), public_key) if path.is_file() else None
        except (InvalidKey, OSError):
            return None

    def key_text(self, product: str) -> str | None:
        """The key held for a product, as it was kept, or None."""
        path = self._path(product)
        try:
            return path.read_text(encoding="utf-8").strip() if path.is_file() else None
        except OSError:
            return None

    def remove(self, product: str) -> bool:
        path = self._path(product)
        if path.is_file():
            path.unlink()
            return True
        return False

    def licenses(self, public_key: Any = None) -> list[License]:
        """Every key held that still verifies. One that doesn't is skipped, never trusted."""
        held = []
        for path in sorted(self.directory.glob("*.key")) if self.directory.is_dir() else []:
            try:
                held.append(read_key(path.read_text(encoding="utf-8"), public_key))
            except (InvalidKey, OSError):
                continue
        return held

    # ── revocation ──────────────────────────────────────────────────────────
    def revocations(self, public_key: Any = None):
        """The lists shipped with this SDK and every newer one saved here."""
        from .revocations import shipped

        known = shipped(public_key)
        path = self.directory / REVOCATIONS_FILE
        try:
            lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        except OSError:
            lines = []
        known.add_tokens(lines, public_key)
        return known

    def add_revocations(self, token: str, public_key: Any = None) -> bool:
        """Keep a revocation list if it verifies and is newer than the one held from its signer.
        Returns whether anything changed. Raises InvalidKey for a list that does not verify."""
        from .revocations import read_revocations

        rev = read_revocations(token, public_key)
        known = self.revocations(public_key)
        if not known.add(rev):
            return False
        self._write(self.directory / REVOCATIONS_FILE, "\n".join(known.tokens()) + "\n")
        return True

    def entitlement(self, product: str, public_key: Any = None, *, version: str | None = None,
                    now: datetime | None = None, machine: str | None = None) -> Entitlement:
        held = self.licenses(public_key)
        if machine is None and any(lic.machine for lic in held):
            from .machine import fingerprint as this_machine

            machine = this_machine()
        return entitlement(product, held, version=version, now=now,
                           revocations=self.revocations(public_key), machine=machine)
