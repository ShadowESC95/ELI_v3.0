"""Verified self-facts are available and correct in v3.

v3 had the NO INVENTED SELF-MECHANISM guard ported (the prohibition) but not
`self_facts` (the facts it's meant to use instead). A prohibition without a
grounded alternative just makes ELI refuse; these pin the alternative.

Also guards the version bug this port exposed: v3's `_local_version()` read
installed dist metadata for `eli-mkxi` — the v2 package name, still present in
the venv at 2.0.0 — ahead of pyproject, so v3 described itself as version 2.0.0.
"""
from __future__ import annotations

import re

from eli.runtime.self_facts import (
    get_self_facts,
    render_self_facts_block,
    repair_self_description,
)


def test_version_is_the_running_v3_version_not_a_stale_dist():
    from eli.kernel.self_upgrade import SelfUpgrader
    import tomllib
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    expected = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    assert SelfUpgrader()._local_version() == expected
    assert get_self_facts()["version"] == expected
    assert expected.startswith("3."), "v3 must not report a 2.x version"


def test_facts_are_populated_from_the_live_system():
    f = get_self_facts()
    for key in ("version", "install_kind", "databases", "capabilities", "components"):
        assert key in f, f"missing fact: {key}"
    assert f["install_kind"] in {"source", "appimage", "frozen"}
    assert int(f["capabilities"]) > 0


def test_database_paths_are_real_and_absolute():
    """Entries are labelled ("user DB: /path"); the path part must be real."""
    entries = get_self_facts()["databases"]
    assert entries, "no databases reported"
    for entry in entries:
        path = entry.split(": ", 1)[-1]
        assert path.startswith("/") or re.match(r"^[A-Za-z]:", path), entry
        assert path.endswith(".sqlite3"), f"real stores end in .sqlite3, got {entry}"


def test_block_instructs_against_inventing():
    block = render_self_facts_block()
    assert "VERIFIED SELF-FACTS" in block
    assert "Do NOT invent" in block


def test_repair_fixes_an_invented_path():
    bad = "My databases live at /home/someone/eli/artifacts/db/agent.sqlite"
    fixed, fixes = repair_self_description(bad)
    assert fixes, "an invented path should be repaired"
    assert "/home/someone/" not in fixed
    assert "agent.sqlite3" in fixed


def test_repair_removes_an_invented_upgrade_script():
    bad = "I upgrade myself by running ./upgrade.sh"
    fixed, fixes = repair_self_description(bad)
    assert fixes
    assert "upgrade.sh" not in fixed


def test_repair_is_a_noop_on_ordinary_text():
    text = "Sure — I can help with that. What would you like to look at first?"
    fixed, fixes = repair_self_description(text)
    assert fixed == text
    assert not fixes
