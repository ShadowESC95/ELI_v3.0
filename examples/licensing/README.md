# ELI v3 licensing — examples & stubs

**Not for production sales.** Use these to walk through activation, fulfillment, and
store integration before you rotate to a real signing keypair.

| File | Purpose |
|---|---|
| [tier_policy.example.json](../config/licensing/tier_policy.example.json) | Checkout SKU / URL placeholders |
| [webhook_order.example.json](webhook_order.example.json) | Sample payment webhook payload |
| [sample_fulfillment_email.txt](sample_fulfillment_email.txt) | What `fulfill_order.py` prints |
| [sample_license_key.txt](sample_license_key.txt) | Example `ELIV3-…` key (dev keypair only) |
| `dev_keypair/product_signing.pub` | Dev public key (safe to share) |
| `dev_keypair/product_signing.pem` | Dev private key — **gitignored**, local only |

Committed verification key for docs/tests: `eli/licensing/keys/product_signing.example.pub`

## 1. Try fulfillment locally (dev keypair)

```bash
cd /path/to/ELI_v3.0

export ELI_LICENSE_SIGNING_KEY=examples/licensing/dev_keypair/product_signing.pem
export ELI_LICENSE_PUBLIC_KEY=examples/licensing/dev_keypair/product_signing.pub

python3 scripts/licensing/fulfill_order.py \
  --tier personal \
  --customer-ref EXAMPLE-ORDER-001 \
  --email-to buyer@example.com
```

Copy the printed key, then activate (dev checkout):

```bash
export ELI_LICENSE_PUBLIC_KEY=examples/licensing/dev_keypair/product_signing.pub
export ELI_REQUIRE_LICENSE=1

eli --activate-key 'ELIV3-...'
eli --license-status
```

## 2. Before first real sale

1. Generate a **production** keypair (off-repo):

   ```bash
   python3 scripts/licensing/generate_signing_keypair.py --out-dir ~/eli-license-signing
   cp ~/eli-license-signing/product_signing.pub eli/licensing/keys/product_signing.pub
   git add eli/licensing/keys/product_signing.pub
   ```

2. Set real prices / checkout IDs in `config/licensing/tier_policy.json` (use
   `tier_policy.example.json` as a template).

3. Lawyer review: `COMMERCIAL_EULA.md` + `docs/eula/*.md`.

## 3. Automated checkout (Phase 2)

```
Lemon Squeezy / Gumroad / Stripe
  → POST webhook (see webhook_order.example.json)
  → queue job (do not expose production .pem on a public handler)
  → fulfill_order.py --tier … --customer-ref … --email-to …
  → email key + EULA link to buyer
```

See [docs/STORE.md](../docs/STORE.md) for the full store workflow.
