"""DNP variant: fitted enable without pull/driver is ERROR."""

from __future__ import annotations

from pathlib import Path

from backend.pinscopex.dnp_check import check_dnp_enables
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
from backend.pinscopex.parsers import parse_bom


def _graph(components, nets, bom_fields=None):
    net_objs = {
        name: Net(
            name=name, net_type=ntype,
            pins=[PinConnection(component_ref=r, pin_number=str(p)) for r, p in conns],
        )
        for name, (ntype, conns) in nets.items()
    }
    return DesignGraph(components=components, nets=net_objs, bom_fields=bom_fields or {})


def _cons():
    return {
        "LDOX": ComponentConstraints(
            mpn="LDOX",
            pintable=[
                Pin(number=1, name="VIN"),
                Pin(number=2, name="VOUT"),
                Pin(number=3, name="EN"),
                Pin(number=4, name="GND"),
            ],
            absolute_maximum_ratings=[], rules=[],
        )
    }


def _ldo():
    return Component(
        reference="U1", value="", footprint="",
        component_type=ComponentType.IC, mpn="LDOX",
        pins={"1": "VIN", "2": "VOUT", "3": "EN_NET", "4": "GND"},
    )


def _r(dnp_net="EN_NET"):
    return Component(
        reference="R1", value="10k", footprint="",
        component_type=ComponentType.RESISTOR, mpn="R1",
        pins={"1": dnp_net, "2": "VIN"},
        specs=ResistorSpecs(value_ohms=10000, value_formatted="10k"),
    )


def test_dnp_pull_leaves_enable_floating():
    g = _graph(
        {"U1": _ldo(), "R1": _r()},
        {
            "VIN": (NetType.POWER, [("U1", "1"), ("R1", "2")]),
            "VOUT": (NetType.POWER, [("U1", "2")]),
            "EN_NET": (NetType.SIGNAL, [("U1", "3"), ("R1", "1")]),
            "GND": (NetType.GROUND, [("U1", "4")]),
        },
        bom_fields={
            "U1": {"mpn": "LDOX", "value": "", "dnp": False},
            "R1": {"mpn": "R1", "value": "10k", "dnp": True},
        },
    )
    findings = check_dnp_enables(g, _cons())
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-DNP-001"
    assert findings[0].status == "ERROR"


def test_fitted_pull_is_silent():
    g = _graph(
        {"U1": _ldo(), "R1": _r()},
        {
            "VIN": (NetType.POWER, [("U1", "1"), ("R1", "2")]),
            "VOUT": (NetType.POWER, [("U1", "2")]),
            "EN_NET": (NetType.SIGNAL, [("U1", "3"), ("R1", "1")]),
            "GND": (NetType.GROUND, [("U1", "4")]),
        },
        bom_fields={
            "U1": {"mpn": "LDOX", "value": "", "dnp": False},
            "R1": {"mpn": "R1", "value": "10k", "dnp": False},
        },
    )
    assert check_dnp_enables(g, _cons()) == []


def test_no_dnp_column_skips_floating_enable():
    g = _graph(
        {"U1": _ldo()},
        {
            "VIN": (NetType.POWER, [("U1", "1")]),
            "VOUT": (NetType.POWER, [("U1", "2")]),
            "EN_NET": (NetType.SIGNAL, [("U1", "3")]),
            "GND": (NetType.GROUND, [("U1", "4")]),
        },
        bom_fields={"U1": {"mpn": "LDOX", "value": ""}},
    )
    assert check_dnp_enables(g, _cons()) == []


def test_parse_bom_reads_dnp_and_skips_when_column_absent(tmp_path: Path):
    with_dnp = tmp_path / "dnp.csv"
    with_dnp.write_text(
        "Reference,Value,DNP,Manufacturer Part Number\n"
        "U1,LDO,,LDOX\n"
        "R1,10k,1,Rpull\n"
    )
    bom = parse_bom(with_dnp)
    assert bom["U1"]["dnp"] is False
    assert bom["R1"]["dnp"] is True

    no_col = tmp_path / "plain.csv"
    no_col.write_text("Reference,Value,Manufacturer Part Number\nU1,LDO,LDOX\n")
    bom2 = parse_bom(no_col)
    assert "dnp" not in bom2["U1"]
