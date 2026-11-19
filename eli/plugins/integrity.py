"""Integrity and publisher identity for community plugins.

The marketplace belongs to the community, so there is no vendor to vouch for an
upload and ELI must never pretend otherwise. What ELI CAN do is make two much
narrower promises, both verifiable on the operator's own machine:

  * **You got what the listing described.** The listing carries a sha256 of the
    plugin source; the download is hashed before it is ever written to disk. A
    mismatch is a hard refusal — this is what stops a compromised mirror, a
    man-in-the-middle on a plain-http source, or a publisher silently swapping the
    file under an unchanged listing.

  * **It came from a publisher you already chose to trust.** Optional ed25519
    signatures verified against keys the OPERATOR added, plus the key of ELI's own
    curated marketplace, which ships with ELI. On a community registry an unsigned
    plugin, or one from an unknown key, is not blocked; it is reported as
    unverified, loudly, and the operator decides. On the curated registry it is
    refused (see eli.plugins.marketplace).

  * **The release is the one that was signed.** A signature over the file proves
    the bytes and nothing else, so a release signature covers a statement of
    everything that decides what runs and what it may do — id, version, checksum,
    permissions, dependencies, MCP launch command. And one id and version is one
    release: a listing that changes under a version this machine has already seen
    is refused.

Hash checking is mandatory and dependency-free. Signature checking needs
`cryptography`; where it is absent the plugin is reported as unverifiable rather
than treated as verified.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from eli.core.secure_io import secure_write_text
from eli.utils.log import get_logger

log = get_logger(__name__)

VERIFIED_SIGNED = "signed_trusted"
VERIFIED_HASH_ONLY = "hash_only"
UNVERIFIED = "unverified"
FAILED = "failed"

_lock = threading.RLock()


def _publishers_path() -> Path:
    from eli.core.paths import config_dir
    return Path(config_dir()) / "plugin_publishers.json"


# ── hashing ────────────────────────────────────────────────────────────────────

def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_hash(data: bytes, expected: Optional[str]) -> Dict[str, Any]:
    """Compare a download against the hash the listing promised.

    A listing with no hash is not an error here — it is reported as unpinned so the
    caller can decide. It is, however, the single biggest quality signal about a
    publisher, and the install flow surfaces it as such.
    """
    actual = sha256_of(data)
    if not expected:
        return {"ok": True, "pinned": False, "actual": actual,
                "reason": "The listing did not pin a checksum, so ELI cannot tell "
                          "whether this is the file the publisher intended."}
    expected = str(expected).strip().lower()
    if actual != expected:
        return {"ok": False, "pinned": True, "actual": actual, "expected": expected,
                "reason": ("The downloaded file does not match the checksum in the "
                           "listing. It has been altered in transit or at the source. "
                           "Installation refused.")}
    return {"ok": True, "pinned": True, "actual": actual, "expected": expected,
            "reason": "Matches the checksum in the listing."}


# ── publisher trust ────────────────────────────────────────────────────────────

def _load_publishers() -> Dict[str, Dict[str, Any]]:
    p = _publishers_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("publishers", {}) if isinstance(data, dict) else {}
    except Exception:
        log.debug("[PLUGIN-TRUST] unreadable publisher file", exc_info=True)
        return {}


def _save_publishers(pubs: Dict[str, Dict[str, Any]]) -> None:
    p = _publishers_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    secure_write_text(p, json.dumps(
        {"version": 1, "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "publishers": pubs}, indent=2))


# ── the marketplace's own signing identity ─────────────────────────────────────
# ELI's official marketplace is CURATED: submissions are reviewed and only listed
# once the maintainer signs them, and this is the key that signature is checked
# against. It ships with ELI rather than being added by the operator, because a
# key the user has to install by hand is a key most users will never install —
# and an unverifiable curated registry is just an uncurated one with a badge.
#
# Empty until the marketplace is stood up; see docs/MARKETPLACE.md. Overridable
# via ELI_MARKETPLACE_PUBLISHER_KEY for testing and for anyone running their own
# curated registry from an ELI build.
OFFICIAL_PUBLISHER_ID = "eli-marketplace"
OFFICIAL_PUBLISHER_KEY = "EDziNM24LH45Cn8OzuKS2SJQJp3QiT8B7DWXQX17hVY="


def _builtin_publishers() -> Dict[str, Dict[str, Any]]:
    """The shipped official key, if this build has one."""
    key = (os.environ.get("ELI_MARKETPLACE_PUBLISHER_KEY", "").strip()
           or OFFICIAL_PUBLISHER_KEY).strip()
    if not key:
        return {}
    try:
        raw = base64.b64decode(key, validate=True)
        if len(raw) != 32:
            log.warning("[INTEGRITY] official marketplace key is %d bytes, not 32 — ignored",
                        len(raw))
            return {}
    except Exception:
        log.warning("[INTEGRITY] official marketplace key is not valid base64 — ignored")
        return {}
    return {OFFICIAL_PUBLISHER_ID: {
        "public_key": base64.b64encode(raw).decode("ascii"),
        "label": "ELI Marketplace (official)",
        "added": "shipped",
        "fingerprint": hashlib.sha256(raw).hexdigest()[:16],
        "builtin": True,
    }}


def trusted_publishers() -> Dict[str, Dict[str, Any]]:
    """Operator-trusted publishers, plus the shipped official marketplace key.

    The builtin entry is merged LAST so an operator file cannot shadow it. Letting
    a local edit replace the official key would turn "reviewed and signed by the
    maintainer" into "signed by whoever wrote your config", which is exactly the
    claim curation is supposed to make unforgeable.
    """
    with _lock:
        out = {k: dict(v) for k, v in _load_publishers().items()}
    out.update(_builtin_publishers())
    return out


def trust_publisher(publisher_id: str, public_key_b64: str, label: str = "") -> Dict[str, Any]:
    """Add a publisher key the operator has decided to trust.

    Deliberately an explicit act with no 'trust on first use' shortcut: TOFU here
    would mean the first plugin you install from anyone becomes trusted forever,
    which is the same as no check at all.
    """
    publisher_id = str(publisher_id or "").strip()
    if not publisher_id:
        return {"ok": False, "problems": ["Publisher id is required."]}
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
    except Exception as exc:
        return {"ok": False, "problems": [f"Public key is not valid base64: {exc}"]}
    if len(raw) != 32:
        return {"ok": False, "problems": [
            f"An ed25519 public key is 32 bytes; this is {len(raw)}."]}

    with _lock:
        pubs = _load_publishers()
        pubs[publisher_id] = {
            "public_key": base64.b64encode(raw).decode("ascii"),
            "label": str(label or publisher_id),
            "added": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "fingerprint": hashlib.sha256(raw).hexdigest()[:16],
        }
        _save_publishers(pubs)
    return {"ok": True, "publisher": publisher_id,
            "fingerprint": pubs[publisher_id]["fingerprint"], "problems": []}


def untrust_publisher(publisher_id: str) -> Dict[str, Any]:
    if publisher_id in _builtin_publishers():
        return {"ok": False, "problems": [
            f"{publisher_id!r} is the official marketplace's own key and ships with ELI; "
            f"it cannot be removed. Removing it would not stop curated installs, it "
            f"would only stop them being verified. Disable the registry instead."]}
    with _lock:
        pubs = _load_publishers()
        if publisher_id not in pubs:
            return {"ok": False, "problems": [f"Publisher {publisher_id!r} is not trusted."]}
        pubs.pop(publisher_id)
        _save_publishers(pubs)
    return {"ok": True, "problems": []}


# ── signatures ─────────────────────────────────────────────────────────────────

def signing_available() -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: F401
        return True
    except Exception:
        return False


def verify_signature(data: bytes, signature_b64: Optional[str],
                     publisher_id: Optional[str]) -> Dict[str, Any]:
    """Check an ed25519 signature against a publisher key the operator trusts."""
    if not signature_b64 or not publisher_id:
        return {"ok": False, "status": UNVERIFIED,
                "reason": "This plugin is not signed. ELI cannot confirm who wrote it."}

    pubs = trusted_publishers()
    entry = pubs.get(str(publisher_id))
    if not entry:
        return {"ok": False, "status": UNVERIFIED,
                "reason": (f"Signed by '{publisher_id}', who is not in your list of trusted "
                           f"publishers. ELI cannot confirm the signature is theirs.")}

    if not signing_available():
        return {"ok": False, "status": UNVERIFIED,
                "reason": ("This build cannot check signatures (the 'cryptography' package "
                           "is not installed), so the signature is being ignored rather "
                           "than trusted.")}

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(entry["public_key"]))
        key.verify(base64.b64decode(signature_b64), data)
    except Exception as exc:
        return {"ok": False, "status": FAILED,
                "reason": (f"The signature does not match publisher '{publisher_id}'. "
                           f"The file may have been altered ({type(exc).__name__}). "
                           f"Installation refused.")}

    return {"ok": True, "status": VERIFIED_SIGNED,
            "reason": (f"Signed by '{entry.get('label') or publisher_id}' "
                       f"(fingerprint {entry.get('fingerprint')}), a publisher you trust.")}


def assess(data: bytes, listing: Dict[str, Any]) -> Dict[str, Any]:
    """Full integrity verdict for one download.

    Returns {ok, status, hash, signature, warnings}. `ok` False means REFUSE —
    reserved for active evidence of tampering (a hash or signature mismatch), never
    for the ordinary case of an unsigned community plugin.
    """
    h = verify_hash(data, listing.get("sha256"))
    s = verify_signature(data, listing.get("signature"), listing.get("publisher_id"))
    r = verify_release(listing)

    warnings = []
    if not h["ok"]:
        return {"ok": False, "status": FAILED, "hash": h, "signature": s, "release": r,
                "warnings": [h["reason"]]}
    for check in (s, r):
        if check["status"] == FAILED:
            return {"ok": False, "status": FAILED, "hash": h, "signature": s, "release": r,
                    "warnings": [check["reason"]]}

    if not h.get("pinned"):
        warnings.append(h["reason"])
    # A release signature vouches for the file only through the checksum it signs.
    release_covers_file = bool(r["ok"] and h.get("pinned"))
    signed = s["ok"] or release_covers_file
    if not signed:
        warnings.append(r["reason"] if r.get("present") else s["reason"])

    status = VERIFIED_SIGNED if signed else (
        VERIFIED_HASH_ONLY if h.get("pinned") else UNVERIFIED)
    return {"ok": True, "status": status, "hash": h, "signature": s, "release": r,
            "warnings": warnings}


# ── release statements ─────────────────────────────────────────────────────────
# A signature over the file proves the bytes, and nothing else: the permissions a
# listing asks for, the PyPI packages it installs and the command an MCP server is
# started with all sit in the listing beside it, where anyone who can rewrite the
# index could change them under a still-valid signature. A release statement is
# the signed record of all of it at once, for one id and version. Display text
# (name, description, price, links) is left out on purpose, so a publisher can fix
# a typo or change a price without a new review.

RELEASE_STATEMENT_VERSION = 1


def listing_kind(listing: Dict[str, Any]) -> str:
    return str(listing.get("kind") or ("mcp" if listing.get("mcp") else "plugin"))


def release_statement(listing: Dict[str, Any]) -> bytes:
    """The canonical bytes a release signature covers."""
    mcp = listing.get("mcp")
    body = {
        "statement": RELEASE_STATEMENT_VERSION,
        "id": str(listing.get("id") or ""),
        "version": str(listing.get("version") or ""),
        "kind": listing_kind(listing),
        "sha256": str(listing.get("sha256") or "").lower() or None,
        "permissions": sorted({str(p) for p in (listing.get("permissions") or [])}),
        "pip": sorted(str(p) for p in (listing.get("pip") or [])),
        "mcp": mcp if isinstance(mcp, dict) and mcp else None,
        "official": bool(listing.get("official")),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def release_digest(listing: Dict[str, Any]) -> str:
    return hashlib.sha256(release_statement(listing)).hexdigest()


def verify_release(listing: Dict[str, Any]) -> Dict[str, Any]:
    """Check a listing's release signature against the publishers this machine trusts.

    Same shape as ``verify_signature``, plus ``present``. FAILED means the statement
    was signed and then changed, which is evidence of tampering.
    """
    signature = listing.get("release_signature")
    if not signature:
        return {"ok": False, "status": UNVERIFIED, "present": False,
                "reason": ("The listing has no release signature, so nothing vouches "
                           "for its permissions or dependencies.")}
    result = verify_signature(release_statement(listing), signature,
                              listing.get("publisher_id"))
    result["present"] = True
    if result["status"] == FAILED:
        result["reason"] = (
            f"The release signature does not match publisher "
            f"'{listing.get('publisher_id')}': the listing's id, version, checksum, "
            f"permissions or dependencies changed after it was signed. Refused.")
    return result


def is_official(listing: Dict[str, Any], release: Optional[Dict[str, Any]] = None) -> bool:
    """Published by ELI itself: only the marketplace key can say so, inside a signed
    statement. A publisher's own key claiming ``official`` proves nothing."""
    release = release if release is not None else verify_release(listing)
    return bool(release.get("ok")
                and str(listing.get("publisher_id") or "") == OFFICIAL_PUBLISHER_ID
                and listing.get("official"))


# ── trust states, as the operator sees them ─────────────────────────────────────

TRUST_OFFICIAL = "official"
TRUST_REVIEWED = "reviewed"
TRUST_SIGNED = "signed"
TRUST_CHECKSUM = "checksum"
TRUST_UNVERIFIED = "unverified"

TRUST_LABELS = {
    TRUST_OFFICIAL: "Official ELI: published and supported by ELI",
    TRUST_REVIEWED: ("Reviewed: a person read this exact version before the "
                     "marketplace signed it"),
    TRUST_SIGNED: "Signed by a publisher you trust",
    TRUST_CHECKSUM: "Checksum only: the file matches the listing, nothing proves who wrote it",
    TRUST_UNVERIFIED: "Unverified: nothing proves who wrote it or that it is unchanged",
}


def trust_state(listing: Dict[str, Any], *, curated: bool,
                release: Optional[Dict[str, Any]] = None) -> str:
    """What the listing itself proves, before anything is downloaded.

    Never more than was checked: "reviewed" needs the curated registry AND the
    marketplace key's signature on the release; "official" needs that and the
    signed statement saying so.
    """
    release = release if release is not None else verify_release(listing)
    if release.get("ok"):
        if curated and str(listing.get("publisher_id") or "") == OFFICIAL_PUBLISHER_ID:
            return TRUST_OFFICIAL if is_official(listing, release) else TRUST_REVIEWED
        if not curated:
            return TRUST_SIGNED
    if listing.get("sha256"):
        return TRUST_CHECKSUM
    return TRUST_UNVERIFIED


# ── one id and version, one release ─────────────────────────────────────────────

def _releases_path() -> Path:
    from eli.core.paths import config_dir
    return Path(config_dir()) / "plugin_releases.json"


def _release_key(registry: str, listing: Dict[str, Any]) -> str:
    return f"{registry}|{listing.get('id')}|{listing.get('version')}"


def _load_releases() -> Dict[str, str]:
    p = _releases_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return dict(data.get("releases") or {})
    except Exception:
        log.debug("[INTEGRITY] release ledger unreadable — starting afresh", exc_info=True)
        return {}


def check_release_unchanged(registry: str, listing: Dict[str, Any]) -> Dict[str, Any]:
    """Refuse a listing whose release changed under an id and version seen before.

    A version that was reviewed is that exact release. Different bytes, permissions or
    dependencies under the same number mean either a publisher re-using a version or
    someone swapping the release behind the review, and both need a new version.
    """
    seen = _load_releases().get(_release_key(registry, listing))
    if seen and seen != release_digest(listing):
        return {"ok": False, "reason": (
            f"'{listing.get('id')}' version {listing.get('version')} has changed since "
            f"this machine last saw it: its checksum, permissions, dependencies or launch "
            f"command differ under the same version number. An approved version never "
            f"changes, so this is refused. The publisher must release a new version.")}
    return {"ok": True}


def remember_release(registry: str, listing: Dict[str, Any]) -> None:
    with _lock:
        releases = _load_releases()
        releases[_release_key(registry, listing)] = release_digest(listing)
        p = _releases_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            secure_write_text(p, json.dumps({"version": 1, "releases": releases}, indent=2))
        except OSError:
            log.debug("[INTEGRITY] could not record release", exc_info=True)


def status_summary(status: str) -> str:
    """One line for the operator. Never overstates what was actually checked."""
    return {
        VERIFIED_SIGNED: "Signed by a publisher you trust, and the file matches the listing.",
        VERIFIED_HASH_ONLY: ("The file matches the checksum in the listing, but nothing "
                             "proves who wrote it."),
        UNVERIFIED: ("Unverified. Nothing proves who wrote this or that it is unchanged. "
                     "Install only if you trust the source."),
        FAILED: "Verification FAILED — do not install.",
    }.get(status, "Unknown verification state.")


__all__ = [
    "sha256_of", "verify_hash", "verify_signature", "assess", "status_summary",
    "trust_publisher", "untrust_publisher", "trusted_publishers", "signing_available",
    "OFFICIAL_PUBLISHER_ID", "OFFICIAL_PUBLISHER_KEY",
    "VERIFIED_SIGNED", "VERIFIED_HASH_ONLY", "UNVERIFIED", "FAILED",
    "listing_kind", "release_statement", "release_digest", "verify_release", "is_official",
    "trust_state", "TRUST_LABELS", "check_release_unchanged", "remember_release",
]
