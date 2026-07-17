"""Provider factory + per-stage routing."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Awaitable, Callable, TypeVar

from backend.config import settings
from backend.services.llm.base import LLMProvider

log = logging.getLogger(__name__)

T = TypeVar("T")


@lru_cache(maxsize=4)
def get_provider_by_name(name: str) -> LLMProvider:
    """Return a singleton provider instance for ``name`` ("anthropic" |
    "gemini"). Used by :func:`get_provider` and :func:`call_with_fallback`."""
    if name == "anthropic":
        from backend.services.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    if name == "gemini":
        from backend.services.llm.gemini_provider import GeminiProvider
        return GeminiProvider()
    raise ValueError(f"Unknown LLM provider: {name!r}")


# Backwards-compatible alias
_get_provider_by_name = get_provider_by_name


def get_provider(stage: str) -> LLMProvider:
    """Return the provider configured for ``stage``.

    Falls back to ``settings.provider_default`` if no per-stage override.
    Providers are cached per-name, so repeated calls return the same
    instance (and share the underlying SDK client)."""
    name = settings.provider_for_stage(stage)
    return get_provider_by_name(name)


async def call_with_fallback(
    stage: str,
    body: Callable[[LLMProvider, str], Awaitable[T]],
) -> T:
    """Run ``body(provider, model)`` for ``stage``; on any exception,
    retry once with the fallback provider/model if one is configured via
    ``FALLBACK_PROVIDER_<STAGE>`` / ``FALLBACK_MODEL_<STAGE>``.

    The fallback runs ``body`` from scratch — any tokens spent in the
    primary attempt are lost (and not logged). ``asyncio.CancelledError``
    is always re-raised so cancellation still works.
    """
    primary_provider = get_provider(stage)
    primary_model = settings.model_for_stage(stage)
    try:
        return await body(primary_provider, primary_model)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        fb = settings.fallback_for_stage(stage)
        if fb is None:
            raise
        log.warning(
            "[%s] primary %s/%s failed (%s) — falling back to %s/%s",
            stage, primary_provider.name, primary_model,
            exc, fb[0], fb[1],
        )
        fallback_provider = get_provider_by_name(fb[0])
        return await body(fallback_provider, fb[1])
