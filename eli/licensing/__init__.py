from __future__ import annotations

from eli.licensing.activation import (
    activate_license_key,
    activation_required,
    assert_launch_allowed,
    deactivate_license,
    validate_activation,
)
from eli.licensing.storage import activation_state_path, ensure_license_dir, license_dir

__all__ = [
    "activate_license_key",
    "activation_required",
    "assert_launch_allowed",
    "deactivate_license",
    "validate_activation",
    "license_dir",
    "ensure_license_dir",
    "activation_state_path",
]
