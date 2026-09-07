from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from eli.licensing.machine_id import (
    legacy_machine_fingerprints,
    machine_fingerprint,
    machine_label,
)
from eli.licensing.signature import key_fingerprint, normalize_license_key, parse_license_key
from eli.licensing.storage import (
    clear_activation_state,
    load_activation_state,
    save_activation_state,
)
from eli.licensing.types import ActivationState, ActivationStatus, MachineBinding

log = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _payload_expired(expires_at: str | None) -> bool:
    dt = _parse_iso(expires_at)
    if dt is None:
        return False
    return datetime.now(timezone.utc) > dt


def activation_required() -> bool:
    if os.environ.get("ELI_LICENSE_BYPASS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    if os.environ.get("ELI_REQUIRE_LICENSE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if os.environ.get("ELI_RETAIL_BUILD", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    try:
        from eli.core.runtime_settings import load_settings
        if load_settings().get("retail_build") is True:
            return True
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        from eli.core.paths import is_frozen, _is_dev_mode

        if is_frozen():
            return True
        if _is_dev_mode():
            return False
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return False


def _machine_is_bound(state: ActivationState, fp: str) -> bool:
    return any(m.id_hash == fp for m in state.machines)


def _migrate_legacy_binding(state: ActivationState, fp: str) -> bool:
    """Rewrite a v1 (uuid.getnode-based) binding for this host to the v2 hash.

    Activations created before the fingerprint was stabilised recorded a hash
    derived from whichever NIC the runtime picked. Recognise those, swap in the
    stable hash, and report whether anything changed so the caller can persist.
    """
    legacy = set(legacy_machine_fingerprints())
    if not legacy:
        return False
    stale = [m for m in state.machines if m.id_hash in legacy]
    if not stale:
        return False
    keep = [m for m in state.machines if m.id_hash not in legacy]
    label = stale[0].label or machine_label()
    bound_at = stale[0].bound_at or _utc_now()
    keep.append(MachineBinding(id_hash=fp, label=label, bound_at=bound_at))
    state.machines = keep
    return True


def _bind_machine(state: ActivationState, fp: str, label: str) -> None:
    if _machine_is_bound(state, fp):
        return
    state.machines.append(MachineBinding(id_hash=fp, label=label, bound_at=_utc_now()))


def activate_license_key(key: str, *, force_rebind: bool = False) -> ActivationState:
    normalized = normalize_license_key(key)
    payload = parse_license_key(normalized)
    if _payload_expired(payload.expires_at):
        raise ValueError("license key has expired")

    fp = machine_fingerprint()
    label = machine_label()
    existing = load_activation_state()

    if force_rebind:
        # Discard all prior bindings, including for the *same* license id. This
        # is the documented repair when a machine is no longer recognised (e.g.
        # a binding left behind by a network interface that has since vanished):
        # without it, a one-seat license would report "seat limit reached" and
        # the holder would have no way back in.
        existing = None
    elif existing and existing.license_id and existing.license_id != payload.license_id:
        # A purchased key always supersedes this install's trial — requiring
        # --force there would strand every customer who buys during a trial.
        from eli.licensing.trial import TRIAL_SOURCE

        if existing.source == TRIAL_SOURCE:
            existing = None
        else:
            raise ValueError(
                "another license is already activated; run deactivate first "
                "or pass force_rebind=True"
            )

    state = existing or ActivationState()
    state.version = 1
    state.activated_at = _utc_now()
    state.license_id = payload.license_id
    state.tier = payload.tier
    state.seats = payload.seats
    state.expires_at = payload.expires_at
    state.customer_ref = payload.customer_ref
    state.key_fingerprint = key_fingerprint(normalized)
    state.last_validated_at = _utc_now()
    state.source = "offline"

    if not _machine_is_bound(state, fp):
        # Reuse a pre-stabilisation binding for this same host rather than
        # spending a second seat on it.
        if not _migrate_legacy_binding(state, fp):
            if len(state.machines) >= state.seats:
                raise ValueError(
                    f"seat limit reached ({state.seats}); deactivate on another machine first"
                )
            _bind_machine(state, fp, label)

    save_activation_state(state)
    return state


def deactivate_license(*, machine_only: bool = False) -> bool:
    state = load_activation_state()
    if state is None:
        return False
    if machine_only:
        fp = machine_fingerprint()
        state.machines = [m for m in state.machines if m.id_hash != fp]
        if state.machines:
            save_activation_state(state)
            return True
    return clear_activation_state()


def validate_activation() -> ActivationStatus:
    required = activation_required()
    env_key = os.environ.get("ELI_LICENSE_KEY", "").strip()
    if env_key:
        try:
            payload = parse_license_key(env_key)
            if _payload_expired(payload.expires_at):
                return ActivationStatus(
                    active=False,
                    required=required,
                    tier=payload.tier,
                    seats=payload.seats,
                    machines_bound=0,
                    expires_at=payload.expires_at,
                    message="ELI_LICENSE_KEY is expired",
                )
            return ActivationStatus(
                active=True,
                required=required,
                tier=payload.tier,
                seats=payload.seats,
                machines_bound=1,
                expires_at=payload.expires_at,
                message="activated via ELI_LICENSE_KEY",
            )
        except Exception as exc:
            return ActivationStatus(
                active=False,
                required=required,
                tier=None,
                seats=0,
                machines_bound=0,
                expires_at=None,
                message=f"invalid ELI_LICENSE_KEY: {exc}",
            )

    from eli.licensing.trial import TRIAL_SOURCE, trial_state

    state = load_activation_state()
    if state is None:
        if not required:
            return ActivationStatus(
                active=True,
                required=False,
                tier=None,
                seats=0,
                machines_bound=0,
                expires_at=None,
                message="license not required for this build",
            )
        return ActivationStatus(
            active=False,
            required=True,
            tier=None,
            seats=0,
            machines_bound=0,
            expires_at=None,
            message="no license activated",
            trial=trial_state(),
        )

    if state.source == TRIAL_SOURCE:
        # For trials the signed marker decides, not activation.json: that file is
        # plain JSON a user can edit, so trusting its expires_at would let anyone
        # extend their own trial indefinitely.
        from eli.licensing.trial import load_trial_record

        ts = trial_state()
        rec = load_trial_record()
        if rec is not None and rec.license_id and rec.license_id != state.license_id:
            ts = "expired"  # state does not belong to this install's trial
        if ts != "active":
            return ActivationStatus(
                active=False,
                required=required,
                tier=state.tier,
                seats=state.seats,
                machines_bound=len(state.machines),
                expires_at=(rec.expires_at if rec is not None else state.expires_at),
                message="trial revoked" if ts == "revoked" else "trial expired",
                state=state,
                trial=ts,
            )

    if _payload_expired(state.expires_at):
        is_trial = state.source == TRIAL_SOURCE
        return ActivationStatus(
            active=False,
            required=required,
            tier=state.tier,
            seats=state.seats,
            machines_bound=len(state.machines),
            expires_at=state.expires_at,
            message="trial expired" if is_trial else "license expired",
            state=state,
            trial="expired" if is_trial else trial_state(),
        )

    fp = machine_fingerprint()
    if not _machine_is_bound(state, fp):
        # Before rejecting, check whether this is the same host under its old
        # pre-stabilisation hash; if so, migrate the binding and carry on.
        if _migrate_legacy_binding(state, fp):
            state.last_validated_at = _utc_now()
            try:
                save_activation_state(state)
            except OSError:
                # Read-only dir: still honour the license for this session.
                log.debug("could not persist migrated binding", exc_info=True)
        else:
            return ActivationStatus(
                active=False,
                required=required,
                tier=state.tier,
                seats=state.seats,
                machines_bound=len(state.machines),
                expires_at=state.expires_at,
                message=(
                    "this machine is not activated for the current license "
                    "(re-bind it with: eli --activate-key YOUR-KEY --force)"
                ),
                state=state,
                trial=trial_state(),
            )

    if state.source == TRIAL_SOURCE:
        from eli.licensing.trial import trial_days_remaining

        days = trial_days_remaining()
        message = f"trial active — {days} day{'s' if days != 1 else ''} remaining"
    else:
        message = "license active"

    return ActivationStatus(
        active=True,
        required=required,
        tier=state.tier,
        seats=state.seats,
        machines_bound=len(state.machines),
        expires_at=state.expires_at,
        message=message,
        state=state,
        trial="active" if state.source == TRIAL_SOURCE else None,
    )


class LicenseRequiredError(RuntimeError):
    """Raised when a retail build requires activation but none is valid."""


def assert_launch_allowed() -> None:
    from eli.licensing.launch_gate import ensure_license_or_exit
    ensure_license_or_exit(allow_gui_prompt=True)
