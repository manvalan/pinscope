"""Per-IC normalize pass — dedup findings with a shared root cause and
re-grade severity against a fixed rubric.

Two runs of the reviewer on identical inputs can produce different *judgments*
(severity choices, finding-splitting) even when they reach the same underlying
observations. This module runs a single small LLM call against the structured
findings (no PDF, no graph tools) to:

  - merge findings that describe the same defect from different angles, and
  - re-grade each remaining finding's severity using an anchored rubric.

It is intentionally conservative: if the call fails, the schema is malformed,
or the index coverage is invalid, the original findings are returned
unchanged. A normalize failure must never break the per-IC review.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

from backend.config import settings
from backend.pinscopex.models import Finding
from backend.services.api_logs import ApiLogger
from backend.services.llm import Message, TextBlock
from backend.services.llm.factory import call_with_fallback
from backend.services.llm.types import ToolSchema

log = logging.getLogger(__name__)

# Severity ordering for the downgrade-only clamp. Normalize may lower a
# finding's severity but never raise it above what the reviewer chose — the
# reviewer had the datasheet + graph; this pass sees only text.
_INFO, _WARN, _ERR = 0, 1, 2
_SEVERITY_RANK = {"INFO": _INFO, "WARNING": _WARN, "ERROR": _ERR}
_RANK_TO_SEV = {_INFO: "INFO", _WARN: "WARNING", _ERR: "ERROR"}


def _is_unverified(why: str | None) -> bool:
    """True when a finding's ``why`` is flagged ``Unverified:`` — the reviewer
    could not confirm the spec from the datasheet and deliberately hedged."""
    return (why or "").lstrip().lower().startswith("unverified:")


SYSTEM_PROMPT = """\
You normalize a single IC's review findings for a hardware design review tool.

Three operations:
1. **Drop** self-cancelling findings whose own analysis confirms the \
design is correct.
2. **Merge** findings that share a single-fix root cause (atomic-fix test).
3. **Re-grade severity** independently against the rubric below.

You CANNOT invent new findings or new facts. Every original finding \
(numbered 1..N) must end up in exactly one of:
- a kept/merged entry in `findings` (referenced by `merged_from`), or
- a dropped entry in `dropped` (referenced by `index`).

You ARE shown the reviewer's original severity. The reviewer graded each \
finding with the datasheet PDF and the design graph in front of it; you \
see only the finding text. You may **lower** a severity when the rubric \
clearly supports a milder grade — over-stated, conditional, or the \
`why` itself flags incomplete evidence — but you must **never raise** a \
finding above the reviewer's grade. Upgrading is where you have the \
least evidence and do the most damage: a normalize pass that promotes a \
hedged WARNING into a confident ERROR is the exact failure this rule \
exists to prevent.

### Drop rule (self-cancelling findings)

A finding is self-cancelling when its own `why` confirms the requirement \
is met or no issue actually exists. The surface reading suggested a \
problem; the analysis itself proved otherwise. Examples:

- "Output cap C1 (100 nF) is below the 1 µF minimum, but C24 (1 µF) in \
parallel satisfies the spec." → drop. Total Cout meets spec; no issue.
- "No dedicated input decoupling cap directly at VIN — but C3 (1 µF) is \
on the VIN net and satisfies the requirement." → drop. C3 IS the input \
cap, in the correct place.
- "Pin X appears unconnected, however net Y shows it is grounded." → drop.

Drop these via the `dropped` array with a short `reason`. Do NOT keep \
them as INFO — they dilute the signal of real issues. If the `why` \
contains "satisfies", "meets the requirement", "is in the correct \
place", "no issue", or equivalent language confirming the design is \
correct, the finding is almost certainly self-cancelling.

A finding that flags a real concern but acknowledges *partial* \
mitigation or *conditional* validity ("works at low load only", "meets \
spec only at room temperature") is NOT self-cancelling — keep it.

### Root-cause merge rule (atomic-fix test)

Two findings share a root cause if a SINGLE atomic change resolves both. \
The atomic-fix test: can you describe the fix in `single_fix` as ONE \
action — remove X, replace X with Y, rewire X to Z, or add X — without \
using "and", "also", or describing multiple steps?

If yes: merge. Write the combined `finding` title naming the root cause \
once. Restate downstream consequences inside `why`. Keep `source_page`, \
`source_quote`, and `reference` from the original with the strongest \
evidence.

If no: do NOT merge. Two defects involving the same component, the same \
net, or the same fix-area are still separate root causes when they \
require separate changes.

**Invalid merge example**: combining "R1 (17.8Ω) in series with VIN \
causes dropout" with "EN tied to VIN — no independent enable" into a \
single ERROR with `single_fix` = "Remove R1 AND route EN from a \
separate GPIO." That is TWO changes (remove R1; rewire EN). Keep these \
as two separate findings — the dropout finding alone may be ERROR or \
WARNING; the EN finding is INFO.

When you merge, you MUST populate `single_fix` with the one atomic \
action. If you cannot, do not merge.

### Severity rubric (grade independently)

- **ERROR**: The circuit, as wired, will not function correctly. The \
output won't reach spec, the regulator won't regulate, the signal \
won't reach the destination, abs-max is exceeded with a strict \
inequality (actual > limit), or a required pin is left undriven. A \
concrete failure mode is reachable from the design as drawn.

- **WARNING**: The circuit functions but has reduced margin, degraded \
performance, or conditional malfunction (depends on load, \
temperature, or firmware state). A recommended-but-not-required \
component is missing. The finding is "unverified" because evidence \
was incomplete.

- **INFO**: A valid topology choice that disables an optional \
feature, or a documentation/layout observation that cannot be \
verified from a netlist. Examples: EN tied to VIN to use the LDO's \
always-on mode (firmware shutdown unavailable but the chip works), \
an optional bypass cap omitted on a non-critical pin.

Grade each kept finding against this rubric, but only ever *downward* \
from the reviewer's original severity (shown to you). A merged \
finding's severity may not exceed the highest original severity among \
its members. If a finding's `why` begins with `Unverified:`, the \
reviewer could not confirm the spec from the datasheet — keep the \
`Unverified:` prefix and never grade it above WARNING.

### Output

Call the `submit_normalized` tool exactly once with:
- `findings`: kept and merged entries (each with `merged_from` indices, \
`single_fix` if merged, and a re-graded `status`).
- `dropped`: self-cancelling entries (each with `index` and `reason`).

Every original index 1..N must appear in exactly one location across \
both arrays. No index may appear twice.
"""


SUBMIT_NORMALIZED_SCHEMA = ToolSchema(
    name="submit_normalized",
    description=(
        "Submit the normalized findings. Every original finding (1..N) "
        "must appear in exactly one location across `findings.merged_from` "
        "or `dropped.index`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "description": (
                    "Kept and merged findings. Re-graded severity; merged "
                    "entries must include `single_fix`."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "merged_from": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 1,
                            "description": (
                                "1-indexed positions in the original "
                                "findings list this output entry "
                                "represents. Length 1 = passed through; "
                                "length > 1 = merged."
                            ),
                        },
                        "finding": {"type": "string"},
                        "why": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["ERROR", "WARNING", "INFO"],
                        },
                        "recommendation": {"type": "string"},
                        "source_page": {"type": ["integer", "null"]},
                        "source_quote": {"type": "string"},
                        "reference": {"type": "string"},
                        "single_fix": {
                            "type": "string",
                            "description": (
                                "REQUIRED when merged_from has length > 1. "
                                "The single atomic component or net change "
                                "that resolves ALL members of the merge "
                                "(remove X, replace X with Y, rewire X to "
                                "Z, or add X). If you cannot write the fix "
                                "in one sentence without 'and' / 'also' / "
                                "multiple steps, do NOT merge."
                            ),
                        },
                        "change_rationale": {
                            "type": "string",
                            "description": (
                                "≤1 line: 'unchanged', or what changed "
                                "and why (merged X+Y, graded <sev> per "
                                "rubric because <reason>, ...)."
                            ),
                        },
                    },
                    "required": [
                        "merged_from",
                        "finding",
                        "why",
                        "status",
                        "recommendation",
                        "change_rationale",
                    ],
                },
            },
            "dropped": {
                "type": "array",
                "description": (
                    "Self-cancelling findings whose own `why` confirms "
                    "the design is correct. These should NOT appear in "
                    "`findings` — they are removed entirely from the "
                    "report. Use this rather than demoting to INFO."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": (
                                "1-indexed position of the original "
                                "finding being dropped."
                            ),
                        },
                        "reason": {
                            "type": "string",
                            "description": (
                                "Short explanation of why the finding is "
                                "self-cancelling (e.g., 'C1<1µF but C24 "
                                "in parallel meets spec', 'C3 is the "
                                "input cap, already in the correct "
                                "place')."
                            ),
                        },
                    },
                    "required": ["index", "reason"],
                },
            },
        },
        "required": ["findings"],
    },
)


def _serialize_findings_for_prompt(findings: list[Finding]) -> str:
    """Number the original findings 1..N and emit a compact JSON block.

    The reviewer's `status` IS included: normalize re-grades only *downward*
    from it (the reviewer had the datasheet + graph; this pass sees only
    text). A deterministic clamp in ``_build_normalized`` enforces the
    downgrade-only invariant regardless of what the model returns.
    """
    rows: list[dict] = []
    for i, f in enumerate(findings, start=1):
        rows.append({
            "index": i,
            "reviewer_severity": f.status,
            "finding": f.finding,
            "why": f.why,
            "recommendation": f.recommendation,
            "source_page": f.source_page,
            "source_quote": f.source_quote,
            "reference": f.reference,
        })
    return json.dumps(rows, indent=2)


def _build_normalized(
    raw_findings: list[dict],
    raw_dropped: list[dict],
    originals: list[Finding],
) -> tuple[list[Finding], list[dict]] | None:
    """Validate the tool output and reconstruct Finding objects.

    Returns ``(kept_findings, dropped_records)`` or ``None`` if coverage /
    schema validation fails (caller falls back to originals).

    A merge with ``len(merged_from) > 1`` that omits ``single_fix`` is not
    a hard failure — the merge is rejected and its members fall back to
    their per-index originals. Self-cancelling drops require a non-empty
    `reason`; missing reason = treat as ungrouped and fail coverage.

    The `change_rationale` and `single_fix` fields are informational and
    are not carried onto Finding objects; the full normalize trace keeps
    them for forensics.
    """
    n = len(originals)
    seen: set[int] = set()
    result: list[Finding] = []
    dropped_records: list[dict] = []

    # Process explicit drops first so indices are reserved before any
    # accidental double-coverage from a merge.
    for d in raw_dropped or []:
        if not isinstance(d, dict):
            return None
        try:
            idx = int(d.get("index"))
        except (TypeError, ValueError):
            return None
        if idx < 1 or idx > n or idx in seen:
            return None
        reason = str(d.get("reason") or "").strip()
        if not reason:
            return None
        seen.add(idx)
        dropped_records.append({
            "index": idx,
            "reason": reason,
            "original_finding": originals[idx - 1].model_dump(mode="json"),
        })

    for entry in raw_findings:
        if not isinstance(entry, dict):
            return None
        merged_from = entry.get("merged_from") or []
        if not isinstance(merged_from, list) or not merged_from:
            return None
        try:
            indices = [int(x) for x in merged_from]
        except (TypeError, ValueError):
            return None
        for idx in indices:
            if idx < 1 or idx > n or idx in seen:
                return None
            seen.add(idx)

        # Atomic-fix test: a merge (len > 1) must populate `single_fix`.
        # If missing, reject the merge and fall back to the per-index
        # originals — preserves coverage but un-merges. The reviewer's
        # original severity is preserved on the fallback path because we
        # construct each Finding directly from `originals[i-1]`.
        if len(indices) > 1:
            single_fix = str(entry.get("single_fix") or "").strip()
            if not single_fix:
                log.warning(
                    "normalize: merge of %s lacks single_fix — falling "
                    "back to per-index originals (un-merging)",
                    indices,
                )
                for idx in indices:
                    result.append(originals[idx - 1])
                continue

        # Use the first original in the group as the canonical source for
        # fields the normalize layer doesn't own (designator, mpn, aspect,
        # finding_id). These are identical across an IC's findings anyway
        # since normalize is per-IC.
        canon = originals[indices[0] - 1]

        # Severity safety net — downgrade-only. Normalize may lower a
        # finding's severity but never raise it above the reviewer's
        # calibrated grade (the reviewer had the datasheet + graph; this
        # pass sees only text). Cap at the highest original severity among
        # merged members; findings the reviewer marked "Unverified:" are
        # capped at WARNING and keep that prefix. This deterministic clamp
        # holds even when the model ignores the prompt instruction.
        members = [originals[i - 1] for i in indices]
        ceiling = max(_SEVERITY_RANK.get(m.status, _ERR) for m in members)
        unverified = any(_is_unverified(m.why) for m in members)
        if unverified:
            ceiling = min(ceiling, _WARN)
        proposed = str(entry.get("status") or canon.status)
        final_status = _RANK_TO_SEV[
            min(_SEVERITY_RANK.get(proposed, ceiling), ceiling)
        ]

        new_why = str(entry.get("why") or canon.why)
        if unverified and not _is_unverified(new_why):
            new_why = "Unverified: " + new_why

        try:
            result.append(Finding(
                finding_id=canon.finding_id,
                designator=canon.designator,
                mpn=canon.mpn,
                aspect=canon.aspect,
                finding=str(entry.get("finding") or canon.finding),
                why=new_why,
                source_page=entry.get("source_page", canon.source_page),
                source_quote=str(entry.get("source_quote") or canon.source_quote),
                source_designator=canon.source_designator,
                status=final_status,
                recommendation=str(entry.get("recommendation") or canon.recommendation),
                reference=str(entry.get("reference") or canon.reference),
            ))
        except Exception:
            log.exception("normalize: failed to build merged Finding")
            return None
    if seen != set(range(1, n + 1)):
        return None
    return result, dropped_records


async def normalize_findings_async(
    ic_ref: str,
    mpn: str,
    findings: list[Finding],
    *,
    api_logger: ApiLogger | None = None,
    on_progress: Callable[[str, int, str, str], Awaitable[None]] | None = None,
) -> tuple[list[Finding], dict]:
    """Run the per-IC normalize pass.

    Returns ``(normalized_findings, trace)``. On any failure (LLM error,
    schema violation, index coverage gap), returns the original findings
    unchanged with an ``error`` field set in the trace.
    """
    trace: dict = {
        "ic_ref": ic_ref,
        "mpn": mpn,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input_findings": [f.model_dump(mode="json") for f in findings],
        "output_findings": None,
        "dropped_findings": None,
        "submission": None,
        "model": None,
        "provider": None,
        "duration_ms": None,
        "error": None,
    }

    # Nothing to do for 0 findings. With 1 finding there is no merge to
    # consider but the drop and re-grade rules still apply — let it
    # through to the LLM call.
    if not findings:
        trace["output_findings"] = []
        trace["dropped_findings"] = []
        trace["error"] = "skipped: 0 findings"
        return findings, trace

    user_text = (
        f"Original findings for IC {ic_ref} ({mpn}). "
        f"There are {len(findings)} findings. "
        f"Indices are 1-based.\n\n"
        f"{_serialize_findings_for_prompt(findings)}\n\n"
        f"Normalize them per the rubric and call submit_normalized."
    )

    t0 = time.monotonic()

    async def _run(provider, model):
        trace["model"] = model
        trace["provider"] = provider.name
        session = await provider.create_session(
            model=model,
            system=SYSTEM_PROMPT,
            max_tokens=4096,
            temperature=0.0,
        )
        try:
            completion = await session.complete(
                messages=[Message(
                    role="user",
                    content=[TextBlock(text=user_text, cacheable=False)],
                )],
                tools=[SUBMIT_NORMALIZED_SCHEMA],
                tool_choice={"name": "submit_normalized"},
            )
            if api_logger:
                api_logger.log(
                    stage="normalize",
                    identifier=ic_ref,
                    model=model,
                    provider=provider.name,
                    input_tokens=completion.usage.input_tokens,
                    output_tokens=completion.usage.output_tokens,
                    cache_creation_input_tokens=completion.usage.cache_creation_tokens,
                    cache_read_input_tokens=completion.usage.cache_read_tokens,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    stop_reason="submit_normalized",
                    turns=1,
                )
            for tc in completion.tool_calls:
                if tc.name == "submit_normalized":
                    return tc.input
            return None
        finally:
            await session.close()

    try:
        submission = await call_with_fallback("normalize", _run)
    except Exception as exc:
        log.exception("normalize: call failed for %s", ic_ref)
        trace["error"] = f"{type(exc).__name__}: {exc}"
        trace["duration_ms"] = int((time.monotonic() - t0) * 1000)
        trace["output_findings"] = trace["input_findings"]
        return findings, trace

    trace["duration_ms"] = int((time.monotonic() - t0) * 1000)
    trace["submission"] = submission

    if not submission or not isinstance(submission, dict):
        trace["error"] = "no submission"
        trace["output_findings"] = trace["input_findings"]
        return findings, trace

    raw_findings = submission.get("findings") or []
    if not isinstance(raw_findings, list):
        trace["error"] = "submission.findings not a list"
        trace["output_findings"] = trace["input_findings"]
        return findings, trace

    raw_dropped = submission.get("dropped") or []
    if not isinstance(raw_dropped, list):
        trace["error"] = "submission.dropped not a list"
        trace["output_findings"] = trace["input_findings"]
        return findings, trace

    built = _build_normalized(raw_findings, raw_dropped, findings)
    if built is None:
        trace["error"] = "invalid index coverage or schema"
        trace["output_findings"] = trace["input_findings"]
        log.warning(
            "normalize: invalid output for %s (%d originals, %d kept, "
            "%d dropped) — falling back to originals",
            ic_ref, len(findings), len(raw_findings), len(raw_dropped),
        )
        if on_progress:
            try:
                await on_progress(
                    ic_ref, 0, "normalize_skipped",
                    f"invalid output, kept {len(findings)} originals",
                )
            except Exception:
                pass
        return findings, trace

    normalized, dropped_records = built
    trace["output_findings"] = [f.model_dump(mode="json") for f in normalized]
    trace["dropped_findings"] = dropped_records
    if on_progress:
        try:
            await on_progress(
                ic_ref, 0, "normalized",
                f"{len(findings)} → {len(normalized)} kept, "
                f"{len(dropped_records)} dropped",
            )
        except Exception:
            pass
    return normalized, trace
