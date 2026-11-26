"""Release versions and the ranges a bundle or package works with."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering
from typing import Any

_LEADING = re.compile(r"^\s*v?(\d+(?:\.\d+)*)")


@total_ordering
@dataclass(frozen=True)
class Version:
    parts: tuple[int, ...]

    @classmethod
    def parse(cls, text: Any) -> Version | None:
        """'3.0.137', 'v2.3', '1.0.0-beta.2' (the suffix is ignored). None if it isn't one."""
        match = _LEADING.match(str(text or ""))
        if match is None:
            return None
        return cls(tuple(int(p) for p in match.group(1).split(".")))

    @property
    def major(self) -> int:
        return self.parts[0]

    def _padded(self, width: int) -> tuple[int, ...]:
        return self.parts + (0,) * (width - len(self.parts))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        width = max(len(self.parts), len(other.parts))
        return self._padded(width) == other._padded(width)

    def __lt__(self, other: Version) -> bool:
        width = max(len(self.parts), len(other.parts))
        return self._padded(width) < other._padded(width)

    def __hash__(self) -> int:
        trimmed = list(self.parts)
        while len(trimmed) > 1 and trimmed[-1] == 0:
            trimmed.pop()
        return hash(tuple(trimmed))

    def __str__(self) -> str:
        return ".".join(str(p) for p in self.parts)


_SPEC = re.compile(r"^\s*(>=|<=|<|>|==)?\s*(v?\d+(?:\.\d+)*)(\.\*)?\s*$")


@dataclass(frozen=True)
class VersionRange:
    """minimum and maximum are inclusive, below is exclusive. Any of them may be absent."""

    minimum: Version | None = None
    maximum: Version | None = None
    below: Version | None = None

    @classmethod
    def parse(cls, spec: Any) -> VersionRange:
        """From {"min": "3.0", "max": "3.9", "below": "4"} or ">=3.0,<4". Raises ValueError."""
        if spec is None or spec == "" or spec == {}:
            return cls()
        if isinstance(spec, dict):
            unknown = set(spec) - {"min", "max", "below"}
            if unknown:
                raise ValueError(f"unknown range keys: {', '.join(sorted(unknown))}")
            return cls._checked(*(cls._version(spec.get(k)) for k in ("min", "max", "below")))
        minimum = maximum = below = None
        for clause in str(spec).split(","):
            match = _SPEC.match(clause)
            if match is None:
                raise ValueError(f"not a version range: {spec!r}")
            op, number, star = match.group(1) or ">=", Version.parse(match.group(2)), match.group(3)
            if op == ">=":
                minimum = number
            elif op == ">":
                minimum = Version(number.parts + (1,))
            elif op == "<=":
                maximum = number
            elif op == "<":
                below = number
            elif star:
                minimum, below = number, Version(number.parts[:-1] + (number.parts[-1] + 1,))
            else:
                minimum = maximum = number
        return cls._checked(minimum, maximum, below)

    @staticmethod
    def _version(value: Any) -> Version | None:
        if value in (None, ""):
            return None
        parsed = Version.parse(value)
        if parsed is None:
            raise ValueError(f"not a version: {value!r}")
        return parsed

    @classmethod
    def _checked(cls, minimum, maximum, below) -> VersionRange:
        rng = cls(minimum, maximum, below)
        if minimum is not None and ((maximum is not None and maximum < minimum)
                                    or (below is not None and below <= minimum)):
            raise ValueError(f"empty version range: {rng}")
        return rng

    def contains(self, version: Version | str) -> bool:
        v = version if isinstance(version, Version) else Version.parse(version)
        if v is None:
            return False
        if self.minimum is not None and v < self.minimum:
            return False
        if self.maximum is not None and v > self.maximum:
            return False
        return not (self.below is not None and v >= self.below)

    @property
    def unbounded(self) -> bool:
        return self.minimum is None and self.maximum is None and self.below is None

    def __str__(self) -> str:
        parts = []
        if self.minimum is not None:
            parts.append(f">={self.minimum}")
        if self.maximum is not None:
            parts.append(f"<={self.maximum}")
        if self.below is not None:
            parts.append(f"<{self.below}")
        return ",".join(parts) or "any"
