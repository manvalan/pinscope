from pathlib import Path

from backend.services.datasheet_finder import find_local_pdf
from backend.services.passive_from_mpn import specs_from_mpn


def test_walsin_c0g_18pf():
    model = specs_from_mpn("0805CG180J500NT")
    assert model is not None
    specs = model.specs
    assert specs.specs_type == "capacitor"
    assert abs(specs.value_farads - 18e-12) < 1e-15
    assert specs.package == "0805"
    assert specs.dielectric == "C0G"
    assert specs.tolerance == "±5%"
    assert specs.voltage_rating_v == "50V"


def test_avx_c0g_150pf():
    model = specs_from_mpn("08055A151FAT2A")
    assert model is not None
    specs = model.specs
    assert abs(specs.value_farads - 150e-12) < 1e-15
    assert specs.package == "0805"
    assert specs.dielectric == "C0G"
    assert specs.voltage_rating_v == "50V"


def test_chip_resistor_from_mpn():
    model = specs_from_mpn("FRC0805F1212TS")
    assert model is not None
    assert abs(model.specs.value_ohms - 12100) < 0.1
    assert model.specs.package == "0805"
    assert model.specs.tolerance == "±1%"

    model = specs_from_mpn("0805W8F2201T5E")
    assert model is not None
    assert abs(model.specs.value_ohms - 2200) < 0.1


def test_murata_lqw18an():
    model = specs_from_mpn("LQW18AN12NG00D")
    assert model is not None
    assert model.specs.specs_type == "inductor"
    assert abs(model.specs.value_henries - 12e-9) < 1e-15
    assert model.specs.package == "0603"
    assert model.specs.tolerance == "±2%"

    model = specs_from_mpn("LQW18AN18NJ00D")
    assert abs(model.specs.value_henries - 18e-9) < 1e-15
    assert model.specs.tolerance == "±5%"

    model = specs_from_mpn("LQW18AN2N2D00D")
    assert abs(model.specs.value_henries - 2.2e-9) < 1e-15
    assert model.specs.tolerance == "±0.5nH"

    model = specs_from_mpn("LQW18ANR10G00D")
    assert abs(model.specs.value_henries - 100e-9) < 1e-15


def test_skips_opaque_murata_and_bare_value():
    assert specs_from_mpn("GRM21A5C2J200JA01") is None
    assert specs_from_mpn("18pF") is None
    assert specs_from_mpn("CH340E") is None


def test_find_local_pdf_family_vs_orderable(tmp_path: Path):
    pdf = tmp_path / "ESP32-S31-WROOM-3-N16R16V.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    hit = find_local_pdf(tmp_path, "ESP32-S31-WROOM-3")
    assert hit is not None
    assert hit.name == pdf.name

    other = tmp_path / "only"
    other.mkdir()
    family = other / "ESP32-S31-WROOM-3.pdf"
    family.write_bytes(b"%PDF-1.4\n" + b"y" * 100)
    hit = find_local_pdf(other, "ESP32-S31-WROOM-3-N16R16V")
    assert hit is not None
    assert hit.name == family.name


def test_find_local_pdf_does_not_steal_sibling_die(tmp_path: Path):
    (tmp_path / "CH340E.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 100)
    assert find_local_pdf(tmp_path, "CH340") is None
