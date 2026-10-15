# ELI v3 — Store & fulfillment

How paid licenses are sold and delivered. **Legal terms:** [COMMERCIAL_EULA.md](../COMMERCIAL_EULA.md) +
tier supplements in [docs/eula/](eula/) + a Product Schedule for each official bundle in
[docs/eula/bundles/](eula/bundles/).

ELI v3 and each official bundle (ELI Legal, ELI VR, …) are **separate products with separate keys**:
fulfil a bundle order with `--product <id>`.

## Stack (already in repo)

| Piece | Location |
|---|---|
| Signed license keys | `eli/licensing/` + `product_signing.pub` |
| Tier defaults | [config/licensing/tier_policy.json](../config/licensing/tier_policy.json) |
| Customer activate | `eli --activate-key 'ELIV3-…'` · GUI prompt on retail builds |
| Author issue keys | `scripts/licensing/issue_license_key.py` |
| Order fulfillment | `scripts/licensing/fulfill_order.py` |

## Phase 1 — Manual (now)

Best fit until automated checkout exists:

1. **Buyer** pays via **[Ko-fi](https://ko-fi.com/shadowesc95)** (tip + note with tier + email) or
   email invoice for Pro/Enterprise.
2. **You** run fulfillment (private signing key **never** in git):

```bash
export ELI_LICENSE_SIGNING_KEY=~/eli-license-signing/product_signing.pem

python3 scripts/licensing/fulfill_order.py \
  --tier pro \
  --customer-ref ko-fi-12345 \
  --email-to buyer@example.com
```

3. **Buyer** receives key + link to tier EULA; activates:

```bash
eli --activate-key 'ELIV3-...'
eli --license-status
```

4. **Retail portable** builds: set `ELI_RETAIL_BUILD=1` in the installer or ship with
   `config/settings.json` `"retail_build": true` (see below).

### One-time signing key setup

**Production** (before first paid sale):

```bash
python3 scripts/licensing/generate_signing_keypair.py --out-dir ~/eli-license-signing
cp ~/eli-license-signing/product_signing.pub eli/licensing/keys/product_signing.pub
git add eli/licensing/keys/product_signing.pub && git commit -m "chore: add license verification key"
```

**Dev stubs** (no commit of private key): see [examples/licensing/README.md](../../examples/licensing/README.md) and
`eli/licensing/keys/product_signing.example.pub`.

## Phase 2 — Storefront (when ready)

Plug any provider that webhooks on payment:

| Provider | Notes |
|---|---|
| **Lemon Squeezy / Gumroad** | Webhook → small server or manual export → `fulfill_order.py` |
| **Stripe Payment Link** | Same; store `customer_ref` = payment intent id |
| **Ko-fi shop** | Manual or Zapier → email template |

Do **not** embed the private signing key in a webhook handler on a public server unless it runs
on your machine or a locked-down HSM. Safer pattern: webhook queues order → you run
`fulfill_order.py` locally (or a dedicated signing microservice with one job).

## Retail build flag

Force activation on a shipped installer:

```bash
export ELI_RETAIL_BUILD=1
./RUN_ELI.sh
```

Or in `config/settings.json` after install (future GUI hook):

```json
"retail_build": true
```

Dev source checkouts stay unlocked unless `ELI_REQUIRE_LICENSE=1`.

## Pricing hints

See [tier_policy.json](../config/licensing/tier_policy.json) — `price_hint_usd` is indicative
until checkout goes live. Enterprise is always **contact sales**.

## Support

- License / billing: jaybridgeman0095@gmail.com
- Technical: GitHub issues (eval) or paid tier email per [COMMERCIAL_EULA.md](../COMMERCIAL_EULA.md)
