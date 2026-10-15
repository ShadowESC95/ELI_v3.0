#!/usr/bin/env python3
"""Generate a marketplace listing for a plugin — correct by construction.

A registry index is static JSON, so the hard part of publishing is not hosting it,
it is getting the listing *right*. Two fields decide how ELI treats a plugin, and
both are easy to get wrong by hand:

  * **sha256** — a listing without one blocks the one-click install (ELI cannot say
    the file is the one described), and a stale one is a hard refusal on every
    machine that tries. It must be regenerated on every change to the source.
  * **permissions** — undeclared capability use is a refusal, not a warning, so a
    manifest that under-declares produces a plugin nobody can install.

This tool computes the hash, validates the manifest, runs the same scanners the
client will run, and prints the entry to paste into `index.json`. It refuses to
emit a listing for something that would be rejected on the other end — better a
publisher finds out here than every user finds out separately.

Signing produces two signatures. `release_signature` covers the release statement
(id, version, checksum, permissions, dependencies, MCP launch command, official
flag) and is what current ELI builds check. `signature` covers the file alone, for
builds that predate release statements. Any change to what the statement covers
needs a new version: pass `--index` to have that checked against the live index.

Usage:
    python tools/marketplace/publish.py plugin.py \\
        --manifest eli_plugin.json \\
        --source-url https://acme.github.io/eli-registry/plugins/thing.py
    python tools/marketplace/publish.py plugin.py --sign-key ed25519.key --publisher acme
    python tools/marketplace/publish.py --manifest mcp_server.json --sign-key k --publisher acme
    python tools/marketplace/publish.py plugin.py --sign-key marketplace.key \\
        --publisher eli-marketplace --official --index index.json
    python tools/marketplace/publish.py --new-key ed25519.key
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _new_key(path: Path) -> int:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except Exception:
        print("Signing needs the 'cryptography' package: pip install cryptography",
              file=sys.stderr)
        return 1
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes_raw())
    try:
        path.chmod(0o600)
    except Exception:
        pass
    pub = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    print(f"Private key written to {path} (keep it secret, keep it backed up).\n")
    print("Publish this public key so operators can trust you:\n")
    print(f"  publisher_id: <your-id>")
    print(f"  public_key:   {pub}\n")
    print("An operator adds it once, then every plugin you sign verifies for them.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plugin", nargs="?",
                    help="the plugin.py to publish (omit for an MCP server: give --manifest)")
    ap.add_argument("--manifest", help="eli_plugin.json (defaults to one beside the plugin)")
    ap.add_argument("--official", action="store_true",
                    help="mark the release as published by ELI itself (marketplace key only)")
    ap.add_argument("--index", help="the registry's index.json, to refuse re-using a version")
    ap.add_argument("--source-url", help="where the plugin.py will be served from (https)")
    ap.add_argument("--sign-key", help="ed25519 private key file to sign with")
    ap.add_argument("--publisher", help="publisher id the signature is attributed to")
    ap.add_argument("--new-key", help="generate a signing key at this path and exit")
    ap.add_argument("--allow-findings", action="store_true",
                    help="emit the listing even if the scan is not clean (it will still "
                         "stop every operator's one-click install)")
    args = ap.parse_args(argv)

    if args.new_key:
        return _new_key(Path(args.new_key))
    if not args.plugin and not args.manifest:
        ap.error("a plugin file, or --manifest for an MCP server, is required "
                 "(or use --new-key)")

    from eli.plugins.integrity import OFFICIAL_PUBLISHER_ID, sha256_of
    from eli.plugins.manifest import validate_manifest, verify_against_source
    from eli.plugins.security_scan import scan

    if args.official and args.publisher != OFFICIAL_PUBLISHER_ID:
        print(f"--official needs --publisher {OFFICIAL_PUBLISHER_ID} and the marketplace key: "
              f"no other key can make that claim, and ELI ignores it from any other.",
              file=sys.stderr)
        return 1

    plugin_path = Path(args.plugin) if args.plugin else None
    if plugin_path is not None and not plugin_path.is_file():
        print(f"No such file: {plugin_path}", file=sys.stderr)
        return 1
    manifest_path = Path(args.manifest) if args.manifest else plugin_path.with_name(
        "eli_plugin.json")
    if not manifest_path.is_file():
        print(f"No manifest at {manifest_path}. Every listing needs one — it is what "
              f"declares the permissions.", file=sys.stderr)
        return 1

    check = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    if not check["ok"]:
        print("Manifest problems:", file=sys.stderr)
        for problem in check["problems"]:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    manifest = check["manifest"]
    for warning in check["warnings"]:
        print(f"warning: {warning}", file=sys.stderr)

    if plugin_path is None:
        return _publish_mcp(manifest, args)
    raw = plugin_path.read_bytes()

    source = raw.decode("utf-8", "replace")
    code = verify_against_source(manifest, source)
    if not code["ok"]:
        print("This plugin would be refused by every client:", file=sys.stderr)
        for problem in code["problems"]:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if code["over_declared"]:
        print("note: declares permissions the code does not appear to use: "
              + ", ".join(code["over_declared"])
              + " — ask for the least you need.", file=sys.stderr)

    report = scan(raw, manifest, deep=True)
    if report["verdict"] != "clean" and not args.allow_findings:
        print(f"Scan verdict: {report['verdict']} — refusing to publish.", file=sys.stderr)
        for finding in report["findings"][:10]:
            line = f" (line {finding['line']})" if finding.get("line") else ""
            print(f"  [{finding['severity']}] {finding['title']}{line}", file=sys.stderr)
        print("\nFix these, or pass --allow-findings if you are certain "
              "(it will still stop one-click installs).", file=sys.stderr)
        return 1
    if not report["complete"]:
        print("note: your scan coverage was partial ("
              + ", ".join(report["engines_unavailable"])
              + "). Installing ClamAV and a YARA ruleset gives publishers the same "
                "view their users get.", file=sys.stderr)

    listing = {
        "id": manifest["id"],
        "name": manifest["name"],
        "version": manifest["version"],
        "description": manifest["description"],
        "author": manifest["author"],
        "license": manifest["license"],
        "permissions": manifest.get("permissions") or [],
        "pip": manifest.get("pip") or [],
        "sha256": sha256_of(raw),
        "source": args.source_url or manifest.get("source") or "https://REPLACE-ME",
        "price": manifest.get("price", 0),
    }
    _copy_optional(manifest, listing)
    if args.official:
        listing["official"] = True

    if _version_reused(listing, args.index):
        return 1
    if args.sign_key:
        key = _signing_key(args)
        if key is None:
            return 1
        listing["publisher_id"] = args.publisher
        listing["signature"] = base64.b64encode(key.sign(raw)).decode()
        _sign_release(listing, key)

    if not str(listing["source"]).startswith("https://"):
        print("warning: the source URL is not https. Plain http stops the one-click "
              "install on every machine, because the download can be altered in "
              "transit.", file=sys.stderr)

    print(json.dumps(listing, indent=2, ensure_ascii=False))
    return 0


# Fields carried from the manifest into the listing when present. None of them is in
# the release statement, so they can be corrected later without a new review.
_OPTIONAL = ("homepage", "purchase_url", "currency", "kind", "mcp", "purchase",
             "entitlement", "support_url", "privacy_url", "terms_url", "license_url",
             "eli_min_version", "eli_max_version", "publisher")


def _copy_optional(manifest: dict, listing: dict) -> None:
    for optional in _OPTIONAL:
        if manifest.get(optional):
            listing[optional] = manifest[optional]


def _signing_key(args):
    if not args.publisher:
        print("--sign-key needs --publisher (the id operators will trust).", file=sys.stderr)
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return Ed25519PrivateKey.from_private_bytes(Path(args.sign_key).read_bytes())
    except Exception as exc:
        print(f"Could not load the signing key: {exc}", file=sys.stderr)
        return None


def _sign_release(listing: dict, key) -> None:
    """Sign the release statement. Done last: it covers fields set above."""
    from eli.plugins.integrity import release_statement
    listing["release_signature"] = base64.b64encode(
        key.sign(release_statement(listing))).decode()


def _version_reused(listing: dict, index_path) -> bool:
    """True (after saying why) when the index already holds this id and version as a
    different release. An approved version never changes."""
    if not index_path:
        return False
    from eli.plugins.integrity import release_digest
    try:
        entries = json.loads(Path(index_path).read_text(encoding="utf-8")).get("plugins") or []
    except Exception as exc:
        print(f"Could not read {index_path}: {exc}", file=sys.stderr)
        return True
    for entry in entries:
        if (isinstance(entry, dict) and entry.get("id") == listing["id"]
                and str(entry.get("version")) == str(listing["version"])):
            if release_digest(entry) != release_digest(listing):
                print(f"{listing['id']} {listing['version']} is already in {index_path} as a "
                      f"different release (checksum, permissions, dependencies or launch "
                      f"command). Bump the version: ELI refuses a release that changes "
                      f"under a version it has seen.", file=sys.stderr)
                return True
            print(f"note: {listing['id']} {listing['version']} is already listed with the "
                  f"same release; only display fields would change.", file=sys.stderr)
    return False


def _publish_mcp(manifest: dict, args) -> int:
    """An MCP server has no file to hash: the release signature over its launch
    command and permissions is the whole of what ELI can check."""
    if not manifest.get("mcp"):
        print("Without a plugin file the manifest must describe an MCP server "
              "(an 'mcp' object with its transport and command).", file=sys.stderr)
        return 1
    listing = {
        "id": manifest["id"],
        "name": manifest["name"],
        "version": manifest["version"],
        "description": manifest["description"],
        "author": manifest["author"],
        "license": manifest["license"],
        "kind": "mcp",
        "permissions": manifest.get("permissions") or [],
        "price": manifest.get("price", 0),
    }
    _copy_optional(manifest, listing)
    listing["kind"] = "mcp"
    if args.official:
        listing["official"] = True
    if _version_reused(listing, args.index):
        return 1
    if args.sign_key:
        key = _signing_key(args)
        if key is None:
            return 1
        listing["publisher_id"] = args.publisher
        _sign_release(listing, key)
    else:
        print("note: unsigned. The official registry refuses an MCP listing without a "
              "release signature, since nothing else proves its launch command.",
              file=sys.stderr)
    print(json.dumps(listing, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
