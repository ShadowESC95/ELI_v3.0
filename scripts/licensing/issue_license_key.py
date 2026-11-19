#!/usr/bin/env python3
"""Issue a signed license key for ELI v3 or an official bundle (author / sales tooling)."""
from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timezone

from eli.licensing.products import default_major, parse_includes, product_ids, product_label
from eli.licensing.signature import issue_license_key
from eli.licensing.tiers import default_expires_at, default_seats, tier_config
from eli.licensing.types import VALID_TIERS, LicensePayload, key_format_version


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue a license key for an ELI Product")
    parser.add_argument("--product", default="eli", choices=product_ids(),
                        help="eli (ELI v3 itself, the default) or an official bundle")
    parser.add_argument("--include", action="append", default=[], metavar="BUNDLE[:MAJOR]",
                    help="an official bundle this ELI v3 key includes (a bundled sale); repeatable")
    parser.add_argument("--major-version", type=int, default=0,
                        help="major version line the key covers (0 = 3 for eli, 1 for a bundle)")
    parser.add_argument("--tier", required=True, choices=sorted(VALID_TIERS))
    parser.add_argument("--seats", type=int, default=0, help="0 = tier default from tier_policy.json")
    parser.add_argument("--expires", help="ISO date expiry (optional; trial defaults from policy)")
    parser.add_argument("--customer-ref", help="Optional order id or customer reference")
    parser.add_argument("--license-id", help="Optional stable license id (uuid generated if omitted)")
    args = parser.parse_args()

    seats = args.seats if args.seats > 0 else default_seats(args.tier)
    expires = args.expires or default_expires_at(args.tier)
    cfg = tier_config(args.tier)

    major = args.major_version if args.major_version > 0 else default_major(args.product)
    payload = LicensePayload(
        version=key_format_version(args.product),
        license_id=args.license_id or str(uuid.uuid4()),
        tier=args.tier,
        seats=seats,
        issued_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        expires_at=expires,
        customer_ref=args.customer_ref,
        product=args.product,
        major_version=major,
        includes=parse_includes(args.include),
    )
    key = issue_license_key(payload)
    print(key)
    print(f"# product={payload.product} ({product_label(payload.product)} {major}.x)")
    print(f"# tier={payload.tier} ({cfg.get('label')}) seats={payload.seats} license_id={payload.license_id}")
    if expires:
        print(f"# expires_at={expires}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
