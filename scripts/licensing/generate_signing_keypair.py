#!/usr/bin/env python3
"""Generate an Ed25519 signing keypair for ELI v3 license keys.

Writes PEM files to --out-dir only. The private key is never printed to stdout.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ELI v3 license signing keypair")
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory to write product_signing.pem and product_signing.pub",
    )
    args = parser.parse_args()

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    out = Path(args.out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path = out / "product_signing.pem"
    pub_path = out / "product_signing.pub"
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)
    try:
        priv_path.chmod(0o600)
    except OSError:
        pass

    print(f"Wrote private key: {priv_path}")
    print(f"Wrote public key:  {pub_path}")
    print("Install the public key into eli/licensing/keys/product_signing.pub before issuing keys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
