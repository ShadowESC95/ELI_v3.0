# ELI v3 — licensing scripts (author / sales only)

> Keys for every ELI product are now made with **eli-licensing** (private repository
> `ShadowESC95/eli-licensing`), which also keeps the register of customers and licences, emails
> keys and fulfils Stripe sales. These scripts still make a key quickly by hand.
>
> - `sync_sdk.py` copies eli-sdk into `eli/licensing/_sdk` (keys from the store, revocation,
>   release manifests); `tests/test_licensing_sdk.py` checks the copy.
> - `sign_release_manifest.py` signs `eli/RELEASE.manifest` into each source package at release
>   (secret `ELI_RELEASE_SIGNING_KEY`); without it an installed source copy will not start.

Every key names one **product**: `eli` (ELI v3 itself) or an official bundle (`legal`, `vr`,
`healthcare`, `finance`). Bundles are sold separately, so each needs its own key. The products are
listed in [`eli/licensing/products.py`](../../eli/licensing/products.py); add a new bundle there
before issuing its first key.

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
python3 scripts/licensing/issue_license_key.py --product legal --tier pro --customer-ref manual-test
```

Seats and trial expiry default from [tier_policy.json](../../config/licensing/tier_policy.json).
`--major-version` sets the version line the key covers. It defaults to 3 for ELI v3 and to 1 for a
bundle, and a bundle's releases before 1.0 belong to its 1.x line.

A bundled sale is one ELI v3 key that includes bundles: `--include legal` (its default line) or
`--include vr:2`, repeated for each. The customer activates the one key.

## 3. Fulfill an order (recommended)

Generates key + email template for the buyer:

```bash
export ELI_LICENSE_SIGNING_KEY=~/eli-license-signing/product_signing.pem
python3 scripts/licensing/fulfill_order.py \
  --tier pro \
  --customer-ref ko-fi-12345 \
  --email-to buyer@example.com

# a bundle: same command, with --product; the email names the bundle and its schedule
python3 scripts/licensing/fulfill_order.py --product legal --tier pro \
  --customer-ref ko-fi-12346 --email-to buyer@example.com
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
python3 -m eli.licensing.cli deactivate --product legal
eli-license status
```
