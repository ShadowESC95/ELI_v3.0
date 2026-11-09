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

## Environment flags

| Variable | Effect |
|---|---|
| `ELI_REQUIRE_LICENSE=1` | Force activation even in source checkout |
| `ELI_RETAIL_BUILD=1` | Retail installer — activation required |
| `ELI_LICENSE_BYPASS=1` | Skip activation checks — source checkout only; not read in a packaged build or one marked retail |
| `ELI_LICENSE_KEY` | Ephemeral key for CI/automation (not persisted) |
| `ELI_LICENSE_PUBLIC_KEY` | Override path to `product_signing.pub` — source checkout only; not read in a packaged build or one marked retail |
| `ELI_LICENSE_SIGNING_KEY` | Private PEM path (issuer scripts only) |

## Issuing keys (author only)

See `scripts/licensing/README.md`, `docs/STORE.md`, and `eli/licensing/keys/README.md`.

**Stubs:** [tier_policy.example.json](tier_policy.example.json) · [examples/licensing/README.md](../../examples/licensing/README.md)

Template: [activation.template.json](activation.template.json)
