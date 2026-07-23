"""Anthropic provider — wraps AsyncAnthropic + Console Skills.

Translates the unified ``Message`` / ``Completion`` shapes into Anthropic's
native message-block format and back. Caching is per-block via
``cache_control: ephemeral``."""

from __future__ import annotations

import base64
import re
import time
from pathlib import Path

import anthropic

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


_SKILL_MAX_TURNS = 10

# Sampling params were removed on newer Claude models (Sonnet 5, Opus 4.7+,
# Fable/Mythos 5) — sending `temperature` returns 400 "`temperature` is
# deprecated for this model". Allowlist the families that still accept it so
# unknown/future models fail safe (omit → default sampling) instead of
# 400-ing every call in the session.
_TEMPERATURE_OK = re.compile(r"^claude-(3-|opus-4-[0-6]|sonnet-4-|haiku-)")


def _model_accepts_temperature(model: str) -> bool:
    return bool(_TEMPERATURE_OK.match(model))


# ---------------------------------------------------------------------------
# Translation helpers — unified types ↔ Anthropic dicts
# ---------------------------------------------------------------------------


def _encode_pdf_block(path: Path | str, *, cache: bool) -> dict:
    data = base64.standard_b64encode(Path(path).read_bytes()).decode()
    block: dict = {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": data},
    }
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def _to_anthropic_block(b: ContentBlock) -> dict:
    if isinstance(b, TextBlock):
        d: dict = {"type": "text", "text": b.text}
        if b.cacheable:
            d["cache_control"] = {"type": "ephemeral"}
        return d
    if isinstance(b, PdfBlock):
        return _encode_pdf_block(b.path, cache=b.cacheable)
    if isinstance(b, ToolCall):
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    if isinstance(b, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": b.tool_use_id,
            "content": b.content,
        }
    raise TypeError(f"Unknown ContentBlock: {type(b).__name__}")


def _to_anthropic_message(m: Message) -> dict:
    return {"role": m.role, "content": [_to_anthropic_block(b) for b in m.content]}


# Anthropic allows at most 4 cache_control breakpoints per request. The system
# prompt always consumes one (see AnthropicSession.complete), leaving 3 for
# message content. A multi-turn review attaches a cacheable PDF for each
# get_datasheet_excerpt fetch (validation_tools.py), so a hub IC that verifies
# two interface excerpts produced 5 breakpoints — system + initial PDF + initial
# context + 2 excerpts — and the API rejected the request with
# "A maximum of 4 blocks with cache_control may be provided. Found 5."
#
# Cap the message-block breakpoints in the translated request, keeping the most
# valuable ones: the first cacheable block (the full-datasheet anchor — a stable,
# guaranteed cache hit every turn) plus the two most recent (incremental caching
# of the growing tail). Any caller-set cache_control beyond that is dropped.
_MAX_MESSAGE_CACHE_BREAKPOINTS = 3


def _enforce_cache_breakpoint_limit(messages: list[dict]) -> None:
    """Strip excess cache_control markers from message blocks in place so that
    system(1) + message breakpoints never exceed Anthropic's per-request limit."""
    marked: list[dict] = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and "cache_control" in block:
                marked.append(block)
    if len(marked) <= _MAX_MESSAGE_CACHE_BREAKPOINTS:
        return
    keep = {id(marked[0]), id(marked[-1]), id(marked[-2])}
    for block in marked:
        if id(block) not in keep:
            block.pop("cache_control", None)


def _to_anthropic_tool(t: ToolSchema) -> dict:
    return {"name": t.name, "description": t.description, "input_schema": t.input_schema}


def _to_anthropic_tool_choice(c: ToolChoice) -> dict:
    if c == "auto":
        return {"type": "auto"}
    if c == "none":
        return {"type": "none"}
    if isinstance(c, dict) and "name" in c:
        return {"type": "tool", "name": c["name"]}
    raise ValueError(f"Invalid tool_choice: {c!r}")


def _from_anthropic_response(resp) -> Completion:
    """Parse an Anthropic message response into a unified Completion."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    raw_blocks: list[ContentBlock] = []

    for block in resp.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
            raw_blocks.append(TextBlock(text=block.text))
        elif btype == "tool_use":
            tc = ToolCall(id=block.id, name=block.name, input=dict(block.input))
            tool_calls.append(tc)
            raw_blocks.append(tc)
        # Other block types (server tool calls etc.) are pass-through ignored

    usage = Usage(
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cache_creation_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
    )

    return Completion(
        text="".join(text_parts),
        tool_calls=tool_calls,
        usage=usage,
        stop_reason=resp.stop_reason or "unknown",
        raw_assistant_blocks=raw_blocks,
    )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class AnthropicSession(LLMSession):
    provider_name = "anthropic"

    def __init__(
        self,
        *,
        client: anthropic.AsyncAnthropic,
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

    async def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        tool_choice: ToolChoice = "auto",
    ) -> Completion:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": [{
                "type": "text",
                "text": self._system,
                "cache_control": {"type": "ephemeral"},
            }],
            "messages": [_to_anthropic_message(m) for m in messages],
        }
        _enforce_cache_breakpoint_limit(kwargs["messages"])
        if self._temperature is not None and _model_accepts_temperature(self.model):
            kwargs["temperature"] = self._temperature
        if tools:
            kwargs["tools"] = [_to_anthropic_tool(t) for t in tools]
            kwargs["tool_choice"] = _to_anthropic_tool_choice(tool_choice)

        # Streaming, not create(): SDK 0.83+ raises ValueError pre-flight on
        # `messages.create` whenever max_tokens crosses ~21k for Sonnet
        # (the "may take longer than 10 minutes" guard). Review uses 32k
        # max_tokens for Gemini thinking headroom; streaming bypasses that
        # client-side timeout cap. get_final_message() returns the same
        # shape as create(), so _from_anthropic_response is reused as-is.
        async with self._client.messages.stream(**kwargs) as stream:
            resp = await stream.get_final_message()
        return _from_anthropic_response(resp)

    async def close(self) -> None:
        # Anthropic ephemeral cache cleans up on its own (5-min TTL).
        pass


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def create_session(
        self,
        *,
        model: str,
        system: str,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> LLMSession:
        return AnthropicSession(
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
        """Anthropic Console Skills — multi-turn skill execution with the
        ``skills-2025-10-02`` + ``code-execution-2025-08-25`` betas.

        Skill mounts in a per-call container; the model reads ``SKILL.md``,
        runs ``validate.py`` server-side via code_execution, and voluntarily
        calls ``output_tool`` once it has well-formed data.
        """
        try:
            skill_id, version = settings.get_skill(skill_name)
        except Exception:
            skill_id, version = None, None

        # Build initial user content
        user_content: list[dict] = []
        if pdf_path:
            user_content.append(_encode_pdf_block(pdf_path, cache=True))
        user_content.append({"type": "text", "text": user_text})

        messages: list[dict] = [{"role": "user", "content": user_content}]
        container: dict | None = None
        if skill_id:
            container = {
                "skills": [{
                    "type": "custom",
                    "skill_id": skill_id,
                    "version": version,
                }],
            }

        total_input = 0
        total_output = 0
        total_cache_creation = 0
        total_cache_read = 0
        t0 = time.monotonic()
        last_resp = None

        for turn in range(_SKILL_MAX_TURNS):
            resp = await self._client.beta.messages.create(
                model=model,
                max_tokens=16384,
                system=[{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=[
                    {"type": "code_execution_20250825", "name": "code_execution"},
                    _to_anthropic_tool(output_tool),
                ],
                container=container,
                messages=messages,
                betas=["skills-2025-10-02", "code-execution-2025-08-25"],
            )
            last_resp = resp

            total_input += resp.usage.input_tokens
            total_output += resp.usage.output_tokens
            total_cache_creation += getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
            total_cache_read += getattr(resp.usage, "cache_read_input_tokens", 0) or 0

            # Reuse container for subsequent turns
            if hasattr(resp, "container") and resp.container:
                container = {"id": resp.container.id}

            for block in resp.content:
                if (
                    getattr(block, "type", None) == "tool_use"
                    and block.name == output_tool.name
                ):
                    completion = Completion(
                        text="",
                        tool_calls=[ToolCall(id=block.id, name=block.name, input=dict(block.input))],
                        usage=Usage(
                            input_tokens=total_input,
                            output_tokens=total_output,
                            cache_creation_tokens=total_cache_creation,
                            cache_read_tokens=total_cache_read,
                        ),
                        stop_reason=resp.stop_reason or "unknown",
                    )
                    # Stash turns count via attribute for callers that need it
                    completion.turns = turn + 1  # type: ignore[attr-defined]
                    completion.duration_ms = int((time.monotonic() - t0) * 1000)  # type: ignore[attr-defined]
                    return dict(block.input), completion

            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "pause_turn":
                continue

            if resp.stop_reason == "end_turn":
                messages.append({
                    "role": "user",
                    "content": f"Please call {output_tool.name} with the extracted data.",
                })
                continue

            # tool_use from code_execution — let the loop continue
            continue

        raise RuntimeError(
            f"Skill {skill_name!r} did not produce {output_tool.name} "
            f"in {_SKILL_MAX_TURNS} turns"
        )
