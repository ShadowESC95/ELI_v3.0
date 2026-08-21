# MCP — Model Context Protocol

ELI works both directions here, deliberately separated: it **gains** tools from MCP
servers you configure, and it **offers** its own actions to any other MCP client. The
first grows ELI's capability surface without a code change; the second is what makes it
a platform rather than an application.

Code lives in `eli/integrations/mcp/` (`client.py`, `server.py`), the action surface in
`eli/execution/effectors/mcp_tools.py`, and there is a dedicated page in ELI's own web
server at **`/mcp`** — linked from the sidebar — showing live connection state,
discovered tools, and the real reason for anything that failed.

---

### ELI as host (client)

Configured servers are launched on demand and spoken to over stdio JSON-RPC. Their
tools become available through three actions:

| Action | Phrase |
|---|---|
| `MCP_STATUS` | "what MCP servers do I have" |
| `MCP_TOOLS` | "list my MCP tools" |
| `MCP_CALL` | "run mcp tool files.read_file" |

Config lives at `config/mcp_servers.json` (see `mcp_servers.example.json`) and uses the
familiar `mcpServers` shape, so a server already running elsewhere can be pasted in.

Discovered tools deliberately do **not** each become an entry in `SUPPORTED_ACTIONS`:
the action surface would then differ per machine and the capability manifest could never
be checked against source. One action dispatches by tool name instead.

### ELI as server

```
python -m eli.integrations.mcp.server
```

exposes ELI's own action surface to any MCP client. This is the half that makes ELI a
platform rather than an application.

Actions that drive the machine, run code, or change ELI itself are withheld unless
`ELI_MCP_ALLOW_CONTROL=1`. Handing an arbitrary external client the mouse, the shell and
the self-upgrade path by default would be a poor trade for convenience.

### Why stdio only

An MCP server is a local subprocess, so tool use stays inside the offline-by-default
posture and never touches netguard's network surface. Remote (HTTP/SSE) transports are
absent **by choice** — adding one would route around the process-wide socket guard, and
that is a decision to make deliberately rather than inherit from a convenience feature.

---

## Failure reporting

A server that will not start, a tool that errors, a call that times out — each returns
its real reason rather than an empty success. A tool call that silently returns "done"
is the confabulation failure mode with someone else's process attached.

## The web page

`GET /mcp` on ELI's built-in server (`api/server.py`) serves a dedicated page backed by
two read-only JSON endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /v1/mcp/status` | configured servers, connection state, real failure reasons |
| `GET /v1/mcp/tools?refresh=1` | every discovered tool across every server |

Both sit behind the same token gate as the rest of `/v1` — tokenless on loopback, token
required over `--lan`, matching the server's existing posture. The page itself is served
unauthenticated like the chat UI, and reports the gate plainly if the data is refused.


## The GUI

The desktop app carries the same thing in the **🔌 Extensions** tab
(`eli/gui/tabs/extensions_tab.py`), on the **MCP servers** page: the configured
servers, whether each is enabled, whether each is *verified*, how many tools it
offers, and **Re-check**, which is `doctor()` with each server's exact fault printed.

**➕ Add server…** is the way in that did not exist — the pane could enable, disable
and remove servers but never add one, so the only routes were a chat action or
hand-editing JSON. The dialog accepts either a pasted config or a filled-in form:

* **Paste** takes the whole `mcpServers` block, a bare name→spec mapping, or a single
  server object, because all three shapes appear in READMEs and accepting only the
  canonical one rejects most of what people actually copy. Multi-server blocks offer
  a picker.
* **Form** takes name, command, arguments (shell-quoted, so a path with a space
  survives), `KEY=VALUE` environment pairs, and a *needs network* box that maps to the
  `network` permission — leaving it unticked is what puts the server in an empty
  network namespace where this machine can enforce that.

Either way the entry goes through `install_server` on a worker thread: validate,
runtime preflight, real handshake, then write. A server that fails never reaches the
config, and the operator is told which of those stages failed.

### Settings that are actually read

| Setting | Read by | Effect |
|---|---|---|
| `mcp_start_timeout` | `eli.plugins.mcp.start_timeout()`, `eli.integrations.mcp.client` | seconds a server gets to start and answer the handshake. Read **per server start**, not captured at import — the operator raises it *because* a server timed out, so requiring a restart would defeat it. An explicit argument from a caller still wins. |
| `mcp_expose_control` | `eli.integrations.mcp.server._allow_control()` | whether ELI-as-a-server offers its control actions. `ELI_MCP_ALLOW_CONTROL` still wins where set (including an explicit `0`), because that half usually runs as its own process launched by someone else's client — a GUI toggle that only set an env var inside ELI's interpreter would have had no effect on the process that actually serves. The checkbox disables itself and says so when the env var is present. |
