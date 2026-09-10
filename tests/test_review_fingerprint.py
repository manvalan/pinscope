from backend.pinscopex.models import (
    Component,
    ComponentType,
    DesignGraph,
    Net,
    NetType,
    PinConnection,
)
from backend.pinscopex.review_fingerprint import (
    graph_ic_fingerprints,
    skip_unchanged_ics,
)


def _g():
    u1 = Component(
        reference="U1", value="", footprint="",
        component_type=ComponentType.IC, mpn="IC1",
        pins={"1": "3V3", "2": "SDA"},
    )
    r1 = Component(
        reference="R1", value="4k7", footprint="",
        component_type=ComponentType.RESISTOR, mpn="R",
        pins={"1": "SDA", "2": "3V3"},
    )
    return DesignGraph(
        components={"U1": u1, "R1": r1},
        nets={
            "3V3": Net(name="3V3", net_type=NetType.POWER, pins=[
                PinConnection(component_ref="U1", pin_number="1"),
                PinConnection(component_ref="R1", pin_number="2"),
            ]),
            "SDA": Net(name="SDA", net_type=NetType.SIGNAL, pins=[
                PinConnection(component_ref="U1", pin_number="2"),
                PinConnection(component_ref="R1", pin_number="1"),
            ]),
        },
    )


def test_fingerprint_changes_when_neighbor_added():
    g = _g()
    fp1 = graph_ic_fingerprints(g)["U1"]
    c2 = Component(
        reference="C1", value="100n", footprint="",
        component_type=ComponentType.CAPACITOR, mpn="C",
        pins={"1": "3V3", "2": "GND"},
    )
    g.components["C1"] = c2
    g.nets["3V3"].pins.append(PinConnection(component_ref="C1", pin_number="1"))
    fp2 = graph_ic_fingerprints(g)["U1"]
    assert fp1 != fp2


def test_skip_unchanged_drops_changed_refs():
    prev = {"U1": "aaa", "U2": "bbb"}
    cur = {"U1": "aaa", "U2": "ccc"}
    skip = skip_unchanged_ics({"U1", "U2"}, prev, cur)
    assert skip == {"U1"}
