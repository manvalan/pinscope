"""Gemini provider — wraps google-genai async client.

Translates the unified ``Message`` / ``Completion`` shapes into Gemini's
native ``Content`` / ``Part`` format. Caching uses ``CachedContent``: on the
first ``complete()`` call, cacheable blocks (system + any block flagged
``cacheable=True`` in the first user message) are uploaded as a
``CachedContent`` with TTL=30min; subsequent calls reference the cache by
name. On ``close()`` the cache is deleted. If creation fails (e.g.
sub-threshold token count), the session falls back to inline content with no
caching for the remainder of the conversation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types as gtypes

from backend.config import settings
from backend.services.llm.base import LLMProvider, LLMSession
from backend.services.llm.types import (
    Completion,
    ContentBlock,
    Message,
    PdfBlock,
    TextBlock,
    ToolCall,
    ToolChoice,
    ToolResultBlock,
    ToolSchema,
    Usage,
)

log = logging.getLogger(__name__)

_CACHE_TTL = "1800s"  # 30 min — covers our longest agent loop with margin


# ---------------------------------------------------------------------------
# Translation helpers — unified types ↔ Gemini Parts/Contents
# ---------------------------------------------------------------------------


def _block_to_part(b: ContentBlock) -> gtypes.Part:
    if isinstance(b, TextBlock):
        return gtypes.Part(
            text=b.text,
            thought_signature=b.thought_signature,
        )
    if isinstance(b, PdfBlock):
        return gtypes.Part(
            inline_data=gtypes.Blob(
                mime_type="application/pdf",
                data=Path(b.path).read_bytes(),
            ),
        )
    if isinstance(b, ToolCall):
        return gtypes.Part(
            function_call=gtypes.FunctionCall(
                id=b.id or None,
                name=b.name,
                args=b.input,
            ),
            thought_signature=b.thought_signature,
        )
    if isinstance(b, ToolResultBlock):
        return gtypes.Part(
            function_response=gtypes.FunctionResponse(
                id=b.tool_use_id or None,
                name=b.name,
                # FunctionResponse.response is a dict — wrap string content
                response={"result": b.content},
            ),
        )
    raise TypeError(f"Unknown ContentBlock: {type(b).__name__}")


def _message_to_content(m: Message) -> gtypes.Content:
    # Gemini uses "user" and "model" (not "assistant")
    role = "model" if m.role == "assistant" else "user"
    return gtypes.Content(
        role=role,
        parts=[_block_to_part(b) for b in m.content],
    )


def _tool_to_function_declaration(t: ToolSchema) -> gtypes.FunctionDeclaration:
    return gtypes.FunctionDeclaration(
        name=t.name,
        description=t.description,
        parameters_json_schema=t.input_schema,
    )


def _tools_to_gemini(tools: list[ToolSchema]) -> list[gtypes.Tool]:
    return [
        gtypes.Tool(
            function_declarations=[_tool_to_function_declaration(t) for t in tools],
        ),
    ]


def _tool_choice_to_config(c: ToolChoice) -> gtypes.ToolConfig:
    if c == "auto":
        return gtypes.ToolConfig(
            function_calling_config=gtypes.FunctionCallingConfig(mode="AUTO"),
        )
    if c == "none":
        return gtypes.ToolConfig(
            function_calling_config=gtypes.FunctionCallingConfig(mode="NONE"),
        )
    if isinstance(c, dict) and "name" in c:
        return gtypes.ToolConfig(
            function_calling_config=gtypes.FunctionCallingConfig(
                mode="ANY",
                allowed_function_names=[c["name"]],
            ),
        )
    raise ValueError(f"Invalid tool_choice: {c!r}")


def _from_gemini_response(resp: Any) -> Completion:
    """Parse a Gemini GenerateContentResponse into a unified Completion."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    raw_blocks: list[ContentBlock] = []
    stop_reason = "unknown"

    candidates = getattr(resp, "candidates", None) or []
    if candidates:
        cand = candidates[0]
        finish = getattr(cand, "finish_reason", None)
        if finish:
            stop_reason = str(finish).lower().split(".")[-1]
        content = getattr(cand, "content", None)
        if content and content.parts:
            for part in content.parts:
                # Preserve thought_signature (Gemini 3 thinking-mode) for
                # exact replay on subsequent turns; missing signatures cause
                # 400 INVALID_ARGUMENT on the next call.
                sig = getattr(part, "thought_signature", None)
                if getattr(part, "text", None):
                    text_parts.append(part.text)
                    raw_blocks.append(TextBlock(
                        text=part.text, thought_signature=sig,
                    ))
                elif getattr(part, "function_call", None):
                    fc = part.function_call
                    tc = ToolCall(
                        id=fc.id or f"{fc.name}_{len(tool_calls)}",
                        name=fc.name,
                        input=dict(fc.args or {}),
                        thought_signature=sig,
                    )
                    tool_calls.append(tc)
                    raw_blocks.append(tc)

    usage_md = getattr(resp, "usage_metadata", None)
    if usage_md is not None:
        prompt_tokens = usage_md.prompt_token_count or 0
        cached_tokens = usage_md.cached_content_token_count or 0
        # Gemini reports prompt_token_count as the TOTAL prompt tokens —
        # cached tokens are billed at the cache-read rate, the rest at the
        # input rate. Subtract so they don't double-count.
        non_cached = max(0, prompt_tokens - cached_tokens)
        # Thinking-mode models (2.5 Pro, 3 series) report reasoning tokens
        # in thoughts_token_count, billed at the output rate. Fold into
        # output_tokens so cost accounting matches Gemini's actual bill.
        thoughts_tokens = getattr(usage_md, "thoughts_token_count", 0) or 0
        usage = Usage(
            input_tokens=non_cached,
            output_tokens=(usage_md.candidates_token_count or 0) + thoughts_tokens,
            cache_creation_tokens=0,  # Gemini doesn't expose this separately
            cache_read_tokens=cached_tokens,
        )
    else:
        usage = Usage()

    return Completion(
        text="".join(text_parts),
        tool_calls=tool_calls,
        usage=usage,
        stop_reason=stop_reason,
        raw_assistant_blocks=raw_blocks,
    )


def _is_first_user_message_fully_cacheable(messages: list[Message]) -> bool:
    """We cache only when EVERY block in the very first user message is
    flagged cacheable. This matches our actual usage (validation + power
    tree both pass entirely cacheable initial messages) and avoids brittle
    partial-cache scenarios."""
    if not messages:
        return False
    first = messages[0]
    if first.role != "user" or not first.content:
        return False
    return all(
        isinstance(b, (TextBlock, PdfBlock)) and b.cacheable
        for b in first.content
    )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class GeminiSession(LLMSession):
    provider_name = "gemini"

    def __init__(
        self,
        *,
        client: genai.Client,
        model: str,
        system: str,
        max_tokens: int,
        temperature: float | None = None,
    ) -> None:
        self._client = client
        self.model = model
        self._system = system
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._cache_name: str | None = None
        self._cache_attempted = False

    async def _try_create_cache(self, first_msg: Message) -> str | None:
        """Attempt to create a CachedContent from system + first user message.
        Returns the cache name on success, None on failure."""
        try:
            parts = [_block_to_part(b) for b in first_msg.content]
            cache = await self._client.aio.caches.create(
                model=self.model,
                config=gtypes.CreateCachedContentConfig(
                    system_instruction=self._system,
                    contents=[gtypes.Content(role="user", parts=parts)],
                    ttl=_CACHE_TTL,
                ),
            )
            log.info(
                "Gemini cache created (%s, model=%s, ttl=%s)",
                cache.name, self.model, _CACHE_TTL,
            )
            return cache.name
        except Exception as exc:
            log.info(
                "Gemini cache creation skipped (%s) — falling back to inline",
                exc,
            )
            return None

    async def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        tool_choice: ToolChoice = "auto",
    ) -> Completion:
        if not messages:
            raise ValueError("Gemini complete() requires at least one message")

        # First call: decide whether to cache
        if not self._cache_attempted:
            self._cache_attempted = True
            if _is_first_user_message_fully_cacheable(messages):
                self._cache_name = await self._try_create_cache(messages[0])

        # Build per-call contents
        if self._cache_name:
            # Skip the cached first message — its contents are in the cache
            contents = [_message_to_content(m) for m in messages[1:]]
        else:
            contents = [_message_to_content(m) for m in messages]

        # Build config
        config_kwargs: dict[str, Any] = {
            "max_output_tokens": self._max_tokens,
        }
        if self._temperature is not None:
            config_kwargs["temperature"] = self._temperature
        if self._cache_name:
            config_kwargs["cached_content"] = self._cache_name
        else:
            config_kwargs["system_instruction"] = self._system
        if tools:
            config_kwargs["tools"] = _tools_to_gemini(tools)
            config_kwargs["tool_config"] = _tool_choice_to_config(tool_choice)

        config = gtypes.GenerateContentConfig(**config_kwargs)

        # When using cached_content, Gemini still requires non-empty contents.
        # If the cached path leaves us with no per-call contents (only happens
        # on the very first turn with a cached initial message), seed with a
        # minimal continuation prompt.
        if self._cache_name and not contents:
            contents = [gtypes.Content(role="user", parts=[gtypes.Part(text="Continue.")])]

        try:
            resp = await self._client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            # Cache may have expired mid-loop — drop it and retry inline once
            if self._cache_name and "cache" in str(exc).lower():
                log.warning("Gemini cache failed (%s) — retrying inline", exc)
                self._cache_name = None
                return await self.complete(
                    messages=messages, tools=tools, tool_choice=tool_choice,
                )
            raise

        return _from_gemini_response(resp)

    async def close(self) -> None:
        if self._cache_name:
            try:
                await self._client.aio.caches.delete(name=self._cache_name)
            except Exception as exc:
                log.warning("Gemini cache delete failed (%s): %s", self._cache_name, exc)
            finally:
                self._cache_name = None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        api_key = settings.gemini_api_key
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Either set it in .env or route "
                "this stage to Anthropic via PROVIDER_<STAGE>=anthropic."
            )
        self._client = genai.Client(api_key=api_key)

    async def create_session(
        self,
        *,
        model: str,
        system: str,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> LLMSession:
        return GeminiSession(
            client=self._client,
            model=model,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def run_skill(
        self,
        *,
        skill_name: str,
        model: str,
        system: str,
        user_text: str,
        pdf_path: str | None,
        output_tool: ToolSchema,
    ) -> tuple[dict, Completion]:
        raise NotImplementedError(
            f"GeminiProvider.run_skill() not implemented (skill={skill_name!r}). "
            f"Anthropic Console Skills have no Gemini equivalent. To migrate "
            f"this skill to Gemini, inline its SKILL.md as the system prompt "
            f"and run validate.py locally."
        )
