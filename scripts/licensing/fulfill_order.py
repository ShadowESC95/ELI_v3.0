#!/usr/bin/env python3
"""Fulfill a paid order for ELI v3 or an official bundle — issue key + customer email template."""
from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timezone

from pathlib import Path

from eli.licensing.products import PLATFORM, default_major, parse_includes, product_ids, product_label
from eli.licensing.signature import issue_license_key
from eli.licensing.tiers import default_expires_at, default_seats, eula_path, tier_config
from eli.licensing.types import VALID_TIERS, LicensePayload, key_format_version

ROOT = Path(__file__).resolve().parents[2]


def _schedule(product: str) -> Path | None:
    """A bundle's Product Schedule (docs/eula/bundles/<ID>.md), if it has one."""
    path = ROOT / "docs" / "eula" / "bundles" / f"{product.upper()}.md"
    return path if path.is_file() else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Fulfill a license order for an ELI Product")
    ap.add_argument("--product", default="eli", choices=product_ids(),
                    help="eli (ELI v3 itself, the default) or an official bundle")
    ap.add_argument("--include", action="append", default=[], metavar="BUNDLE[:MAJOR]",
                    help="an official bundle this ELI v3 key includes (a bundled sale); repeatable")
    ap.add_argument("--major-version", type=int, default=0,
                    help="major version line the key covers (0 = 3 for eli, 1 for a bundle)")
    ap.add_argument("--tier", required=True, choices=sorted(VALID_TIERS))
    ap.add_argument("--customer-ref", required=True, help="Order id (Ko-fi, Stripe, invoice #)")
    ap.add_argument("--seats", type=int, default=0, help="Override seats (0 = tier default)")
    ap.add_argument("--expires", help="Override expiry ISO date")
    ap.add_argument("--email-to", help="Customer email (prints mailto block)")
    ap.add_argument("--license-id", help="Stable license id (uuid if omitted)")
    args = ap.parse_args()

    tier = args.tier
    product = args.product
    label = product_label(product)
    major = args.major_version if args.major_version > 0 else default_major(product)
    cfg = tier_config(tier)
    seats = args.seats if args.seats > 0 else default_seats(tier)
    expires = args.expires or default_expires_at(tier)

    payload = LicensePayload(
        version=key_format_version(product),
        license_id=args.license_id or str(uuid.uuid4()),
        tier=tier,
        seats=seats,
        issued_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        expires_at=expires,
        customer_ref=args.customer_ref,
        product=product,
        major_version=major,
        includes=parse_includes(args.include),
    )
    key = issue_license_key(payload)
    eula = eula_path(tier)
    schedule = _schedule(product) if product != PLATFORM else None

    print("=" * 60)
    print("LICENSE KEY (send to customer securely)")
    print("=" * 60)
    print(key)
    print()
    print(f"product={product} ({label} {major}.x)")
    included = ", ".join(f"{product_label(b)} {line}.x" for b, line in payload.includes.items())
    if included:
        print(f"includes={included}")
    print(f"tier={tier} seats={seats} license_id={payload.license_id}")
    print(f"customer_ref={args.customer_ref}")
    if expires:
        print(f"expires_at={expires}")
    print(f"eula={eula.name if eula.is_file() else 'COMMERCIAL_EULA.md'}")
    if schedule is not None:
        print(f"schedule={schedule.relative_to(ROOT)}")
    print()

    if args.email_to:
        print("-" * 60)
        print(f"To: {args.email_to}")
        print(f"Subject: Your {label} license key")
        print("-" * 60)
        terms = f"Terms: COMMERCIAL_EULA.md (in the product), {eula.name}"
        if schedule is not None:
            terms += f" and the {label} schedule ({schedule.relative_to(ROOT)})"
        print(
            f"Thanks for purchasing {label} ({cfg.get('label', tier)}).\n\n"
            f"License key (keep private):\n{key}\n\n"
            "Activate:\n"
            "  eli --activate-key 'PASTE-KEY-HERE'\n"
            "  eli --license-status\n\n"
            f"This key covers every {label} {major}.x release"
            + (f", and includes {included}" if included else "") + ".\n"
            f"{terms}.\n\n"
            "Support: jaybridgeman0095@gmail.com"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
