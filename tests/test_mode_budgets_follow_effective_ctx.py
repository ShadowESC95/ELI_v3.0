"""The reasoning-mode budgets must be sized for the context that actually loaded.

Two sizing paths disagree by design. The startup tuner (`hardware_profile.recommend`)
holds GPU layers and cuts n_ctx to fit the KV budget; the loader (`smart_fit_config`)
sheds layers and batch first and cuts ctx LAST. On a 2060 SUPER at 2.1.82 the tuner
recommended ctx=4096 while the loader ran the pinned ctx=10384.

n_ctx itself is protected from that mismatch — it stays the user's, with the tuner's
value filed under hw_profile_n_ctx as a load fallback. But `max_tokens` and
`mode_presets`, both DERIVED from the tuner's n_ctx, were written to the canonical
keys and never revisited. The panel said so out loud:

    mode_presets: 5 reasoning modes derived from base (ctx=4096, max_tokens_ref=1024)

So every reasoning mode planned against less than half the context that was pinned.
"""
import pytest

from eli.core.startup_hardware_optimizer import (
    max_tokens_from_ctx,
    mode_presets,
    resize_budgets_to_effective_ctx,
)


TUNER_CTX = 4096      # what the VRAM budget estimated
EFFECTIVE_CTX = 10384  # what the loader actually ran


def test_the_two_contexts_really_do_produce_different_budgets():
    """If these ever coincide the rest of this file proves nothing."""
    small = mode_presets(TUNER_CTX, max_tokens_from_ctx(TUNER_CTX))
    large = mode_presets(EFFECTIVE_CTX, max_tokens_from_ctx(EFFECTIVE_CTX))

    assert max_tokens_from_ctx(TUNER_CTX) == 2048
    assert max_tokens_from_ctx(EFFECTIVE_CTX) == 5192
    assert small["self_consistency"]["samples"] < large["self_consistency"]["samples"]
    assert small["tree_of_thoughts"]["branches"] < large["tree_of_thoughts"]["branches"]
    assert small["cot"]["max_tokens"] < large["cot"]["max_tokens"]


@pytest.fixture
def settings_sized_for_the_tuner(monkeypatch):
    """Settings as the startup tuner leaves them: budgets built for 4096."""
    store = {
        "n_ctx": EFFECTIVE_CTX,           # the user's pinned value, preserved
        "hw_profile_n_ctx": TUNER_CTX,    # the tuner's fallback
        "max_tokens": max_tokens_from_ctx(TUNER_CTX),
        "mode_presets": mode_presets(TUNER_CTX, max_tokens_from_ctx(TUNER_CTX)),
    }
    import eli.core.runtime_settings as rs

    monkeypatch.setattr(rs, "load_settings", lambda *a, **k: dict(store))
    monkeypatch.setattr(rs, "save_settings", lambda s: store.update(s))
    return store


def test_budgets_are_rebuilt_for_the_context_that_loaded(settings_sized_for_the_tuner):
    store = settings_sized_for_the_tuner
    assert store["max_tokens"] == 2048          # before: sized for 4096

    delta = resize_budgets_to_effective_ctx(EFFECTIVE_CTX)

    assert set(delta) == {"max_tokens", "mode_presets"}
    assert store["max_tokens"] == 5192
    assert store["mode_presets"]["self_consistency"]["samples"] == 3
    assert store["mode_presets"]["tree_of_thoughts"]["branches"] == 3
    assert store["mode_presets_ctx"] == EFFECTIVE_CTX


def test_no_write_when_the_budgets_already_match(settings_sized_for_the_tuner):
    """A load that agrees with the tuner must not churn settings.json."""
    resize_budgets_to_effective_ctx(EFFECTIVE_CTX)
    assert resize_budgets_to_effective_ctx(EFFECTIVE_CTX) == {}


def test_a_smaller_effective_ctx_shrinks_the_budgets_too(settings_sized_for_the_tuner):
    """The correction runs in both directions — a load that had to reduce ctx must
    not leave budgets sized for a context the model no longer has."""
    store = settings_sized_for_the_tuner
    resize_budgets_to_effective_ctx(EFFECTIVE_CTX)
    assert store["max_tokens"] == 5192

    resize_budgets_to_effective_ctx(2048)
    assert store["max_tokens"] == max_tokens_from_ctx(2048)
    assert store["mode_presets_ctx"] == 2048


@pytest.mark.parametrize("bad", [0, -1, None])
def test_an_unknown_context_changes_nothing(settings_sized_for_the_tuner, bad):
    """Called before a successful load, this must be a no-op, not a reset."""
    assert resize_budgets_to_effective_ctx(bad) == {}
    assert settings_sized_for_the_tuner["max_tokens"] == 2048


def test_only_the_budgets_are_saved_not_the_whole_settings_view(monkeypatch):
    """load_settings() is a view: defaults, one-session environment overrides and derived values.
    Handed back whole, every one of them was written into the file after a model load."""
    view = {"n_ctx": 2048, "temperature": 0.7, "model_dirs": "", "max_tokens": 1,
            "mode_presets": {}}
    saved = []
    import eli.core.runtime_settings as rs
    monkeypatch.setattr(rs, "load_settings", lambda *a, **k: dict(view))
    monkeypatch.setattr(rs, "save_settings", lambda s: saved.append(dict(s)))
    resize_budgets_to_effective_ctx(EFFECTIVE_CTX)
    assert len(saved) == 1
    assert set(saved[0]) == {"max_tokens", "mode_presets", "mode_presets_ctx"}


def test_budgets_follow_the_context_the_model_can_use_not_one_built_past_its_training(
        settings_sized_for_the_tuner, monkeypatch):
    """A model trained on 32768 loaded at 39168 is given 32768: budgets for the rest are budgets
    for tokens it never sees."""
    import eli.cognition.gguf_inference as gi
    store = settings_sized_for_the_tuner
    trained = 8192
    assert TUNER_CTX < trained < EFFECTIVE_CTX
    monkeypatch.setattr(gi, "current_context_limit", lambda: trained)
    resize_budgets_to_effective_ctx(EFFECTIVE_CTX)
    assert store["max_tokens"] == max_tokens_from_ctx(trained)
    assert store["mode_presets_ctx"] == trained
