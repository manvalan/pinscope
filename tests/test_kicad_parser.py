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


# ---------------------------------------------------------------------------
# Hierarchical .kicad_sch (root + child sheets)
# ---------------------------------------------------------------------------

_LIB_R = """
  (lib_symbols
    (symbol "Device:R"
      (pin passive (at 0 3.81 90) (length 2.54)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
      (pin passive (at 0 -3.81 90) (length 2.54)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27))))
      )
    )
  )
"""


def _resistor(ref: str, value: str, x: float = 0, y: float = 0) -> str:
    return f"""
  (symbol
    (lib_id "Device:R")
    (at {x} {y} 0)
    (unit 1)
    (uuid "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    (property "Reference" "{ref}" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "{value}" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid "p1"))
    (pin "2" (uuid "p2"))
  )
"""


def _sch(*body: str) -> str:
    return "(kicad_sch (version 20250114) (uuid \"11111111-1111-1111-1111-111111111111\")" + _LIB_R + "".join(body) + "\n)\n"


def test_parse_single_sheet_kicad_sch(tmp_path: Path):
    from backend.pinscopex.parsers import parse_netlist_any

    p = tmp_path / "one.kicad_sch"
    p.write_text(_sch(
        _resistor("R1", "10k"),
        """
  (global_label "GND" (at 0 3.81 0) (uuid "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
""",
    ))
    parts, nets, fmt = parse_netlist_any(p)
    assert fmt == "kicad_sch"
    assert "R1" in parts
    assert ("R1", "1") in nets["GND"]


def test_hierarchical_global_gnd_merges_across_sheets(tmp_path: Path):
    from backend.pinscopex.parsers import parse_netlist_any

    child = tmp_path / "child.kicad_sch"
    child.write_text(_sch(
        _resistor("C1", "100n"),
        """
  (global_label "GND" (at 0 3.81 0) (uuid "cccccccccccccccccccccccccccccccccccc"))
""",
    ))
    root = tmp_path / "root.kicad_sch"
    root.write_text(_sch(
        _resistor("R1", "10k"),
        """
  (global_label "GND" (at 0 3.81 0) (uuid "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
  (sheet
    (at 50 0)
    (size 20 20)
    (property "Sheetname" "Child" (at 50 0 0) (effects (font (size 1.27 1.27))))
    (property "Sheetfile" "child.kicad_sch" (at 50 0 0) (effects (font (size 1.27 1.27))))
  )
""",
    ))
    _parts, nets, fmt = parse_netlist_any(root)
    assert fmt == "kicad_sch"
    gnd = nets["GND"]
    assert ("R1", "1") in gnd
    assert ("C1", "1") in gnd


def test_hierarchical_label_connects_through_sheet_pin(tmp_path: Path):
    from backend.pinscopex.parsers import parse_netlist_any

    child = tmp_path / "analog.kicad_sch"
    child.write_text(_sch(
        _resistor("C1", "100n"),
        """
  (hierarchical_label "VIN" (at 0 3.81 0) (uuid "cccccccccccccccccccccccccccccccccccc"))
""",
    ))
    root = tmp_path / "root.kicad_sch"
    root.write_text(_sch(
        _resistor("R1", "10k"),
        """
  (wire (pts (xy 0 3.81) (xy 50 3.81)))
  (sheet
    (at 50 0)
    (size 20 20)
    (property "Sheetname" "Analog" (at 50 0 0) (effects (font (size 1.27 1.27))))
    (property "Sheetfile" "analog.kicad_sch" (at 50 0 0) (effects (font (size 1.27 1.27))))
    (pin "VIN" unspecified (at 50 3.81 180) (uuid "dddddddd-dddd-dddd-dddd-dddddddddddd"))
  )
""",
    ))
    _parts, nets, _fmt = parse_netlist_any(root)
    vin = nets["VIN"]
    assert ("R1", "1") in vin
    assert ("C1", "1") in vin


def test_local_labels_same_name_do_not_merge_across_sheets(tmp_path: Path):
    from backend.pinscopex.parsers import parse_netlist_any

    child = tmp_path / "child.kicad_sch"
    child.write_text(_sch(
        _resistor("R2", "1k"),
        """
  (label "FOO" (at 0 3.81 0) (uuid "cccccccccccccccccccccccccccccccccccc"))
""",
    ))
    root = tmp_path / "root.kicad_sch"
    root.write_text(_sch(
        _resistor("R1", "10k"),
        """
  (label "FOO" (at 0 3.81 0) (uuid "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
  (sheet
    (at 50 0)
    (size 20 20)
    (property "Sheetname" "Child" (at 50 0 0) (effects (font (size 1.27 1.27))))
    (property "Sheetfile" "child.kicad_sch" (at 50 0 0) (effects (font (size 1.27 1.27))))
  )
""",
    ))
    _parts, nets, _fmt = parse_netlist_any(root)
    r1_nets = [n for n, pins in nets.items() if ("R1", "1") in pins]
    r2_nets = [n for n, pins in nets.items() if ("R2", "1") in pins]
    assert len(r1_nets) == 1 and len(r2_nets) == 1
    assert r1_nets[0] != r2_nets[0]
    assert "FOO" not in nets or len(nets.get("FOO", [])) <= 1


def test_sheetfile_parent_traversal_is_rejected(tmp_path: Path):
    import pytest
    from backend.pinscopex.parsers_kicad import parse_kicad

    root = tmp_path / "root.kicad_sch"
    root.write_text(_sch(
        _resistor("R1", "10k"),
        """
  (global_label "GND" (at 0 3.81 0) (uuid "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
  (sheet
    (at 50 0)
    (size 20 20)
    (property "Sheetname" "Escape" (at 50 0 0) (effects (font (size 1.27 1.27))))
    (property "Sheetfile" "../outside.kicad_sch" (at 50 0 0) (effects (font (size 1.27 1.27))))
  )
""",
    ))
    with pytest.raises(ValueError, match="rejected"):
        parse_kicad(root)


def test_missing_child_sheet_raises(tmp_path: Path):
    import pytest
    from backend.pinscopex.parsers_kicad import parse_kicad

    root = tmp_path / "root.kicad_sch"
    root.write_text(_sch(
        _resistor("R1", "10k"),
        """
  (global_label "GND" (at 0 3.81 0) (uuid "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
  (sheet
    (at 50 0)
    (size 20 20)
    (property "Sheetname" "Missing" (at 50 0 0) (effects (font (size 1.27 1.27))))
    (property "Sheetfile" "nope.kicad_sch" (at 50 0 0) (effects (font (size 1.27 1.27))))
  )
""",
    ))
    with pytest.raises(ValueError, match="Missing"):
        parse_kicad(root)


def test_cyclic_sheet_include_is_rejected(tmp_path: Path):
    import pytest
    from backend.pinscopex.parsers_kicad import parse_kicad

    child = tmp_path / "child.kicad_sch"
    child.write_text(_sch(
        _resistor("C1", "100n"),
        """
  (global_label "GND" (at 0 3.81 0) (uuid "cccccccccccccccccccccccccccccccccccc"))
  (sheet
    (at 50 0)
    (size 20 20)
    (property "Sheetname" "Root" (at 50 0 0) (effects (font (size 1.27 1.27))))
    (property "Sheetfile" "root.kicad_sch" (at 50 0 0) (effects (font (size 1.27 1.27))))
  )
""",
    ))
    root = tmp_path / "root.kicad_sch"
    root.write_text(_sch(
        _resistor("R1", "10k"),
        """
  (global_label "GND" (at 0 3.81 0) (uuid "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
  (sheet
    (at 50 0)
    (size 20 20)
    (property "Sheetname" "Child" (at 50 0 0) (effects (font (size 1.27 1.27))))
    (property "Sheetfile" "child.kicad_sch" (at 50 0 0) (effects (font (size 1.27 1.27))))
  )
""",
    ))
    with pytest.raises(ValueError, match="[Cc]yclic"):
        parse_kicad(root)
