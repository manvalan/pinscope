from backend.services.passive_from_distributor import specs_from_distributor


def test_lcsc_description_capacitor():
    model = specs_from_distributor(
        mpn="CL21B225KPFNNNE",
        params=[{"name": "Package / Case", "value": "0805"}],
        category="Capacitors / Ceramic Capacitors",
        description="2.2uF ±10% 10V X7R 0805",
    )
    assert model is not None
    specs = model.specs
    assert specs.component_subtype == "passive.capacitor.ceramic"
    assert abs(specs.value_farads - 2.2e-6) < 1e-12
    assert specs.dielectric == "X7R"
    assert specs.package == "0805"


def test_digikey_params_resistor():
    model = specs_from_distributor(
        mpn="RC0805FR-0710KL",
        params=[
            {"name": "Resistance", "value": "10 kOhms"},
            {"name": "Tolerance", "value": "±1%"},
            {"name": "Power (Watts)", "value": "0.125W"},
            {"name": "Package / Case", "value": "0805 (2012 Metric)"},
        ],
        category="Resistors / Chip Resistor - Surface Mount",
        description="RES SMD 10K OHM 1% 1/8W 0805",
    )
    assert model is not None
    assert model.specs.value_ohms == 10000
    assert model.specs.package == "0805"


def test_skips_when_no_value():
    assert specs_from_distributor(
        mpn="MYSTERY",
        params=[{"name": "Manufacturer", "value": "Murata"}],
        category="",
        description="some module",
    ) is None


def test_ferrite_bead_from_impedance():
    model = specs_from_distributor(
        mpn="BLM18PG121SN1D",
        params=[
            {"name": "Impedance @ Frequency", "value": "120 Ohms @ 100 MHz"},
            {"name": "Package / Case", "value": "0603"},
            {"name": "Current Rating", "value": "2 A"},
        ],
        category="Filters / Ferrite Beads",
        description="FERRITE BEAD 120 OHM 0603 1LN",
    )
    assert model is not None
    specs = model.specs
    assert specs.component_subtype == "passive.ferrite_bead"
    assert specs.impedance_ohm == 120
    assert specs.value_henries is None
    assert "120ohm" in specs.value_formatted
    assert "100" in specs.value_formatted
    assert specs.package == "0603"
    assert specs.current_rating_a == "2 A"


def test_specs_from_lcsc_payload():
    from backend.services.passive_from_distributor import specs_from_lcsc_payload

    model = specs_from_lcsc_payload(
        "CL21B225KPFNNNE",
        {
            "package": "0805",
            "manufacturer": "Samsung",
            "category": "Capacitors",
            "subcategory": "MLCC",
            "description": "2.2uF ±10% 10V X7R 0805",
        },
    )
    assert model is not None
    assert abs(model.specs.value_farads - 2.2e-6) < 1e-12


def test_auto_resolve_skips_llm_when_catalog_parses(monkeypatch):
    import asyncio
    from pathlib import Path

    from backend.services.extraction import auto_resolve_specs

    async def boom(*_a, **_k):
        raise AssertionError("LLM must not run")

    monkeypatch.setattr("backend.services.extraction.call_with_fallback", boom)
    tax = Path(__file__).resolve().parents[1] / "taxonomy"
    model = asyncio.run(
        auto_resolve_specs(
            mpn="CL21B225KPFNNNE",
            digikey_params=[{"name": "Package / Case", "value": "0805"}],
            digikey_category="Capacitors / Ceramic Capacitors",
            digikey_description="2.2uF ±10% 10V X7R 0805",
            component_type="passive",
            taxonomy_dir=tax,
        )
    )
    assert abs(model.specs.value_farads - 2.2e-6) < 1e-12


def test_auto_resolve_use_llm_false_on_ferrite(monkeypatch):
    import asyncio
    from pathlib import Path

    from backend.services.extraction import auto_resolve_specs

    async def boom(*_a, **_k):
        raise AssertionError("LLM must not run")

    monkeypatch.setattr("backend.services.extraction.call_with_fallback", boom)
    tax = Path(__file__).resolve().parents[1] / "taxonomy"
    model = asyncio.run(
        auto_resolve_specs(
            mpn="BLM18PG121SN1D",
            digikey_params=[
                {"name": "Impedance @ Frequency", "value": "120 Ohms @ 100 MHz"},
            ],
            digikey_category="Filters / Ferrite Beads",
            digikey_description="FERRITE BEAD 120 OHM 0603 1LN",
            component_type="passive",
            taxonomy_dir=tax,
            use_llm=False,
        )
    )
    assert model.specs.impedance_ohm == 120
    assert model.specs.component_subtype == "passive.ferrite_bead"
