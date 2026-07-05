# ELI v3 — licensing scripts (author / sales only)

**Customers:** activate with `eli --activate-key 'ELIV3-…'` — see [config/licensing/README.md](../../config/licensing/README.md).

**Store workflow:** [docs/STORE.md](../../docs/STORE.md)

## 1. Generate signing keypair (once)

```bash
python3 scripts/licensing/generate_signing_keypair.py --out-dir ~/eli-license-signing
cp ~/eli-license-signing/product_signing.pub eli/licensing/keys/product_signing.pub
```

Keep `~/eli-license-signing/product_signing.pem` **private** — never commit it.

## 2. Issue a key (quick)

```bash
export ELI_LICENSE_SIGNING_KEY=~/eli-license-signing/product_signing.pem
python3 scripts/licensing/issue_license_key.py --tier pro --customer-ref manual-test
```

Seats and trial expiry default from [tier_policy.json](../../config/licensing/tier_policy.json).

## 3. Fulfill an order (recommended)

Generates key + email template for the buyer:

```bash
export ELI_LICENSE_SIGNING_KEY=~/eli-license-signing/product_signing.pem
python3 scripts/licensing/fulfill_order.py \
  --tier pro \
  --customer-ref ko-fi-12345 \
  --email-to buyer@example.com
```

## 4. Customer activates

```bash
bash scripts/licensing/activate_license.sh 'ELIV3-...'
# or
eli --activate-key 'ELIV3-...'
eli --license-status
```

## CLI module

```bash
python3 -m eli.licensing.cli status
python3 -m eli.licensing.cli activate 'ELIV3-...'
python3 -m eli.licensing.cli deactivate --machine-only
eli-license status
```
