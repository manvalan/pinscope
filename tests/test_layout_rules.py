"""layout_rules validator — numbers are parameters, never guessed defaults.

Favor: a numeric max_distance_mm / min_via_count is kept as given.
Against: unknown kind rejected; non-numeric distance becomes null.
"""

from __future__ import annotations

from backend.pinscopex.layout_rules import validate_layout_rules


def test_numeric_max_distance_mm_is_kept():
    given = 2.0
    ok, errors = validate_layout_rules([
        {"kind": "decoupling_proximity", "max_distance_mm": given, "source_page": 1},
    ])
    assert errors == []
    assert ok[0]["max_distance_mm"] == given


def test_length_match_keeps_max_distance_mm_parameter():
    given = 2.0
    ok, errors = validate_layout_rules([
        {"kind": "length_match", "max_distance_mm": given},
    ])
    assert errors == []
    assert ok[0]["max_distance_mm"] == given


def test_empty_list_is_explicit_skip():
    ok, errors = validate_layout_rules([])
    assert ok == []
    assert errors == []


def test_unknown_kind_rejected_and_non_numeric_distance_is_null():
    ok, errors = validate_layout_rules([
        {"kind": "not_a_kind", "max_distance_mm": 1.0},
        {"kind": "decoupling_proximity", "max_distance_mm": "close"},
        {"kind": "thermal_via", "min_via_count": 4},
    ])
    assert errors
    dist_rows = [r for r in ok if r["kind"] == "decoupling_proximity"]
    assert dist_rows[0]["max_distance_mm"] is None
    via = [r for r in ok if r["kind"] == "thermal_via"]
    assert via[0]["min_via_count"] == 4
