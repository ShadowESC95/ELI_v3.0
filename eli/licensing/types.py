from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from eli.licensing.products import PLATFORM


# Key format versions. A key's format follows from the product it names (see
# ``key_format_version``): version 1 is an ELI v3 key, readable by every ELI v3 build;
# version 2 names an official bundle. Builds from before bundle keys existed refuse
# version 2 outright, which is the point: they ignore the ``product`` field, so a
# version-1 bundle key would open ELI v3 for anyone who bought only the bundle.
LICENSE_VERSION = 1
PRODUCT_LICENSE_VERSION = 2
SUPPORTED_LICENSE_VERSIONS = frozenset({LICENSE_VERSION, PRODUCT_LICENSE_VERSION})
VALID_TIERS = frozenset({"trial", "personal", "pro", "enterprise"})
# Keys and activations from before products existed were all ELI v3. This stays 3
# when ELI moves on to v4: it describes those old keys, not the current build.
LEGACY_MAJOR = 3


def key_format_version(product: str) -> int:
    return LICENSE_VERSION if product == PLATFORM else PRODUCT_LICENSE_VERSION


@dataclass
class LicensePayload:
    version: int
    license_id: str
    tier: str
    seats: int
    issued_at: str
    expires_at: str | None = None
    customer_ref: str | None = None
    # Keys issued before products existed carry neither field: they are ELI v3 keys.
    product: str = PLATFORM
    major_version: int = LEGACY_MAJOR
    # Official bundles an ELI v3 key includes, each with the major version line it covers
    # ({"legal": 1}): a bundled sale. A bundle bought on its own has its own key instead.
    includes: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LicensePayload:
        return cls(
            version=int(raw.get("version", LICENSE_VERSION)),
            license_id=str(raw["license_id"]),
            tier=str(raw["tier"]).lower(),
            seats=max(1, int(raw.get("seats", 1))),
            issued_at=str(raw["issued_at"]),
            expires_at=raw.get("expires_at"),
            customer_ref=raw.get("customer_ref"),
            product=str(raw.get("product") or PLATFORM).lower(),
            major_version=int(raw.get("major_version") or LEGACY_MAJOR),
            includes={str(k).lower(): int(v) for k, v in dict(raw.get("includes") or {}).items()},
        )


@dataclass
class MachineBinding:
    id_hash: str
    label: str
    bound_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MachineBinding:
        return cls(
            id_hash=str(raw["id_hash"]),
            label=str(raw.get("label", "")),
            bound_at=str(raw["bound_at"]),
        )


@dataclass
class ActivationState:
    version: int = 1
    activated_at: str = ""
    license_id: str = ""
    tier: str = ""
    seats: int = 1
    expires_at: str | None = None
    customer_ref: str | None = None
    key_fingerprint: str = ""
    machines: list[MachineBinding] = field(default_factory=list)
    last_validated_at: str | None = None
    source: str = "offline"
    product: str = PLATFORM
    major_version: int = LEGACY_MAJOR
    # The signed key itself, re-verified on every check so that editing this file
    # cannot change what the licence grants. Empty for trials (their signed marker
    # decides) and for activations made before keys were kept.
    license_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "activated_at": self.activated_at,
            "license_id": self.license_id,
            "tier": self.tier,
            "seats": self.seats,
            "expires_at": self.expires_at,
            "customer_ref": self.customer_ref,
            "key_fingerprint": self.key_fingerprint,
            "machines": [m.to_dict() for m in self.machines],
            "last_validated_at": self.last_validated_at,
            "source": self.source,
            "product": self.product,
            "major_version": self.major_version,
            "license_key": self.license_key,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ActivationState:
        machines = [
            MachineBinding.from_dict(m)
            for m in (raw.get("machines") or [])
            if isinstance(m, dict)
        ]
        return cls(
            version=int(raw.get("version", 1)),
            activated_at=str(raw.get("activated_at", "")),
            license_id=str(raw.get("license_id", "")),
            tier=str(raw.get("tier", "")),
            seats=max(1, int(raw.get("seats", 1))),
            expires_at=raw.get("expires_at"),
            customer_ref=raw.get("customer_ref"),
            key_fingerprint=str(raw.get("key_fingerprint", "")),
            machines=machines,
            last_validated_at=raw.get("last_validated_at"),
            source=str(raw.get("source", "offline")),
            product=str(raw.get("product") or PLATFORM).lower(),
            major_version=int(raw.get("major_version") or LEGACY_MAJOR),
            license_key=str(raw.get("license_key") or ""),
        )


@dataclass
class ActivationStatus:
    active: bool
    required: bool
    tier: str | None
    seats: int
    machines_bound: int
    expires_at: str | None
    message: str
    state: ActivationState | None = None
    # Trial lifecycle for this install: available | active | expired | revoked.
    # None when trials are irrelevant (paid license active, or env-key path).
    trial: str | None = None
