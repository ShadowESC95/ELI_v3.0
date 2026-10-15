# ELI Mobile Companion (v3)

Optional **native shell** for talking to your **ELI home server** on phone or tablet.

This is **not** core ELI inference. Your PC (or home server) still runs ELI + the model; the
phone is a thin client — same architecture as the built-in
[PWA dashboard](../../docs/SERVER_AND_WEB_APP.md).

## Recommended path (no store, no Google/Apple account)

For most users, **use the PWA** — already ships with ELI:

1. On the PC: `./scripts/eli_serve.sh --lan`
2. On the phone browser (same Wi‑Fi): open `http://<host-ip>:8081/?token=…`

No install, no publisher account, fully aligned with ELI's privacy model.

Use this native companion only when you want a home-screen app, haptics, or a sideloaded APK.

## What it does today

- Connect to `http://<your-pc-ip>:8081` on your **LAN** (Mode 1 — home Wi‑Fi)
- Bearer token auth (required when ELI runs with `--lan`)
- **Streaming chat** via `/v1/chat/stream` (matches the web dashboard)
- Save host, token, and session between launches
- Open Connect URLs / QR links (`http://IP:8081/?token=…`)

## What it does **not** do

- Run the AI model on the phone
- Work away from home unless you add your own secure remote access (VPN) later
- Replace the full web dashboard (chat first; no Home/Research/Audit tabs yet)
- Send data to geteli or any ELI cloud — it talks **only** to the URL you configure

## Prerequisites

1. **ELI desktop or server** on your network:
   ```bash
   ./scripts/eli_serve.sh --lan
   ```
2. **Node.js 20+** and **npm** (development / building APKs only)

## Development

```bash
cd clients/mobile
npm install
npx expo start
```

Scan the QR with Expo Go, or press `a` / `i` for Android / iOS simulators.

### First connection

1. On the PC, note the LAN URL from `--lan`, e.g. `http://192.168.1.50:8081/?token=…`
2. In the app → **Settings** → paste the IP (port **8081** is added automatically if omitted)
3. Paste the **token** from the URL
4. **Save connection** → chat

Default port is **8081** (ELI's standard API port).

---

## Distribution without stores (recommended)

ELI does **not** require Play Store or App Store distribution. Store accounts imply a
publisher relationship with Google/Apple; the companion app is designed so users can avoid
that entirely.

### Option A — PWA (simplest)

See [`docs/SERVER_AND_WEB_APP.md`](../../docs/SERVER_AND_WEB_APP.md). Zero extra build step.

For voice in mobile browsers, start the server with HTTPS:

```bash
./scripts/eli_serve.sh --lan --https
```

### Option B — Official GitHub APK (Android sideload)

Build a release APK and attach it to a **GitHub Release** (same channel as ELI desktop).

**Maintainer build (EAS, no Play account required for users):**

```bash
cd clients/mobile
npm install
npm install -g eas-cli   # optional; or use local npx eas
eas login                # Expo account only — for building, not for end users
eas build:configure      # once

# Unsigned/internal APK suitable for sideload testing
eas build -p android --profile preview
```

Download the `.apk` from the EAS build page or CI artifact, publish on GitHub Releases with
a **SHA-256 checksum**:

```bash
sha256sum eli-companion-*.apk
```

**User install (Android):**

1. Download the APK from the official ELI v3 release page
2. Verify the checksum matches the release notes
3. Settings → Security → allow install from this source (one-time)
4. Open the APK → install
5. Settings in app → paste PC IP + token from `eli_serve --lan`

**Trust model:** Only install APKs from official ELI releases. Third-party rebuilds may
capture your LAN token — treat unofficial builds like untrusted plugins.

### Option C — Build from source

Anyone can audit and build the same APK without trusting a binary:

```bash
git clone https://github.com/ShadowESC95/ELI_v3.0.git
cd ELI_v3.0/clients/mobile
npm install
npx expo run:android --variant release   # requires Android SDK locally
```

### Option D — F-Droid (future)

Possible long-term for the privacy community. Requires reproducible builds and a maintainer;
not required for launch.

### Deferred — Play Store / App Store

Store distribution is **optional** and only needed for non-technical mass discoverability.
If you pursue it later, bundle ID is already reserved as `tech.geteli.companion`.

```bash
# Play Store (AAB) / App Store — only when you accept publisher accounts
eas build -p android --profile production
eas build -p ios --profile production
eas submit -p android --profile production   # needs Google Play developer account
eas submit -p ios --profile production       # needs Apple Developer account
```

Store listing must state clearly: **requires ELI running on the user's own PC**; the app
does not provide cloud AI and does not phone home to geteli.

---

## Relation to community plugins / MCPs

| Layer | Extends | Runs on |
|-------|---------|---------|
| ELI plugins / MCP servers | PC capabilities | **Host PC** |
| Mobile Companion | Chat UI | **Phone** (thin client) |
| PWA | Full dashboard | **Phone browser** |

Community plugins and MCPs make ELI more capable **on the PC**. They complement — but do
not replace — the mobile companion or PWA. See [`docs/MARKETPLACE.md`](../../docs/MARKETPLACE.md).

## Relation to `~/Desktop/ELIMobile`

The old **ELIMobile** folder mixed a stale Toga Android prototype with an Expo app.
**Only the Expo client lives here (v3).** The Toga `android_app/` path was dropped (wrong
port, wrong `/chat` route, no token auth).

## Roadmap

- [ ] QR scanner for Connect tab links
- [ ] Offline / unreachable UI when away from home LAN
- [ ] Voice in → `/v1/voice/stt`
- [ ] Voice out → `/v1/voice/tts` with background audio
- [ ] Deep link scheme `elicompanion://` from Connect QR
- [ ] Tablet split layout (settings + chat)

## License

Part of ELI v3, under the same terms: the [ELI End User Licence Agreement](../../COMMERCIAL_EULA.md).
See the project `LICENSE`.
