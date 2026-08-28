"""KiCad BOM: Value/PNM as MPN when Manufacturer Part Number is empty."""

from pathlib import Path

from backend.pinscopex.parsers import parse_bom


def test_parse_bom_uses_value_for_ic_when_mpn_column_empty(tmp_path: Path):
    csv = tmp_path / "bom.csv"
    csv.write_text(
        "Reference,Qty,Value,DNP,Footprint,Datasheet,PNM\n"
        "U1,1,TPS22965DSGR,,SON-8,,\n"
        '"U9,U13",5,TPD2E007DCKR,,SOT-23,,\n'
        "C1,1,100nF,,0805,,\n"
    )
    bom = parse_bom(csv, mpn_col="Manufacturer Part Number")
    assert bom["U1"]["mpn"] == "TPS22965DSGR"
    assert bom["U9"]["mpn"] == "TPD2E007DCKR"
    assert bom["U13"]["mpn"] == "TPD2E007DCKR"
    assert bom["C1"]["mpn"] is None


def test_parse_bom_keeps_pnm_and_datasheet_url(tmp_path: Path):
    csv = tmp_path / "bom.csv"
    csv.write_text(
        "Reference,Value,PNM,Datasheet\n"
        "U31,TMP117,TMP117NAIDRVR,https://www.ti.com/lit/ds/symlink/tmp117.pdf\n"
    )
    bom = parse_bom(csv, mpn_col="Manufacturer Part Number")
    assert bom["U31"]["mpn"] == "TMP117NAIDRVR"
    assert bom["U31"]["datasheet_url"].endswith("tmp117.pdf")
