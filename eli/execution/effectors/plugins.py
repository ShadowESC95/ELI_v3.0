"""Plugins effector — install/uninstall/enable/disable/list/search/status."""
from __future__ import annotations

import json

from eli.utils.log import get_logger

log = get_logger(__name__)


def _eli_marketplace_install(query: str):
    """Install a community listing through the verified path, or None if not one.

    Returns None when the query does not match anything in a configured community
    registry, so the caller falls through to the bundled-plugin path. Chat can
    trigger a verified install but NEVER a permissioned one: anything that would
    open the consent dialog is reported back as needing the desktop screen, because
    a chat message cannot carry informed consent for granting capabilities.
    """
    try:
        from eli.plugins.marketplace import needs_review, preview_install, search
    except Exception:
        return None
    try:
        matches = search(query)
    except Exception:
        return None
    exact = [m for m in matches if str(m.get("id", "")).lower() == query.lower()]
    candidates = exact or matches
    if not candidates:
        return None
    if len(candidates) > 1 and not exact:
        listing = None
    else:
        listing = candidates[0]
    if listing is None:
        names = ", ".join(str(m.get("id")) for m in candidates[:5])
        return {"ok": True,
                "message": f"Several community listings match '{query}': {names}. "
                           f"Name one exactly and I'll check it."}

    if str(listing.get("kind")) == "mcp":
        from eli.plugins.mcp import network_caveat
        return {"ok": True,
                "message": (f"'{listing.get('name')}' is an MCP server, which runs as a "
                            f"separate program.\n\n{network_caveat()}\n\n"
                            f"Add it from Settings > Marketplace > MCP servers.")}

    preview = preview_install(listing)
    reasons = needs_review(preview)
    if reasons:
        scan = (preview.get("scan") or {})
        detail = "; ".join(reasons)
        verdict = scan.get("verdict")
        head = (f"I checked '{listing.get('name')}' and will not install it from chat: "
                if verdict == "malicious" else
                f"I checked '{listing.get('name')}'. It needs your decision: ")
        return {"ok": verdict != "malicious",
                "message": head + detail +
                           "\n\nOpen Settings > Marketplace to see the full report and "
                           "decide."}

    from eli.plugins.marketplace import install
    result = install(listing, confirm=lambda _p: True)
    if not result.get("ok"):
        return {"ok": False,
                "message": "Install failed: " + "; ".join(result.get("problems") or [])}
    return {"ok": True,
            "message": (f"Installed '{listing.get('name')}' — it matched the checksum in "
                        f"its listing, scanned clean, and asks for no permissions. "
                        f"It is switched OFF; say 'enable plugin "
                        f"{listing.get('id')}' when you want it.")}


def _h_plugin_status(a, args):
    try:
        from eli.plugins.manager import get_manager
        mgr = get_manager()
        installed = mgr.list_installed() or []
        available = mgr.list_available() or []
        lines = [f"Plugin system status:"]
        lines.append(f"  Installed: {len(installed)}  |  Available in registry: {len(available)}")
        if installed:
            lines.append("")
            lines.append("Installed plugins:")
            for p in installed:
                status = "enabled" if p.get("enabled") else "disabled"
                lines.append(f"  {p['id']:20s} [{status}]  {p.get('description','')[:60]}")
        else:
            lines.append("  No plugins installed.")
        # Check each installed plugin imports cleanly
        lines.append("")
        lines.append("Plugin health:")
        for p in installed:
            try:
                mod_path = f"plugins.{p['id']}.plugin"
                __import__(mod_path)
                lines.append(f"  {p['id']:20s} OK")
            except Exception as pe:
                lines.append(f"  {p['id']:20s} ERROR: {pe}")
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "installed": len(installed), "available": len(available)}
    except Exception as e:
        msg = f"Plugin status error: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}



def _h_plugin_install(a, args):
    try:
        from eli.plugins.manager import get_manager
        mgr = get_manager()
        query = (args.get("query") or args.get("plugin") or args.get("name") or "").strip()
        if not query:
            return {"ok": False, "action": a, "error": "No plugin specified",
                    "content": json.dumps({"missing_arg": "plugin", "action": a}, ensure_ascii=False),
                    "response": json.dumps({"missing_arg": "plugin", "action": a}, ensure_ascii=False)}

        # Try exact match first
        # Community listings go through the marketplace path — checksum, signature,
        # static check, eleven scanners, consent — not manager.install(), which
        # downloads over raw urllib and executes the result. A bundled built-in
        # still uses the manager: it shipped with ELI.
        market = _eli_marketplace_install(query)
        if market is not None:
            return {"ok": market["ok"], "action": a,
                    "content": market["message"], "response": market["message"]}

        entry = mgr.get_registry_entry(query.lower().replace(" ", "_"))
        if entry:
            result = mgr.install(entry["id"])
            return {"ok": result.get("ok", False), "action": a,
                    "content": result.get("content", str(result)),
                    "response": result.get("response", str(result))}

        # Semantic search
        matches = mgr.search(query)
        if not matches:
            msg = f"No plugins found matching '{query}'. Use 'list plugins' to see what's available."
            return {"ok": False, "action": a, "content": msg, "response": msg}

        if len(matches) == 1:
            result = mgr.install(matches[0]["id"])
            return {"ok": result.get("ok", False), "action": a,
                    "content": result.get("content", str(result)),
                    "response": result.get("response", str(result))}

        # Multiple matches — show options
        lines = [f"Found {len(matches)} plugins matching '{query}':"]
        for m in matches[:5]:
            lines.append(f"  • {m['id']} — {m.get('description', '')}")
        lines.append("\nSpecify the exact plugin id to install, e.g. 'install plugin pomodoro'.")
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_plugin_uninstall(a, args):
    try:
        from eli.plugins.manager import get_manager
        pid = (args.get("plugin") or args.get("name") or args.get("id") or "").strip().lower().replace(" ", "_")
        if not pid:
            msg = json.dumps({"missing_arg": "plugin", "action": a}, ensure_ascii=False)
            return {"ok": False, "action": a, "error": "No plugin specified",
                    "content": msg,
                    "response": msg}
        result = get_manager().uninstall(pid)
        return {"ok": result.get("ok", False), "action": a,
                "content": result.get("content", str(result)),
                "response": result.get("response", str(result))}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_plugin_list(a, args):
    try:
        from eli.plugins.manager import get_manager
        mgr = get_manager()
        scope = (args.get("scope") or "all").lower()

        if scope == "installed":
            plugins = mgr.list_installed()
            if not plugins:
                msg = "No plugins installed."
            else:
                lines = [f"Installed plugins ({len(plugins)}):"]
                for p in plugins:
                    status = "✓ enabled" if p["enabled"] else "⏸ disabled"
                    lines.append(f"  • {p['id']} ({status}) — {p.get('description', '')}")
                msg = "\n".join(lines)
        else:
            plugins = mgr.list_available()
            installed_ids = {p["id"] for p in mgr.list_installed()}
            lines = [f"Available plugins ({len(plugins)}):"]
            for p in plugins:
                tag = " [installed]" if p["id"] in installed_ids else ""
                lines.append(f"  • {p['id']} — {p.get('description', '')}{tag}")
            msg = "\n".join(lines)

        return {"ok": True, "action": a, "count": len(plugins),
                "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_plugin_search(a, args):
    try:
        from eli.plugins.manager import get_manager
        query = (args.get("query") or args.get("text") or "").strip()
        if not query:
            return {"ok": False, "action": a, "error": "No search query",
                    "content": "What kind of plugin are you looking for?",
                    "response": "What kind of plugin are you looking for?"}
        matches = get_manager().search(query)
        if not matches:
            msg = f"No plugins found matching '{query}'."
        else:
            lines = [f"Plugins matching '{query}' ({len(matches)}):"]
            for m in matches[:8]:
                actions = ", ".join(m.get("actions", [])[:3])
                lines.append(f"  • {m['id']} — {m.get('description', '')} [{actions}]")
            msg = "\n".join(lines)
        return {"ok": True, "action": a, "count": len(matches),
                "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_plugin_enable(a, args):
    try:
        from eli.plugins.manager import get_manager
        pid = (args.get("plugin") or args.get("name") or args.get("id") or "").strip().lower().replace(" ", "_")
        if not pid:
            msg = json.dumps({"missing_arg": "plugin", "action": a}, ensure_ascii=False)
            return {"ok": False, "action": a, "content": msg, "response": msg}
        result = get_manager().enable(pid)
        return {"ok": result.get("ok", False), "action": a,
                "content": result.get("content", str(result)),
                "response": result.get("response", str(result))}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_plugin_disable(a, args):
    try:
        from eli.plugins.manager import get_manager
        pid = (args.get("plugin") or args.get("name") or args.get("id") or "").strip().lower().replace(" ", "_")
        if not pid:
            msg = json.dumps({"missing_arg": "plugin", "action": a}, ensure_ascii=False)
            return {"ok": False, "action": a, "content": msg, "response": msg}
        result = get_manager().disable(pid)
        return {"ok": result.get("ok", False), "action": a,
                "content": result.get("content", str(result)),
                "response": result.get("response", str(result))}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



# ── MCP servers ──────────────────────────────────────────────────────────────
# Separate processes ELI configures rather than code it loads, so they get their own
# actions rather than being squeezed into the plugin ones.

def _h_mcp_list(a, args):
    # Lifecycle view: what is CONFIGURED. The runtime view — what tools a live
    # server exposes — is MCP_STATUS/MCP_TOOLS in effectors/mcp_tools.py, which
    # owns those actions. Keeping them separate keeps "what did I install" and
    # "what can it do right now" from being the same question.
    try:
        from eli.plugins.mcp import config_path, list_servers
        servers = list_servers()
        if not servers:
            msg = ("No MCP servers are configured. Add one from Settings > "
                   "Marketplace > MCP servers, or ask me to add one by name.")
            return {"ok": True, "action": a, "content": msg, "response": msg}
        lines = [f"{len(servers)} MCP server(s) — config: {config_path()}"]
        for srv in servers:
            state = "on" if srv.get("enabled") else "off"
            lines.append(f"  - {srv['id']} [{state}] {len(srv.get('tools') or [])} tool(s)"
                         + ("" if srv.get("verified") else "  (unverified)"))
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_mcp_doctor(a, args):
    try:
        from eli.plugins.mcp import doctor
        report = doctor()
        lines = [report["summary"]]
        for srv in report.get("servers", []):
            lines.append(f"  - {srv['id']}: "
                         + (f"ok, {srv['tools']} tool(s)" if srv["ok"] else srv["problem"]))
        msg = "\n".join(lines)
        return {"ok": report.get("ok", False), "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_mcp_remove(a, args):
    try:
        from eli.plugins.mcp import remove_server
        sid = (args.get("server") or args.get("id") or args.get("name") or "").strip()
        if not sid:
            msg = json.dumps({"missing_arg": "server", "action": a}, ensure_ascii=False)
            return {"ok": False, "action": a, "error": "No server specified",
                    "content": msg, "response": msg}
        result = remove_server(sid)
        msg = (f"Removed MCP server '{sid}'." if result["ok"]
               else "; ".join(result["problems"]))
        return {"ok": result["ok"], "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_mcp_add(a, args):
    # Deliberately NOT installable from chat: adding an MCP server means running a
    # program outside ELI's process, and the containment that applies is
    # machine-dependent. Chat points at the screen that states what it will be.
    try:
        from eli.plugins.mcp import network_caveat
        name = (args.get("server") or args.get("name") or "").strip()
        msg = ("Adding an MCP server runs a separate program on this machine, so it "
               "needs the confirmation screen rather than a chat message.\n\n"
               + network_caveat()
               + "\n\nOpen Settings > Marketplace > MCP servers"
               + (f" and search for '{name}'." if name else " to add one."))
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "MCP_ADD": _h_mcp_add,
    "MCP_DOCTOR": _h_mcp_doctor,
    "MCP_LIST": _h_mcp_list,
    "MCP_REMOVE": _h_mcp_remove,
    "PLUGIN_STATUS": _h_plugin_status,
    "PLUGIN_INSTALL": _h_plugin_install,
    "PLUGIN_UNINSTALL": _h_plugin_uninstall,
    "PLUGIN_LIST": _h_plugin_list,
    "PLUGIN_SEARCH": _h_plugin_search,
    "PLUGIN_ENABLE": _h_plugin_enable,
    "PLUGIN_DISABLE": _h_plugin_disable,
}.items():
    _register(_action, _fn)
