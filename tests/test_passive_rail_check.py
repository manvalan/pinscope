"""Supply decoupling and I2C/reset pull-up checks — graph topology only."""

from __future__ import annotations

from backend.pinscopex.graph import _infer_net_properties
from backend.pinscopex.models import (
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Net,
    NetType,
    Pin,
    PinConnection,
)
from backend.pinscopex.passive_rail_check import (
    check_i2c_pullups,
    check_reset_pullups,
    check_supply_decoupling,
)


def test_ki_cad_voltage_prefix_is_power():
    ntype, volts = _infer_net_properties("3V3_DIGITAL")
    assert ntype == NetType.POWER
    assert volts == 3.3
    ntype, volts = _infer_net_properties("1V8_SI4684")
    assert ntype == NetType.POWER
    assert volts == 1.8
    ntype, _ = _infer_net_properties("I2C1-SCL-3V3")
    assert ntype == NetType.SIGNAL


def _graph(components, nets):
    net_objs = {}
    for name, (ntype, conns) in nets.items():
        net_objs[name] = Net(
            name=name, net_type=ntype,
            pins=[PinConnection(component_ref=r, pin_number=str(p)) for r, p in conns],
        )
    return DesignGraph(components=components, nets=net_objs)


def _ic(ref, pins, mpn="UTEST"):
    return Component(
        reference=ref, value="", footprint="",
        component_type=ComponentType.IC, mpn=mpn, pins=pins,
    )


def _cmap_vdd():
    return {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=1, name="VDD"), Pin(number=2, name="GND")],
            absolute_maximum_ratings=[], rules=[],
        )
    }


def test_missing_decoupling_is_warning():
    g = _graph(
        {"U1": _ic("U1", {"1": "3V3", "2": "GND"})},
        {
            "3V3": (NetType.POWER, [("U1", "1")]),
            "GND": (NetType.GROUND, [("U1", "2")]),
        },
    )
    findings = check_supply_decoupling(g, _cmap_vdd())
    assert len(findings) == 1
    assert findings[0].status == "WARNING"
    assert findings[0].source == "supply_decoupling_check"
    assert "3V3" in findings[0].finding


def test_cap_to_gnd_clears_decoupling():
    cap = Component(
        reference="C1", value="100n", footprint="",
        component_type=ComponentType.CAPACITOR, mpn="C1",
        pins={"1": "3V3", "2": "GND"},
    )
    g = _graph(
        {"U1": _ic("U1", {"1": "3V3", "2": "GND"}), "C1": cap},
        {
            "3V3": (NetType.POWER, [("U1", "1"), ("C1", "1")]),
            "GND": (NetType.GROUND, [("U1", "2"), ("C1", "2")]),
        },
    )
    assert check_supply_decoupling(g, _cmap_vdd()) == []


def test_i2c_missing_pullup():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=8, name="SDA")],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    g = _graph(
        {"U1": _ic("U1", {"8": "I2C_SDA"})},
        {"I2C_SDA": (NetType.SIGNAL, [("U1", "8")])},
    )
    findings = check_i2c_pullups(g, cons)
    assert len(findings) == 1
    assert findings[0].source == "i2c_pullup_check"
    assert findings[0].rule_id == "PS-I2C-001"
    assert findings[0].net == "I2C_SDA"


def test_i2c_pullup_present():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=8, name="SDA")],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    r = Component(
        reference="R1", value="4.7k", footprint="",
        component_type=ComponentType.RESISTOR, mpn="R1",
        pins={"1": "I2C_SDA", "2": "3V3"},
    )
    g = _graph(
        {"U1": _ic("U1", {"8": "I2C_SDA"}), "R1": r},
        {
            "I2C_SDA": (NetType.SIGNAL, [("U1", "8"), ("R1", "1")]),
            "3V3": (NetType.POWER, [("R1", "2")]),
        },
    )
    assert check_i2c_pullups(g, cons) == []


def test_i2c_pullup_to_3v3_digital_typed_as_signal():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=8, name="SDA")],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    r = Component(
        reference="R1", value="4.7k", footprint="",
        component_type=ComponentType.RESISTOR, mpn="R1",
        pins={"1": "I2C_SDA", "2": "3V3_DIGITAL"},
    )
    g = _graph(
        {"U1": _ic("U1", {"8": "I2C_SDA"}), "R1": r},
        {
            "I2C_SDA": (NetType.SIGNAL, [("U1", "8"), ("R1", "1")]),
            "3V3_DIGITAL": (NetType.SIGNAL, [("R1", "2")]),
        },
    )
    assert check_i2c_pullups(g, cons) == []


def test_spi_pin_alias_sda_is_not_i2c():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=38, name="MISO/SDA")],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    g = _graph(
        {"U1": _ic("U1", {"38": "SPI_MISO"})},
        {"SPI_MISO": (NetType.SIGNAL, [("U1", "38")])},
    )
    assert check_i2c_pullups(g, cons) == []


def test_reset_no_finding_when_gpio_drives():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=3, name="nRESET")],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    u2 = _ic("U2", {"1": "MCU_RST"}, mpn="MCU2")
    g = _graph(
        {"U1": _ic("U1", {"3": "MCU_RST"}), "U2": u2},
        {"MCU_RST": (NetType.SIGNAL, [("U1", "3"), ("U2", "1")])},
    )
    assert check_reset_pullups(g, cons) == []


def test_reset_floating_is_warning():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=3, name="nRESET")],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    g = _graph(
        {"U1": _ic("U1", {"3": "NRST_NET"})},
        {"NRST_NET": (NetType.SIGNAL, [("U1", "3")])},
    )
    findings = check_reset_pullups(g, cons)
    assert len(findings) == 1
    assert findings[0].source == "reset_pullup_check"
    assert findings[0].status == "WARNING"


def test_enable_strapped_to_rail_is_not_decoupling():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[
                Pin(number=1, name="EN"),
                Pin(number=2, name="GND"),
            ],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    g = _graph(
        {"U1": _ic("U1", {"1": "3V3", "2": "GND"})},
        {
            "3V3": (NetType.POWER, [("U1", "1")]),
            "GND": (NetType.GROUND, [("U1", "2")]),
        },
    )
    assert check_supply_decoupling(g, cons) == []


def test_i2c_from_slash_alias_in_pin_name():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=12, name="GPIO12/I2C1_SDA")],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    g = _graph(
        {"U1": _ic("U1", {"12": "NET-U1-12"})},
        {"NET-U1-12": (NetType.SIGNAL, [("U1", "12")])},
    )
    findings = check_i2c_pullups(g, cons)
    assert len(findings) == 1
    assert findings[0].source == "i2c_pullup_check"


def test_nc_supply_net_is_skipped():
    g = _graph(
        {"U1": _ic("U1", {"1": "NC"})},
        {"NC": (NetType.POWER, [("U1", "1")])},
    )
    assert check_supply_decoupling(g, _cmap_vdd()) == []


def test_fb_and_rn_prefixes():
    from backend.pinscopex.graph import _classify_component
    from backend.pinscopex.models import ComponentType

    assert _classify_component("FB1", "") == ComponentType.INDUCTOR
    assert _classify_component("RN4", "") == ComponentType.RESISTOR
    assert _classify_component("F1", "") == ComponentType.FUSE


def _res(ref, pins, value="4.7k", ohms=None):
    specs = None
    if ohms is not None:
        from backend.pinscopex.models import ResistorSpecs
        specs = ResistorSpecs(value_ohms=ohms, value_formatted=f"{ohms}")
    return Component(
        reference=ref, value=value, footprint="",
        component_type=ComponentType.RESISTOR, mpn=ref, pins=pins, specs=specs,
    )


def _cap(ref, pins, value="100n", farads=None):
    specs = None
    if farads is not None:
        from backend.pinscopex.models import CapacitorSpecs
        specs = CapacitorSpecs(value_farads=farads, value_formatted=value)
    return Component(
        reference=ref, value=value, footprint="",
        component_type=ComponentType.CAPACITOR, mpn=ref, pins=pins, specs=specs,
    )


def _cmap_i2c():
    return {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=8, name="SDA")],
            absolute_maximum_ratings=[], rules=[],
        )
    }


def test_i2c_4k7_pullup_is_in_nxp_wide_band():
    r = _res("R1", {"1": "I2C_SDA", "2": "3V3"}, value="4.7k")
    g = _graph(
        {"U1": _ic("U1", {"8": "I2C_SDA"}), "R1": r},
        {
            "I2C_SDA": (NetType.SIGNAL, [("U1", "8"), ("R1", "1")]),
            "3V3": (NetType.POWER, [("R1", "2")]),
        },
    )
    assert check_i2c_pullups(g, _cmap_i2c()) == []


def test_i2c_100ohm_pullup_is_too_stiff():
    r = _res("R1", {"1": "I2C_SDA", "2": "3V3"}, ohms=100)
    g = _graph(
        {"U1": _ic("U1", {"8": "I2C_SDA"}), "R1": r},
        {
            "I2C_SDA": (NetType.SIGNAL, [("U1", "8"), ("R1", "1")]),
            "3V3": (NetType.POWER, [("R1", "2")]),
        },
    )
    findings = check_i2c_pullups(g, _cmap_i2c())
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-I2C-002"
    assert findings[0].status == "WARNING"


def test_i2c_100k_pullup_is_too_weak():
    r = _res("R1", {"1": "I2C_SDA", "2": "3V3"}, ohms=100_000)
    g = _graph(
        {"U1": _ic("U1", {"8": "I2C_SDA"}), "R1": r},
        {
            "I2C_SDA": (NetType.SIGNAL, [("U1", "8"), ("R1", "1")]),
            "3V3": (NetType.POWER, [("R1", "2")]),
        },
    )
    findings = check_i2c_pullups(g, _cmap_i2c())
    assert [f.rule_id for f in findings] == ["PS-I2C-002"]


def test_i2c_pullup_without_value_is_not_sized():
    r = _res("R1", {"1": "I2C_SDA", "2": "3V3"}, value="")
    g = _graph(
        {"U1": _ic("U1", {"8": "I2C_SDA"}), "R1": r},
        {
            "I2C_SDA": (NetType.SIGNAL, [("U1", "8"), ("R1", "1")]),
            "3V3": (NetType.POWER, [("R1", "2")]),
        },
    )
    assert check_i2c_pullups(g, _cmap_i2c()) == []


def test_nrst_pulldown_is_warning():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=4, name="NRST")],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    r = _res("R1", {"1": "/NRST", "2": "GND"}, value="10k")
    g = _graph(
        {"U1": _ic("U1", {"4": "/NRST"}), "R1": r},
        {
            "/NRST": (NetType.SIGNAL, [("U1", "4"), ("R1", "1")]),
            "GND": (NetType.GROUND, [("R1", "2")]),
        },
    )
    findings = check_reset_pullups(g, cons)
    assert any(f.rule_id == "PS-RST-002" for f in findings)


def test_nrst_pullup_is_not_pulldown():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=4, name="NRST")],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    r = _res("R8", {"1": "+3V3", "2": "/NRST"}, value="5k1")
    g = _graph(
        {"U1": _ic("U1", {"4": "/NRST"}), "R8": r},
        {
            "/NRST": (NetType.SIGNAL, [("U1", "4"), ("R8", "2")]),
            "+3V3": (NetType.POWER, [("R8", "1")]),
        },
    )
    assert check_reset_pullups(g, cons) == []


def test_ldo_vout_needs_cout():
    cons = {
        "LDOX": ComponentConstraints(
            mpn="LDOX",
            pintable=[
                Pin(number=1, name="VIN"),
                Pin(number=2, name="VOUT"),
                Pin(number=3, name="GND"),
            ],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    cin = _cap("C1", {"1": "VIN", "2": "GND"}, value="1u")
    g = _graph(
        {
            "U1": Component(
                reference="U1", value="", footprint="",
                component_type=ComponentType.IC, mpn="LDOX",
                pins={"1": "VIN", "2": "VOUT", "3": "GND"},
            ),
            "C1": cin,
        },
        {
            "VIN": (NetType.POWER, [("U1", "1"), ("C1", "1")]),
            "VOUT": (NetType.POWER, [("U1", "2")]),
            "GND": (NetType.GROUND, [("U1", "3"), ("C1", "2")]),
        },
    )
    findings = check_supply_decoupling(g, cons)
    assert any(f.net == "VOUT" and f.rule_id == "PS-DEC-001" for f in findings)
    assert not any(f.net == "VIN" for f in findings)


def test_ldo_vout_100n_only_is_value_warning():
    cons = {
        "LDOX": ComponentConstraints(
            mpn="LDOX",
            pintable=[Pin(number=2, name="VOUT")],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    cout = _cap("C2", {"1": "VOUT", "2": "GND"}, farads=100e-9)
    g = _graph(
        {
            "U1": Component(
                reference="U1", value="", footprint="",
                component_type=ComponentType.IC, mpn="LDOX",
                pins={"2": "VOUT"},
            ),
            "C2": cout,
        },
        {
            "VOUT": (NetType.POWER, [("U1", "2"), ("C2", "1")]),
            "GND": (NetType.GROUND, [("C2", "2")]),
        },
    )
    findings = check_supply_decoupling(g, cons)
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-DEC-002"
    assert findings[0].status == "WARNING"


def test_vdd_100n_is_not_a_value_warning():
    cap = _cap("C1", {"1": "3V3", "2": "GND"}, farads=100e-9)
    g = _graph(
        {"U1": _ic("U1", {"1": "3V3", "2": "GND"}), "C1": cap},
        {
            "3V3": (NetType.POWER, [("U1", "1"), ("C1", "1")]),
            "GND": (NetType.GROUND, [("U1", "2"), ("C1", "2")]),
        },
    )
    assert check_supply_decoupling(g, _cmap_vdd()) == []
