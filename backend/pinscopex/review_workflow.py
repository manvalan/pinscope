"""Finding review disposition, ECO export, and release signature.

Review state lives beside comments on the report JSON — it is not a
Finding field, so a pipeline re-run can keep dispositions by finding_id.
Empty reason is invalid. false_positive / wontfix / open are not ECO rows.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

from backend.pinscopex.models import Finding

ReviewState = Literal["open", "false_positive", "accepted", "wontfix"]
VALID_STATES: frozenset[str] = frozenset({"open", "false_positive", "accepted", "wontfix"})


class ReviewError(ValueError):
    """Invalid review payload; do not store a silent default."""


def apply_review_state(
    current: dict[str, dict[str, Any]],
    finding_id: str,
    *,
    state: str,
    reason: str,
    user_id: str,
    user_name: str = "",
    updated_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    if not finding_id:
        raise ReviewError("finding_id is required")
    if state not in VALID_STATES:
        raise ReviewError(f"invalid review state {state!r}")
    text = (reason or "").strip()
    if state != "open" and not text:
        raise ReviewError("reason is required")
    rec = {
        "state": state,
        "reason": text,
        "user_id": user_id,
        "user_name": user_name,
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
    }
    next_states = dict(current)
    if state == "open":
        next_states.pop(finding_id, None)
        return next_states
    next_states[finding_id] = rec
    return next_states


def _state_of(states: dict[str, dict[str, Any]], finding_id: str | None) -> str:
    if not finding_id:
        return "open"
    rec = states.get(finding_id)
    if not rec:
        return "open"
    return rec.get("state") or "open"


def build_eco(
    findings: Iterable[Finding],
    review_states: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for f in findings:
        fid = f.finding_id
        if _state_of(review_states, fid) != "accepted":
            continue
        rec = review_states.get(fid or "", {})
        items.append({
            "finding_id": fid or "",
            "rule_id": f.rule_id or "",
            "ref": f.designator,
            "before": f.finding,
            "after": f.recommendation or "",
            "reason": rec.get("reason") or "",
        })
    return items


def eco_csv(items: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["finding_id", "rule_id", "ref", "before", "after", "reason"],
    )
    writer.writeheader()
    writer.writerows(items)
    return buf.getvalue()


def sign_report(report: dict[str, Any], *, user_id: str, timestamp: str | None = None) -> dict[str, str]:
    payload = json.dumps(report.get("findings") or [], sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {
        "sha256": digest,
        "user_id": user_id,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }
