"""A body (ELI VR) coming and going is something ELI remembers (eli/world/embodiment.py): asked
about the VR session it had been in an hour earlier, ELI said it had no record of it."""
from __future__ import annotations

from eli.world import embodiment


def test_a_body_arriving_and_leaving_is_remembered_once_each(monkeypatch):
    kept = []
    monkeypatch.setattr(embodiment, "_remember", lambda title, text: kept.append((title, text)))
    embodiment.forget()
    kept.clear()
    scene = {"surface": "eli_vr", "room": "core_room", "room_name": "Core Room", "headset": True, "minutes": 1}
    embodiment.report(scene)
    embodiment.report({**scene, "minutes": 2})
    assert len(kept) == 1 and kept[0][0] == "A body arrived"
    assert kept[0][1].startswith("ELI VR (headset) joined ELI in Core Room at ")
    assert embodiment.present()[0]["surface"] == "eli_vr"
    embodiment.forget("eli_vr")
    assert len(kept) == 2 and kept[1][0] == "A body left" and "after 1 minute with ELI (it came in Core Room)" in kept[1][1]
    assert embodiment.present() == []
    embodiment.forget("eli_vr")
    assert len(kept) == 2, "leaving twice is not remembered twice"


def test_a_body_gone_quiet_is_remembered_as_having_left(monkeypatch):
    kept = []
    monkeypatch.setattr(embodiment, "_remember", lambda title, text: kept.append(title))
    embodiment.forget()
    kept.clear()
    embodiment.report({"surface": "eli_vr", "room": "workshop", "headset": False})
    assert embodiment.live() is not None and kept == ["A body arrived"]
    assert embodiment.live(max_age=-1.0) is None, "stale: gone"
    assert kept == ["A body arrived", "A body left"]


# ── presence is per session, ends at the last moment seen, and is shared on disk ──

def _clock(monkeypatch, start=1_800_000_000.0):
    now = {"t": start}
    monkeypatch.setattr(embodiment, "_now", lambda: now["t"])
    return now


def _quiet(monkeypatch):
    kept = []
    monkeypatch.setattr(embodiment, "_remember", lambda title, text: kept.append((title, text)))
    monkeypatch.setattr(embodiment, "_measure_headset", lambda: None)
    embodiment.forget()
    kept.clear()
    return kept


def test_a_new_session_on_the_same_body_is_an_arrival_and_ends_the_old_one(monkeypatch):
    """A second VR session that started while the first was still marked present was not recorded
    at all: presence was kept by surface name, not by session."""
    kept = _quiet(monkeypatch)
    now = _clock(monkeypatch)
    scene = {"surface": "eli_vr", "room": "core_room", "room_name": "Core Room", "headset": True}
    embodiment.report({**scene, "session": "eli-vr-1"})
    now["t"] += 20
    embodiment.report({**scene, "session": "eli-vr-1", "room": "workshop", "room_name": "Workshop"})
    now["t"] += 10
    embodiment.report({**scene, "session": "eli-vr-2"})
    assert [title for title, _ in kept] == ["A body arrived", "A body left", "A body arrived"]
    assert "It was also in Workshop." in kept[1][1]
    assert [b["session"] for b in embodiment.present()] == ["eli-vr-2"]
    first = [s for s in embodiment.sessions_between(now["t"] - 60, now["t"] + 1) if s["session"] == "eli-vr-1"]
    assert first and first[-1]["ended"] == now["t"] - 10, "it ended when it was last seen"
    assert first[-1]["rooms"] == ["Core Room", "Workshop"] and first[-1]["end"] == "a new session began"
    embodiment.forget()


def test_a_session_ends_at_its_last_scene_when_its_link_closes(monkeypatch):
    kept = _quiet(monkeypatch)
    now = _clock(monkeypatch)
    embodiment.report({"surface": "eli_vr", "session": "eli-vr-9", "room": "workshop", "room_name": "Workshop"})
    started = now["t"]
    for _ in range(5):                                # a scene every 25 s, as a body sends them
        now["t"] += 25
        embodiment.report({"surface": "eli_vr", "session": "eli-vr-9", "room": "workshop", "room_name": "Workshop"})
    now["t"] += 3000                                  # the link closes long after the last scene
    embodiment.end("eli_vr", "eli-vr-9", why="its world link closed")
    assert kept[-1][0] == "A body left" and "after 2 minutes with ELI" in kept[-1][1]
    done = [s for s in embodiment.sessions_between(started - 1, started + 1) if s["session"] == "eli-vr-9"]
    assert done[-1]["started"] == started and done[-1]["ended"] == started + 125
    assert len([t for t, _ in kept if t == "A body arrived"]) == 1, "one session, one arrival"
    assert done[-1]["end"] == "its world link closed"
    assert embodiment.sessions_between(started + 200, started + 300) == [], "a period after it left"


def test_every_eli_process_on_the_data_folder_sees_the_body(monkeypatch):
    """ELI VR started with `eli --bundle vr` is served by a headless ELI of its own; the desktop
    window beside it is another process, and held presence in memory never saw the body."""
    import importlib.util
    _quiet(monkeypatch)
    spec = importlib.util.spec_from_file_location("eli_world_embodiment_other_process", embodiment.__file__)
    other = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(other)
    monkeypatch.setattr(other, "_remember", lambda title, text: True)
    embodiment.report({"surface": "eli_vr", "session": "eli-vr-3", "room": "workshop", "room_name": "Workshop",
                       "headset": True})
    assert other.live()["room"] == "workshop"
    assert [b["session"] for b in other.present()] == ["eli-vr-3"]
    assert "standing in the Workshop" in other.context_block()
    other.forget()
    assert embodiment.live() is None
