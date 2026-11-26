"""The ELI Products a licence key can name.

``eli`` is ELI itself. Each official bundle is a product of its own, sold on its own, and its
product id is the ``edition`` its bundle description declares. Bundles from other publishers are
not ELI Products: their publishers license them.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from .versions import Version

PLATFORM = "eli"
PLATFORM_LABEL = "ELI v3"
CURRENT_PLATFORM_MAJOR = 3
# Keys issued before products existed name none and were all ELI v3 keys.
LEGACY_MAJOR = 3

# What a key of each tier is for, unless the key says otherwise: seats, and how long a trial lasts.
TIER_SEATS: dict[str, int] = {"trial": 1, "personal": 1, "pro": 3, "enterprise": 10}
TRIAL_DAYS = 7

OFFICIAL_BUNDLES: dict[str, str] = {
    "legal": "ELI Legal",
    "vr": "ELI VR",
    "healthcare": "ELI Healthcare",
    "finance": "ELI Finance",
}

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


def is_product(product: str) -> bool:
    return product == PLATFORM or product in OFFICIAL_BUNDLES


def product_ids() -> list[str]:
    return [PLATFORM, *OFFICIAL_BUNDLES]


def product_label(product: str) -> str:
    if product == PLATFORM:
        return PLATFORM_LABEL
    return OFFICIAL_BUNDLES.get(product, product)


def default_major(product: str) -> int:
    return CURRENT_PLATFORM_MAJOR if product == PLATFORM else 1


def major_line(version: str | Version | None) -> int | None:
    """The version line a release belongs to. Releases before 1.0 count as 1.x."""
    parsed = version if isinstance(version, Version) else Version.parse(version)
    return None if parsed is None else max(1, parsed.major)


def bundle_product(bundle_id: str, edition: str = "") -> str | None:
    """The product that licenses a bundle, or None for another publisher's bundle.

    The id counts as well as the edition, so an official bundle can't be relabelled out of
    needing its key.
    """
    for candidate in (str(edition or "").lower(), str(bundle_id or "").lower()):
        if candidate in OFFICIAL_BUNDLES:
            return candidate
    return None


def parse_includes(items: Iterable[str] | None) -> dict[str, int]:
    """["legal", "vr:2"] -> {"legal": 1, "vr": 2}."""
    out: dict[str, int] = {}
    for item in items or []:
        name, _, line = str(item).partition(":")
        name = name.strip().lower()
        if name:
            out[name] = int(line) if line.strip() else default_major(name)
    return out


def valid_id(value: str) -> bool:
    return bool(_ID.match(str(value or "")))
