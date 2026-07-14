"""Smart-home effector — SMART_HOME (ELI's own MQTT device server + Bluetooth verbs)."""
from __future__ import annotations



from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_smart_home(a, args):
    try:
        from eli.runtime.device_server import get_server
        srv = get_server()
        device = (args.get("device") or args.get("name") or "").strip()
        raw = (args.get("command") or args.get("action") or args.get("state") or "").strip().lower()
        # Bluetooth verbs (connect/disconnect/pair/use_for_audio) — resolve the spoken name
        # against discovered/registered Bluetooth devices and drive the OS BT stack. Handled
        # HERE, before the on/off mapping below ("on" is a substring of "c-on-nect").
        _btc = raw.replace(" ", "_").replace("-", "_")
        if args.get("bt") or _btc in ("connect", "disconnect", "pair", "trust", "use_for_audio"):
            if _btc in ("use_for_audio", "audio", "route_audio") and device:
                from eli.runtime.local_connectivity import route_audio_by_name
                _ar = route_audio_by_name(device)
                if _ar.get("ok"):
                    _label = _ar.get("display_name") or _ar.get("alias") or device
                    _say = f"Audio is now playing through {_label}."
                    return {"ok": True, "action": a, "content": _say, "response": _say}
            from eli.runtime.device_server import bluetooth_control_by_name
            _r = bluetooth_control_by_name(device, _btc or "connect")
            _nm = _r.get("device_name") or device or "the device"
            if _r.get("ok"):
                _say = {
                    "use_for_audio": f"Audio is now playing through {_nm}.",
                    "connect": f"Connected to {_nm}.",
                    "disconnect": f"Disconnected {_nm}.",
                    "pair": f"Paired with {_nm}.",
                    "trust": f"Trusted {_nm}.",
                }.get(_btc, f"{_nm}: {_btc}.")
                return {"ok": True, "action": a, "content": _say, "response": _say}
            _e = _r.get("error") or "I couldn't do that with Bluetooth."
            return {"ok": False, "action": a, "error": _e, "content": _e, "response": _e}
        cmd = "on" if "on" in raw else "off" if "off" in raw else raw
        if not device:
            msg = "Which device or room? For example: 'turn on the desk lamp'."
            return {"ok": False, "action": a, "error": "no_device", "content": msg, "response": msg}
        devs = srv.list_devices()
        if not devs:
            msg = ("No devices set up yet — add them in the Home tab of ELI's web app "
                   "(ELI's own MQTT device server).")
            return {"ok": False, "action": a, "error": "no_devices", "content": msg, "response": msg}
        dl = device.lower()
        match = next((d for d in devs if d["id"].lower() == dl or (d.get("name", "").lower() == dl)), None)
        if match is None:
            match = next((d for d in devs if dl and dl in (d.get("name", "").lower())), None)
        if match is not None:
            res = srv.control(match["id"], cmd)
            label = match.get("name") or match["id"]
            if res.get("ok"):
                msg = f"{label}: {cmd}"
                return {"ok": True, "action": a, "content": msg, "response": msg}
            err = res.get("error") or "control failed"
            return {"ok": False, "action": a, "error": err, "content": err, "response": err}
        # Not a device — maybe a room name ("turn off the kitchen").
        room_names = {r["room"].lower(): r["room"] for r in srv.rooms()}
        if dl in room_names:
            res = srv.control_room(room_names[dl], cmd)
            if res.get("ok"):
                msg = f"{room_names[dl]}: {cmd} ({res.get('count', 0)} device(s))"
                return {"ok": True, "action": a, "content": msg, "response": msg}
            err = res.get("error") or "no controllable devices"
            return {"ok": False, "action": a, "error": err, "content": err, "response": err}
        # Not a device or room — maybe a scene ("activate movie mode" / "movie mode").
        scene_names = {s["name"].lower(): s["name"] for s in srv.list_scenes()}
        scene_key = next((k for k in scene_names if k in dl or dl in k), None)
        if scene_key:
            res = srv.activate_scene(scene_names[scene_key])
            if res.get("ok"):
                msg = f"Scene '{res.get('scene')}' activated ({res.get('ran', 0)} device(s))."
                return {"ok": True, "action": a, "content": msg, "response": msg}
            err = res.get("error") or "scene failed"
            return {"ok": False, "action": a, "error": err, "content": err, "response": err}
        msg = f"I don't have a device, room, or scene called '{device}'."
        return {"ok": False, "action": a, "error": "not_found", "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "SMART_HOME": _h_smart_home,
}.items():
    _register(_action, _fn)
