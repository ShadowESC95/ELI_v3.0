# ELI v3 license activation (config)

Runtime activation state is stored per machine under your ELI config directory:

| Platform | Path |
|---|---|
| Linux | `~/.config/eli/licensing/activation.json` |
| macOS | `~/Library/Preferences/eli/licensing/activation.json` |
| Windows | `%APPDATA%/eli/licensing/activation.json` |
| Dev checkout | `<project>/config/licensing/activation.json` |

Override with `ELI_LICENSE_DIR` or `ELI_CONFIG_DIR`.

Each official bundle (ELI Legal, ELI VR, …) is a separate product with its own key, activated the
same way and kept beside ELI v3's as `activation-<product>.json` (for example
`activation-legal.json`). The signed key is stored in the file and re-verified on every check.

## Activate (paid build)

```bash
eli --activate-key 'ELIV3-...'
# or
python3 -m eli.licensing.cli activate 'ELIV3-...'
# or
bash scripts/licensing/activate_license.sh 'ELIV3-...'
```

## Status / deactivate

```bash
eli --license-status
eli --deactivate-license
python3 -m eli.licensing.cli status
python3 -m eli.licensing.cli deactivate --machine-only
python3 -m eli.licensing.cli deactivate --product legal   # a bundle's licence
```

## Who needs a licence

Every copy of ELI v3 runs with a licence or its 7-day trial: the frozen builds, and every copy
installed from source (the .deb, the wheel, the portable folders and tarballs), which until
3.0.174 needed nothing. A key can be entered at any time, during the trial or after it.

- **The trial** starts on first use. Its records are mirrored in the licence folder, the data
  folder and fixed places found from the account itself (not from `ELI_LICENSE_DIR`,
  `ELI_DATA_DIR`, `ELI_CONFIG_DIR`, `$HOME` or `XDG_*`), sealed for this machine. Deleting them
  once ELI's databases exist starts no new trial; a clock set back behind the last time ELI ran
  pauses it until the clock is right.
- **Installed source copies** carry `eli/RELEASE.manifest`, signed at release with ELI's release
  key (`scripts/licensing/sign_release_manifest.py`): a copy whose licensing files or entry points
  were changed, removed or added to refuses to start until it is reinstalled.
- **Keys** may come from ELI's offline key or from the licence store's certified issuer key, may
  be bound to one machine (store trial keys), and stop working once ELI withdraws them (signed
  revocation lists in the shared key folder). An ELI v3 key that includes a bundle released
  after this build still opens ELI v3 and the bundles this build knows.
- Only the developer's own **git checkout** (a `.git` folder beside `eli/`, no release
  manifest) may skip the licence, and only with `ELI_LICENSE_BYPASS=1`.

## Environment flags

| Variable | Effect |
|---|---|
| `ELI_REQUIRE_LICENSE=1` | Activation required (it is anyway, except in a git checkout with the bypass) |
| `ELI_RETAIL_BUILD=1` | Treat this copy as a sold build: the developer switches below are not read |
| `ELI_LICENSE_BYPASS=1` | Skip activation checks — the developer's git checkout only; not read in any installed copy |
| `ELI_LICENSE_KEY` | Ephemeral key for CI/automation (not persisted) |
| `ELI_LICENSE_PUBLIC_KEY` | Override path to `product_signing.pub` — source checkout only; not read in a packaged build or one marked retail |
| `ELI_LICENSE_SIGNING_KEY` | Private PEM path (issuer scripts only) |

## Issuing keys (author only)

Keys for every ELI product are made, recorded, emailed and withdrawn with **eli-licensing**
(private repository `ShadowESC95/eli-licensing`): by hand, in its app, or from Stripe sales with
no server. The scripts in `scripts/licensing/` still work for a quick key. See also
`docs/STORE.md` and `eli/licensing/keys/README.md`.

**Stubs:** [tier_policy.example.json](tier_policy.example.json) · [examples/licensing/README.md](../../examples/licensing/README.md)

Template: [activation.template.json](activation.template.json)
