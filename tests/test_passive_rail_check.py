"""Supply decoupling and I2C/reset pull-up checks — graph topology only."""

from __future__ import annotations

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
