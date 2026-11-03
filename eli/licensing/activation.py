from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from eli.licensing.machine_id import (
    legacy_machine_fingerprints,
    machine_fingerprint,
    machine_label,
)
from eli.licensing.products import PLATFORM, PLATFORM_MAJOR, major_line, product_label
from eli.licensing.signature import (
    key_fingerprint,
    normalize_license_key,
    parse_license_key,
    require_product,
)
from eli.licensing.storage import (
    clear_activation_state,
    load_activation_state,
    save_activation_state,
    share_key,
    shared_key,
    unshare_key,
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


def _platform_major_problem(major_version: int) -> str:
    """Why an ELI v3 key does not open this build, or "" when it does.

    A key for a later major version opens this one: nobody who bought v4 should be
    refused v3. A key for an earlier one does not; that upgrade is a new purchase.
    """
    if major_version < PLATFORM_MAJOR:
        return (
            f"this key is for ELI v{major_version}; this is ELI v{PLATFORM_MAJOR}, "
            "which needs its own key"
        )
    return ""


def activate_license_key(key: str, *, force_rebind: bool = False) -> ActivationState:
    """Activate a key on this machine, for whichever ELI Product it names."""
    normalized = normalize_license_key(key)
    payload = parse_license_key(normalized)
    if _payload_expired(payload.expires_at):
        raise ValueError("license key has expired")
    product = payload.product
    if product == PLATFORM:
        problem = _platform_major_problem(payload.major_version)
        if problem:
            raise ValueError(problem)

    fp = machine_fingerprint()
    label = machine_label()
    existing = load_activation_state(product)

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
                f"another license is already activated for {product_label(product)}; "
                "run deactivate first or pass force_rebind=True"
            )

    state = existing or ActivationState(product=product)
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
    state.product = product
    state.major_version = payload.major_version
    state.license_key = normalized

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
    # and where ELI VR, ELI Legal and the other ELI products look for it
    share_key(product, normalized)
    return state


def deactivate_license(*, machine_only: bool = False, product: str = PLATFORM) -> bool:
    state = load_activation_state(product)
    if state is None:
        return False
    if machine_only:
        fp = machine_fingerprint()
        state.machines = [m for m in state.machines if m.id_hash != fp]
        if state.machines:
            save_activation_state(state)
            return True
    # gone from this computer for every ELI product, or the next check would take it up again
    unshare_key(product)
    return clear_activation_state(product)


def _verify_stored_key(state: ActivationState) -> str:
    """Re-check the signed key kept in an activation; "" when it holds, else why not.

    On success the state's grant (tier, seats, expiry, major version) is replaced with
    what the key itself says, so editing the activation file changes nothing.
    """
    try:
        payload = require_product(parse_license_key(state.license_key), state.product)
    except Exception as exc:
        return f"the stored license key no longer verifies ({exc})"
    if payload.license_id != state.license_id:
        return "the stored license key does not belong to this activation"
    state.tier = payload.tier
    state.seats = payload.seats
    state.expires_at = payload.expires_at
    state.customer_ref = payload.customer_ref
    state.major_version = payload.major_version
    return ""


def _inactive(
    required: bool,
    message: str,
    state: ActivationState | None = None,
    *,
    trial: str | None = None,
) -> ActivationStatus:
    return ActivationStatus(
        active=False,
        required=required,
        tier=state.tier if state else None,
        seats=state.seats if state else 0,
        machines_bound=len(state.machines) if state else 0,
        expires_at=state.expires_at if state else None,
        message=message,
        state=state,
        trial=trial,
    )


def _ensure_bound_here(state: ActivationState) -> bool:
    """Whether this machine is bound to the activation, migrating a pre-v2 binding."""
    fp = machine_fingerprint()
    if _machine_is_bound(state, fp):
        return True
    # Before rejecting, check whether this is the same host under its old
    # pre-stabilisation hash; if so, migrate the binding and carry on.
    if not _migrate_legacy_binding(state, fp):
        return False
    state.last_validated_at = _utc_now()
    try:
        save_activation_state(state)
    except OSError:
        # Read-only dir: still honour the license for this session.
        log.debug("could not persist migrated binding", exc_info=True)
    return True


_NOT_BOUND = (
    "this machine is not activated for the current license "
    "(re-bind it with: eli --activate-key YOUR-KEY --force)"
)


def _adopt_shared_key(product: str) -> Optional[ActivationState]:
    """A key another ELI product kept for `product` (ELI VR's licence screen, ELI Legal), activated
    here too, so one key entered anywhere covers them all. None when there is none that holds."""
    key = shared_key(product)
    if not key:
        return None
    try:
        return activate_license_key(key)
    except ValueError:
        return None


def validate_activation() -> ActivationStatus:
    required = activation_required()
    env_key = os.environ.get("ELI_LICENSE_KEY", "").strip()
    if env_key:
        try:
            payload = require_product(parse_license_key(env_key), PLATFORM)
            problem = _platform_major_problem(payload.major_version)
            if problem:
                raise ValueError(problem)
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

    state = load_activation_state() or _adopt_shared_key(PLATFORM)
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

    if state.source != TRIAL_SOURCE and state.license_key:
        problem = _verify_stored_key(state) or _platform_major_problem(state.major_version)
        if problem:
            return _inactive(required, problem, state, trial=trial_state())
    # An activation without a stored key predates keys being kept. It is honoured
    # as it always was, so nobody who activated earlier is locked out by an update.

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

    if not _ensure_bound_here(state):
        return _inactive(required, _NOT_BOUND, state, trial=trial_state())

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


def _included_with_platform(product: str, version: str) -> Optional[ActivationStatus]:
    """The bundle as part of the active ELI v3 licence (a bundled sale), or None when that
    licence does not include it. Read from the signed key, never from the activation file."""
    platform = validate_activation()
    if not platform.active:
        return None
    key = (platform.state.license_key if platform.state is not None else "") \
        or os.environ.get("ELI_LICENSE_KEY", "").strip()
    if not key:
        return None   # a trial, or an activation from before keys were kept: nothing to read
    try:
        payload = require_product(parse_license_key(key), PLATFORM)
    except Exception:
        return None
    line = int((payload.includes or {}).get(product) or 0)
    if line < 1:
        return None
    label = product_label(product)
    needed = major_line(version)
    if needed is not None and line < needed:
        return _inactive(
            platform.required,
            f"your ELI v3 license includes {label} {line}.x; version {version} needs an upgrade",
        )
    return ActivationStatus(
        active=True,
        required=platform.required,
        tier=payload.tier,
        seats=payload.seats,
        machines_bound=platform.machines_bound,
        expires_at=payload.expires_at,
        message=f"{label} is included in your ELI v3 license",
    )


def product_status(product: str, *, version: str = "") -> ActivationStatus:
    """Whether this machine is licensed for an ELI Product.

    For ``eli`` this is ``validate_activation()``. For a bundle product it reads that
    product's own activation, re-verifies its signed key, and, when ``version`` (the
    installed release) is given, checks that the key covers its major version line. A bundle
    the active ELI v3 key includes is licensed the same way, without a key of its own.
    """
    if product == PLATFORM:
        return validate_activation()
    own = _own_product_status(product, version=version)
    if own.active or not own.required:
        return own
    included = _included_with_platform(product, version)
    if included is not None and (included.active or own.state is None):
        return included
    return own


def _own_product_status(product: str, *, version: str = "") -> ActivationStatus:
    """A bundle licensed by its own key (see product_status)."""
    label = product_label(product)
    required = activation_required()
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
    state = load_activation_state(product) or _adopt_shared_key(product)
    if state is None:
        return _inactive(
            required,
            f"{label} is a separate product and no {label} license is activated on this "
            "machine (activate its key with: eli --activate-key YOUR-KEY)",
        )
    problem = (
        _verify_stored_key(state)
        if state.license_key
        else f"the {label} activation has no license key; activate the key again"
    )
    if problem:
        return _inactive(required, problem, state)
    if _payload_expired(state.expires_at):
        return _inactive(required, f"the {label} license has expired", state)
    line = major_line(version)
    if line is not None and state.major_version < line:
        return _inactive(
            required,
            f"your {label} license covers {label} {state.major_version}.x; "
            f"version {version} needs an upgrade",
            state,
        )
    if not _ensure_bound_here(state):
        return _inactive(required, _NOT_BOUND, state)
    return ActivationStatus(
        active=True,
        required=required,
        tier=state.tier,
        seats=state.seats,
        machines_bound=len(state.machines),
        expires_at=state.expires_at,
        message=f"{label} license active",
        state=state,
    )


class LicenseRequiredError(RuntimeError):
    """Raised when a retail build requires activation but none is valid."""


def assert_launch_allowed() -> None:
    from eli.licensing.launch_gate import ensure_license_or_exit
    ensure_license_or_exit(allow_gui_prompt=True)
