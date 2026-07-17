"""Tests for the Anthropic cache_control breakpoint cap.

Anthropic rejects requests with >4 cache_control blocks. The review path adds
one per cacheable PDF excerpt, so a hub IC fetching two interface excerpts hit
5 (system + initial PDF + initial context + 2 excerpts) and the API returned
"A maximum of 4 blocks with cache_control may be provided. Found 5." The
provider caps message-block breakpoints so system(1) + messages never exceed 4.
"""

from __future__ import annotations

from backend.services.llm.anthropic_provider import _enforce_cache_breakpoint_limit


def _cc(block: dict) -> bool:
    return "cache_control" in block


def _count(messages: list[dict]) -> int:
    return sum(_cc(b) for m in messages for b in m["content"])


def _ephemeral() -> dict:
    return {"cache_control": {"type": "ephemeral"}}


def test_u2_two_excerpts_capped_to_three():
    """The exact failure: initial PDF + context + 2 excerpts = 4 message
    breakpoints (5 with system). Cap to 3 so the total lands at 4."""
    initial_pdf = {"type": "document", "source": {}, **_ephemeral()}
    initial_text = {"type": "text", "text": "ctx", **_ephemeral()}
    excerpt1 = {"type": "document", "source": {}, **_ephemeral()}
    excerpt2 = {"type": "document", "source": {}, **_ephemeral()}
    messages = [
        {"role": "user", "content": [initial_pdf, initial_text]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "x", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "."}, excerpt1]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "b", "name": "x", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b", "content": "."}, excerpt2]},
    ]

    _enforce_cache_breakpoint_limit(messages)

    assert _count(messages) == 3  # +1 system = 4 total, within the limit
    # The stable full-datasheet anchor and the two most recent excerpts survive;
    # the initial context block (cheap to reprocess) loses its breakpoint.
    assert _cc(initial_pdf)
    assert not _cc(initial_text)
    assert _cc(excerpt1)
    assert _cc(excerpt2)


def test_heavy_fanout_keeps_anchor_and_tail():
    """A heavy-fanout review (many excerpts) still caps to 3 and always keeps
    the first cacheable block (the full-datasheet anchor)."""
    anchor = {"type": "document", "source": {}, **_ephemeral()}
    messages = [{"role": "user", "content": [anchor, {"type": "text", "text": "x", **_ephemeral()}]}]
    excerpts = []
    for _ in range(6):
        ex = {"type": "document", "source": {}, **_ephemeral()}
        excerpts.append(ex)
        messages.append({"role": "user", "content": [ex]})

    _enforce_cache_breakpoint_limit(messages)

    assert _count(messages) == 3
    assert _cc(anchor)
    # Two most-recent excerpts retained for incremental tail caching.
    assert _cc(excerpts[-1]) and _cc(excerpts[-2])


def test_under_limit_untouched():
    """Three or fewer message breakpoints are left exactly as-is."""
    pdf = {"type": "document", "source": {}, **_ephemeral()}
    text = {"type": "text", "text": "x", **_ephemeral()}
    messages = [{"role": "user", "content": [pdf, text]}]

    _enforce_cache_breakpoint_limit(messages)

    assert _cc(pdf) and _cc(text)
    assert _count(messages) == 2
