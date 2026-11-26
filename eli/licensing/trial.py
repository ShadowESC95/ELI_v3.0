"""Per-install 7-day trial grant, expiry and revocation.

A trial is a *local grant*, not a signed key: issuing a signed key needs the
product private key, which never ships. Each install still gets its own
identity — a unique ``license_id`` and a support-facing reference
(``ELI-TRIAL-…``) — so one individual's trial is distinguishable from another's
and cannot be passed around.

Trial lifecycle is recorded in marker files kept *outside* the activation state,
in two independent directories. Deleting ``activation.json`` therefore does not
re-arm the trial, and neither does deleting a single marker: the surviving copy
still carries the original ``started_at`` and any revocation.

Tamper-evidence, not tamper-proofing: markers are HMAC-signed with a constant
compiled into the product, so edits are detected, but anyone willing to read the
binary can forge one. Offline trials cannot do better than that — treat the
trial as a convenience for honest users, and rely on the signed-key path for
anything that must actually be enforced.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from eli.licensing.activation import _parse_iso
from eli.licensing.machine_id import machine_fingerprint, machine_label
from eli.licensing.types import ActivationState, MachineBinding

# Obfuscation only — see the module docstring on what this does and does not buy.
_MARKER_SECRET = b"eli-v3-trial-marker-v1/6f2a1c94d38b"
# 2: the record also keeps the latest time ELI ran (last_seen), so a clock set back is noticed.
_MARKER_VERSION = 2
_DEFAULT_TRIAL_DAYS = 7
# A clock behind the last time ELI ran by more than this stops the trial until it is right.
_CLOCK_SLACK = timedelta(hours=6)
_FIXED_NAME = ".eli-v3-trial.json"

TRIAL_SOURCE = "trial"

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def trial_duration_days() -> int:
    """Trial length from tier policy, defaulting to 7 days. ELI_TRIAL_DAYS sets it only when
    working from source (eli.licensing.activation.developer_override)."""
    from eli.licensing.activation import developer_override

    override = developer_override("ELI_TRIAL_DAYS")
    if override:
        try:
            n = int(override)
            if n > 0:
                return n
        except ValueError:
            log.debug("ignoring non-numeric ELI_TRIAL_DAYS=%r", override, exc_info=True)
    try:
        from eli.licensing.tiers import tier_config

        days = tier_config("trial").get("duration_days_default")
        if days is not None:
            n = int(days)
            if n > 0:
                return n
    except Exception:
        log.debug("tier policy unreadable; using default trial length", exc_info=True)
    return _DEFAULT_TRIAL_DAYS


# ── Marker records ──────────────────────────────────────────────────────────

@dataclass
class TrialRecord:
    version: int = _MARKER_VERSION
    license_id: str = ""
    reference: str = ""
    machine: str = ""
    started_at: str = ""
    expires_at: str = ""
    revoked_at: str | None = None
    last_seen: str = ""

    def signable(self) -> bytes:
        payload = {
            "version": self.version,
            "license_id": self.license_id,
            "reference": self.reference,
            "machine": self.machine,
            "started_at": self.started_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
        }
        if self.version >= 2:  # a version-1 record was sealed without it
            payload["last_seen"] = self.last_seen
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def signature(self) -> str:
        return hmac.new(_MARKER_SECRET, self.signable(), hashlib.sha256).hexdigest()

    def to_dict(self) -> dict[str, object]:
        data = json.loads(self.signable().decode("utf-8"))
        data["sig"] = self.signature()
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> TrialRecord | None:
        try:
            rec = cls(
                version=int(raw.get("version", _MARKER_VERSION)),  # type: ignore[arg-type]
                license_id=str(raw.get("license_id", "")),
                reference=str(raw.get("reference", "")),
                machine=str(raw.get("machine", "")),
                started_at=str(raw.get("started_at", "")),
                expires_at=str(raw.get("expires_at", "")),
                revoked_at=raw.get("revoked_at") or None,  # type: ignore[arg-type]
                last_seen=str(raw.get("last_seen", "")),
            )
        except (TypeError, ValueError):
            return None
        expected = rec.signature()
        provided = str(raw.get("sig", ""))
        if not hmac.compare_digest(expected, provided):
            return None
        return rec


def marker_paths() -> list[Path]:
    """Every location a trial marker is mirrored to.

    Two independent roots so removing one does not reset the trial. Both are
    resolved lazily because the frozen runtime hook rewrites these dirs.
    """
    paths: list[Path] = []
    try:
        from eli.licensing.storage import license_dir

        paths.append(license_dir() / "trial.json")
    except Exception:
        log.debug("license dir unavailable for trial marker", exc_info=True)
    try:
        from eli.core.paths import data_dir

        paths.append(data_dir() / ".eli_trial_state.json")
    except Exception:
        log.debug("data dir unavailable for trial marker", exc_info=True)
    if not paths:
        paths.append(Path.home() / ".eli-v3-trial-state.json")
    # And in fixed places found from the account itself, never from ELI_CONFIG_DIR, ELI_DATA_DIR,
    # ELI_LICENSE_DIR, $HOME or XDG_*: pointing those somewhere new made an ended trial look
    # like a first run.
    paths += [folder / _FIXED_NAME for folder in _fixed_marker_dirs()]
    return list(dict.fromkeys(paths))


def _fixed_marker_dirs() -> list[Path]:
    try:
        from eli.licensing._sdk.trial import default_locations

        return default_locations()
    except Exception:
        log.debug("fixed trial locations unavailable", exc_info=True)
        return []


def _default_data_dirs() -> list[Path]:
    """Where an installed ELI v3 keeps its data, found from the account itself."""
    from eli.licensing._sdk.machine import account_home, windows_folder

    home = account_home()
    if os.name == "nt":
        base = windows_folder("local") or home / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = home / ".local" / "share"
    return [base / "ELI_v3" / "artifacts"]


def used_before() -> bool:
    """ELI v3's own data from earlier use on this machine (its databases). With no trial record
    that means a trial was used and its records deleted, so none starts again."""
    folders = list(_default_data_dirs())
    try:
        from eli.core.paths import data_dir

        folders.append(data_dir())
    except Exception:
        pass
    for folder in folders:
        db = Path(folder) / "db"
        try:
            if db.is_dir() and any(p.suffix == ".sqlite3" and p.stat().st_size > 0 for p in db.iterdir()):
                return True
        except OSError:
            continue
    return False


def _read_marker(path: Path) -> TrialRecord | None:
    try:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return TrialRecord.from_dict(raw)


def load_trial_record() -> TrialRecord | None:
    """Most authoritative surviving marker for this machine.

    Revocation always wins, then the earliest ``started_at`` — so deleting the
    newer copy of a marker cannot buy extra days.
    """
    fp = machine_fingerprint()
    found = [r for p in marker_paths() if (r := _read_marker(p)) is not None]
    found = [r for r in found if not r.machine or r.machine == fp]
    if not found:
        return None
    latest_seen = max((r.last_seen for r in found), default="")
    revoked = [r for r in found if r.revoked_at]
    chosen = (sorted(revoked, key=lambda r: r.revoked_at or "")[0] if revoked
              else sorted(found, key=lambda r: r.started_at or "")[0])
    chosen.last_seen = max(chosen.last_seen, latest_seen)  # the latest any copy saw
    return chosen


def save_trial_record(rec: TrialRecord) -> list[Path]:
    """Mirror a marker to every location; succeeds if at least one write lands."""
    payload = json.dumps(rec.to_dict(), indent=2, sort_keys=True) + "\n"
    written: list[Path] = []
    for path in marker_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                log.debug("could not restrict permissions on %s", path, exc_info=True)
            written.append(path)
        except OSError:
            continue
    return written


# ── State machine ───────────────────────────────────────────────────────────

def trial_state() -> str:
    """One of ``available``, ``active``, ``expired``, ``revoked``."""
    if os.environ.get("ELI_TRIAL_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "revoked"
    rec = load_trial_record()
    if rec is None:
        return "expired" if used_before() else "available"
    if rec.revoked_at:
        return "revoked"
    started = _parse_iso(rec.started_at)
    expires = _parse_iso(rec.expires_at)
    if expires is None:
        return "expired"
    now = _utc_now()
    # Clock rolled back behind the start date: treat as tampering, not as extra
    # runway. Without this, setting the system clock back re-opens the trial.
    if started is not None and now < started - timedelta(hours=24):
        return "expired"
    # Behind the last time ELI ran: stopped until the clock is right (it cannot add days: the
    # end is fixed, and once ELI has run past it the trial is revoked for good).
    seen = _parse_iso(rec.last_seen) if rec.last_seen else None
    if seen is not None and now < seen - _CLOCK_SLACK:
        return "clock_behind"
    return "active" if now <= expires else "expired"


def touch_trial() -> None:
    """Record that ELI is running now, and write every copy of the record back, so deleting
    some copies never helps. Called at each launch while the trial runs."""
    rec = load_trial_record()
    if rec is None or rec.revoked_at or trial_state() != "active":
        return
    now = _iso(_utc_now())
    if now > (rec.last_seen or ""):
        rec.last_seen = now
    rec.version = _MARKER_VERSION
    save_trial_record(rec)


def trial_days_remaining() -> int:
    """Whole days left, rounded up — 0 means the trial is over."""
    rec = load_trial_record()
    if rec is None or rec.revoked_at:
        return 0
    expires = _parse_iso(rec.expires_at)
    if expires is None:
        return 0
    seconds = int((expires - _utc_now()).total_seconds())
    if seconds <= 0:
        return 0
    return -(-seconds // 86400)  # ceil


def new_trial_reference() -> str:
    raw = uuid.uuid4().hex[:16].upper()
    return f"ELI-TRIAL-{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"


def grant_trial() -> ActivationState | None:
    """Start this install's own trial, or return None if not available."""
    if trial_state() != "available":
        return None

    now = _utc_now()
    expires = now + timedelta(days=trial_duration_days())
    fp = machine_fingerprint()
    license_id = f"trial-{fp[:12]}-{uuid.uuid4().hex[:12]}"
    reference = new_trial_reference()

    rec = TrialRecord(
        license_id=license_id,
        reference=reference,
        machine=fp,
        started_at=_iso(now),
        expires_at=_iso(expires),
        revoked_at=None,
        last_seen=_iso(now),
    )
    if not save_trial_record(rec):
        return None

    state = ActivationState(
        version=1,
        activated_at=_iso(now),
        license_id=license_id,
        tier="trial",
        seats=1,
        expires_at=_iso(expires),
        customer_ref=reference,
        key_fingerprint="",
        machines=[MachineBinding(id_hash=fp, label=machine_label(), bound_at=_iso(now))],
        last_validated_at=_iso(now),
        source=TRIAL_SOURCE,
    )

    from eli.licensing.storage import save_activation_state

    save_activation_state(state)
    return state


def _activation_from_record(rec: TrialRecord) -> ActivationState:
    now = _iso(_utc_now())
    fp = machine_fingerprint()
    return ActivationState(
        version=1,
        activated_at=rec.started_at or now,
        license_id=rec.license_id,
        tier="trial",
        seats=1,
        expires_at=rec.expires_at,
        customer_ref=rec.reference,
        key_fingerprint="",
        machines=[MachineBinding(id_hash=fp, label=machine_label(), bound_at=rec.started_at or now)],
        last_validated_at=now,
        source=TRIAL_SOURCE,
    )


def ensure_trial_activation() -> ActivationState | None:
    """Grant a new trial, or rebuild the activation state for a running one.

    Two distinct cases reach here with no usable activation state:

    * never trialled -> issue this install's own trial
    * trial still running but ``activation.json`` is gone (wiped config, or an
      attempt to reset the clock) -> restore it from the surviving marker, with
      the *original* id and expiry, so the remaining days are neither lost nor
      extended.
    """
    state = trial_state()
    if state == "available":
        return grant_trial()
    if state != "active":
        return None
    touch_trial()

    rec = load_trial_record()
    if rec is None:
        return None

    from eli.licensing.storage import load_activation_state, save_activation_state

    current = load_activation_state()
    if current is not None:
        if current.license_id == rec.license_id:
            return current  # nothing to restore
        if current.source != TRIAL_SOURCE:
            return None  # a paid license holds this install; never clobber it

    restored = _activation_from_record(rec)
    save_activation_state(restored)
    return restored


def revoke_trial(*, reason: str = "expired") -> bool:
    """Permanently revoke this install's trial and drop its activation state."""
    rec = load_trial_record()
    if rec is None:
        rec = TrialRecord(
            license_id="",
            reference="",
            machine=machine_fingerprint(),
            started_at=_iso(_utc_now()),
            expires_at=_iso(_utc_now()),
        )
    if rec.revoked_at:
        already_revoked = True
    else:
        already_revoked = False
        rec.revoked_at = _iso(_utc_now())
        save_trial_record(rec)

    # Clear the activation state only when it is the trial's own — never touch a
    # paid activation that happens to sit alongside a spent trial marker.
    from eli.licensing.storage import clear_activation_state, load_activation_state

    state = load_activation_state()
    if state is not None and state.source == TRIAL_SOURCE:
        clear_activation_state()
        return True
    return not already_revoked


def trial_summary() -> dict[str, object]:
    rec = load_trial_record()
    return {
        "state": trial_state(),
        "duration_days": trial_duration_days(),
        "reference": rec.reference if rec else None,
        "license_id": rec.license_id if rec else None,
        "started_at": rec.started_at if rec else None,
        "expires_at": rec.expires_at if rec else None,
        "revoked_at": rec.revoked_at if rec else None,
        "days_remaining": trial_days_remaining(),
        "markers": [str(p) for p in marker_paths()],
    }
