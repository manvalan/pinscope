"""Per-IC normalize pass — schema + validator behavior.

Locks in the rules added after the staging-project audit:

1. The reviewer's `status` is shown to the normalize LLM, which may only
   re-grade *downward*: a deterministic clamp forbids raising any finding
   above the reviewer's calibrated severity, caps `Unverified:` findings
   at WARNING, and preserves the `Unverified:` prefix. (This is the fix
   for the U2-001 false positive, where normalize laundered a hedged
   WARNING into a confident ERROR.)
2. Self-cancelling findings can be dropped via a `dropped` array
   (index + reason) and are then removed from the report entirely.
3. Merges (`len(merged_from) > 1`) require a non-empty `single_fix`
   describing the one atomic component/net change that resolves all
   members; missing `single_fix` un-merges back to per-index originals.
"""

from __future__ import annotations

import json

from backend.pinscopex.models import Finding
from backend.services.normalize_findings import (
    SUBMIT_NORMALIZED_SCHEMA,
    _build_normalized,
    _serialize_findings_for_prompt,
)


def _f(idx: int, status: str = "WARNING", why: str = "w") -> Finding:
    return Finding(
        designator="U3",
        mpn="X",
        finding=f"finding {idx}",
        why=why,
        status=status,
        recommendation="",
        source_page=idx,
        source_quote="",
        reference="",
    )


def test_serialize_for_prompt_shows_reviewer_severity():
    """Normalize IS shown the reviewer's severity so it can re-grade
    downward from it — the deterministic clamp enforces downgrade-only."""
    findings = [_f(1, status="ERROR"), _f(2, status="INFO")]
    out = _serialize_findings_for_prompt(findings)
    parsed = json.loads(out)
    assert [row["reviewer_severity"] for row in parsed] == ["ERROR", "INFO"]


def test_schema_exposes_dropped_array_and_single_fix_field():
    props = SUBMIT_NORMALIZED_SCHEMA.input_schema["properties"]
    assert "dropped" in props
    dropped_item = props["dropped"]["items"]
    assert dropped_item["required"] == ["index", "reason"]

    finding_props = props["findings"]["items"]["properties"]
    assert "single_fix" in finding_props


def test_drop_self_cancelling_finding_removes_from_kept_list():
    originals = [_f(1), _f(2, why="satisfies the spec via C24")]
    raw_findings = [{
        "merged_from": [1], "finding": "f1", "why": "w",
        "status": "WARNING", "recommendation": "", "change_rationale": "unchanged",
    }]
    raw_dropped = [{"index": 2, "reason": "self-cancelling: C24 satisfies spec"}]
    built = _build_normalized(raw_findings, raw_dropped, originals)
    assert built is not None
    kept, dropped = built
    assert len(kept) == 1
    assert kept[0].finding == "f1"
    assert len(dropped) == 1
    assert dropped[0]["index"] == 2
    assert "C24" in dropped[0]["reason"]
    # Original finding is preserved in the dropped record for forensics.
    assert dropped[0]["original_finding"]["finding"] == "finding 2"


def test_drop_with_empty_reason_is_rejected():
    originals = [_f(1), _f(2)]
    raw_findings = [{
        "merged_from": [1], "finding": "f1", "why": "w",
        "status": "WARNING", "recommendation": "", "change_rationale": "unchanged",
    }]
    raw_dropped = [{"index": 2, "reason": ""}]
    assert _build_normalized(raw_findings, raw_dropped, originals) is None


def test_drop_and_keep_cannot_cover_same_index():
    """Double-coverage (drop + keep both name index 1) must be rejected."""
    originals = [_f(1), _f(2)]
    raw_findings = [
        {"merged_from": [1], "finding": "f1", "why": "w", "status": "INFO",
         "recommendation": "", "change_rationale": "unchanged"},
        {"merged_from": [2], "finding": "f2", "why": "w", "status": "INFO",
         "recommendation": "", "change_rationale": "unchanged"},
    ]
    raw_dropped = [{"index": 1, "reason": "shouldn't also be kept"}]
    assert _build_normalized(raw_findings, raw_dropped, originals) is None


def test_coverage_gap_is_rejected():
    """Every original index must end up somewhere (kept, merged, or dropped)."""
    originals = [_f(1), _f(2)]
    raw_findings = [{
        "merged_from": [1], "finding": "f1", "why": "w",
        "status": "INFO", "recommendation": "", "change_rationale": "unchanged",
    }]
    # Index 2 is uncovered.
    assert _build_normalized(raw_findings, [], originals) is None


def test_merge_without_single_fix_unmerges_to_originals():
    """A merge whose model omitted `single_fix` is not valid — break it
    apart and surface the per-index originals (severity preserved)."""
    originals = [_f(1, status="WARNING"), _f(2, status="INFO")]
    raw_findings = [{
        "merged_from": [1, 2],
        "finding": "merged into ERROR",
        "why": "combined harm",
        "status": "ERROR",
        "recommendation": "",
        "change_rationale": "merged",
        # single_fix intentionally omitted
    }]
    built = _build_normalized(raw_findings, [], originals)
    assert built is not None
    kept, dropped = built
    assert len(dropped) == 0
    assert len(kept) == 2
    # Originals are preserved verbatim — severity not laundered up.
    assert kept[0].status == "WARNING"
    assert kept[1].status == "INFO"


def test_merge_severity_clamped_to_strongest_member():
    """A merge cannot exceed the highest original severity among members.
    Merging WARNING + INFO and asking for ERROR clamps to WARNING."""
    originals = [_f(1, status="WARNING"), _f(2, status="INFO")]
    raw_findings = [{
        "merged_from": [1, 2],
        "finding": "single root cause",
        "why": "combined harm",
        "status": "ERROR",  # over-graded — must clamp to WARNING
        "recommendation": "remove R1",
        "change_rationale": "merged (atomic)",
        "single_fix": "remove R1 from the VIN path",
    }]
    built = _build_normalized(raw_findings, [], originals)
    assert built is not None
    kept, _ = built
    assert len(kept) == 1
    assert kept[0].status == "WARNING"  # clamped down from the proposed ERROR
    assert kept[0].finding == "single root cause"


def test_merge_keeps_error_when_a_member_was_error():
    """The clamp is a ceiling, not a cap-to-WARNING: an ERROR member lets
    the merged finding stay ERROR."""
    originals = [_f(1, status="ERROR"), _f(2, status="WARNING")]
    raw_findings = [{
        "merged_from": [1, 2],
        "finding": "single root cause",
        "why": "combined harm",
        "status": "ERROR",
        "recommendation": "fix",
        "change_rationale": "merged",
        "single_fix": "rewire X to Z",
    }]
    built = _build_normalized(raw_findings, [], originals)
    assert built is not None
    kept, _ = built
    assert kept[0].status == "ERROR"


def test_normalize_cannot_upgrade_single_finding():
    """The U2-001 bug: reviewer graded WARNING, normalize must not promote
    it to ERROR even on a passthrough (len-1 group)."""
    originals = [_f(1, status="WARNING")]
    raw_findings = [{
        "merged_from": [1],
        "finding": "f1",
        "why": "w",
        "status": "ERROR",  # attempted upgrade
        "recommendation": "",
        "change_rationale": "graded ERROR per rubric",
    }]
    built = _build_normalized(raw_findings, [], originals)
    assert built is not None
    kept, _ = built
    assert kept[0].status == "WARNING"  # upgrade rejected


def test_unverified_finding_capped_at_warning_and_prefix_preserved():
    """A finding whose `why` starts with 'Unverified:' can never be ERROR,
    and the prefix survives even if the model rewrote `why` without it."""
    originals = [_f(1, status="WARNING", why="Unverified: abs-max for PA14 not confirmed")]
    raw_findings = [{
        "merged_from": [1],
        "finding": "PA14 overvoltage",
        "why": "The 5V output exceeds the 3.6V abs-max and will damage the MCU",
        "status": "ERROR",  # confident upgrade + dropped the Unverified prefix
        "recommendation": "level shift",
        "change_rationale": "graded ERROR",
    }]
    built = _build_normalized(raw_findings, [], originals)
    assert built is not None
    kept, _ = built
    assert kept[0].status == "WARNING"
    assert kept[0].why.lower().startswith("unverified:")


def test_unchanged_passthrough_with_single_index_does_not_require_single_fix():
    """`single_fix` is only required for true merges (len > 1)."""
    originals = [_f(1, status="ERROR")]
    raw_findings = [{
        "merged_from": [1],
        "finding": "passthrough",
        "why": "w",
        "status": "WARNING",  # normalize re-graded down
        "recommendation": "",
        "change_rationale": "downgraded per rubric",
    }]
    built = _build_normalized(raw_findings, [], originals)
    assert built is not None
    kept, _ = built
    assert len(kept) == 1
    assert kept[0].status == "WARNING"


def test_all_findings_dropped_is_valid():
    """An IC where every finding was self-cancelling produces an empty
    report — that is a valid outcome, not a coverage failure."""
    originals = [_f(1, why="X satisfies spec"), _f(2, why="Y is in the right place")]
    raw_findings = []
    raw_dropped = [
        {"index": 1, "reason": "X meets spec"},
        {"index": 2, "reason": "Y is the input cap"},
    ]
    built = _build_normalized(raw_findings, raw_dropped, originals)
    assert built is not None
    kept, dropped = built
    assert kept == []
    assert len(dropped) == 2
