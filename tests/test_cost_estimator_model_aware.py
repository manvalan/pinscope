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
        "anthropic_model", "gemini_model", "deepseek_model",
        "provider_default", "provider_validation",
        "provider_pintable", "provider_pattern", "provider_specs",
        "provider_auto_resolve",
        "model_validation", "model_validation_gemini", "model_validation_deepseek",
        "model_pintable", "model_pintable_gemini", "model_pintable_deepseek",
        "model_pattern", "model_pattern_gemini", "model_pattern_deepseek",
        "model_specs", "model_specs_gemini", "model_specs_deepseek",
        "model_auto_resolve", "model_auto_resolve_gemini", "model_auto_resolve_deepseek",
        "fallback_provider_validation", "fallback_model_validation",
    ]
    snapshot = {f: getattr(settings, f) for f in fields if hasattr(settings, f)}
    yield
    for f, v in snapshot.items():
        setattr(settings, f, v)


def test_review_cost_changes_with_validation_model(restore_settings):
    """Routing validation to two DeepSeek models should produce different
    per-IC review costs."""
    settings.provider_validation = "deepseek"

    settings.model_validation_deepseek = "deepseek-v4-pro"
    pro_cost = estimate_stage_cost_usd("review")

    settings.model_validation_deepseek = "deepseek-flash"
    flash_cost = estimate_stage_cost_usd("review")

    assert pro_cost > 0
    assert flash_cost > 0
    assert pro_cost != flash_cost


def test_review_cost_stays_on_deepseek_table(restore_settings):
    """PROVIDER_VALIDATION=anthropic must still price DeepSeek, not Claude."""
    settings.provider_validation = "anthropic"
    settings.model_validation_deepseek = "deepseek-flash"
    settings.model_validation = "claude-sonnet-4-6"
    cost = estimate_stage_cost_usd("review")
    assert cost > 0
    settings.provider_validation = "deepseek"
    ds_cost = estimate_stage_cost_usd("review")
    assert cost == pytest.approx(ds_cost, rel=1e-9)


def test_unknown_model_falls_back_to_default_rate(restore_settings):
    """A model not in PRICING[provider] should price against
    PRICING[provider]['default'], not crash."""
    settings.provider_validation = "deepseek"
    settings.model_validation_deepseek = "deepseek-totally-made-up-2099"
    cost = estimate_stage_cost_usd("review")

    settings.model_validation_deepseek = ""
    settings.deepseek_model = "deepseek-totally-made-up-2099"
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
