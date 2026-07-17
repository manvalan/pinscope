"""Verify the cost estimator + credit gate auto-update when model
routing env vars change.

Before this refactor, ``cost_estimator`` exposed flat ``DEFAULT_*_USD``
constants that had to be hand-bumped every time someone changed a
``MODEL_*`` / ``PROVIDER_*`` env var. Now ``estimate_stage_cost_usd``
resolves the runtime provider+model from settings and prices against
``services.llm.pricing.PRICING`` — the same table that real billing
reads.

These tests pin that contract: same stage, two different models, two
different costs (in the direction the rate table predicts).
"""

from __future__ import annotations

import importlib

import pytest

from backend.config import settings
from backend.services.cost_estimator import (
    STAGE_TOKEN_BASELINES,
    estimate_stage_cost_usd,
)
from backend.services.llm.pricing import PRICING


@pytest.fixture
def restore_settings():
    """Snapshot every per-stage routing field; restore after the test."""
    fields = [
        "anthropic_model", "gemini_model",
        "provider_default", "provider_validation",
        "provider_pintable", "provider_pattern", "provider_specs",
        "provider_auto_resolve",
        "model_validation", "model_validation_gemini",
        "model_pintable", "model_pintable_gemini",
        "model_pattern", "model_pattern_gemini",
        "model_specs", "model_specs_gemini",
        "model_auto_resolve", "model_auto_resolve_gemini",
    ]
    snapshot = {f: getattr(settings, f) for f in fields if hasattr(settings, f)}
    yield
    for f, v in snapshot.items():
        setattr(settings, f, v)


def test_review_cost_changes_with_validation_model(restore_settings):
    """Routing validation to Sonnet vs Haiku should produce different
    per-IC review costs — and Haiku should be cheaper than Sonnet."""
    settings.provider_validation = "anthropic"

    settings.model_validation = "claude-sonnet-4-6"
    sonnet_cost = estimate_stage_cost_usd("review")

    settings.model_validation = "claude-haiku-4-5"
    haiku_cost = estimate_stage_cost_usd("review")

    assert sonnet_cost > 0
    assert haiku_cost > 0
    # Haiku is ~3× cheaper than Sonnet on input ($1 vs $3) and 3× on
    # output ($5 vs $15). The blended ratio with cache_read should
    # land Haiku at <50% of Sonnet's cost — wide enough margin to be
    # robust to baseline tweaks.
    assert haiku_cost < sonnet_cost * 0.6


def test_review_cost_changes_with_validation_provider(restore_settings):
    """Flipping PROVIDER_VALIDATION between anthropic and gemini must
    swap the rate table the estimator pulls from."""
    settings.provider_validation = "anthropic"
    settings.model_validation = "claude-sonnet-4-6"
    anthropic_cost = estimate_stage_cost_usd("review")

    settings.provider_validation = "gemini"
    settings.gemini_model = "gemini-3.1-pro-preview"
    settings.model_validation_gemini = ""  # fall back to gemini_model
    gemini_cost = estimate_stage_cost_usd("review")

    # Both > 0 and they're different — the test doesn't lock direction
    # because the cache-read multiplier asymmetry between providers
    # could legitimately swing it either way as the rate tables evolve.
    assert anthropic_cost > 0
    assert gemini_cost > 0
    assert abs(anthropic_cost - gemini_cost) > 0.01, (
        f"expected materially different costs, got "
        f"anthropic={anthropic_cost!r} gemini={gemini_cost!r}"
    )


def test_unknown_model_falls_back_to_default_rate(restore_settings):
    """A model not in PRICING[provider] should price against
    PRICING[provider]['default'], not crash."""
    settings.provider_validation = "anthropic"
    settings.model_validation = "claude-totally-made-up-2099"
    cost = estimate_stage_cost_usd("review")

    # Same baseline against PRICING['anthropic']['default']
    settings.model_validation = ""  # forces anthropic_model fallback
    settings.anthropic_model = "claude-totally-made-up-2099"
    cost_via_global_default = estimate_stage_cost_usd("review")

    assert cost > 0
    assert cost == pytest.approx(cost_via_global_default, rel=1e-9)


def test_baselines_cover_every_estimator_stage_kind():
    """STAGE_TOKEN_BASELINES must have an entry for every CostItem.kind
    the estimator emits — otherwise estimate_stage_cost_usd crashes
    with a KeyError mid-estimate."""
    expected = {
        "ic_extraction", "simple_extraction", "passive_pattern",
        "digikey_resolve", "review",
    }
    assert expected.issubset(STAGE_TOKEN_BASELINES.keys()), (
        f"missing baselines: {expected - set(STAGE_TOKEN_BASELINES.keys())}"
    )


def test_settings_stages_are_known_to_config(restore_settings):
    """The 'settings_stage' field of every baseline must be a key
    accepted by Settings.model_for_stage / provider_for_stage."""
    for stage, base in STAGE_TOKEN_BASELINES.items():
        s = str(base["settings_stage"])
        # Should not raise; should return non-empty strings for
        # provider+model.
        provider = settings.provider_for_stage(s)
        model = settings.model_for_stage(s)
        assert provider, f"empty provider for stage {stage!r} -> {s!r}"
        assert model, f"empty model for stage {stage!r} -> {s!r}"


def test_pricing_table_has_all_default_entries():
    """estimate_stage_cost_usd's safety-net fall-through assumes every
    provider has a 'default' row. Pin that contract."""
    for provider, table in PRICING.items():
        assert "default" in table, f"PRICING[{provider!r}] missing 'default'"
