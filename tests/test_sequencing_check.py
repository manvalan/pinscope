"""PG→EN sequencing only when power_sequence is in IC specs."""

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
    SimpleComponentSpecs,
)
from backend.pinscopex.sequencing_check import check_power_sequencing


def _graph(components, nets):
    net_objs = {
        name: Net(
            name=name, net_type=ntype,
            pins=[PinConnection(component_ref=r, pin_number=str(p)) for r, p in conns],
        )
        for name, (ntype, conns) in nets.items()
    }
    return DesignGraph(components=components, nets=net_objs)


def _cons():
    return {
        "L1": ComponentConstraints(
            mpn="L1", component_subtype="ic.power.ldo",
            pintable=[
                Pin(number=1, name="VIN"), Pin(number=2, name="VOUT"),
                Pin(number=3, name="PG"), Pin(number=4, name="GND"),
            ],
            absolute_maximum_ratings=[], rules=[],
        ),
        "L2": ComponentConstraints(
            mpn="L2", component_subtype="ic.power.ldo",
            pintable=[
                Pin(number=1, name="VIN"), Pin(number=2, name="VOUT"),
                Pin(number=3, name="EN"), Pin(number=4, name="GND"),
            ],
            absolute_maximum_ratings=[], rules=[],
        ),
    }


def _ldo(ref, mpn, pins, values=None):
    return Component(
        reference=ref, value="", footprint="",
        component_type=ComponentType.IC, component_subtype="ic.power.ldo",
        mpn=mpn, pins=pins,
        specs=SimpleComponentSpecs(
            specs_type="ic", component_subtype="ic.power.ldo", values=values or {},
        ),
    )


def _dual(pg_net, en_net, sequence=True):
    u1 = _ldo("U1", "L1", {"1": "5V", "2": "3V3", "3": pg_net, "4": "GND"})
    vals = {"power_sequence": "pg_before_en"} if sequence else {}
    u2 = _ldo("U2", "L2", {"1": "3V3", "2": "1V8", "3": en_net, "4": "GND"}, vals)
    return _graph(
        {"U1": u1, "U2": u2},
        {
            "5V": (NetType.POWER, [("U1", "1")]),
            "3V3": (NetType.POWER, [("U1", "2"), ("U2", "1")]),
            "1V8": (NetType.POWER, [("U2", "2")]),
            pg_net: (NetType.SIGNAL, [("U1", "3")] + ([("U2", "3")] if pg_net == en_net else [])),
            **({en_net: (NetType.SIGNAL, [("U2", "3")])} if pg_net != en_net else {}),
            "GND": (NetType.GROUND, [("U1", "4"), ("U2", "4")]),
        },
    )


def test_pg_not_tied_to_en_is_warning():
    findings = check_power_sequencing(_dual("PGOOD", "EN_1V8"), _cons())
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-SEQ-001"
    assert findings[0].status == "WARNING"


def test_pg_tied_to_en_is_silent():
    assert check_power_sequencing(_dual("SEQ", "SEQ"), _cons()) == []


def test_no_power_sequence_spec_skips_even_if_pg_open():
    assert check_power_sequencing(_dual("PGOOD", "EN_1V8", sequence=False), _cons()) == []
