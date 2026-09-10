"""Internal features (block-diagram extraction) — open-drain pull-up.

Favor: pin listed in pullup_pins with no resistor to a rail → PS-INT-001.
Against: empty internal_features is silent; listed pin with a pull-up is
silent; a pin not in pullup_pins is not guessed as open-drain.
"""

from __future__ import annotations

from backend.pinscopex.internal_features_check import check_internal_features
from backend.pinscopex.models import (
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    InternalFeatures,
    Net,
    NetType,
    Pin,
    PinConnection,
    ResistorSpecs,
)


def _graph(with_pull: bool):
    u = Component(
        reference="U1", value="", footprint="",
        component_type=ComponentType.IC, mpn="UTEST",
        pins={"1": "SDA", "2": "GND"},
    )
    comps = {"U1": u}
    nets = {
        "SDA": (NetType.SIGNAL, [("U1", "1")]),
        "GND": (NetType.GROUND, [("U1", "2")]),
        "3V3": (NetType.POWER, []),
    }
    if with_pull:
        r = Component(
            reference="R1", value="4k7", footprint="",
            component_type=ComponentType.RESISTOR, mpn="R1",
            pins={"1": "SDA", "2": "3V3"},
            specs=ResistorSpecs(value_ohms=4700, value_formatted="4k7"),
        )
        comps["R1"] = r
        nets["SDA"] = (NetType.SIGNAL, [("U1", "1"), ("R1", "1")])
        nets["3V3"] = (NetType.POWER, [("R1", "2")])
    net_objs = {
        name: Net(
            name=name, net_type=ntype,
            pins=[PinConnection(component_ref=a, pin_number=str(b)) for a, b in conns],
        )
        for name, (ntype, conns) in nets.items()
    }
    return DesignGraph(components=comps, nets=net_objs)


def _cons(features: InternalFeatures | None):
    return {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=1, name="SDA"), Pin(number=2, name="GND")],
            absolute_maximum_ratings=[], rules=[],
            internal_features=features,
        )
    }


def test_listed_open_drain_without_pull_is_warning():
    feats = InternalFeatures(pullup_pins=["SDA"])
    findings = check_internal_features(_graph(False), _cons(feats))
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-INT-001"
    assert findings[0].status == "WARNING"


def test_listed_pin_with_pullup_is_silent():
    feats = InternalFeatures(pullup_pins=["SDA"])
    assert check_internal_features(_graph(True), _cons(feats)) == []


def test_empty_features_does_not_guess_open_drain():
    assert check_internal_features(_graph(False), _cons(None)) == []
    assert check_internal_features(_graph(False), _cons(InternalFeatures())) == []
