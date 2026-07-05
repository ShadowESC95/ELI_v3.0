#!/usr/bin/env python3
"""Issue a signed ELI v3 license key (author / sales tooling)."""
from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timezone

from eli.licensing.signature import issue_license_key
from eli.licensing.tiers import default_expires_at, default_seats, tier_config
from eli.licensing.types import LICENSE_VERSION, LicensePayload


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue an ELI v3 license key")
    parser.add_argument("--tier", required=True, choices=["trial", "personal", "pro", "enterprise"])
    parser.add_argument("--seats", type=int, default=0, help="0 = tier default from tier_policy.json")
    parser.add_argument("--expires", help="ISO date expiry (optional; trial defaults from policy)")
    parser.add_argument("--customer-ref", help="Optional order id or customer reference")
    parser.add_argument("--license-id", help="Optional stable license id (uuid generated if omitted)")
    args = parser.parse_args()

    seats = args.seats if args.seats > 0 else default_seats(args.tier)
    expires = args.expires or default_expires_at(args.tier)
    cfg = tier_config(args.tier)

    payload = LicensePayload(
        version=LICENSE_VERSION,
        license_id=args.license_id or str(uuid.uuid4()),
        tier=args.tier,
        seats=seats,
        issued_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        expires_at=expires,
        customer_ref=args.customer_ref,
    )
    key = issue_license_key(payload)
    print(key)
    print(f"# tier={payload.tier} ({cfg.get('label')}) seats={payload.seats} license_id={payload.license_id}")
    if expires:
        print(f"# expires_at={expires}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
