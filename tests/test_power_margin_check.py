"""Regulator Iout margin and series-R IR drop — no invented IQ or traces."""

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
from backend.pinscopex.power_margin_check import check_power_margin


def _graph(components, nets):
    net_objs = {}
    for name, (ntype, volt, conns) in nets.items():
        net_objs[name] = Net(
            name=name, net_type=ntype, voltage=volt,
            pins=[PinConnection(component_ref=r, pin_number=str(p)) for r, p in conns],
        )
    return DesignGraph(components=components, nets=net_objs)


def _cons():
    return {
        "LDOX": ComponentConstraints(
            mpn="LDOX", component_subtype="ic.power.ldo",
            pintable=[
                Pin(number=1, name="VIN"),
                Pin(number=2, name="VOUT"),
                Pin(number=3, name="GND"),
            ],
            absolute_maximum_ratings=[], rules=[],
        )
    }


def _ldo(values):
    return Component(
        reference="U1", value="", footprint="",
        component_type=ComponentType.IC, component_subtype="ic.power.ldo",
        mpn="LDOX",
        pins={"1": "VIN", "2": "VOUT", "3": "GND"},
        specs=SimpleComponentSpecs(specs_type="ic", component_subtype="ic.power.ldo", values=values),
    )


def _mcu(iq=None):
    values = {} if iq is None else {"iq_a": iq}
    return Component(
        reference="U2", value="", footprint="",
        component_type=ComponentType.IC, mpn="MCU",
        pins={"1": "VOUT", "2": "GND"},
        specs=SimpleComponentSpecs(specs_type="ic", values=values) if values else None,
    )


def test_load_over_iout_max_is_ps_pwr_001():
    g = _graph(
        {
            "U1": _ldo({"i_load_a": 0.4, "iout_max_a": 0.5}),
            "U2": _mcu(0.2),
        },
        {
            "VIN": (NetType.POWER, 5.0, [("U1", "1")]),
            "VOUT": (NetType.POWER, 3.3, [("U1", "2"), ("U2", "1")]),
            "GND": (NetType.GROUND, 0.0, [("U1", "3"), ("U2", "2")]),
        },
    )
    findings = check_power_margin(g, _cons())
    assert any(f.rule_id == "PS-PWR-001" and f.designator == "U1" for f in findings)


def test_missing_iq_is_not_guessed_into_margin_fail():
    g = _graph(
        {
            "U1": _ldo({"iout_max_a": 0.1}),
            "U2": _mcu(None),
        },
        {
            "VIN": (NetType.POWER, 5.0, [("U1", "1")]),
            "VOUT": (NetType.POWER, 3.3, [("U1", "2"), ("U2", "1")]),
            "GND": (NetType.GROUND, 0.0, [("U1", "3"), ("U2", "2")]),
        },
    )
    assert check_power_margin(g, _cons()) == []


def test_series_r_ir_drop_uses_i_load_not_trace():
    r = Component(
        reference="R1", value="1", footprint="",
        component_type=ComponentType.RESISTOR, mpn="R1",
        pins={"1": "USB", "2": "VIN"},
        specs=ResistorSpecs(value_ohms=1.0, value_formatted="1"),
    )
    g = _graph(
        {"U1": _ldo({"i_load_a": 0.5, "iout_max_a": 1.0}), "R1": r},
        {
            "USB": (NetType.POWER, 5.0, [("R1", "1")]),
            "VIN": (NetType.POWER, 5.0, [("R1", "2"), ("U1", "1")]),
            "VOUT": (NetType.POWER, 3.3, [("U1", "2")]),
            "GND": (NetType.GROUND, 0.0, [("U1", "3")]),
        },
    )
    findings = check_power_margin(g, _cons())
    assert any(f.designator == "R1" and f.rule_id == "PS-PWR-001" for f in findings)


def test_no_series_r_does_not_invent_trace_drop():
    g = _graph(
        {"U1": _ldo({"i_load_a": 0.5, "iout_max_a": 1.0})},
        {
            "VIN": (NetType.POWER, 5.0, [("U1", "1")]),
            "VOUT": (NetType.POWER, 3.3, [("U1", "2")]),
            "GND": (NetType.GROUND, 0.0, [("U1", "3")]),
        },
    )
    assert check_power_margin(g, _cons()) == []
