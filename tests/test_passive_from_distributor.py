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
