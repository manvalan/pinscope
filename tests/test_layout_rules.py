"""layout_rules from datasheet — closed kind enum, no invented millimetres.

Favor: decoupling_proximity with a numeric max_distance_mm and source_page;
empty list is valid (explicit skip).
Against: unknown kind is rejected; a non-numeric distance becomes null
(not a guessed JEDEC 3 mm); thermal_via without min_via_count is kept
but distance stays unset.
"""

from __future__ import annotations

from backend.pinscopex.layout_rules import validate_layout_rules


def test_valid_decoupling_proximity_keeps_distance():
    ok, errors = validate_layout_rules([
        {
            "kind": "decoupling_proximity",
            "pin": "VDD",
            "cap_value_hint": "100nF",
            "max_distance_mm": 2.0,
            "same_layer": True,
            "source_page": 14,
        }
    ])
    assert errors == []
    assert len(ok) == 1
    assert ok[0]["max_distance_mm"] == 2.0
    assert ok[0]["kind"] == "decoupling_proximity"


def test_length_match_kind_is_accepted():
    ok, errors = validate_layout_rules([
        {"kind": "length_match", "net_class": "diff", "max_distance_mm": 2.0, "source_page": 9},
    ])
    assert errors == []
    assert ok[0]["kind"] == "length_match"
    assert ok[0]["max_distance_mm"] == 2.0


def test_empty_list_is_explicit_skip():
    ok, errors = validate_layout_rules([])
    assert ok == []
    assert errors == []


def test_unknown_kind_rejected_and_bad_distance_not_invented():
    ok, errors = validate_layout_rules([
        {"kind": "jedec_land_pattern", "max_distance_mm": 3.0},
        {
            "kind": "decoupling_proximity",
            "pin": "VDD",
            "max_distance_mm": "close",
            "source_page": 2,
        },
        {"kind": "thermal_via", "pin": "EP", "min_via_count": 4},
    ])
    assert any("kind" in e.lower() or "jedec" in e.lower() for e in errors)
    dist_rows = [r for r in ok if r["kind"] == "decoupling_proximity"]
    assert len(dist_rows) == 1
    assert dist_rows[0]["max_distance_mm"] is None
    via = [r for r in ok if r["kind"] == "thermal_via"]
    assert len(via) == 1 and via[0]["min_via_count"] == 4
