# ELI optional clients (v3)

Thin clients that talk to a running **ELI home server** (`ELI --server --lan`). They do not
ship inference or models — your PC still runs ELI.

| Path | Purpose |
|------|---------|
| [`mobile/`](mobile/README.md) | **ELI Mobile Companion** — optional Expo shell for phones/tablets |

**Recommended for most users:** the built-in [PWA dashboard](../api/static/) (no install).
See [`docs/SERVER_AND_WEB_APP.md`](../docs/SERVER_AND_WEB_APP.md).

The native companion is for users who want a home-screen app, haptics, or a sideloaded APK —
without Play Store / App Store accounts or publisher telemetry.
