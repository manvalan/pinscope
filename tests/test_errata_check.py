"""Errata catalog — workaround on the graph, no scraping.

Favor: known MPN with a pull-up workaround missing on the net → PS-ERRATA-001.
Against: MPN not in catalog is silent (even TI-looking); workaround pull-up
present is silent; catalog entry without url is skipped.
"""

from __future__ import annotations

from backend.pinscopex.errata_check import check_errata
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
)


def _cons():
    return {
        "ERRX": ComponentConstraints(
            mpn="ERRX",
            pintable=[Pin(number=1, name="NRST"), Pin(number=2, name="GND")],
            absolute_maximum_ratings=[], rules=[],
        )
    }


def _graph(with_pull: bool):
    u = Component(
        reference="U1", value="", footprint="",
        component_type=ComponentType.IC, mpn="ERRX",
        pins={"1": "NRST", "2": "GND"},
    )
    comps = {"U1": u}
    nets = {
        "NRST": (NetType.SIGNAL, [("U1", "1")]),
        "GND": (NetType.GROUND, [("U1", "2")]),
        "3V3": (NetType.POWER, []),
    }
    if with_pull:
        r = Component(
            reference="R1", value="10k", footprint="",
            component_type=ComponentType.RESISTOR, mpn="R1",
            pins={"1": "NRST", "2": "3V3"},
            specs=ResistorSpecs(value_ohms=10000, value_formatted="10k"),
        )
        comps["R1"] = r
        nets["NRST"] = (NetType.SIGNAL, [("U1", "1"), ("R1", "1")])
        nets["3V3"] = (NetType.POWER, [("R1", "2")])
    net_objs = {
        name: Net(
            name=name, net_type=ntype,
            pins=[PinConnection(component_ref=r, pin_number=str(p)) for r, p in conns],
        )
        for name, (ntype, conns) in nets.items()
    }
    return DesignGraph(components=comps, nets=net_objs)


CATALOG = {
    "ERRX": {
        "url": "https://www.ti.com/lit/er/fixture",
        "workarounds": [
            {"kind": "pullup", "pin_name": "NRST", "note": "10 kΩ to VDD per errata"},
        ],
    }
}


def test_missing_errata_pullup_is_ps_errata_001():
    findings = check_errata(_graph(False), _cons(), CATALOG)
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-ERRATA-001"
    assert findings[0].status == "WARNING"
    assert findings[0].source == "errata_check"
    assert "ti.com/lit/er" in findings[0].reference


def test_pullup_present_is_silent():
    assert check_errata(_graph(True), _cons(), CATALOG) == []


def test_unknown_mpn_and_url_less_entry_are_silent():
    g = _graph(False)
    g.components["U1"].mpn = "UNKNOWNPART"
    assert check_errata(g, _cons(), CATALOG) == []
    no_url = {"ERRX": {"url": "", "workarounds": [{"kind": "pullup", "pin_name": "NRST"}]}}
    assert check_errata(_graph(False), _cons(), no_url) == []
