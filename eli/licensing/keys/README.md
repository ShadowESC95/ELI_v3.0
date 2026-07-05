# ELI v3 product signing keys

Ship **only** the public key in this folder:

- `product_signing.pub` — Ed25519 public PEM (safe to commit)

Keep the **private** signing key **outside the repository**:

```bash
# One-time key generation (writes files only — private key is NOT printed)
python3 scripts/licensing/generate_signing_keypair.py \
  --out-dir ~/eli-license-signing

# Install public key for ELI to verify paid license keys
cp ~/eli-license-signing/product_signing.pub eli/licensing/keys/product_signing.pub
```

Issue keys for customers:

```bash
export ELI_LICENSE_SIGNING_KEY=~/eli-license-signing/product_signing.pem
python3 scripts/licensing/issue_license_key.py --tier pro --seats 3
```

Never commit `*.pem` private keys.
