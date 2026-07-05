"""Console entry point. `python -m eli` and the `eli` script both call this.

Flags:
  --headless, -H   Run as a terminal REPL without any GUI.  Useful for
                   scripting, servers, and headless environments.
  --trust-agent <path>
                   Register a custom agent file as trusted (adds its SHA-256
                   hash to config/trusted_agents.json) then exit.
  --activate-key <key>
                   Activate a commercial ELI v3 license on this machine.
  --license-status Show license activation status (JSON) and exit.
  --deactivate-license
                   Remove local license activation and exit.
"""
from __future__ import annotations
import sys


def _handle_license_flags(args: list[str]) -> int | None:
    if "--activate-key" in args:
        idx = args.index("--activate-key")
        if idx + 1 >= len(args):
            print("Error: --activate-key requires a license key", file=sys.stderr)
            return 1
        from eli.licensing.activation import activate_license_key

        try:
            state = activate_license_key(args[idx + 1])
        except Exception as exc:
            print(f"Activation failed: {exc}", file=sys.stderr)
            return 1
        print(f"License activated ({state.tier}, {state.seats} seat(s)).")
        return 0

    if "--license-status" in args:
        import json
        from eli.licensing.activation import validate_activation
        from eli.licensing.storage import activation_state_path, license_dir

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
        print(json.dumps(payload, indent=2))
        return 0 if status.active or not status.required else 2

    if "--deactivate-license" in args:
        from eli.licensing.activation import deactivate_license

        if deactivate_license():
            print("License deactivated.")
            return 0
        print("No license activation found.")
        return 1

    return None


def main() -> int:
    args = sys.argv[1:]

    license_rc = _handle_license_flags(args)
    if license_rc is not None:
        return license_rc

    # ── Trust-agent utility ───────────────────────────────────────────────────
    if "--trust-agent" in args:
        idx = args.index("--trust-agent")
        if idx + 1 < len(args):
            from pathlib import Path
            from eli.cognition.agent_bus import _trust_custom_agent
            target = Path(args[idx + 1]).expanduser().resolve()
            if not target.exists():
                print(f"Error: file not found: {target}", file=sys.stderr)
                return 1
            _trust_custom_agent(target)
            return 0
        else:
            print("Error: --trust-agent requires a file path argument", file=sys.stderr)
            return 1

    # ── Commercial license gate (retail / ELI_REQUIRE_LICENSE builds) ────────
    try:
        from eli.licensing.activation import LicenseRequiredError, assert_launch_allowed
        assert_launch_allowed()
    except LicenseRequiredError as exc:
        print(exc, file=sys.stderr)
        return 1

    # ── First-run/boot DB + machine-inventory bootstrap (idempotent) ─────────
    # Ensures the full schema exists and the app index is populated even when ELI
    # was not launched via install.sh (copied tree / portable bundle / bare run).
    try:
        from eli.core.init_data import bootstrap_once
        bootstrap_once()
    except Exception:
        import logging
        logging.getLogger("eli.boot").debug("boot bootstrap skipped", exc_info=True)

    # ── Headless REPL ────────────────────────────────────────────────────────
    if "--headless" in args or "-H" in args:
        from eli.cli.headless import run_headless
        return int(run_headless() or 0)

    # ── GUI (default) ────────────────────────────────────────────────────────
    from eli.gui.app import main as _gui_main
    return int(_gui_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
