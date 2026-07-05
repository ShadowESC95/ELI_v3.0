# Commercial licensing — ELI v3 (paid product)

ELI v3 is **not open source**. The repo `LICENSE` (PolyForm Internal Use 1.0.0) governs
what people can do with the **source on GitHub**:

| Allowed (PolyForm Internal) | Not allowed without a separate deal |
|---|---|
| Download, run, modify for **your own** internal/personal use | Redistribute, sublicense, or sell the software |
| Private evaluation and development | Host as a multi-tenant service for others |
| | Ship a fork or rebranded build |

**You (the copyright holder) already retain all commercial rights.** PolyForm Internal
does *not* stop you from selling ELI — it stops *everyone else* from selling or sharing it.

## What to use for paying customers

Do **not** switch the public repo to MIT/Apache/GPL if v3 stays a paid product — that
would grant redistribution rights you do not want.

Recommended stack:

1. **GitHub / eval:** keep `LICENSE` = PolyForm Internal Use (current).
2. **Paid buyers:** issue a separate **Commercial License Agreement (EULA)** per tier
   (personal / pro / enterprise) — seat count, updates, support window, no redistribution.
3. **Runtime enforcement:** `eli/licensing/` (activation keys, machine binding) — already
   wired for v3; keys are signed offline, verified at startup.
4. **Deliverable:** ship binaries/portable packages + license file + key — not necessarily
   the full private source unless the tier includes it.

## Optional later

- **PolyForm Noncommercial** — if you want a stricter public license (no commercial use
  by third parties at all, even “internal business” edge cases).
- **Dual licensing page** — “PolyForm Internal for eval; contact for commercial license.”
- **Trademark** — “ELI” branding on paid builds; PolyForm does not cover trademarks.

Questions / commercial terms: jaybridgeman0095@gmail.com
