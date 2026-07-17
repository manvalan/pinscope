"""Provider-agnostic message and completion types.

These dataclasses are the lingua franca between calling code and providers.
Each provider implementation translates these into its native shape on the
way out and back into these on the way in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Content blocks — what goes inside a Message
# ---------------------------------------------------------------------------


@dataclass
class TextBlock:
    text: str
    cacheable: bool = False
    # Gemini 3 / thinking-mode: opaque bytes the model returns alongside text
    # parts that came from internal reasoning. Must be replayed verbatim when
    # this turn is fed back into the conversation, or the next call 400s.
    # Anthropic: always None.
    thought_signature: bytes | None = None


@dataclass
class PdfBlock:
    """Inline PDF document. Provider encodes as base64 (Anthropic) or
    inline_data (Gemini) and applies caching policy if cacheable=True."""
    path: Path
    cacheable: bool = False


@dataclass
class ToolCall:
    """Assistant turn: model called a tool."""
    id: str
    name: str
    input: dict[str, Any]
    # Same purpose as TextBlock.thought_signature — Gemini 3 attaches one
    # to every function_call part when thinking is on. Round-trip required.
    thought_signature: bytes | None = None


@dataclass
class ToolResultBlock:
    """User turn: result fed back from a tool the model invoked previously."""
    tool_use_id: str
    name: str
    content: str


ContentBlock = TextBlock | PdfBlock | ToolCall | ToolResultBlock


@dataclass
class Message:
    role: Literal["user", "assistant"]
    content: list[ContentBlock]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@dataclass
class ToolSchema:
    """JSON-schema tool definition. Both providers accept the same shape."""
    name: str
    description: str
    input_schema: dict[str, Any]


# Tool choice: "auto" (model picks), "none" (no tools), or a forced name
ToolChoice = Literal["auto", "none"] | dict  # {"name": "save_xyz"}


# ---------------------------------------------------------------------------
# Completion / usage
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    """Token usage normalised across providers.

    Anthropic exposes cache_creation_input_tokens (write) and
    cache_read_input_tokens (hit). Gemini only exposes a cache hit count
    (cached_content_token_count) — its cache writes don't bill as input.

    For Gemini, ``cache_creation_tokens`` is always 0; ``cache_read_tokens``
    holds the cached hit count when a cache was used.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass
class Completion:
    """Result of a single provider.complete() / session.complete() call."""
    text: str  # any text block(s) concatenated
    tool_calls: list[ToolCall]
    usage: Usage
    stop_reason: str
    raw_assistant_blocks: list[ContentBlock] = field(default_factory=list)
    """The full assistant message, in our normalised content-block form, so
    callers can append it back to the conversation history when continuing
    the loop."""
