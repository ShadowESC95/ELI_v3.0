#!/usr/bin/env python3
"""Fulfill a paid order — issue key + customer email template."""
from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timezone

from eli.licensing.signature import issue_license_key
from eli.licensing.tiers import default_expires_at, default_seats, eula_path, tier_config
from eli.licensing.types import LICENSE_VERSION, LicensePayload


def main() -> int:
    ap = argparse.ArgumentParser(description="Fulfill an ELI v3 license order")
    ap.add_argument("--tier", required=True, choices=["trial", "personal", "pro", "enterprise"])
    ap.add_argument("--customer-ref", required=True, help="Order id (Ko-fi, Stripe, invoice #)")
    ap.add_argument("--seats", type=int, default=0, help="Override seats (0 = tier default)")
    ap.add_argument("--expires", help="Override expiry ISO date")
    ap.add_argument("--email-to", help="Customer email (prints mailto block)")
    ap.add_argument("--license-id", help="Stable license id (uuid if omitted)")
    args = ap.parse_args()

    tier = args.tier
    cfg = tier_config(tier)
    seats = args.seats if args.seats > 0 else default_seats(tier)
    expires = args.expires or default_expires_at(tier)

    payload = LicensePayload(
        version=LICENSE_VERSION,
        license_id=args.license_id or str(uuid.uuid4()),
        tier=tier,
        seats=seats,
        issued_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        expires_at=expires,
        customer_ref=args.customer_ref,
    )
    key = issue_license_key(payload)
    eula = eula_path(tier)

    print("=" * 60)
    print("LICENSE KEY (send to customer securely)")
    print("=" * 60)
    print(key)
    print()
    print(f"tier={tier} seats={seats} license_id={payload.license_id}")
    print(f"customer_ref={args.customer_ref}")
    if expires:
        print(f"expires_at={expires}")
    print(f"eula={eula.name if eula.is_file() else 'COMMERCIAL_EULA.md'}")
    print()

    if args.email_to:
        print("-" * 60)
        print(f"To: {args.email_to}")
        print("Subject: Your ELI v3 license key")
        print("-" * 60)
        print(
            f"Thanks for purchasing ELI v3 ({cfg.get('label', tier)}).\n\n"
            f"License key (keep private):\n{key}\n\n"
            "Activate:\n"
            "  eli --activate-key 'PASTE-KEY-HERE'\n"
            "  eli --license-status\n\n"
            f"Terms: {eula} (also COMMERCIAL_EULA.md in the product).\n\n"
            "Support: jaybridgeman0095@gmail.com"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
