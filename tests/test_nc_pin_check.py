"""NC pin connectivity check."""

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
from backend.pinscopex.nc_pin_check import check_nc_pins


def test_nc_pin_on_active_net_warns():
    g = DesignGraph(
        components={
            "U1": Component(
                reference="U1", value="IC", footprint="",
                component_type=ComponentType.IC, mpn="PART",
                pins={"1": "SIG", "2": "GND"},
            ),
            "R1": Component(
                reference="R1", value="10k", footprint="",
                component_type=ComponentType.RESISTOR,
                pins={"1": "SIG", "2": "GND"},
            ),
        },
        nets={
            "SIG": Net(
                name="SIG", net_type=NetType.SIGNAL,
                pins=[
                    PinConnection(component_ref="U1", pin_number="1"),
                    PinConnection(component_ref="R1", pin_number="1"),
                ],
            ),
            "GND": Net(
                name="GND", net_type=NetType.GROUND,
                pins=[
                    PinConnection(component_ref="U1", pin_number="2"),
                    PinConnection(component_ref="R1", pin_number="2"),
                ],
            ),
        },
    )
    cmap = {
        "PART": ComponentConstraints(
            mpn="PART",
            pintable=[Pin(number="1", name="NC"), Pin(number="2", name="GND")],
            absolute_maximum_ratings=[],
            rules=[],
        ),
    }
    findings = check_nc_pins(g, cmap)
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-NC-001"
    assert findings[0].net == "SIG"


def test_nc_pin_on_nc_net_silent():
    g = DesignGraph(
        components={
            "U1": Component(
                reference="U1", value="IC", footprint="",
                component_type=ComponentType.IC, mpn="PART",
                pins={"1": "NC"},
            ),
        },
        nets={
            "NC": Net(
                name="NC", net_type=NetType.UNKNOWN,
                pins=[PinConnection(component_ref="U1", pin_number="1")],
            ),
        },
    )
    cmap = {
        "PART": ComponentConstraints(
            mpn="PART",
            pintable=[Pin(number="1", name="NC")],
            absolute_maximum_ratings=[],
            rules=[],
        ),
    }
    assert check_nc_pins(g, cmap) == []


def test_no_pintable_silent():
    g = DesignGraph(
        components={
            "U1": Component(
                reference="U1", value="IC", footprint="",
                component_type=ComponentType.IC, mpn="PART",
                pins={"1": "SIG"},
            ),
        },
        nets={},
    )
    assert check_nc_pins(g, {}) == []
