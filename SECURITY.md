# Security Policy

## Reporting a vulnerability

**Please do _not_ open a public GitHub issue for security vulnerabilities.**

Report them privately by email to **jaybridgeman0095@gmail.com**. Please include:

- a description of the issue and its impact,
- steps to reproduce (or a proof of concept),
- the affected version / commit.

You'll receive an acknowledgement as soon as possible. Please allow reasonable
time for a fix before any public disclosure.

## Scope

ELI runs **entirely locally and offline-by-default**, so the threat model is
different from a hosted service. The areas most worth reporting:

- the **shell security gate** (`RUN_CMD` / `SHELL_EXEC`) — it is fail-closed and
  allowlisted; report any bypass,
- the **custom-agent trust registry** (SHA-256) — report any way to load an
  untrusted/tampered agent,
- the **network guard** (offline-by-default) — report any unauthorised egress,
- the optional **LAN-exposed FastAPI server** — report auth/token bypasses when
  run with `--lan`,
- **loopback / reverse-proxy auth** — tokenless admin via loopback trust, or proxy
  misconfiguration (see below).

Issues that require the user to already have full control of their own machine
(e.g. running arbitrary local code they chose to run) are generally out of scope.

## LAN web server — loopback trust & deployment constraints

The FastAPI server (`api/server.py`) treats **socket loopback peers** (`127.0.0.1`,
`::1`) as the **local operator**: they may call admin endpoints **without a bearer
token**, even when the server is bound to `0.0.0.0` for LAN phone access. This is
intentional for single-user desktop use.

**Deployment footguns (document, do not ignore):**

1. **Reverse proxy on the same machine** — If nginx/Caddy/Traefik terminates TLS
   and forwards to `http://127.0.0.1:<port>`, ELI sees every client as loopback.
   **All remote users become tokenless admin.** Fix: terminate TLS on the LAN bind
   and use `--lan` + bearer tokens / RBAC, or proxy to the LAN IP (not loopback)
   and require tokens.

2. **Shared multi-user host** — Any local Unix user/process that can open
   `http://127.0.0.1:<port>` gets operator admin, **even when RBAC users are
   configured**, unless loopback admin is disabled.

**Opt-out (multi-user / hardened deployments):**

```bash
export ELI_LOOPBACK_ADMIN=0   # require bearer token for localhost too
export ELI_API_TOKEN=your-secret   # or configure RBAC users
```

Default is `ELI_LOOPBACK_ADMIN=1` (loopback operator stays admin so you never lock
yourself out on a personal machine).

## Supported versions

The latest commit on the `master` branch is supported.
