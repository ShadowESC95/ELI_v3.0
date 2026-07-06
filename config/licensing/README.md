# ELI v3 license activation (config)

Runtime activation state is stored per machine under your ELI config directory:

| Platform | Path |
|---|---|
| Linux | `~/.config/eli/licensing/activation.json` |
| macOS | `~/Library/Preferences/eli/licensing/activation.json` |
| Windows | `%APPDATA%/eli/licensing/activation.json` |
| Dev checkout | `<project>/config/licensing/activation.json` |

Override with `ELI_LICENSE_DIR` or `ELI_CONFIG_DIR`.

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
```

## Environment flags

| Variable | Effect |
|---|---|
| `ELI_REQUIRE_LICENSE=1` | Force activation even in source checkout |
| `ELI_RETAIL_BUILD=1` | Retail installer — activation required |
| `ELI_LICENSE_BYPASS=1` | Skip activation checks (dev only) |
| `ELI_LICENSE_KEY` | Ephemeral key for CI/automation (not persisted) |
| `ELI_LICENSE_PUBLIC_KEY` | Override path to `product_signing.pub` |
| `ELI_LICENSE_SIGNING_KEY` | Private PEM path (issuer scripts only) |

## Issuing keys (author only)

See `scripts/licensing/README.md`, `docs/STORE.md`, and `eli/licensing/keys/README.md`.

**Stubs:** [tier_policy.example.json](tier_policy.example.json) · [examples/licensing/README.md](../../examples/licensing/README.md)

Template: [activation.template.json](activation.template.json)
