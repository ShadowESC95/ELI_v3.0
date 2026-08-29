# Runtime data (not in git)

Created automatically on `install.sh`, first `eli` launch, or `python -m eli.core.init_data`.

Fresh clones also get **schema-only DB templates** from `config/templates/db/` (copied
by `install.sh` when `artifacts/db/` is empty). No personal data is written until you use ELI.

```
artifacts/
  db/              user.sqlite3, agent.sqlite3, system_index.sqlite3, coding_memory.sqlite3
  vectors/         FAISS index
  runtime/         world model, snapshots, user profiles
  conversations/   chat logs
  logs/
```

**Dev mode:** `<project>/artifacts/`  
**Installed / frozen:** `~/.local/share/ELI_v3/` (Linux), `%LOCALAPPDATA%\\ELI_v3` (Windows),
`~/Library/Application Support/ELI_v3` (macOS).

Factory reset (GUI Advanced → Clear Memory, or `python tools/clear_memory.py`) wipes
all learned state, resets `persona.auto.txt`, clears identity from settings, and
rebuilds the full DB architecture via `init_all_data`.
