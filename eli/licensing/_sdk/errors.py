"""Errors raised by the SDK."""
from __future__ import annotations


class SDKError(Exception):
    """Base class for everything the SDK raises."""


class InvalidKey(SDKError, ValueError):
    """A licence key that is malformed, unsigned by the given key, or not a valid grant."""


class NeedsActivation(InvalidKey):
    """A key bought to be activated: it opens nothing until the store binds it to a computer
    (eli_sdk.activation.activate)."""


class InvalidManifest(SDKError, ValueError):
    """A bundle description or package manifest that breaks the contract."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems) or "invalid manifest")
