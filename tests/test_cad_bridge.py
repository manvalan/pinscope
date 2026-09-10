"""E2 cad-bridge JSON and KiCad plugin focus helpers."""

from __future__ import annotations

from pathlib import Path

from backend.pinscopex.cad_bridge import annotate_findings_cad, build_cad_bridge
from backend.pinscopex.models import CadIndexEntry, Finding, ValidationReport
from backend.pinscopex.parsers_kicad import kicad_part_fields, parse_kicad
from plugins.kicad.focus import find_bridge_file, focus_target, load_bridge


def _f(**kwargs) -> Finding:
    defaults = dict(designator="U3", finding="mux", status="WARNING")
    defaults.update(kwargs)
    return Finding(**defaults)


def test_bridge_exports_version_and_strips_pin_prefix():
    report = ValidationReport(
        project="p", timestamp="t",
        findings=[_f(
            finding_id="U3-001", rule_id="PS-MUX-001",
            pins=["U3.12"], cad_sheet="power.kicad_sch",
            cad_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            net="UART5_TX",
        )],
        summary={"total": 1},
    )
    payload = build_cad_bridge(report, "proj-1", url_base="https://app/report")
    assert payload["version"] == 1
    assert payload["project_id"] == "proj-1"
    row = payload["findings"][0]
    assert row["pins"] == ["12"]
    assert row["sheet"] == "power.kicad_sch"
    assert row["uuid"].startswith("aaaa")
    assert row["severity"] == "warning"
    assert "finding=U3-001" in row["url"]
    assert row["target"] == "sch"


def test_missing_uuid_stays_empty_and_pcb_rule_targets_board():
    report = ValidationReport(
        project="p", timestamp="t",
        findings=[_f(rule_id="PS-PLC-001", pins=["1"], status="ERROR")],
        summary={"total": 1},
    )
    row = build_cad_bridge(report, "x")["findings"][0]
    assert row["uuid"] == ""
    assert row["sheet"] == ""
    assert row["target"] == "pcb"
    assert row["severity"] == "error"


def test_annotate_does_not_overwrite_existing_cad_fields():
    idx = {"U3": CadIndexEntry(uuid="from-index", sheet="child.kicad_sch")}
    f = _f(cad_uuid="already", cad_sheet=None)
    annotate_findings_cad([f], idx)
    assert f.cad_uuid == "already"
    assert f.cad_sheet == "child.kicad_sch"


def test_kicad_sch_fields_include_uuid_and_child_sheet(tmp_path: Path):
    from tests.test_kicad_parser import _resistor, _sch

    child = tmp_path / "analog.kicad_sch"
    child.write_text(_sch(
        _resistor("U3", "MCU"),
        """
  (global_label "GND" (at 0 3.81 0) (uuid "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
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
    (property "Sheetname" "Analog" (at 50 0 0) (effects (font (size 1.27 1.27))))
    (property "Sheetfile" "analog.kicad_sch" (at 50 0 0) (effects (font (size 1.27 1.27))))
  )
""",
    ))
    parse_kicad(root)
    fields = kicad_part_fields(root)
    assert fields["U3"]["cad_sheet"] == "analog.kicad_sch"
    assert fields["R1"]["cad_sheet"] == "root.kicad_sch"
    assert fields["U3"].get("cad_uuid")
    assert fields["U3"]["cad_uuid"] != fields["R1"]["cad_uuid"]


def test_plugin_finds_bridge_and_pcb_target(tmp_path: Path):
    (tmp_path / "pinscope-findings.json").write_text(
        '{"version":1,"project_id":"p","findings":[]}\n'
    )
    nested = tmp_path / "board"
    nested.mkdir()
    found = find_bridge_file(nested / "x.kicad_pcb")
    assert found == tmp_path / "pinscope-findings.json"
    assert load_bridge(found)["version"] == 1
    t = focus_target({"ref": "U1", "rule_id": "PS-PLC-001", "uuid": "x", "sheet": ""})
    assert t["kind"] == "pcb" and t["ref"] == "U1"
    missing = tmp_path / "nowhere"
    missing.mkdir()
    assert find_bridge_file(missing, max_up=0) is None
