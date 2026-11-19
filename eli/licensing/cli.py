from __future__ import annotations

import argparse
import json
import sys

from eli.licensing.activation import activate_license_key, deactivate_license
from eli.licensing.products import product_ids
from eli.licensing.storage import activation_state_path, ensure_license_dir


def _cmd_status(_args: argparse.Namespace | None) -> int:
    from eli.licensing.flags import status_payload

    payload = status_payload()
    print(json.dumps(payload, indent=2))
    return 0 if payload["active"] or not payload["required"] else 2


def _cmd_trial(_args: argparse.Namespace) -> int:
    from eli.licensing.trial import trial_summary

    print(json.dumps(trial_summary(), indent=2))
    return 0


def _cmd_start_trial(_args: argparse.Namespace | None) -> int:
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


def _cmd_revoke_trial(_args: argparse.Namespace) -> int:
    from eli.licensing.trial import revoke_trial

    if revoke_trial(reason="manual"):
        print("Trial revoked.")
        return 0
    print("No active trial to revoke.")
    return 1


def _cmd_activate(args: argparse.Namespace) -> int:
    try:
        state = activate_license_key(args.key, force_rebind=args.force)
    except Exception as exc:
        print(f"Activation failed: {exc}", file=sys.stderr)
        return 1
    from eli.licensing.products import product_label

    print(f"{product_label(state.product)} license activated.")
    print(f"  product:    {state.product} (covers {state.major_version}.x)")
    print(f"  tier:       {state.tier}")
    print(f"  seats:      {state.seats}")
    print(f"  license_id: {state.license_id}")
    print(f"  stored at:  {activation_state_path(state.product)}")
    return 0


def _cmd_deactivate(args: argparse.Namespace) -> int:
    ok = deactivate_license(machine_only=args.machine_only, product=args.product)
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
    p_act.add_argument("key", help="ELIV3-… license key, for ELI v3 or an official bundle")
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
    p_deact.add_argument(
        "--product",
        default="eli",
        choices=product_ids(),
        help="which product's license to remove (default: eli, ELI v3 itself)",
    )
    p_deact.set_defaults(func=_cmd_deactivate)

    p_trial = sub.add_parser("trial", help="show trial state for this install")
    p_trial.set_defaults(func=_cmd_trial)

    p_start = sub.add_parser("start-trial", help="start this install's trial")
    p_start.set_defaults(func=_cmd_start_trial)

    p_revoke = sub.add_parser("revoke-trial", help="revoke this install's trial")
    p_revoke.set_defaults(func=_cmd_revoke_trial)

    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_license_dir()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
