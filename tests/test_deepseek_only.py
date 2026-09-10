"""Pinscope LLM routing is DeepSeek only.

Favor: every pipeline stage uses DeepSeek even if PROVIDER_* is set to
anthropic; model_for_stage stays on deepseek-flash.
Against: anthropic fallback is ignored; get_provider_by_name('anthropic')
does not construct the Anthropic SDK client.
"""

from __future__ import annotations

import pytest

from backend.config import settings
from backend.services.llm.factory import get_provider, get_provider_by_name


@pytest.fixture
def restore_routing():
    snap = {
        "provider_default": settings.provider_default,
        "provider_validation": settings.provider_validation,
        "fallback_provider_validation": settings.fallback_provider_validation,
        "fallback_model_validation": settings.fallback_model_validation,
    }
    yield
    for k, v in snap.items():
        setattr(settings, k, v)
    get_provider_by_name.cache_clear()


def test_stage_stays_deepseek_when_env_says_anthropic(restore_routing):
    settings.provider_validation = "anthropic"
    assert settings.provider_for_stage("validation") == "deepseek"
    get_provider_by_name.cache_clear()
    assert get_provider("validation").name == "deepseek"
    assert "deepseek" in settings.model_for_stage("validation")


def test_anthropic_fallback_is_not_used(restore_routing):
    settings.fallback_provider_validation = "anthropic"
    settings.fallback_model_validation = "claude-sonnet-4-6"
    assert settings.fallback_for_stage("validation") is None


def test_get_provider_by_name_does_not_load_anthropic(restore_routing):
    get_provider_by_name.cache_clear()
    try:
        p = get_provider_by_name("anthropic")
        assert p.name == "deepseek"
    finally:
        get_provider_by_name.cache_clear()
