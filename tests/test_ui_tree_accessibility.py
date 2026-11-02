"""Locks on the accessibility-tree UI targeting backend (Phase 1, Linux/AT-SPI).

Before this, `locate_on_screen` was OCR-only: screenshot → pytesseract → fuzzy text
match → click a coordinate. That is blind to what makes a UI a UI. Verified live on a
GNOME desktop, searching "Files" returns BOTH:

    push button 'Files'          at (2837,2195)
    label 'Files' [disabled]     at (2837,2072)

OCR sees identical pixels for those two and would click either with equal confidence.
The accessibility tree ranks the button first and reports the label as disabled.

The backend is a STRATEGY, not a replacement — coverage in the wild is uneven (GTK
good, Electron often needs --force-renderer-accessibility, some Qt apps expose almost
nothing), so OCR remains the fallback and every entry point degrades to an empty
result rather than raising. These tests must therefore pass on a headless CI runner
with no AT-SPI at all, which is why the live-desktop ones skip rather than fail.
"""
import pytest

from eli.perception import ui_tree


def _live() -> bool:
    return ui_tree.available()


# ── degradation: the whole point is that this is safe to call anywhere ──────


def test_reason_is_populated_exactly_when_unavailable():
    if ui_tree.available():
        assert ui_tree.unavailable_reason() == ""
    else:
        assert ui_tree.unavailable_reason().strip(), "unavailable with no explanation"


@pytest.mark.parametrize("q", ["", "   ", None])
def test_empty_queries_return_nothing_rather_than_raising(q):
    assert ui_tree.find(q) == []


def test_invoke_rejects_a_match_with_no_node():
    r = ui_tree.invoke({"text": "x"})
    assert r["ok"] is False and r["error"]


def test_invoke_never_raises_on_junk():
    for junk in (None, {}, {"_node": None}, "not a dict"):
        r = ui_tree.invoke(junk)
        assert r["ok"] is False


# ── scoring / ranking contract ──────────────────────────────────────────────
def test_exact_name_outranks_partial():
    assert ui_tree._score("Save", "Save") > ui_tree._score("Save", "Save As…")


def test_scoring_is_case_and_space_insensitive():
    assert ui_tree._score("save as", "  Save   As  ") == 1.0


def test_unrelated_names_score_low():
    assert ui_tree._score("Save", "Zoom Out") < 0.5


def test_actionable_roles_exclude_static_text():
    """The live case: a `label` reading "Files" is not the Files button."""
    assert "label" not in ui_tree.ACTIONABLE_ROLES
    assert "push button" in ui_tree.ACTIONABLE_ROLES


def test_walk_is_bounded():
    """An accessibility tree can expose every DOM node in a browser, and this runs on
    a user-facing 'click X' path."""
    assert 0 < ui_tree._MAX_NODES <= 20000
    assert 0 < ui_tree._MAX_DEPTH <= 64


# ── serialisation: live handles must never leak into a result ───────────────
def test_public_matches_strips_the_live_node_handle():
    boxes = [{"text": "a", "cx": 1, "cy": 2, "_node": object()}]
    out = ui_tree.public_matches(boxes)
    assert "_node" not in out[0]
    assert out[0]["text"] == "a"


def test_public_matches_handles_empty():
    assert ui_tree.public_matches([]) == []
    assert ui_tree.public_matches(None) == []


def test_describe_is_safe_on_junk():
    assert ui_tree.describe(None) == ""
    assert isinstance(ui_tree.describe({"role": "button", "text": "Go"}), str)


def test_describe_flags_disabled():
    assert "disabled" in ui_tree.describe(
        {"role": "push button", "text": "Save", "enabled": False, "cx": 1, "cy": 2})


# ── live desktop (skipped where AT-SPI is absent, e.g. CI) ──────────────────


@pytest.mark.skipif(not _live(), reason="no AT-SPI on this machine")
def test_live_matches_carry_geometry_and_state():
    hits = ui_tree.find("", actionable_only=False) or ui_tree.find("e", actionable_only=False,
                                                                   min_score=0.0, max_matches=3)
    for b in hits[:3]:
        for key in ("text", "x", "y", "w", "h", "cx", "cy", "role", "enabled"):
            assert key in b, f"missing {key}"
        assert b["w"] > 0 and b["h"] > 0
