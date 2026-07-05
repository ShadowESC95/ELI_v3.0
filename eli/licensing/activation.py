from __future__ import annotations

import os
from datetime import datetime, timezone

from eli.licensing.machine_id import machine_fingerprint, machine_label
from eli.licensing.signature import key_fingerprint, normalize_license_key, parse_license_key
from eli.licensing.storage import (
    clear_activation_state,
    load_activation_state,
    save_activation_state,
)
from eli.licensing.types import ActivationState, ActivationStatus, MachineBinding


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
        from eli.core.paths import is_frozen, _is_dev_mode

        if is_frozen():
            return True
        if _is_dev_mode():
            return False
    except Exception:
        pass
    return False


def _machine_is_bound(state: ActivationState, fp: str) -> bool:
    return any(m.id_hash == fp for m in state.machines)


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

    if existing and existing.license_id and existing.license_id != payload.license_id:
        if not force_rebind:
            raise ValueError(
                "another license is already activated; run deactivate first "
                "or pass force_rebind=True"
            )
        existing = None

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
        )

    if _payload_expired(state.expires_at):
        return ActivationStatus(
            active=False,
            required=required,
            tier=state.tier,
            seats=state.seats,
            machines_bound=len(state.machines),
            expires_at=state.expires_at,
            message="license expired",
            state=state,
        )

    fp = machine_fingerprint()
    if not _machine_is_bound(state, fp):
        return ActivationStatus(
            active=False,
            required=required,
            tier=state.tier,
            seats=state.seats,
            machines_bound=len(state.machines),
            expires_at=state.expires_at,
            message="this machine is not activated for the current license",
            state=state,
        )

    return ActivationStatus(
        active=True,
        required=required,
        tier=state.tier,
        seats=state.seats,
        machines_bound=len(state.machines),
        expires_at=state.expires_at,
        message="license active",
        state=state,
    )


class LicenseRequiredError(RuntimeError):
    """Raised when a retail build requires activation but none is valid."""


def assert_launch_allowed() -> None:
    status = validate_activation()
    if status.active:
        return
    if not status.required:
        return
    raise LicenseRequiredError(
        "ELI v3 license activation required.\n"
        f"  Reason: {status.message}\n"
        "  Activate:  eli --activate-key YOUR-KEY\n"
        "  Status:    eli --license-status\n"
        "  Purchase:  jaybridgeman0095@gmail.com\n"
        "  Docs:      COMMERCIAL_EULA.md"
    )
