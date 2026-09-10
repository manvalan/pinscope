"""Lifecycle from distributor payload.

Favor: DigiKey Obsolete → PS-LF-001 WARNING and uses ProductSubstitutions;
NRND → PS-LF-002 INFO; explicit RoHS Non-Compliant → PS-LF-003.
Against: Active is silent; RoHS Not Applicable is not a fail; missing
catalog row is silent; no substitution key means no invented replacement.
"""

from __future__ import annotations

from backend.pinscopex.lifecycle import (
    check_lifecycle,
    parse_distributor_product,
)
from backend.pinscopex.models import Component, ComponentType, DesignGraph, Net, NetType, PinConnection


def _graph(mpn="PARTX"):
    u = Component(
        reference="U1", value="", footprint="",
        component_type=ComponentType.IC, mpn=mpn,
        pins={"1": "VDD"},
    )
    return DesignGraph(
        components={"U1": u},
        nets={"VDD": Net(name="VDD", net_type=NetType.POWER, pins=[
            PinConnection(component_ref="U1", pin_number="1"),
        ])},
    )


def test_obsolete_is_warning_and_uses_distributor_replacement():
    rec = parse_distributor_product("ABC", {
        "ManufacturerProductNumber": "ABC",
        "ProductStatus": "Obsolete",
        "RoHSStatus": "RoHS3 Compliant",
        "QuantityAvailable": 12,
        "ManufacturerLeadWeeks": "8",
        "ProductSubstitutions": [{"ManufacturerProductNumber": "ABC-B"}],
    })
    assert rec.lifecycle == "eol"
    assert rec.rohs_compliant is True
    assert rec.stock == 12
    assert rec.replacement == "ABC-B"
    findings = check_lifecycle(_graph("ABC"), {"ABC": rec})
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-LF-001"
    assert findings[0].status == "WARNING"
    assert findings[0].source == "lifecycle_check"
    assert "ABC-B" in (findings[0].recommendation or "")


def test_nrnd_is_info():
    rec = parse_distributor_product("N1", {"ProductStatus": "Not For New Designs"})
    assert rec.lifecycle == "nrnd"
    findings = check_lifecycle(_graph("N1"), {"N1": rec})
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-LF-002"
    assert findings[0].status == "INFO"


def test_explicit_rohs_non_compliant_is_warning():
    rec = parse_distributor_product("R1", {
        "ProductStatus": "Active",
        "RoHSStatus": "Non-Compliant",
    })
    assert rec.rohs_compliant is False
    findings = check_lifecycle(_graph("R1"), {"R1": rec})
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-LF-003"
    assert findings[0].status == "WARNING"


def test_active_is_silent():
    rec = parse_distributor_product("A1", {"ProductStatus": "Active"})
    assert rec.lifecycle == "active"
    assert check_lifecycle(_graph("A1"), {"A1": rec}) == []


def test_rohs_not_applicable_is_not_a_fail():
    rec = parse_distributor_product("R2", {
        "ProductStatus": "Active",
        "RoHSStatus": "Not Applicable",
    })
    assert rec.rohs_compliant is None
    assert check_lifecycle(_graph("R2"), {"R2": rec}) == []


def test_missing_catalog_and_missing_substitute_are_not_guessed():
    assert check_lifecycle(_graph("NOPE"), {}) == []
    rec = parse_distributor_product("EOLX", {"ProductStatus": "Discontinued"})
    assert rec.lifecycle == "eol"
    assert rec.replacement is None
    findings = check_lifecycle(_graph("EOLX"), {"EOLX": rec})
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-LF-001"
    assert "ABC-B" not in (findings[0].recommendation or "")
    assert rec.replacement is None
