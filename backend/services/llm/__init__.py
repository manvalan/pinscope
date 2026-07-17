"""Provider-agnostic LLM client layer.

All Claude API calls in the backend route through this package via the
``LLMProvider`` interface. The default provider is Anthropic; per-stage
overrides via ``Settings.provider_*`` env vars route specific stages to
other providers (currently Anthropic + Gemini).
"""

from backend.services.llm.factory import call_with_fallback, get_provider
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

__all__ = [
    "Completion",
    "ContentBlock",
    "Message",
    "PdfBlock",
    "TextBlock",
    "ToolCall",
    "ToolChoice",
    "ToolResultBlock",
    "ToolSchema",
    "Usage",
    "call_with_fallback",
    "get_provider",
]
