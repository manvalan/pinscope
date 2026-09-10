"""BOM vs schematic MPN/value match.

Favor: identical MPNs silent; real mismatch is ERROR PS-BOM-001 with
designator; orphan BOM line is WARNING.
Against: case/whitespace-only MPN is not a mismatch; empty schematic map
skips the check (PADS path); BOM-empty + schematic MPN is fill, not ERROR.
"""

from __future__ import annotations

from backend.pinscopex.bom_match_check import check_bom_schematic_match
from backend.pinscopex.models import DesignGraph


def test_matching_mpns_produce_no_findings():
    sch = {"U1": {"mpn": "SPX3819M5-L-3-3", "value": "3.3V LDO"}}
    bom = {"U1": {"mpn": "SPX3819M5-L-3-3", "value": "3.3V LDO"}}
    assert check_bom_schematic_match(sch, bom) == []


def test_mpn_mismatch_is_error_ps_bom_001():
    sch = {"U1": {"mpn": "SPX3819M5-L-3-3", "value": "LDO"}}
    bom = {"U1": {"mpn": "AMS1117-3.3", "value": "LDO"}}
    findings = check_bom_schematic_match(sch, bom)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "PS-BOM-001"
    assert f.source == "bom_match"
    assert f.status == "ERROR"
    assert f.designator == "U1"
    assert "SPX3819M5-L-3-3" in f.finding
    assert "AMS1117-3.3" in f.finding


def test_orphan_bom_ref_is_warning():
    sch = {"U1": {"mpn": "MCUX", "value": "MCU"}}
    bom = {
        "U1": {"mpn": "MCUX", "value": "MCU"},
        "R99": {"mpn": "RC0603", "value": "10k"},
    }
    findings = check_bom_schematic_match(sch, bom)
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-BOM-002"
    assert findings[0].status == "WARNING"
    assert findings[0].designator == "R99"


def test_mpn_case_and_whitespace_are_not_a_mismatch():
    sch = {"U1": {"mpn": " mspm0g3507sptr ", "value": "MCU"}}
    bom = {"U1": {"mpn": "MSPM0G3507SPTR", "value": "MCU"}}
    assert check_bom_schematic_match(sch, bom) == []


def test_empty_bom_mpn_with_schematic_mpn_is_not_a_mismatch():
    sch = {"U1": {"mpn": "MSPM0G3507SPTR", "value": "MCU"}}
    bom = {"U1": {"mpn": None, "value": "MSPM0"}}
    assert check_bom_schematic_match(sch, bom) == []


def test_empty_schematic_map_skips_check():
    """PADS/EDIF graphs have no schematic property table — do not treat
    every BOM line as an orphan."""
    sch = {}
    bom = {"U1": {"mpn": "MCUX", "value": "MCU"}, "R1": {"mpn": "RC", "value": "10k"}}
    assert check_bom_schematic_match(sch, bom) == []


def test_legacy_design_graph_without_source_fields_still_validates():
    g = DesignGraph.model_validate({
        "components": {},
        "nets": {},
    })
    assert g.bom_fields == {}
    assert g.schematic_fields == {}


def test_build_graph_kicad_mpn_mismatch_surfaces(tmp_path):
    from backend.pinscopex.graph import build_graph

    net = tmp_path / "net.xml"
    net.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<export version="E">
  <components>
    <comp ref="U1">
      <value>LDO</value>
      <fields><field name="MPN">SPX3819M5-L-3-3</field></fields>
    </comp>
  </components>
  <nets>
    <net code="1" name="GND"><node ref="U1" pin="2"/></net>
  </nets>
</export>
"""
    )
    bom = tmp_path / "bom.csv"
    bom.write_text(
        "Reference,Value,Footprint,Manufacturer Part Number\n"
        "U1,LDO,,AMS1117-3.3\n"
    )
    g = build_graph(
        net, bom, tmp_path / "empty_ex", tmp_path / "empty_pat", tmp_path / "empty_mod",
    )
    findings = check_bom_schematic_match(g.schematic_fields, g.bom_fields)
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-BOM-001"
    assert findings[0].designator == "U1"
