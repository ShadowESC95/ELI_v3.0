"""The ELI Products a licence key can name.

Every licence key names exactly one product. ``eli`` is ELI v3 itself. Each official bundle
(ELI Legal, ELI VR, ...) is a separate product, bought separately, with a key of its own: an
ELI v3 key does not open ELI Legal, and an ELI Legal key does not open ELI v3. A bundle's product
id is its ``edition`` — the name its bundle description already gives (see ``eli.bundles``).

Bundles from other publishers are not ELI Products. Their publishers license them, so nothing
here gates them.

A key covers one major version line of its product. A product numbered below 1.0 counts as its
1.x line, so a key bought for ELI Legal 0.6 still opens ELI Legal 1.4, but not 2.0.
"""
from __future__ import annotations

import re

PLATFORM = "eli"
PLATFORM_LABEL = "ELI v3"
# The major version of ELI this build is. A key for an earlier major version does not open it.
PLATFORM_MAJOR = 3

# Official bundles, by product id. Add a bundle here before issuing its first key: a product not
# listed is refused when a key is issued, and a bundle not listed is treated as a third party's.
OFFICIAL_BUNDLES: dict[str, str] = {
    "legal": "ELI Legal",
    "vr": "ELI VR",
    "healthcare": "ELI Healthcare",
    "finance": "ELI Finance",
    "creation": "ELI Creation Station",
}

_LEADING_INT = re.compile(r"\s*v?(\d+)")


def is_product(product: str) -> bool:
    return product == PLATFORM or product in OFFICIAL_BUNDLES


def product_ids() -> list[str]:
    return [PLATFORM, *OFFICIAL_BUNDLES]


def product_label(product: str) -> str:
    if product == PLATFORM:
        return PLATFORM_LABEL
    return OFFICIAL_BUNDLES.get(product, product)


def default_major(product: str) -> int:
    """The major version line a new key for this product covers, unless told otherwise."""
    return PLATFORM_MAJOR if product == PLATFORM else 1


def parse_includes(items) -> dict[str, int]:
    """The bundles an ELI v3 key is to include, from "legal" or "vr:2" (the bundle's default
    version line unless one is given): ["legal", "vr:2"] -> {"legal": 1, "vr": 2}."""
    out: dict[str, int] = {}
    for item in items or []:
        name, _, line = str(item).partition(":")
        name = name.strip().lower()
        if name:
            out[name] = int(line) if line.strip() else default_major(name)
    return out


def major_line(version: str) -> int | None:
    """The major version line a release belongs to: '0.6.1' -> 1, '2.3' -> 2, '' -> None."""
    match = _LEADING_INT.match(str(version or ""))
    if match is None:
        return None
    return max(1, int(match.group(1)))


def bundle_product(bundle_id: str, edition: str) -> str | None:
    """The ELI Product that licenses a bundle, or None for a third party's bundle.

    The id counts as well as the edition, so an official bundle cannot be relabelled into a
    licence nobody bought by changing the edition its description declares.
    """
    for candidate in (str(edition or "").lower(), str(bundle_id or "").lower()):
        if candidate in OFFICIAL_BUNDLES:
            return candidate
    return None
