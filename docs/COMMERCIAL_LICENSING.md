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
Frozen bundles count as retail automatically.

Dev checkout: no key required unless `ELI_REQUIRE_LICENSE=1`.

### First-run trial

A retail build with nothing activated grants **its own 7-day trial** on first launch — no key,
no server. Each install gets a unique `license_id` and an `ELI-TRIAL-…` reference, bound to that
machine. Length comes from `tier_policy.json` (`tiers.trial.duration_days_default`).

At expiry the trial is **revoked permanently** and the purchase dialog opens, which doubles as the
"paste your purchased key" path. Buying mid-trial just works — a paid key supersedes a trial with
no `--force` needed.

Trial lifecycle lives in HMAC-signed markers mirrored into two directories (the license dir and the
data dir), so deleting `activation.json`, or either marker, neither restarts nor extends it; the
signed marker — not the editable `activation.json` — decides whether a trial is still live. This is
tamper-*evidence*, not tamper-proofing: the signing constant ships inside the binary, so an offline
trial can always be defeated by someone determined. Enforcement that must hold relies on signed keys.

```bash
eli --license-status     # includes a "trial" block: state, reference, days remaining
eli --start-trial        # start it explicitly
eli-license trial        # same, plus marker locations
eli-license revoke-trial # end it now
```

### Machine binding

Machines are bound by a v2 fingerprint derived from the **OS install id** (`/etc/machine-id`,
Windows `MachineGuid`, macOS `IOPlatformUUID`), falling back to a persisted UUID.

It deliberately excludes `uuid.getnode()` and the hostname. `getnode()` returns whichever NIC the
runtime picks — a frozen bundle and a source checkout can disagree *on the same machine*, and the
answer also moves when Docker/VPN/VM bridges appear or wifi MAC randomisation kicks in. Binding to
it produced "this machine is not activated" on legitimately licensed machines.

Pre-v2 activations are recognised and migrated in place on next validation. The one case that
cannot be migrated is a binding created via a NIC that no longer exists — repair it with:

```bash
eli --activate-key 'ELIV3-…' --force   # clears stale bindings, re-binds this machine
```

Questions: jaybridgeman0095@gmail.com
