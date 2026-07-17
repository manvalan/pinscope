"""Per-provider pricing tables and cost computation.

Replaces the flat ``PRICING`` dict that used to live in
``backend/services/api_logs.py``. Indexed by (provider, model).
"""

from __future__ import annotations


# Per-million-token USD rates. Source-of-truth links:
#   Anthropic: https://docs.anthropic.com/en/docs/about-claude/pricing
#   Google:    https://ai.google.dev/pricing
# Last updated: 2026-07-01
PRICING: dict[str, dict[str, dict[str, float]]] = {
    "anthropic": {
        "claude-opus-4-6":            {"input": 5.00,  "output": 25.00},
        "claude-opus-4-5":            {"input": 5.00,  "output": 25.00},
        "claude-opus-4-1":            {"input": 15.00, "output": 75.00},
        "claude-opus-4":              {"input": 15.00, "output": 75.00},
        "claude-sonnet-4-6":          {"input": 3.00,  "output": 15.00},
        # Sonnet 5 standard rate (== Sonnet 4.6). Introductory pricing of
        # $2/$10 runs through 2026-08-31; intentionally NOT tracked here —
        # chosen set-and-forget so no dated bump is needed on 2026-09-01.
        # (New tokenizer emits ~30% more tokens, so per-run cost still rises.)
        "claude-sonnet-5":            {"input": 3.00,  "output": 15.00},
        "claude-sonnet-4-5":          {"input": 3.00,  "output": 15.00},
        "claude-sonnet-4":            {"input": 3.00,  "output": 15.00},
        "claude-haiku-4-5-20251001":  {"input": 1.00,  "output": 5.00},
        "claude-haiku-4-5":           {"input": 1.00,  "output": 5.00},
        "claude-haiku-3-5":           {"input": 0.80,  "output": 4.00},
        "default":                    {"input": 3.00,  "output": 15.00},
    },
    "gemini": {
        # Gemini 3 Flash pricing (per 1M tokens). Preview alias mirrors GA.
        "gemini-3-flash-preview":     {"input": 0.30,  "output": 2.50},
        "gemini-3-flash":             {"input": 0.30,  "output": 2.50},
        "gemini-flash-latest":        {"input": 0.30,  "output": 2.50},
        "gemini-2.5-flash":           {"input": 0.30,  "output": 2.50},
        "gemini-2.5-pro":             {"input": 1.25,  "output": 10.00},
        # Gemini 3.1 Pro Preview — standard tier, prompts ≤200k tokens.
        # Above 200k Google charges $4.00/$18.00; we don't yet split by
        # prompt size, so we use the smaller-tier rate. Almost every
        # pipeline call here is well under 200k.
        "gemini-3.1-pro-preview":     {"input": 2.00,  "output": 12.00},
        "gemini-3-pro-preview":       {"input": 2.00,  "output": 12.00},
        "default":                    {"input": 0.30,  "output": 2.50},
    },
}


# Per-provider cache token multipliers, applied on top of the input rate.
#   create: cost when a cache is *written* (Anthropic charges 1.25× input;
#           Gemini charges 1.0× input — caching writes are billed as a
#           normal input pass)
#   read:   cost when a cached prefix is *reused* (much cheaper)
CACHE_RATES: dict[str, dict[str, float]] = {
    "anthropic": {"create": 1.25, "read": 0.10},
    "gemini":    {"create": 1.00, "read": 0.25},
}


def cost_for_entry(entry: dict) -> float:
    """USD cost for an api_logs entry. Reads ``provider`` (default
    ``anthropic`` for legacy entries) and ``model`` to pick rates."""
    provider = entry.get("provider") or "anthropic"
    table = PRICING.get(provider) or PRICING["anthropic"]
    rates = table.get(entry.get("model", ""), table["default"])
    cache_rates = CACHE_RATES.get(provider, CACHE_RATES["anthropic"])
    input_rate = rates["input"]
    output_rate = rates["output"]
    return (
        entry.get("input_tokens", 0) * input_rate
        + entry.get("cache_creation_input_tokens", 0) * input_rate * cache_rates["create"]
        + entry.get("cache_read_input_tokens", 0) * input_rate * cache_rates["read"]
        + entry.get("output_tokens", 0) * output_rate
    ) / 1_000_000


def total_cost(entries: list[dict]) -> float:
    """Sum USD across entries."""
    return round(sum(cost_for_entry(e) for e in entries), 6)
