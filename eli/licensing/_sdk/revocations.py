"""Licences that must no longer be honoured: refunded, disputed, leaked, or made by an issuer
that has been withdrawn.

A key works offline, so withdrawing one takes effect when a product learns of it: from the
list shipped inside each SDK release, and from lists the product saves when it is online (a
product that never connects learns at its next update). Each list is signed, complete rather
than a delta, and numbered; a later list from the same signer replaces an earlier one.
ELI's own key may withdraw any licence and any issuer. A certified issuer may withdraw only
licences it issued itself.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

from .errors import InvalidKey
from .keys import (
    License,
    _issuer_verifier,
    _json_object,
    _signed_by,
    _split,
    eli_public_key,
    load_public_key,
    read_certificate,
)

PREFIX = "ELIREV-"


@dataclass(frozen=True)
class RevocationList:
    signer: str  # "" for ELI's own key, else the issuer id
    serial: int
    issued_at: str
    licences: frozenset[str]
    issuers: frozenset[str] = frozenset()
    token: str = ""


def read_revocations(token: str, public_key: Any = None) -> RevocationList:
    """Verify a revocation list. Raises InvalidKey."""
    body, signature = _split(token, PREFIX, "revocation list")
    root = eli_public_key() if public_key is None else load_public_key(public_key)
    raw = _json_object(body, "revocation list") if body else {}
    if raw.get("type") != "eli-revocations":
        raise InvalidKey("not a revocation list")
    signer = ""
    if _signed_by(root, signature, body):
        if raw.get("issuer"):
            raise InvalidKey("a list signed by ELI's own key carries no issuer certificate")
    else:
        if not raw.get("issuer"):
            raise InvalidKey("the revocation list is not signed by ELI")
        cert = read_certificate(str(raw["issuer"]), root)
        if not cert.may_revoke:
            raise InvalidKey(f"issuer {cert.issuer_id!r} may not withdraw licences")
        if not _signed_by(_issuer_verifier(cert), signature, body):
            raise InvalidKey("the revocation list's signature is invalid")
        if raw.get("issuers"):
            raise InvalidKey("only ELI's own key withdraws an issuer")
        signer = cert.issuer_id
    try:
        return RevocationList(
            signer=signer,
            serial=int(raw["serial"]),
            issued_at=str(raw.get("issued_at", "")),
            licences=frozenset(str(x) for x in raw.get("licences") or []),
            issuers=frozenset(str(x) for x in raw.get("issuers") or []),
            token=str(token).strip(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidKey(f"the revocation list is incomplete ({exc})") from None


@dataclass
class Revocations:
    """Every list a product knows of, the newest from each signer."""

    lists: dict[str, RevocationList] = field(default_factory=dict)

    def add(self, rev: RevocationList) -> bool:
        """Keep a list if it is newer than the one held from the same signer."""
        held = self.lists.get(rev.signer)
        if held is not None and held.serial >= rev.serial:
            return False
        self.lists[rev.signer] = rev
        return True

    def add_tokens(self, tokens: Iterable[str], public_key: Any = None) -> int:
        """Add every list that verifies; the others are skipped, never trusted."""
        added = 0
        for token in tokens:
            token = str(token or "").strip()
            if not token or token.startswith("#"):
                continue
            try:
                added += self.add(read_revocations(token, public_key))
            except InvalidKey:
                continue
        return added

    def why(self, lic: License) -> str:
        """Why this licence is withdrawn, or "" if it is not."""
        own = self.lists.get("")
        if own is not None:
            if lic.license_id in own.licences:
                return f"the {lic.label} licence has been withdrawn"
            if lic.issuer_id and lic.issuer_id in own.issuers:
                return f"the {lic.label} licence came from an issuer that has been withdrawn"
        if lic.issuer_id:
            theirs = self.lists.get(lic.issuer_id)
            if theirs is not None and lic.license_id in theirs.licences:
                return f"the {lic.label} licence has been withdrawn"
        return ""

    def tokens(self) -> list[str]:
        return [r.token for _, r in sorted(self.lists.items()) if r.token]


def shipped(public_key: Any = None) -> Revocations:
    """The lists shipped inside this SDK release (``eli_sdk/revoked.list``, one per line)."""
    out = Revocations()
    try:
        text = files(__package__ or "eli_sdk").joinpath("revoked.list").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return out
    out.add_tokens(text.splitlines(), public_key)
    return out
