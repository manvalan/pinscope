"""Layout F1 functional_groups on simple_project — topology only, no mm."""

from __future__ import annotations

import json
from pathlib import Path

from backend.periscopex.functional_groups import build_functional_groups
from backend.periscopex.models import DesignGraph

SIMPLE = Path(__file__).resolve().parents[1] / "simple_project"


def _graph() -> DesignGraph:
    return DesignGraph.model_validate_json(
        (SIMPLE / "design_graph.json").read_text(encoding="utf-8"),
    )


def test_simple_project_has_mcu_ldo_bridge_groups():
    report = build_functional_groups(_graph())
    assert report.objective == "routing"
    refs = {g.ref for g in report.groups}
    assert {"U1", "U2", "U3"} <= refs
    # No millimetre fields on the report model dump
    raw = json.loads(report.model_dump_json())
    blob = json.dumps(raw)
    assert "max_distance_mm" not in blob or all(
        r.get("max_distance_mm") is None
        for g in raw["groups"]
        for r in g.get("layout_rules") or []
    )


def test_u3_owns_crystal_load_caps():
    report = build_functional_groups(_graph())
    u3 = next(g for g in report.groups if g.ref == "U3")
    sat = {s.ref: s.role_hint for s in u3.satellites}
    assert "X1" in sat
    assert sat["X1"] == "crystal"
    assert sat.get("C9") == "load_cap" or sat.get("C10") == "load_cap"
    assert "C9" in sat and "C10" in sat
    assert sat["C9"] == "load_cap"
    assert sat["C10"] == "load_cap"


def test_u1_has_decoupling_or_bulk_on_rails():
    report = build_functional_groups(_graph())
    u1 = next(g for g in report.groups if g.ref == "U1")
    roles = {s.role_hint for s in u1.satellites}
    assert roles & {"decoupling", "bulk"}


def test_domains_cover_all_ics():
    report = build_functional_groups(_graph())
    covered = {r for d in report.domains for r in d.ic_refs}
    assert covered == {"U1", "U2", "U3"}
    assert all(d.assemble_order for d in report.domains)


def test_simple_project_splits_5v_and_3v3_domains():
    """LDO bridges +5V/+3V3 electrically but domains follow primary rails."""
    report = build_functional_groups(_graph())
    by_rail = {d.power_nets[0]: set(d.ic_refs) for d in report.domains if d.power_nets}
    assert by_rail.get("+5V") == {"U2"}
    assert by_rail.get("+3V3") == {"U1", "U3"}
    assert len(report.domains) == 2


def test_ldo_power_satellites_stay_on_primary_rail():
    """LDO input-rail caps must not appear as primary-rail satellites."""
    from backend.periscopex.models import (
        CapacitorSpecs,
        Component,
        ComponentType,
        DesignGraph,
        InductorSpecs,
        Net,
        NetType,
        PinConnection,
    )

    components = {
        "U1": Component(
            reference="U1", value="LDO", footprint="",
            component_type=ComponentType.IC,
            component_subtype="ic.power.ldo",
            pins={"1": "VSYS", "2": "3V3_DIGITAL", "3": "GND", "4": "EN"},
        ),
        "C_in": Component(
            reference="C_in", value="10u", footprint="",
            component_type=ComponentType.CAPACITOR,
            pins={"1": "VSYS", "2": "GND"},
            specs=CapacitorSpecs(value_farads=10e-6, value_formatted="10uF"),
        ),
        "L1": Component(
            reference="L1", value="2.2u", footprint="",
            component_type=ComponentType.INDUCTOR,
            pins={"1": "VSYS", "2": "VSYS"},
            specs=InductorSpecs(value_henries=2.2e-6, value_formatted="2.2uH"),
        ),
        "C_out": Component(
            reference="C_out", value="100n", footprint="",
            component_type=ComponentType.CAPACITOR,
            pins={"1": "3V3_DIGITAL", "2": "GND"},
            specs=CapacitorSpecs(value_farads=100e-9, value_formatted="100nF"),
        ),
        "C_en": Component(
            reference="C_en", value="1u", footprint="",
            component_type=ComponentType.CAPACITOR,
            pins={"1": "EN", "2": "GND"},
            specs=CapacitorSpecs(value_farads=1e-6, value_formatted="1uF"),
        ),
        "C_boot": Component(
            reference="C_boot", value="47n", footprint="",
            component_type=ComponentType.CAPACITOR,
            pins={"1": "BTST", "2": "SW"},
            specs=CapacitorSpecs(value_farads=47e-9, value_formatted="47nF"),
        ),
        "J1": Component(
            reference="J1", value="USB", footprint="",
            component_type=ComponentType.CONNECTOR,
            pins={"1": "3V3_DIGITAL", "2": "GND"},
        ),
        "C_noise": Component(
            reference="C_noise", value="100n", footprint="",
            component_type=ComponentType.CAPACITOR,
            pins={"1": "orphan", "2": "somewhere"},
        ),
    }
    # Extend U1 pins for bootstrap
    components["U1"].pins["5"] = "BTST"
    components["U1"].pins["6"] = "SW"
    nets = {
        "VSYS": Net(
            name="VSYS", net_type=NetType.POWER,
            pins=[
                PinConnection(component_ref="U1", pin_number="1"),
                PinConnection(component_ref="C_in", pin_number="1"),
                PinConnection(component_ref="L1", pin_number="1"),
            ],
        ),
        "3V3_DIGITAL": Net(
            name="3V3_DIGITAL", net_type=NetType.POWER,
            pins=[
                PinConnection(component_ref="U1", pin_number="2"),
                PinConnection(component_ref="C_out", pin_number="1"),
                PinConnection(component_ref="J1", pin_number="1"),
            ],
        ),
        "EN": Net(
            name="EN", net_type=NetType.SIGNAL,
            pins=[
                PinConnection(component_ref="U1", pin_number="4"),
                PinConnection(component_ref="C_en", pin_number="1"),
            ],
        ),
        "BTST": Net(
            name="BTST", net_type=NetType.SIGNAL,
            pins=[
                PinConnection(component_ref="U1", pin_number="5"),
                PinConnection(component_ref="C_boot", pin_number="1"),
            ],
        ),
        "SW": Net(
            name="SW", net_type=NetType.SIGNAL,
            pins=[
                PinConnection(component_ref="U1", pin_number="6"),
                PinConnection(component_ref="C_boot", pin_number="2"),
            ],
        ),
        "GND": Net(
            name="GND", net_type=NetType.GROUND,
            pins=[
                PinConnection(component_ref="U1", pin_number="3"),
                PinConnection(component_ref="C_in", pin_number="2"),
                PinConnection(component_ref="C_out", pin_number="2"),
                PinConnection(component_ref="C_en", pin_number="2"),
                PinConnection(component_ref="J1", pin_number="2"),
            ],
        ),
        "orphan": Net(
            name="orphan", net_type=NetType.SIGNAL,
            pins=[PinConnection(component_ref="C_noise", pin_number="1")],
        ),
        "somewhere": Net(
            name="somewhere", net_type=NetType.SIGNAL,
            pins=[PinConnection(component_ref="C_noise", pin_number="2")],
        ),
    }
    report = build_functional_groups(DesignGraph(components=components, nets=nets))
    u1 = next(g for g in report.groups if g.ref == "U1")
    sat = {s.ref: s.role_hint for s in u1.satellites}
    assert sat.get("C_out") == "decoupling"
    assert sat.get("C_en") == "bulk"
    assert sat.get("C_boot") == "bridge"
    assert "C_in" not in sat
    assert "L1" not in sat
    assert "J1" not in sat
    assert "C_noise" not in sat
    assert "other" not in sat.values()


def test_multi_rail_board_does_not_collapse_to_one_domain():
    """Charger→LDO→MCU must not become a single domain via shared POWER nets."""
    from backend.periscopex.models import (
        Component,
        ComponentType,
        DesignGraph,
        Net,
        NetType,
        PinConnection,
    )

    components = {
        "U1": Component(
            reference="U1", value="BQ25896", footprint="",
            component_type=ComponentType.IC,
            component_subtype="ic.power.battery_charger",
            pins={"1": "VBUS", "2": "VSYS", "3": "GND"},
        ),
        "U2": Component(
            reference="U2", value="AP2112", footprint="",
            component_type=ComponentType.IC,
            component_subtype="ic.power.ldo",
            pins={"1": "VSYS", "2": "3V3_DIGITAL", "3": "GND"},
        ),
        "U3": Component(
            reference="U3", value="ESP32", footprint="",
            component_type=ComponentType.IC,
            component_subtype="ic.rf.wifi_module",
            pins={"1": "3V3_DIGITAL", "2": "GND"},
        ),
        "U4": Component(
            reference="U4", value="SRV05", footprint="",
            component_type=ComponentType.IC,
            component_subtype="ic.protection.esd",
            pins={"1": "VBUS", "2": "GND"},
        ),
    }
    nets = {
        "VBUS": Net(
            name="VBUS", net_type=NetType.POWER,
            pins=[
                PinConnection(component_ref="U1", pin_number="1"),
                PinConnection(component_ref="U4", pin_number="1"),
            ],
        ),
        "VSYS": Net(
            name="VSYS", net_type=NetType.POWER,
            pins=[
                PinConnection(component_ref="U1", pin_number="2"),
                PinConnection(component_ref="U2", pin_number="1"),
            ],
        ),
        "3V3_DIGITAL": Net(
            name="3V3_DIGITAL", net_type=NetType.POWER,
            pins=[
                PinConnection(component_ref="U2", pin_number="2"),
                PinConnection(component_ref="U3", pin_number="1"),
            ],
        ),
        "GND": Net(
            name="GND", net_type=NetType.GROUND,
            pins=[
                PinConnection(component_ref="U1", pin_number="3"),
                PinConnection(component_ref="U2", pin_number="3"),
                PinConnection(component_ref="U3", pin_number="2"),
                PinConnection(component_ref="U4", pin_number="2"),
            ],
        ),
    }
    report = build_functional_groups(DesignGraph(components=components, nets=nets))
    by_rail = {d.power_nets[0]: set(d.ic_refs) for d in report.domains if d.power_nets}
    assert len(report.domains) >= 3
    assert by_rail.get("VBUS") == {"U4"}
    assert by_rail.get("VSYS") == {"U1"}
    assert by_rail.get("3V3_DIGITAL") == {"U2", "U3"}


def test_build_placement_plan_alias():
    from backend.periscopex.functional_groups import build_placement_plan
    report = build_placement_plan(_graph())
    assert report.objective == "routing"
    assert {g.ref for g in report.groups} >= {"U1", "U2", "U3"}

