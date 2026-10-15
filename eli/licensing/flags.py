"""Command-line license flags, shared by every entry point.

``eli/__main__.py`` handles these before its launch gate, but the frozen bundle
does not go through ``__main__`` at all — ``packaging/pyinstaller/eli_entry.py``
dispatches straight to the GUI, whose own gate used to fire first. That made
``--activate-key`` and ``--license-status`` unreachable in exactly the builds
that need them: the failure message told customers to run a command that could
only reprint the same message. Both entry points now call in here first.
"""
from __future__ import annotations

import json
import sys

LICENSE_FLAGS = (
    "--activate-key",
    "--license-status",
    "--deactivate-license",
    "--start-trial",
)


def has_license_flag(args: list[str]) -> bool:
    return any(flag in args for flag in LICENSE_FLAGS)


def status_payload() -> dict[str, object]:
    from eli.licensing.activation import validate_activation
    from eli.licensing.storage import activation_state_path, license_dir
    from eli.licensing.trial import trial_summary

    status = validate_activation()
    payload: dict[str, object] = {
        "active": status.active,
        "required": status.required,
        "tier": status.tier,
        "seats": status.seats,
        "machines_bound": status.machines_bound,
        "expires_at": status.expires_at,
        "message": status.message,
        "trial": trial_summary(),
        "license_dir": str(license_dir()),
        "activation_file": str(activation_state_path()),
    }
    if status.state is not None:
        payload["license_id"] = status.state.license_id
        payload["source"] = status.state.source
        payload["machines"] = [m.to_dict() for m in status.state.machines]
    products = bundle_licences()
    if products:
        payload["products"] = products
    return payload


def bundle_licences() -> dict[str, dict[str, object]]:
    """The official bundles this machine holds a licence for, keyed by product id: by a key
    of their own, or included in the ELI v3 key."""
    from eli.licensing.activation import product_status
    from eli.licensing.products import OFFICIAL_BUNDLES
    from eli.licensing.storage import activation_state_path

    out: dict[str, dict[str, object]] = {}
    for product, label in OFFICIAL_BUNDLES.items():
        own_file = activation_state_path(product).is_file()
        status = product_status(product)
        if not own_file and not (status.active and status.required):
            continue
        out[product] = {
            "label": label,
            "active": status.active,
            "tier": status.tier,
            "seats": status.seats,
            "major_version": status.state.major_version if status.state else None,
            "message": status.message,
            "activation_file": str(activation_state_path(product)) if own_file else None,
        }
    return out


def handle_license_flags(args: list[str]) -> int | None:
    """Run any license flag present. Returns an exit code, or None to continue."""
    if "--activate-key" in args:
        idx = args.index("--activate-key")
        if idx + 1 >= len(args):
            print("Error: --activate-key requires a license key", file=sys.stderr)
            return 1
        from eli.licensing.activation import activate_license_key

        force = "--force" in args
        try:
            state = activate_license_key(args[idx + 1], force_rebind=force)
        except Exception as exc:
            print(f"Activation failed: {exc}", file=sys.stderr)
            return 1
        from eli.licensing.products import product_label

        print(
            f"{product_label(state.product)} license activated "
            f"({state.tier}, {state.seats} seat(s))."
        )
        if state.expires_at:
            print(f"  expires_at: {state.expires_at}")
        return 0

    if "--license-status" in args:
        payload = status_payload()
        print(json.dumps(payload, indent=2))
        return 0 if payload["active"] or not payload["required"] else 2

    if "--deactivate-license" in args:
        from eli.licensing.activation import deactivate_license

        if deactivate_license():
            print("License deactivated.")
            return 0
        print("No license activation found.")
        return 1

    if "--start-trial" in args:
        from eli.licensing.trial import grant_trial, trial_duration_days, trial_state

        state = grant_trial()
        if state is None:
            print(
                f"Trial not available on this machine (state: {trial_state()}).",
                file=sys.stderr,
            )
            return 1
        print(f"{trial_duration_days()}-day trial started.")
        print(f"  reference:  {state.customer_ref}")
        print(f"  expires_at: {state.expires_at}")
        return 0

    return None
