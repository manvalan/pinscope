"""Cross-IC dedup pass — schema + validator behavior.

Locks in the rules for collapsing one physical interface defect reported from
both ICs (the U2-001 / U3-001 duplication) into a single finding:

1. Singletons pass through unchanged (no laundering).
2. A merge uses the `primary_index` member for component attribution + source,
   and its severity is clamped to the strongest member (never upgraded).
3. `Unverified:` members cap the merge at WARNING and keep the prefix.
4. Full index coverage is required; a missing/invalid `primary_index` un-merges
   rather than dropping findings.
"""

from __future__ import annotations

import json

from backend.pinscopex.models import Finding
from backend.services.dedupe_findings import (
    SUBMIT_DEDUPED_SCHEMA,
    _build_deduped,
    _serialize_findings_for_prompt,
)


def _f(idx: int, designator: str = "U2", status: str = "ERROR",
       why: str = "w", source_page: int | None = None) -> Finding:
    return Finding(
        designator=designator,
        mpn=f"MPN{idx}",
        finding=f"finding {idx}",
        why=why,
        status=status,
        recommendation="",
        source_page=source_page if source_page is not None else idx,
        source_quote=f"quote {idx}",
        reference=f"ref {idx}",
    )


def test_serialize_includes_designator_and_severity():
    """The IC + reviewer severity are the primary signals for spotting that
    two findings are the two ends of one interface."""
    out = _serialize_findings_for_prompt([_f(1, "U2", "ERROR"), _f(2, "U3", "WARNING")])
    parsed = json.loads(out)
    assert [r["ic"] for r in parsed] == ["U2", "U3"]
    assert [r["reviewer_severity"] for r in parsed] == ["ERROR", "WARNING"]


def test_schema_requires_member_indices():
    item = SUBMIT_DEDUPED_SCHEMA.input_schema["properties"]["groups"]["items"]
    assert "member_indices" in item["required"]
    assert "primary_index" in item["properties"]


def test_singletons_pass_through_unchanged():
    originals = [_f(1, "U2"), _f(2, "U3")]
    groups = [
        {"member_indices": [1], "change_rationale": "passthrough"},
        {"member_indices": [2], "change_rationale": "passthrough"},
    ]
    built = _build_deduped(groups, originals)
    assert built is not None
    assert [f.finding for f in built] == ["finding 1", "finding 2"]
    assert [f.designator for f in built] == ["U2", "U3"]


def test_merge_collapses_interface_and_uses_primary_source():
    """U2-001 + U3-001 → one finding. primary_index picks which side supplies
    the canonical designator + datasheet citation."""
    originals = [
        _f(1, "U2", "ERROR", source_page=11),
        _f(2, "U3", "ERROR", source_page=25),
    ]
    groups = [{
        "member_indices": [1, 2],
        "primary_index": 2,
        "finding": "CH340E 5V output into MCU non-5V-tolerant pin",
        "why": "abs-max exceeded on the UART interface",
        "status": "ERROR",
        "recommendation": "level shift",
        "change_rationale": "merged 1+2: same UART interface",
    }]
    built = _build_deduped(groups, originals)
    assert built is not None
    assert len(built) == 1
    f = built[0]
    assert f.designator == "U3"          # from primary_index=2
    assert f.source_page == 25           # primary's citation
    assert f.source_quote == "quote 2"   # primary's quote retained
    assert f.status == "ERROR"
    assert "CH340E" in f.finding


def test_merge_severity_clamped_to_strongest_member():
    originals = [_f(1, "U2", "WARNING"), _f(2, "U3", "INFO")]
    groups = [{
        "member_indices": [1, 2],
        "primary_index": 1,
        "finding": "merged",
        "why": "combined",
        "status": "ERROR",  # over-graded — clamp to WARNING
        "recommendation": "",
        "change_rationale": "merged",
    }]
    built = _build_deduped(groups, originals)
    assert built is not None
    assert built[0].status == "WARNING"


def test_merge_with_unverified_member_caps_at_warning_and_keeps_prefix():
    originals = [
        _f(1, "U2", "WARNING", why="Unverified: abs-max for PA14 not confirmed"),
        _f(2, "U3", "ERROR", why="will damage the MCU"),
    ]
    groups = [{
        "member_indices": [1, 2],
        "primary_index": 2,
        "finding": "merged overvoltage",
        "why": "5V into a 3.6V pin",
        "status": "ERROR",
        "recommendation": "",
        "change_rationale": "merged",
    }]
    built = _build_deduped(groups, originals)
    assert built is not None
    assert built[0].status == "WARNING"           # unverified caps the merge
    assert built[0].why.lower().startswith("unverified:")


def test_invalid_primary_index_unmerges_to_originals():
    originals = [_f(1, "U2", "ERROR"), _f(2, "U3", "WARNING")]
    groups = [{
        "member_indices": [1, 2],
        "primary_index": 5,  # not a member → un-merge
        "finding": "merged",
        "why": "x",
        "status": "ERROR",
        "recommendation": "",
        "change_rationale": "merged",
    }]
    built = _build_deduped(groups, originals)
    assert built is not None
    assert len(built) == 2
    assert [f.status for f in built] == ["ERROR", "WARNING"]  # originals intact


def test_missing_primary_index_on_merge_unmerges():
    originals = [_f(1, "U2"), _f(2, "U3")]
    groups = [{
        "member_indices": [1, 2],
        "finding": "merged", "why": "x", "status": "ERROR",
        "recommendation": "", "change_rationale": "merged",
    }]
    built = _build_deduped(groups, originals)
    assert built is not None
    assert len(built) == 2


def test_coverage_gap_is_rejected():
    originals = [_f(1), _f(2)]
    groups = [{"member_indices": [1], "change_rationale": "passthrough"}]
    assert _build_deduped(groups, originals) is None


def test_duplicate_index_is_rejected():
    originals = [_f(1), _f(2)]
    groups = [
        {"member_indices": [1], "change_rationale": "p"},
        {"member_indices": [1, 2], "primary_index": 2, "finding": "m",
         "why": "x", "status": "INFO", "recommendation": "", "change_rationale": "m"},
    ]
    assert _build_deduped(groups, originals) is None
