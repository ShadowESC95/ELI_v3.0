"""Tier policy defaults."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eli.licensing.tiers import default_seats, tier_config


def test_personal_default_seats():
    assert default_seats("personal") == 1


def test_pro_default_seats():
    assert default_seats("pro") == 3


def test_tier_policy_file_loads():
    cfg = tier_config("pro")
    assert cfg.get("commercial_use") is True
    assert "eula" in cfg


def test_unknown_tier_raises():
    with pytest.raises(ValueError, match="unknown tier"):
        tier_config("bogus")
