from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


LICENSE_VERSION = 1
VALID_TIERS = frozenset({"trial", "personal", "pro", "enterprise"})


@dataclass
class LicensePayload:
    version: int
    license_id: str
    tier: str
    seats: int
    issued_at: str
    expires_at: str | None = None
    customer_ref: str | None = None

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
