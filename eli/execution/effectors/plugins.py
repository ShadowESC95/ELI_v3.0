"""Plugins effector — install/uninstall/enable/disable/list/search/status."""
from __future__ import annotations

import json

from eli.utils.log import get_logger

log = get_logger(__name__)


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



from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "PLUGIN_STATUS": _h_plugin_status,
    "PLUGIN_INSTALL": _h_plugin_install,
    "PLUGIN_UNINSTALL": _h_plugin_uninstall,
    "PLUGIN_LIST": _h_plugin_list,
    "PLUGIN_SEARCH": _h_plugin_search,
    "PLUGIN_ENABLE": _h_plugin_enable,
    "PLUGIN_DISABLE": _h_plugin_disable,
}.items():
    _register(_action, _fn)
