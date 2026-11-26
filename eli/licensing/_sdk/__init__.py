"""eli-sdk 1.2.1, the parts ELI v3 uses: licence keys, issuer certificates, revocation lists,
machine identity and release manifests.

A copy of https://github.com/ShadowESC95/eli-sdk (src/eli_sdk), kept here so every ELI v3 build
reads keys exactly as ELI Legal and the store do, without fetching a private package. Change it
in eli-sdk and copy it again (scripts/licensing/sync_sdk.py); tests/test_licensing_sdk.py checks
that the copy is that release, file for file.
"""
from __future__ import annotations

__version__ = "1.2.1"
