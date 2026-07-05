from __future__ import annotations

import argparse
import json
import sys

from eli.licensing.activation import (
    activate_license_key,
    deactivate_license,
    validate_activation,
)
from eli.licensing.storage import activation_state_path, ensure_license_dir, license_dir


def _cmd_status(_args: argparse.Namespace) -> int:
    status = validate_activation()
    payload = {
        "active": status.active,
        "required": status.required,
        "tier": status.tier,
        "seats": status.seats,
        "machines_bound": status.machines_bound,
        "expires_at": status.expires_at,
        "message": status.message,
        "license_dir": str(license_dir()),
        "activation_file": str(activation_state_path()),
    }
    if status.state is not None:
        payload["license_id"] = status.state.license_id
        payload["machines"] = [m.to_dict() for m in status.state.machines]
    print(json.dumps(payload, indent=2))
    return 0 if status.active or not status.required else 2


def _cmd_activate(args: argparse.Namespace) -> int:
    try:
        state = activate_license_key(args.key, force_rebind=args.force)
    except Exception as exc:
        print(f"Activation failed: {exc}", file=sys.stderr)
        return 1
    print("License activated.")
    print(f"  tier:       {state.tier}")
    print(f"  seats:      {state.seats}")
    print(f"  license_id: {state.license_id}")
    print(f"  stored at:  {activation_state_path()}")
    return 0


def _cmd_deactivate(args: argparse.Namespace) -> int:
    ok = deactivate_license(machine_only=args.machine_only)
    if not ok:
        print("No activation state to remove.")
        return 1
    if args.machine_only:
        print("Removed this machine from the current license.")
    else:
        print("License deactivated on this machine.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eli-license",
        description="ELI v3 license activation utilities",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="show activation status")
    p_status.set_defaults(func=_cmd_status)

    p_act = sub.add_parser("activate", help="activate a license key")
    p_act.add_argument("key", help="ELIV3-… license key")
    p_act.add_argument(
        "--force",
        action="store_true",
        help="replace an existing different license",
    )
    p_act.set_defaults(func=_cmd_activate)

    p_deact = sub.add_parser("deactivate", help="remove activation")
    p_deact.add_argument(
        "--machine-only",
        action="store_true",
        help="unbind only this machine (keep license for other seats)",
    )
    p_deact.set_defaults(func=_cmd_deactivate)

    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_license_dir()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
