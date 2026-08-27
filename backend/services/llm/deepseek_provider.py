"""DeepSeek provider — OpenAI-compatible Chat Completions.

Translates the unified ``Message`` / ``Completion`` shapes into DeepSeek's
OpenAI-style chat format. DeepSeek does not accept native PDF documents, so
``PdfBlock`` is converted to extracted text (and page images when the
session model is a vision model). Thinking-mode ``reasoning_content`` is
round-tripped on subsequent turns.

Extraction skills run locally via :mod:`backend.services.llm.local_skill`
(DeepSeek has no Anthropic Console Skills equivalent).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from backend.config import settings
from backend.services.llm.base import LLMProvider, LLMSession
from backend.services.llm.local_skill import run_skill_locally
from backend.services.llm.pdf_ingest import pdf_to_openai_content
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

_VISION_HINT = "vision"


def _is_vision_model(model: str) -> bool:
    return _VISION_HINT in model.lower()


def _to_openai_tool(t: ToolSchema) -> dict:
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.input_schema,
        },
    }


def _to_openai_tool_choice(c: ToolChoice) -> dict | str:
    if c == "auto":
        return "auto"
    if c == "none":
        return "none"
    if isinstance(c, dict) and "name" in c:
        return {"type": "function", "function": {"name": c["name"]}}
    raise ValueError(f"Invalid tool_choice: {c!r}")


def _reasoning_from_blocks(blocks: list[ContentBlock]) -> str | None:
    for b in blocks:
        rc = getattr(b, "reasoning_content", None)
        if rc:
            return rc
    return None


def _pdf_parts(path, *, vision: bool) -> list[dict]:
    return pdf_to_openai_content(
        path,
        vision=vision,
        max_chars=settings.deepseek_pdf_max_chars,
        max_images=settings.deepseek_pdf_image_pages,
    )


def _user_content_parts(blocks: list[ContentBlock], *, vision: bool) -> list[dict]:
    """Flatten user-side blocks (text / pdf) into OpenAI content parts."""
    parts: list[dict] = []
    for b in blocks:
        if isinstance(b, TextBlock):
            parts.append({"type": "text", "text": b.text})
        elif isinstance(b, PdfBlock):
            parts.extend(_pdf_parts(b.path, vision=vision))
        else:
            raise TypeError(
                f"Unexpected block in user content: {type(b).__name__}"
            )
    return parts


def messages_to_openai(messages: list[Message], *, vision: bool) -> list[dict]:
    """Convert unified messages into DeepSeek/OpenAI chat messages.

    Tool results become ``role=tool`` messages (OpenAI does not mix
    ``tool_result`` with documents in one user turn). Any PdfBlocks that
    accompanied tool results are emitted as a following user message.
    """
    out: list[dict] = []
    for m in messages:
        if m.role == "assistant":
            text_parts = [b.text for b in m.content if isinstance(b, TextBlock)]
            tool_calls = [b for b in m.content if isinstance(b, ToolCall)]
            msg: dict[str, Any] = {"role": "assistant"}
            text = "".join(text_parts)
            msg["content"] = text if text else None
            if tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.input),
                        },
                    }
                    for tc in tool_calls
                ]
            reasoning = _reasoning_from_blocks(m.content)
            if reasoning:
                msg["reasoning_content"] = reasoning
            out.append(msg)
            continue

        # user
        tool_results = [b for b in m.content if isinstance(b, ToolResultBlock)]
        other = [b for b in m.content if not isinstance(b, ToolResultBlock)]
        for tr in tool_results:
            out.append({
                "role": "tool",
                "tool_call_id": tr.tool_use_id,
                "content": tr.content,
            })
        if other:
            parts = _user_content_parts(other, vision=vision)
            if len(parts) == 1 and parts[0].get("type") == "text":
                out.append({"role": "user", "content": parts[0]["text"]})
            else:
                out.append({"role": "user", "content": parts})
        elif not tool_results:
            out.append({"role": "user", "content": ""})
    return out


def _parse_tool_arguments(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("DeepSeek tool arguments were not valid JSON: %s", raw[:200])
        return {}
    return data if isinstance(data, dict) else {}


def _cache_hit_tokens(usage: Any) -> int:
    hit = getattr(usage, "prompt_cache_hit_tokens", None)
    if hit:
        return int(hit)
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None)
        if cached:
            return int(cached)
    return 0


def completion_from_openai(resp: Any) -> Completion:
    choice = resp.choices[0]
    msg = choice.message
    text = msg.content or ""
    reasoning = getattr(msg, "reasoning_content", None) or None

    tool_calls: list[ToolCall] = []
    raw_blocks: list[ContentBlock] = []
    if text or reasoning:
        raw_blocks.append(TextBlock(text=text or "", reasoning_content=reasoning))
    for i, tc in enumerate(msg.tool_calls or []):
        fn = tc.function
        parsed = _parse_tool_arguments(getattr(fn, "arguments", None))
        call = ToolCall(
            id=tc.id or f"{fn.name}_{i}",
            name=fn.name,
            input=parsed,
            reasoning_content=reasoning if i == 0 and not text else None,
        )
        tool_calls.append(call)
        raw_blocks.append(call)

    usage_md = getattr(resp, "usage", None)
    if usage_md is not None:
        prompt = int(usage_md.prompt_tokens or 0)
        cached = _cache_hit_tokens(usage_md)
        usage = Usage(
            input_tokens=max(0, prompt - cached),
            output_tokens=int(usage_md.completion_tokens or 0),
            cache_creation_tokens=0,
            cache_read_tokens=cached,
        )
    else:
        usage = Usage()

    stop = choice.finish_reason or "unknown"
    return Completion(
        text=text,
        tool_calls=tool_calls,
        usage=usage,
        stop_reason=str(stop),
        raw_assistant_blocks=raw_blocks,
    )


class DeepSeekSession(LLMSession):
    provider_name = "deepseek"

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
        system: str,
        max_tokens: int,
        temperature: float | None = None,
        thinking: bool = True,
        reasoning_effort: str = "medium",
    ) -> None:
        self._client = client
        self.model = model
        self._system = system
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._thinking = thinking
        self._reasoning_effort = reasoning_effort
        self._vision = _is_vision_model(model)

    async def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        tool_choice: ToolChoice = "auto",
    ) -> Completion:
        oai_messages: list[dict] = [
            {"role": "system", "content": self._system},
        ]
        oai_messages.extend(messages_to_openai(messages, vision=self._vision))

        extra_body: dict[str, Any] = {
            "thinking": {"type": "enabled" if self._thinking else "disabled"},
        }
        if self._thinking:
            extra_body["reasoning_effort"] = self._reasoning_effort
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": oai_messages,
            "max_tokens": self._max_tokens,
            "extra_body": extra_body,
        }
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
        if tools:
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]
            kwargs["tool_choice"] = _to_openai_tool_choice(tool_choice)

        resp = await self._client.chat.completions.create(**kwargs)
        return completion_from_openai(resp)

    async def close(self) -> None:
        return None


class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    def __init__(self) -> None:
        api_key = settings.deepseek_api_key
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Copy backend/.env.example to "
                "backend/.env and add a key from https://platform.deepseek.com/"
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.deepseek_base_url,
        )

    async def create_session(
        self,
        *,
        model: str,
        system: str,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> LLMSession:
        thinking = settings.deepseek_thinking.strip().lower() != "disabled"
        effort = settings.deepseek_reasoning_effort
        if max_tokens >= 16000:
            effort = "high"
        return DeepSeekSession(
            client=self._client,
            model=model,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            reasoning_effort=effort,
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
        return await run_skill_locally(
            self,
            skill_name=skill_name,
            model=model,
            system=system,
            user_text=user_text,
            pdf_path=pdf_path,
            output_tool=output_tool,
        )
