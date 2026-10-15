# ELI Editions — shared core + composable edition packs

ELI is sold as a **family of domain editions on one maintained core**, not eight
forked codebases. This directory is the productization layer.

```
eli/
  <core>                      # engine, executor→effectors, router→routing, memory,
                              # perception, security — domain-neutral, maintained ONCE
  editions/
    __init__.py               # EditionRegistry: pack discovery + activation seam
    base/         (general)   # the default assistant; no domain specialisation
    research/                 # deep-research, citations, literature review
    healthcare/               # clinical documentation (NOT medical advice)
    legal/                    # contract/clause drafting (NOT legal advice)
    coding/                   # repo-scale coding agent, test-gen, IDE
    content/                  # copywriting, social, brand voice, image-gen
    vr/                       # ELI's rooms in a headset; ELI VR is the application
    enterprise/               # all editions + RBAC / audit / fleet admin
```

## Two orthogonal axes

| Axis | Values | Lives in | Governs |
|---|---|---|---|
| **Licence tier** | trial · personal · pro · enterprise | `eli/licensing` | seats, commercial rights, support, updates |
| **Edition** | general · research · healthcare · legal · coding · content · vr · enterprise | `eli/editions` | which domain capability pack(s) light up |

A signed licence key names **one product** and carries a **tier**. ELI v3 is one product; each
official bundle (ELI Legal, ELI VR, …) is another, bought separately with its own key (see
`eli/licensing/products.py` and `docs/BUNDLES.md`).

## Anatomy of an edition pack

Every pack is the same shape (copy `base/` to start a new one):

```
<edition>/
  __init__.py
  manifest.json     # edition name, label, personas, capability categories, templates, tiers
  policy.py         # safety/compliance hooks: system-prompt addendum, disclaimer, blocked actions
  capabilities/     # edition-specific capabilities (compose onto the core registry)
  personas/         # edition personas (feed the emergent persona brief)
  templates/        # report/doc templates the edition adds
  tools/            # edition-specific declarative tools (MCP-style, under the approval gate)
```

The core knows **nothing** domain-specific. It calls `policy.py` hooks and reads
`manifest.json`; the pack supplies the divergence. That is why one core can serve
eight products without eight maintenance burdens.

## How activation will work

`editions.active_editions(entitled)` returns the packs a licence may use. Today it
defaults to `general` only (unchanged default experience). The `entitled` set will
be the bundle products this machine holds a key for, from
`eli.licensing.product_status` (see the `TODO` in `__init__.py`).

## Status

**Scaffold.** Manifests and policy hooks exist; capabilities/personas/templates are
empty and fill in per-edition as each vertical is built out. No pack is wired into
startup yet — the registry is inert until the core composes it.
