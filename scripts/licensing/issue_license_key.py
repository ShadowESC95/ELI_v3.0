#!/usr/bin/env python3
"""Issue a signed ELI v3 license key (author / sales tooling)."""
from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timezone

from eli.licensing.signature import issue_license_key
from eli.licensing.types import LICENSE_VERSION, LicensePayload


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue an ELI v3 license key")
    parser.add_argument("--tier", required=True, choices=["trial", "personal", "pro", "enterprise"])
    parser.add_argument("--seats", type=int, default=1)
    parser.add_argument("--expires", help="ISO date expiry (optional), e.g. 2027-12-31")
    parser.add_argument("--customer-ref", help="Optional order id or customer reference")
    parser.add_argument("--license-id", help="Optional stable license id (uuid generated if omitted)")
    args = parser.parse_args()

    payload = LicensePayload(
        version=LICENSE_VERSION,
        license_id=args.license_id or str(uuid.uuid4()),
        tier=args.tier,
        seats=max(1, args.seats),
        issued_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        expires_at=args.expires,
        customer_ref=args.customer_ref,
    )
    key = issue_license_key(payload)
    print(key)
    print(f"# tier={payload.tier} seats={payload.seats} license_id={payload.license_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
