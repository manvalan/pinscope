from pathlib import Path

from backend.pinscopex.parsers import detect_netlist_format, parse_netlist_any, validate_netlist


XML = """<?xml version="1.0" encoding="UTF-8"?>
<export version="E">
  <components>
    <comp ref="U1">
      <value>MSPM0G3507</value>
      <footprint>Package_QFP:LQFP-48_7x7mm_P0.5mm</footprint>
      <fields>
        <field name="MPN">MSPM0G3507SPTR</field>
      </fields>
    </comp>
    <comp ref="C1">
      <value>100n</value>
      <footprint>Capacitor_SMD:C_0603</footprint>
    </comp>
    <comp ref="R1">
      <value>10k</value>
      <footprint>Resistor_SMD:R_0603</footprint>
    </comp>
  </components>
  <nets>
    <net code="1" name="GND">
      <node ref="U1" pin="8"/>
      <node ref="C1" pin="2"/>
    </net>
    <net code="2" name="3V3">
      <node ref="U1" pin="1"/>
      <node ref="C1" pin="1"/>
      <node ref="R1" pin="2"/>
    </net>
    <net code="3" name="I2C_SDA">
      <node ref="U1" pin="12"/>
      <node ref="R1" pin="1"/>
    </net>
  </nets>
</export>
"""

SEXP = """(export (version "E")
  (components
    (comp (ref "U1")
      (value "MSPM0G3507")
      (footprint "Package_QFP:LQFP-48")
      (fields
        (field (name "MPN") "MSPM0G3507SPTR")
        (field (name "LCSC") "C12345")
      )
    )
    (comp (ref "C1")
      (value "100n")
      (footprint "C_0603")
    )
  )
  (nets
    (net (code "1") (name "GND")
      (node (ref "U1") (pin "8"))
      (node (ref "C1") (pin "2"))
    )
    (net (code "2") (name "3V3")
      (node (ref "U1") (pin "1"))
      (node (ref "C1") (pin "1"))
    )
  )
)
"""


def test_detect_kicad_xml():
    assert detect_netlist_format(XML) == "kicad_xml"
    assert detect_netlist_format(SEXP) == "kicad_sexp"
    assert detect_netlist_format("(kicad_sch (version 20231120)") == "kicad_sch"


def test_parse_kicad_xml(tmp_path: Path):
    p = tmp_path / "net.xml"
    p.write_text(XML)
    parts, nets, fmt = parse_netlist_any(p)
    assert fmt == "kicad_xml"
    assert parts["U1"].startswith("Package_QFP")
    assert ("U1", "8") in nets["GND"]
    assert ("C1", "1") in nets["3V3"]
    assert ("R1", "1") in nets["I2C_SDA"]
    assert validate_netlist(parts, nets) == []


def test_parse_kicad_sexp_and_mpn_fields(tmp_path: Path):
    p = tmp_path / "net.kicad_net"
    p.write_text(SEXP)
    parts, nets, fmt = parse_netlist_any(p)
    assert fmt == "kicad_sexp"
    assert ("U1", "1") in nets["3V3"]
    from backend.pinscopex.parsers_kicad import kicad_part_fields
    fields = kicad_part_fields(p)
    assert fields["U1"]["mpn"] == "MSPM0G3507SPTR"
    assert fields["U1"]["lcsc"] == "C12345"


def test_kicad_mpn_fills_empty_bom(tmp_path: Path):
    from backend.pinscopex.graph import build_graph

    net = tmp_path / "net.xml"
    net.write_text(XML)
    bom = tmp_path / "bom.csv"
    bom.write_text(
        "Reference,Value,Footprint,Manufacturer Part Number\n"
        "U1,MSPM0,,\nC1,100n,C_0603,\nR1,10k,R_0603,\n"
    )
    g = build_graph(
        net, bom, tmp_path / "empty_ex", tmp_path / "empty_pat", tmp_path / "empty_mod",
    )
    assert g.components["U1"].mpn == "MSPM0G3507SPTR"
    assert "GND" in g.nets
