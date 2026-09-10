"""HF coverage INFO when bulk C exists without a 100 nF-class ceramic."""

from backend.pinscopex.hf_coverage_check import check_hf_decoupling_coverage
from backend.pinscopex.models import (
    CapacitorSpecs,
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Net,
    NetType,
    Pin,
    PinConnection,
)


def _graph(components, nets):
    net_objs = {
        name: Net(
            name=name, net_type=ntype,
            pins=[PinConnection(component_ref=r, pin_number=str(p)) for r, p in conns],
        )
        for name, (ntype, conns) in nets.items()
    }
    return DesignGraph(components=components, nets=net_objs)


def _ic():
    return Component(
        reference="U1", value="", footprint="",
        component_type=ComponentType.IC, mpn="UTEST",
        pins={"1": "3V3", "2": "GND"},
    )


def _cmap():
    return {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=1, name="VDD"), Pin(number=2, name="GND")],
            absolute_maximum_ratings=[], rules=[],
        )
    }


def _cap(ref, farads, net="3V3"):
    return Component(
        reference=ref, value="", footprint="C_0603",
        component_type=ComponentType.CAPACITOR, mpn=ref,
        pins={"1": net, "2": "GND"},
        specs=CapacitorSpecs(value_farads=farads, value_formatted="x"),
    )


def test_bulk_only_is_info_ps_esr_001():
    g = _graph(
        {"U1": _ic(), "C1": _cap("C1", 10e-6)},
        {
            "3V3": (NetType.POWER, [("U1", "1"), ("C1", "1")]),
            "GND": (NetType.GROUND, [("U1", "2"), ("C1", "2")]),
        },
    )
    findings = check_hf_decoupling_coverage(g, _cmap())
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-ESR-001"
    assert findings[0].status == "INFO"


def test_bulk_plus_100n_is_silent():
    g = _graph(
        {"U1": _ic(), "C1": _cap("C1", 10e-6), "C2": _cap("C2", 100e-9)},
        {
            "3V3": (NetType.POWER, [("U1", "1"), ("C1", "1"), ("C2", "1")]),
            "GND": (NetType.GROUND, [("U1", "2"), ("C1", "2"), ("C2", "2")]),
        },
    )
    assert check_hf_decoupling_coverage(g, _cmap()) == []


def test_unknown_cap_value_is_not_guessed():
    c = Component(
        reference="C1", value="", footprint="",
        component_type=ComponentType.CAPACITOR, mpn="C1",
        pins={"1": "3V3", "2": "GND"},
    )
    g = _graph(
        {"U1": _ic(), "C1": c},
        {
            "3V3": (NetType.POWER, [("U1", "1"), ("C1", "1")]),
            "GND": (NetType.GROUND, [("U1", "2"), ("C1", "2")]),
        },
    )
    assert check_hf_decoupling_coverage(g, _cmap()) == []
