"""Cross-IC dedup pass — collapse one physical defect reported from both ends.

Direct datasheet review runs once per IC, in isolation. An interface defect
(e.g. a 5V driver into a non-5V-tolerant input on the U2↔U3 UART) is therefore
discovered twice — once when reviewing U2, once when reviewing U3 — and the
per-IC normalize pass cannot collapse them because it only sees one IC's
findings at a time. The two copies land in the report as separate findings,
double-counting the same problem and (worse) sometimes disagreeing with each
other.

This module runs a single small LLM call over the *concatenated* findings from
all ICs (no PDF, no graph tools) to merge findings that describe the same
physical defect on the same net/interface/component. It is the cross-IC analog
of ``normalize_findings`` and follows the same fail-soft contract: on any LLM
error, schema violation, or coverage gap, the original findings are returned
unchanged. It never drops findings (that is normalize's job) and never raises a
merged finding's severity above the highest of its members.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

from backend.pinscopex.models import Finding
from backend.services.api_logs import ApiLogger
from backend.services.llm import Message, TextBlock
from backend.services.llm.factory import call_with_fallback
from backend.services.llm.types import ToolSchema

log = logging.getLogger(__name__)

# Severity ordering — a merged group's severity is capped at the highest
# severity among its members (downgrade-only, same principle as normalize).
_INFO, _WARN, _ERR = 0, 1, 2
_SEVERITY_RANK = {"INFO": _INFO, "WARNING": _WARN, "ERROR": _ERR}
_RANK_TO_SEV = {_INFO: "INFO", _WARN: "WARNING", _ERR: "ERROR"}


def _is_unverified(why: str | None) -> bool:
    return (why or "").lstrip().lower().startswith("unverified:")


SYSTEM_PROMPT = """\
You deduplicate hardware-review findings across multiple ICs.

Each finding was produced by reviewing one IC in isolation, so a defect on
the interface *between* two ICs is reported twice — once from each side. Your
job is to group findings that describe the SAME physical defect and merge each
group into one finding. You may NOT invent findings, drop findings, or change
the engineering substance.

### When two findings are the same defect (merge)

Merge when they describe the same physical problem at the same place:
- the same net or signal (e.g. both flag over-voltage on `/UART0.NCTS`),
- the same component pair / interface (e.g. "U2 RTS# drives U3 PA14" and
  "U3 PA14 is driven by U2's 5V output" are one interface defect seen from
  each end),
- the same shared part with the same fix.

A merged group is resolved by ONE change to the design. Name that interface or
root cause once in the merged `finding`; restate each side's consequence in
`why`.

### When findings are NOT the same defect (keep separate)

Do NOT merge findings that need different fixes, even if they touch the same
component or net:
- different pins / different signals on the same IC,
- a decoupling issue and a voltage issue on the same supply,
- two unrelated problems that happen to involve the same part.

When in doubt, keep them separate. Over-merging hides distinct problems and is
worse than a visible duplicate.

### Severity

Use the HIGHEST severity among a group's members. Never grade a merged finding
above its strongest member. If any member's `why` begins with `Unverified:`,
keep that prefix and do not grade the merged finding above WARNING.

### Output

Call `submit_deduped` exactly once. Provide a `groups` array. Every original
finding (numbered 1..N) must appear in exactly one group's `member_indices`,
and no index may appear twice.
- A group of ONE index is a passthrough — it is kept unchanged (you do not
  need to restate its text).
- A group of MORE THAN ONE index is a merge — supply the merged `finding`,
  `why`, `status`, `recommendation`, and a `primary_index` (one of the group's
  members) whose datasheet citation/page is the strongest evidence; that
  member supplies the finding's component attribution and source reference.
"""


SUBMIT_DEDUPED_SCHEMA = ToolSchema(
    name="submit_deduped",
    description=(
        "Submit the cross-IC deduplicated findings. Every original finding "
        "(1..N) must appear in exactly one group's `member_indices`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "member_indices": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 1,
                            "description": (
                                "1-indexed positions in the original findings "
                                "list this group represents. Length 1 = "
                                "passthrough; length > 1 = merge."
                            ),
                        },
                        "primary_index": {
                            "type": ["integer", "null"],
                            "description": (
                                "REQUIRED when member_indices has length > 1: "
                                "the member whose datasheet citation/source is "
                                "the strongest. Supplies the merged finding's "
                                "component attribution and source reference. "
                                "Must be one of member_indices."
                            ),
                        },
                        "finding": {"type": "string"},
                        "why": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["ERROR", "WARNING", "INFO"],
                        },
                        "recommendation": {"type": "string"},
                        "change_rationale": {
                            "type": "string",
                            "description": (
                                "≤1 line: 'passthrough', or 'merged N+M: "
                                "<shared interface/root cause>'."
                            ),
                        },
                    },
                    "required": ["member_indices", "change_rationale"],
                },
            },
        },
        "required": ["groups"],
    },
)


def _serialize_findings_for_prompt(findings: list[Finding]) -> str:
    """Number findings 1..N with their IC, severity, and text.

    Unlike the per-IC normalize pass, the designator IS included — it is the
    primary signal for spotting that two findings sit on opposite ends of one
    interface.
    """
    rows: list[dict] = []
    for i, f in enumerate(findings, start=1):
        rows.append({
            "index": i,
            "ic": f.designator,
            "mpn": f.mpn,
            "reviewer_severity": f.status,
            "finding": f.finding,
            "why": f.why,
            "recommendation": f.recommendation,
            "source_page": f.source_page,
            "reference": f.reference,
        })
    return json.dumps(rows, indent=2)


def _build_deduped(
    raw_groups: list[dict],
    originals: list[Finding],
) -> list[Finding] | None:
    """Validate the tool output and reconstruct the deduped finding list.

    Returns the kept/merged findings, or ``None`` if coverage/schema
    validation fails (caller falls back to originals). A merge that omits a
    valid ``primary_index`` is not a hard failure — that group falls back to
    its per-index originals (un-merged), preserving coverage and severities.
    """
    n = len(originals)
    seen: set[int] = set()
    result: list[Finding] = []

    for group in raw_groups:
        if not isinstance(group, dict):
            return None
        member_indices = group.get("member_indices") or []
        if not isinstance(member_indices, list) or not member_indices:
            return None
        try:
            indices = [int(x) for x in member_indices]
        except (TypeError, ValueError):
            return None
        for idx in indices:
            if idx < 1 or idx > n or idx in seen:
                return None
            seen.add(idx)

        # Passthrough — keep the original verbatim. No laundering of text or
        # severity for a finding the model chose not to merge.
        if len(indices) == 1:
            result.append(originals[indices[0] - 1])
            continue

        # Merge — needs a valid primary_index naming the canonical member.
        # Missing/invalid → un-merge to per-index originals (coverage kept).
        primary_raw = group.get("primary_index")
        try:
            primary = int(primary_raw)
        except (TypeError, ValueError):
            primary = None
        if primary not in indices:
            log.warning(
                "dedupe: merge of %s has invalid primary_index %r — "
                "falling back to per-index originals (un-merging)",
                indices, primary_raw,
            )
            for idx in indices:
                result.append(originals[idx - 1])
            continue

        canon = originals[primary - 1]
        members = [originals[i - 1] for i in indices]
        ceiling = max(_SEVERITY_RANK.get(m.status, _ERR) for m in members)
        unverified = any(_is_unverified(m.why) for m in members)
        if unverified:
            ceiling = min(ceiling, _WARN)
        proposed = str(group.get("status") or canon.status)
        final_status = _RANK_TO_SEV[
            min(_SEVERITY_RANK.get(proposed, ceiling), ceiling)
        ]

        new_why = str(group.get("why") or canon.why)
        if unverified and not _is_unverified(new_why):
            new_why = "Unverified: " + new_why

        try:
            result.append(Finding(
                finding_id=canon.finding_id,
                designator=canon.designator,
                mpn=canon.mpn,
                aspect=canon.aspect,
                finding=str(group.get("finding") or canon.finding),
                why=new_why,
                source_page=group.get("source_page", canon.source_page),
                source_quote=canon.source_quote,
                source_designator=canon.source_designator,
                status=final_status,
                recommendation=str(
                    group.get("recommendation") or canon.recommendation
                ),
                reference=str(group.get("reference") or canon.reference),
                source=canon.source,
            ))
        except Exception:
            log.exception("dedupe: failed to build merged Finding")
            return None

    if seen != set(range(1, n + 1)):
        return None
    return result


async def dedupe_cross_ic_findings_async(
    findings: list[Finding],
    *,
    api_logger: ApiLogger | None = None,
    on_progress: Callable[[str, int, str, str], Awaitable[None]] | None = None,
) -> tuple[list[Finding], dict]:
    """Run the cross-IC dedup pass over findings from every IC.

    Returns ``(deduped_findings, trace)``. On any failure (LLM error, schema
    violation, coverage gap) returns the original findings unchanged with an
    ``error`` field set in the trace.
    """
    trace: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "input_findings": [f.model_dump(mode="json") for f in findings],
        "output_findings": None,
        "submission": None,
        "model": None,
        "provider": None,
        "duration_ms": None,
        "error": None,
    }

    # Nothing to merge across fewer than two findings.
    if len(findings) < 2:
        trace["output_findings"] = trace["input_findings"]
        trace["error"] = "skipped: <2 findings"
        return findings, trace

    user_text = (
        f"There are {len(findings)} findings across all reviewed ICs. "
        f"Indices are 1-based. Group findings that describe the same physical "
        f"defect (especially the same interface seen from both ICs) and call "
        f"submit_deduped.\n\n{_serialize_findings_for_prompt(findings)}"
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
                tools=[SUBMIT_DEDUPED_SCHEMA],
                tool_choice={"name": "submit_deduped"},
            )
            if api_logger:
                api_logger.log(
                    stage="cross_ic_dedupe",
                    identifier="all",
                    model=model,
                    provider=provider.name,
                    input_tokens=completion.usage.input_tokens,
                    output_tokens=completion.usage.output_tokens,
                    cache_creation_input_tokens=completion.usage.cache_creation_tokens,
                    cache_read_input_tokens=completion.usage.cache_read_tokens,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    stop_reason="submit_deduped",
                    turns=1,
                )
            for tc in completion.tool_calls:
                if tc.name == "submit_deduped":
                    return tc.input
            return None
        finally:
            await session.close()

    try:
        # Reuse the "normalize" stage config (validation-class Sonnet model +
        # any configured fallback); the log entry above is stamped
        # "cross_ic_dedupe" so cost accounting still distinguishes it.
        submission = await call_with_fallback("normalize", _run)
    except Exception as exc:
        log.exception("dedupe: call failed")
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

    raw_groups = submission.get("groups") or []
    if not isinstance(raw_groups, list):
        trace["error"] = "submission.groups not a list"
        trace["output_findings"] = trace["input_findings"]
        return findings, trace

    built = _build_deduped(raw_groups, findings)
    if built is None:
        trace["error"] = "invalid index coverage or schema"
        trace["output_findings"] = trace["input_findings"]
        log.warning(
            "dedupe: invalid output (%d originals, %d groups) — "
            "falling back to originals", len(findings), len(raw_groups),
        )
        return findings, trace

    trace["output_findings"] = [f.model_dump(mode="json") for f in built]
    if on_progress:
        try:
            await on_progress(
                "cross_ic_dedupe", 0, "deduped",
                f"{len(findings)} → {len(built)} findings",
            )
        except Exception:
            pass
    return built, trace
