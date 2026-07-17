"""Abstract LLMProvider + LLMSession interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from backend.services.llm.types import (
    Completion,
    Message,
    ToolChoice,
    ToolSchema,
)


class LLMSession(ABC):
    """A multi-turn conversation with provider-specific cache lifecycle.

    Lifecycle::

        session = await provider.create_session(model=..., system=...)
        try:
            messages = [Message("user", [
                PdfBlock(path, cacheable=True),
                TextBlock(context, cacheable=True),
            ])]
            for turn in range(N):
                completion = await session.complete(
                    messages=messages, tools=..., tool_choice=...,
                )
                # process tool_calls, append to messages, repeat
        finally:
            await session.close()

    Caching: blocks with ``cacheable=True`` participate in provider caching.
    Anthropic stamps ``cache_control: ephemeral`` on each cacheable block on
    every call. Gemini collects all cacheable blocks (plus the system prompt)
    on the first ``complete()`` call into a ``CachedContent`` object and
    references it on subsequent calls. The system prompt is always cached.
    """

    provider_name: str
    """Provider identifier ("anthropic", "gemini") — used for api_logs."""
    model: str

    @abstractmethod
    async def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        tool_choice: ToolChoice = "auto",
    ) -> Completion:
        """Run one inference turn."""

    @abstractmethod
    async def close(self) -> None:
        """Release any provider-side resources (e.g. delete a cache object).
        Safe to call multiple times."""


class LLMProvider(Protocol):
    """Top-level provider interface."""

    name: str

    async def create_session(
        self,
        *,
        model: str,
        system: str,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> LLMSession:
        """Construct a session. ``system`` is always cached by the session.

        ``temperature`` — if not None, applied to every ``complete()`` call on
        this session. ``None`` means use the provider's default. Set to 0.0
        for deterministic-as-possible behavior in agentic loops where the same
        inputs should produce the same outputs."""
        ...

    async def run_skill(
        self,
        *,
        skill_name: str,
        model: str,
        system: str,
        user_text: str,
        pdf_path: str | None,
        output_tool: ToolSchema,
    ) -> tuple[dict, "Completion"]:
        """Execute a managed Skill and return (forced-tool input, Completion).

        Anthropic uses Console Skills (skill_id + container + code_execution
        beta). Gemini raises ``NotImplementedError`` — there is no
        Gemini-managed-Skill equivalent today; if you want a Gemini path for
        skill-style extraction, inline the SKILL.md content as ``system`` and
        run validation locally."""
        ...
