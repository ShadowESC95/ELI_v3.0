# Commercial licensing — ELI v3 (paid product)

ELI v3 is **not open source**. The repo `LICENSE` (PolyForm Internal Use 1.0.0) governs
**GitHub source evaluation**. **Paid customers** accept the [Commercial EULA](../COMMERCIAL_EULA.md)
and a tier supplement under [docs/eula/](eula/).

## Quick map

| Role | Doc / tool |
|---|---|
| **Buyer** | [docs/STORE.md](STORE.md) · [COMMERCIAL_EULA.md](../COMMERCIAL_EULA.md) |
| **Activate** | `eli --activate-key 'ELIV3-…'` · [config/licensing/README.md](../config/licensing/README.md) |
| **You (issue keys)** | [scripts/licensing/README.md](../scripts/licensing/README.md) |
| **Tier limits** | [config/licensing/tier_policy.json](../config/licensing/tier_policy.json) |

## Should you change the license for a paid product?

**No — not the public repo license.** Keep PolyForm Internal on GitHub:

- Evaluators can clone and hack locally
- Nobody else can redistribute or resell
- **You** retain all commercial rights (already stated in LICENSE)

**Paid sales use a separate grant:**

1. **Commercial EULA** at purchase (master terms)
2. **Tier supplement** (Personal / Pro / Enterprise / Trial) — seats, commercial use, support
3. **Signed license key** — enforced by `eli/licensing/` at runtime on retail builds

Do **not** switch to MIT/Apache/GPL for v3 if it stays proprietary.

## Author workflow (manual store today)

```bash
# One-time (private key stays OFF git)
python3 scripts/licensing/generate_signing_keypair.py --out-dir ~/eli-license-signing
cp ~/eli-license-signing/product_signing.pub eli/licensing/keys/product_signing.pub

# Per order
export ELI_LICENSE_SIGNING_KEY=~/eli-license-signing/product_signing.pem
python3 scripts/licensing/fulfill_order.py --tier pro --customer-ref ORDER-123 --email-to buyer@example.com
```

## Retail builds

Set `ELI_RETAIL_BUILD=1` or `"retail_build": true` in settings — activation required before GUI/CLI runs.

Dev checkout: no key required unless `ELI_REQUIRE_LICENSE=1`.

Questions: jaybridgeman0095@gmail.com
