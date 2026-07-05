# ELI v3 license issuance (author tooling)

**Customers** activate with `scripts/licensing/activate_license.sh` or `eli --activate-key`.

**You (author)** generate signing keys once, then issue keys per sale.

## 1. Generate signing keypair (once, offline)

```bash
python3 scripts/licensing/generate_signing_keypair.py --out-dir ~/eli-license-signing
```

Copy the public key into the product tree (safe to commit):

```bash
cp ~/eli-license-signing/product_signing.pub eli/licensing/keys/product_signing.pub
```

Keep `~/eli-license-signing/product_signing.pem` **private** — never commit it.

## 2. Issue a customer key

```bash
export ELI_LICENSE_SIGNING_KEY=~/eli-license-signing/product_signing.pem

python3 scripts/licensing/issue_license_key.py --tier personal --seats 1
python3 scripts/licensing/issue_license_key.py --tier pro --seats 3 --expires 2027-12-31
```

Paste the printed `ELIV3-…` key into the customer receipt / email.

## 3. Customer activation

```bash
eli --activate-key 'ELIV3-...'
eli --license-status
```

Retail installers set `ELI_RETAIL_BUILD=1` so activation is required before launch.

## Layout

| Path | Purpose |
|---|---|
| `eli/licensing/` | Runtime verification + storage |
| `config/licensing/` | Docs + activation JSON template |
| `scripts/licensing/` | Author + customer shell helpers |
