"""LDO/resistor thermal — no invented I_load or θJA."""

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
    ResistorSpecs,
    SimpleComponentSpecs,
)
from backend.pinscopex.thermal_check import check_thermal


def _graph(components, nets):
    net_objs = {}
    for name, (ntype, volt, conns) in nets.items():
        net_objs[name] = Net(
            name=name, net_type=ntype, voltage=volt,
            pins=[PinConnection(component_ref=r, pin_number=str(p)) for r, p in conns],
        )
    return DesignGraph(components=components, nets=net_objs)


def _ldo(values, subtype="ic.power.ldo"):
    return Component(
        reference="U1", value="", footprint="",
        component_type=ComponentType.IC, component_subtype=subtype,
        mpn="LDOX",
        pins={"1": "VIN", "2": "VOUT", "3": "GND"},
        specs=SimpleComponentSpecs(specs_type="ic", component_subtype=subtype, values=values),
    )


def _cons():
    return {
        "LDOX": ComponentConstraints(
            mpn="LDOX",
            component_subtype="ic.power.ldo",
            pintable=[
                Pin(number=1, name="VIN"),
                Pin(number=2, name="VOUT"),
                Pin(number=3, name="GND"),
            ],
            absolute_maximum_ratings=[], rules=[],
        )
    }


def test_ldo_without_theta_ja_is_info():
    g = _graph(
        {"U1": _ldo({"i_load_a": 0.2})},
        {
            "VIN": (NetType.POWER, 5.0, [("U1", "1")]),
            "VOUT": (NetType.POWER, 3.3, [("U1", "2")]),
            "GND": (NetType.GROUND, 0.0, [("U1", "3")]),
        },
    )
    findings = check_thermal(g, _cons())
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-TH-001"
    assert findings[0].status == "INFO"
    assert "theta_ja" in findings[0].finding.lower() or "theta_ja" in findings[0].why.lower()


def test_iout_max_is_not_used_as_load():
    g = _graph(
        {"U1": _ldo({"iout_max_a": 0.5, "theta_ja": 160})},
        {
            "VIN": (NetType.POWER, 5.0, [("U1", "1")]),
            "VOUT": (NetType.POWER, 3.3, [("U1", "2")]),
            "GND": (NetType.GROUND, 0.0, [("U1", "3")]),
        },
    )
    assert check_thermal(g, _cons()) == []


def test_ldo_hot_tj_is_warning():
    # 0.5 A * 1.7 V = 0.85 W * 160 °C/W + 25 = 161 °C
    g = _graph(
        {"U1": _ldo({"i_load_a": 0.5, "theta_ja": 160})},
        {
            "VIN": (NetType.POWER, 5.0, [("U1", "1")]),
            "VOUT": (NetType.POWER, 3.3, [("U1", "2")]),
            "GND": (NetType.GROUND, 0.0, [("U1", "3")]),
        },
    )
    findings = check_thermal(g, _cons())
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-TH-002"
    assert findings[0].status == "WARNING"


def test_shunt_over_rating_is_warning():
    r = Component(
        reference="R1", value="1", footprint="",
        component_type=ComponentType.RESISTOR, mpn="R1",
        pins={"1": "A", "2": "B"},
        specs=ResistorSpecs(value_ohms=1.0, value_formatted="1", power_rating_w="0.125"),
    )
    g = _graph(
        {"R1": r},
        {
            "A": (NetType.POWER, 3.3, [("R1", "1")]),
            "B": (NetType.POWER, 0.0, [("R1", "2")]),
        },
    )
    findings = check_thermal(g)
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-TH-003"
    assert findings[0].status == "WARNING"


def test_led_resistor_within_rating_is_silent():
    led = Component(
        reference="D1", value="LED", footprint="",
        component_type=ComponentType.DISCRETE, component_subtype="discrete.led",
        mpn="LEDX",
        pins={"A": "+5V", "K": "NetK"},
        specs=SimpleComponentSpecs(
            specs_type="discrete", component_subtype="discrete.led",
            values={"forward_voltage_v": 2.0, "forward_current_a": "20mA"},
        ),
    )
    r = Component(
        reference="R1", value="330", footprint="",
        component_type=ComponentType.RESISTOR, mpn="R1",
        pins={"1": "NetK", "2": "GND"},
        specs=ResistorSpecs(value_ohms=330.0, value_formatted="330", power_rating_w="0.125"),
    )
    g = _graph(
        {"D1": led, "R1": r},
        {
            "+5V": (NetType.POWER, 5.0, [("D1", "A")]),
            "NetK": (NetType.SIGNAL, None, [("D1", "K"), ("R1", "1")]),
            "GND": (NetType.GROUND, 0.0, [("R1", "2")]),
        },
    )
    assert check_thermal(g) == []
