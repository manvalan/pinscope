"""LED forward-current check — Ohm's-law over the graph against the LED's rating.

Locks in:
1. An undersized resistor (over-current) is an ERROR with source set.
2. A properly sized resistor produces nothing.
3. Resistance strings like "5.6K" parse correctly (not 5.6 ohm).
4. Unknown rail voltage is not guessed into an ERROR.
"""

from __future__ import annotations

from backend.pinscopex.models import (
    Component,
    ComponentType,
    DesignGraph,
    Net,
    NetType,
    ResistorSpecs,
    SimpleComponentSpecs,
    PinConnection,
)
from backend.pinscopex.led_current_check import check_led_current, _parse_resistance


def _led(values, pins, subtype="discrete.led.rgb"):
    return Component(
        reference="D1", value="RGB", footprint="",
        component_type=ComponentType.DISCRETE, component_subtype=subtype,
        mpn="LEDX",
        pins=pins,
        specs=SimpleComponentSpecs(specs_type="discrete", component_subtype=subtype, values=values),
    )


def _res(ref, ohms_str, pins, value_ohms=None):
    specs = ResistorSpecs(value_ohms=value_ohms, value_formatted=ohms_str) if value_ohms is not None else None
    return Component(reference=ref, value=ohms_str, footprint="",
                     component_type=ComponentType.RESISTOR, mpn=ref, pins=pins, specs=specs)


def _driver(ref, pins):
    return Component(reference=ref, value="", footprint="",
                     component_type=ComponentType.DISCRETE, mpn=ref, pins=pins)


def _graph(components, nets):
    """nets: {name: (net_type, voltage, [(ref, pin)])}"""
    net_objs = {}
    for name, (ntype, volt, conns) in nets.items():
        net_objs[name] = Net(
            name=name, net_type=ntype, voltage=volt,
            pins=[PinConnection(component_ref=r, pin_number=str(p)) for r, p in conns],
        )
    return DesignGraph(components=components, nets=net_objs)


def _rgb_graph(green_resistor):
    led = _led(
        {"forward_voltage_green_v": "2.8V", "forward_current_per_channel_a": "13mA",
         "common_polarity": 1.0},
        {"A": "+5V", "G": "NetD1_G"},
    )
    q = _driver("Q1", {"3": "NetQ_D"})
    comps = {"D1": led, "R1": green_resistor, "Q1": q}
    nets = {
        "+5V": (NetType.POWER, 5.0, [("D1", "A")]),
        "NetD1_G": (NetType.SIGNAL, None, [("D1", "G"), ("R1", "2")]),
        "NetQ_D": (NetType.SIGNAL, None, [("R1", "1"), ("Q1", "3")]),
    }
    return _graph(comps, nets)


def test_over_current_is_error():
    # 100 ohm from 5 V, Vf 2.8 -> 22 mA > 13 mA rating.
    g = _rgb_graph(_res("R1", "100R", {"2": "NetD1_G", "1": "NetQ_D"}, value_ohms=100.0))
    findings = check_led_current(g)
    assert len(findings) == 1
    f = findings[0]
    assert f.status == "ERROR" and f.source == "led_current_check" and f.source_page is None
    assert f.designator == "D1" and "green channel" in f.finding


def test_proper_resistor_no_finding():
    # 5.6K (string only, no typed value_ohms) -> ~0.4 mA, safe.
    g = _rgb_graph(_res("R1", "5.6K", {"2": "NetD1_G", "1": "NetQ_D"}))
    assert check_led_current(g) == []


def test_unknown_rail_no_error():
    # Anode net has no voltage tag and the resistor far net is untagged -> skip.
    led = _led(
        {"forward_voltage_green_v": "2.8V", "forward_current_per_channel_a": "13mA"},
        {"A": "NetD1_A", "G": "NetD1_G"},
    )
    r = _res("R1", "100R", {"2": "NetD1_G", "1": "NetQ_D"}, value_ohms=100.0)
    q = _driver("Q1", {"3": "NetQ_D"})
    g = _graph(
        {"D1": led, "R1": r, "Q1": q},
        {
            "NetD1_A": (NetType.SIGNAL, None, [("D1", "A")]),
            "NetD1_G": (NetType.SIGNAL, None, [("D1", "G"), ("R1", "2")]),
            "NetQ_D": (NetType.SIGNAL, None, [("R1", "1"), ("Q1", "3")]),
        },
    )
    assert check_led_current(g) == []


def test_no_rating_skipped():
    g = _rgb_graph(_res("R1", "100R", {"2": "NetD1_G", "1": "NetQ_D"}, value_ohms=100.0))
    # Strip the rating off the LED specs.
    g.components["D1"].specs.values = {"forward_voltage_green_v": "2.8V"}
    assert check_led_current(g) == []


def test_parse_resistance():
    assert _parse_resistance("5.6K") == 5600.0
    assert _parse_resistance("5K6") == 5600.0
    assert _parse_resistance("150R") == 150.0
    assert _parse_resistance("4R7") == 4.7
    assert _parse_resistance("1M") == 1_000_000.0
    assert _parse_resistance("0") == 0.0
    assert _parse_resistance("100") == 100.0
