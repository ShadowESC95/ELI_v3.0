# Runtime data (not in git)

This folder is **gitignored** on purpose. ELI creates it automatically on install
or first launch — you do not check it into GitHub.

## Created by

- `bash install.sh` → `python -m eli.core.init_data`
- First `eli` run → `bootstrap_once()` in `eli/__main__.py`
- `python -m eli.core.paths` (calls `ensure_dirs()`)

## Typical layout (dev checkout)

```
artifacts/
  db/              user.sqlite3, agent.sqlite3, …
  vectors/         FAISS index
  runtime/         world model, snapshots, user profiles
  conversations/   chat logs
  logs/            startup / runtime logs
  cache/           transient runtime cache
```

**Dev mode:** `<project>/artifacts/`  
**Installed:** `~/.local/share/eli/` (Linux), `%LOCALAPPDATA%/eli/` (Windows), etc.

Fresh clone? Run `install.sh` or start `eli` once — folders and DB schemas appear
empty (blank slate, no personal data until you use ELI).
