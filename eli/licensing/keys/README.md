# ELI v3 product signing keys

Ship **only** public keys in this folder:

| File | Use |
|---|---|
| `product_signing.pub` | Production verification key (commit when you go live) |
| `product_signing.example.pub` | **Dev/docs stub** — pair with `examples/licensing/dev_keypair/*.pem` locally |

Keep the **private** signing key **outside the repository**:

```bash
# Production (one-time — before first paid sale)
python3 scripts/licensing/generate_signing_keypair.py \
  --out-dir ~/eli-license-signing

cp ~/eli-license-signing/product_signing.pub eli/licensing/keys/product_signing.pub
git add eli/licensing/keys/product_signing.pub
```

**Local dev / walkthrough** (stub keypair — see `examples/licensing/README.md`):

```bash
python3 scripts/licensing/generate_signing_keypair.py \
  --out-dir examples/licensing/dev_keypair

export ELI_LICENSE_SIGNING_KEY=examples/licensing/dev_keypair/product_signing.pem
export ELI_LICENSE_PUBLIC_KEY=examples/licensing/dev_keypair/product_signing.pub
python3 scripts/licensing/issue_license_key.py --tier pro --customer-ref dev-test
```

Never commit `*.pem` private keys (`*.pem` is gitignored).
