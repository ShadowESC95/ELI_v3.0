from __future__ import annotations

from eli.licensing.activation import (
    activate_license_key,
    activation_required,
    assert_launch_allowed,
    deactivate_license,
    validate_activation,
)
from eli.licensing.flags import handle_license_flags, has_license_flag, status_payload
from eli.licensing.machine_id import machine_fingerprint, machine_label
from eli.licensing.storage import activation_state_path, ensure_license_dir, license_dir
from eli.licensing.trial import (
    grant_trial,
    revoke_trial,
    trial_days_remaining,
    trial_duration_days,
    trial_state,
    trial_summary,
)

__all__ = [
    "activate_license_key",
    "activation_required",
    "assert_launch_allowed",
    "deactivate_license",
    "validate_activation",
    "license_dir",
    "ensure_license_dir",
    "activation_state_path",
    "handle_license_flags",
    "has_license_flag",
    "status_payload",
    "machine_fingerprint",
    "machine_label",
    "grant_trial",
    "revoke_trial",
    "trial_state",
    "trial_summary",
    "trial_days_remaining",
    "trial_duration_days",
]
