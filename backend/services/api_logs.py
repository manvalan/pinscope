"""Per-project API call logging.

Captures metadata for every LLM API call made during a pipeline run and
serialises to JSONL for storage alongside other project artefacts. Pricing
lives in ``backend.services.llm.pricing`` and is provider-aware.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field

from pydantic import BaseModel

# Re-exported for callers (pipeline.total_cost) — provider-aware now
from backend.services.llm.pricing import cost_for_entry, total_cost  # noqa: F401


class ApiLogEntry(BaseModel):
    timestamp: str
    stage: str  # pintable | rules | pattern | validation | ...
    identifier: str  # MPN or component designator
    model: str
    provider: str = "anthropic"  # anthropic | gemini
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    duration_ms: int
    stop_reason: str
    skill_id: str | None = None
    turns: int | None = None
    error: str | None = None
    cost_usd: float | None = None
    credits_charged: float | None = None
    # True when the call ran in an admin-initiated free context (e.g. regen)
    # — the raw USD cost is still recorded for accounting, but no credits
    # are charged to the user.
    free: bool = False


@dataclass
class CallMeta:
    """Metadata returned alongside every Claude API call result."""
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    duration_ms: int
    stop_reason: str
    turns: int = 1


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

@dataclass
class ApiLogger:
    """Collects API call log entries during a pipeline run.

    ``free=True`` marks every entry as admin-initiated and zeros the
    ``credits_charged`` field so downstream charging / reporting treats the
    run as free to the user. The underlying USD cost is still recorded.
    """
    entries: list[dict] = field(default_factory=list)
    free: bool = False

    def log(self, **kwargs: object) -> None:
        kwargs.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        entry = ApiLogEntry(**kwargs)  # type: ignore[arg-type]
        d = entry.model_dump()
        d["cost_usd"] = round(cost_for_entry(d), 6)
        if self.free:
            d["credits_charged"] = 0.0
            d["free"] = True
        else:
            # Attribute credits to this call using the same margin used by
            # the credit service.  Local import to avoid a module-load cycle.
            from backend.services.billing_hook import get_billing

            d["credits_charged"] = get_billing().credits_for_api_cost(d["cost_usd"])
        self.entries.append(d)

    def to_jsonl(self) -> str:
        if not self.entries:
            return ""
        return "\n".join(json.dumps(e) for e in self.entries) + "\n"

    def flush(self, storage, user_id: str, project_id: str) -> None:
        """Write the current entries to ``api_logs.jsonl`` in storage.

        Called periodically during a pipeline run so a preempted worker
        doesn't lose billing data. Idempotent — safe to call repeatedly;
        each flush overwrites the prior copy with the latest entries.
        """
        text = self.to_jsonl()
        if not text:
            return
        # Local import avoids a cycle with services.projects (which imports
        # from services.storage which imports from here transitively).
        from backend.services.projects import project_prefix

        key = f"{project_prefix(user_id, project_id)}/api_logs.jsonl"
        storage.write_text(key, text)
