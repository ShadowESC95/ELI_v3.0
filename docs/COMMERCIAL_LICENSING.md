# Commercial licensing — ELI v3 (paid product)

ELI v3 is **proprietary commercial software**. [`LICENSE`](../LICENSE) is a short notice; the terms
themselves are the [ELI End User Licence Agreement](../COMMERCIAL_EULA.md), which every build ships
and prints with `eli --license`. Nothing else, including having the source, grants a licence.

## Quick map

| Role | Doc / tool |
|---|---|
| **Buyer** | [docs/STORE.md](STORE.md) · [COMMERCIAL_EULA.md](../COMMERCIAL_EULA.md) |
| **Activate** | `eli --activate-key 'ELIV3-…'` · [config/licensing/README.md](../config/licensing/README.md) |
| **You (issue keys)** | [scripts/licensing/README.md](../scripts/licensing/README.md) |
| **Tier limits** | [config/licensing/tier_policy.json](../config/licensing/tier_policy.json) |
| **Products** | [eli/licensing/products.py](../eli/licensing/products.py) |

## What a purchase is made of

1. **The EULA** (master terms): perpetual, one-time purchase; every 3.x release included; no
   licence server; no remote kill switch; your data is yours.
2. **A tier supplement** (Personal / Pro / Enterprise / Trial, [docs/eula/](eula/)): seats,
   commercial use, support.
3. **For an official bundle, its Product Schedule** ([docs/eula/bundles/](eula/bundles/)): its own
   price and the terms its field needs (ELI Legal's professional-use terms, ELI VR's safety terms).
   `TEMPLATE.md` is the starting point for the next one.
4. **A signed licence key** for that product.

## Products and keys

Every key names **one product**: `eli` (ELI v3 itself) or an official bundle (`legal`, `vr`,
`healthcare`, `finance`). Bundles are separate purchases with separate keys. An ELI v3 key does not
open ELI Legal, and an ELI Legal key does not open ELI v3. Bundles from other publishers are not
ELI Products and need no key of ours.

| Field | Meaning |
|---|---|
| `product` | `eli` or a bundle id. Keys issued before products existed read as `eli` |
| `major_version` | The version line the key covers. An ELI v3 key needs 3 or later. A bundle key covers its line, and a bundle's releases before 1.0 count as 1.x |
| `includes` | ELI v3 keys only: official bundles the licence includes, each with its version line (`{"legal": 1}`), for a bundled sale. A bundle bought on its own has its own key |
| `tier`, `seats`, `expires_at` | As before. Paid keys do not expire |
| `version` | Key format: 1 for ELI v3 keys, which every v3 build reads; 2 for bundle keys, which builds from before products refuse rather than misread as an ELI v3 licence |

**Offline, and the key decides.** Activation keeps the signed key in the activation file
(`activation.json` for ELI v3, `activation-<product>.json` for a bundle) and re-verifies it on every
check, against the public key built into ELI. Editing the file cannot change what it grants, and
nothing contacts a server. An activation made before keys were kept has no stored key; it is still
honoured, so nobody who activated earlier is locked out by an update.

**Seats are contractual.** Each machine keeps its own activation file, so the seat count is not
enforced across machines. The EULA says so (§3.5).

## Author workflow (manual store today)

```bash
# One-time (private key stays OFF git)
python3 scripts/licensing/generate_signing_keypair.py --out-dir ~/eli-license-signing
cp ~/eli-license-signing/product_signing.pub eli/licensing/keys/product_signing.pub

# Per order: ELI v3, or a bundle with --product
export ELI_LICENSE_SIGNING_KEY=~/eli-license-signing/product_signing.pem
python3 scripts/licensing/fulfill_order.py --tier pro --customer-ref ORDER-123 --email-to buyer@example.com
python3 scripts/licensing/fulfill_order.py --product legal --tier pro --customer-ref ORDER-124 --email-to buyer@example.com

# A bundled sale: an ELI v3 key that includes ELI Legal (and ELI VR's 2.x line)
python3 scripts/licensing/fulfill_order.py --tier enterprise --include legal --include vr:2 --customer-ref ORDER-125
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
