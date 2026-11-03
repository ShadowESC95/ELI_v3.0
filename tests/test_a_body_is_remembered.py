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
