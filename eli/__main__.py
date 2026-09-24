"""Console entry point. `python -m eli` and the `eli` script both call this.

Flags:
  --headless, -H   Run as a terminal REPL without any GUI.  Useful for
                   scripting, servers, and headless environments.
  --trust-agent <path> [--force]
                   Register a custom agent file as trusted (adds its SHA-256
                   hash to config/trusted_agents.json) then exit.
  --activate-key <key>
                   Activate a commercial ELI v3 license on this machine.
                   Add --force to replace a different active license.
  --license-status Show license activation status (JSON) and exit.
  --deactivate-license
                   Remove local license activation and exit.
  --start-trial    Start this install's 7-day trial and exit.
  --mcp-server     Run ELI as an MCP server on stdin/stdout, for other apps.
  --mcp-config [--allow-control]
                   Print the mcpServers entry to paste into another app's config.
  --license, --licence
                   Print the PolyForm Internal Use terms ELI ships under, then
                   exit. Distinct from --license-status, which reports this
                   install's ACTIVATION state; this prints the TERMS.
"""
from __future__ import annotations
import sys


def _handle_license_flags(args: list[str]) -> int | None:
    """Delegate to the shared handler (also used by the frozen entry point)."""
    from eli.licensing.flags import handle_license_flags

    return handle_license_flags(args)


def main() -> int:
    args = sys.argv[1:]

    license_rc = _handle_license_flags(args)
    if license_rc is not None:
        return license_rc

    # ── Licence TERMS ─────────────────────────────────────────────────────────
    # Runs after the activation flags above (they match exact strings, so
    # "--license" and "--license-status" cannot collide) and before anything
    # heavy is imported: asking for the terms must work on a broken install.
    if "--license" in args or "--licence" in args:
        from eli.runtime.license_info import print_license
        return print_license()

    # ── ELI as an MCP server ──────────────────────────────────────────────────
    # Before any GUI/engine import: the protocol owns stdout.
    if "--mcp-server" in args:
        from eli.integrations.mcp.server import main as _mcp_main
        return _mcp_main()
    if "--mcp-config" in args:
        import json
        from eli.integrations.mcp.server import connection_config
        print(json.dumps(connection_config("--allow-control" in args), indent=2))
        return 0

    # ── Trust-agent utility ───────────────────────────────────────────────────
    if "--trust-agent" in args:
        idx = args.index("--trust-agent")
        if idx + 1 < len(args):
            from pathlib import Path
            from eli.cognition import agent_trust
            target = Path(args[idx + 1]).expanduser().resolve()
            if not target.exists():
                print(f"Error: file not found: {target}", file=sys.stderr)
                return 1
            # Goes through agent_trust so the CLI and the GUI write the SAME
            # provenance-carrying grant. It also scans first: approving code
            # without looking at it is what the old path did.
            force = "--force" in args
            result = agent_trust.grant(target, approved_by="cli", force=force)
            if not result.get("ok"):
                print(f"Refused to approve {target.name}:", file=sys.stderr)
                for problem in result.get("problems", []):
                    print(f"  - {problem}", file=sys.stderr)
                print("\nRe-run with --force if you wrote this code and understand "
                      "the findings.", file=sys.stderr)
                return 1
            scan = result.get("scan") or {}
            print(result.get("response", f"{target.name} approved."))
            print(f"  scan: {scan.get('verdict', '?')} (score {scan.get('score', 0)}/100)"
                  + ("" if scan.get("complete") else " — coverage partial"))
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
