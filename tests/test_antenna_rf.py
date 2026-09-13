"""Antenna RF verify + design recipe — no invented EM."""

from __future__ import annotations

from backend.periscopex.antenna_rf import build_antenna_report, build_design_recipe
from backend.periscopex.models import (
    CapacitorSpecs,
    Component,
    ComponentType,
    DesignGraph,
    InductorSpecs,
    LayoutDielectric,
    LayoutFootprint,
    LayoutGraph,
    LayoutPad,
    LayoutStackup,
    LayoutZone,
    Net,
    NetType,
    PinConnection,
)


def _graph_with_pi_match():
    components = {
        "U1": Component(
            reference="U1", value="RFIC", footprint="",
            component_type=ComponentType.IC,
            component_subtype="ic.rf.wifi_module",
            pins={"1": "RF_ANT", "2": "GND"},
        ),
        "L1": Component(
            reference="L1", value="2.2n", footprint="",
            component_type=ComponentType.INDUCTOR,
            pins={"1": "RF_ANT", "2": "ANT_MID"},
            specs=InductorSpecs(value_henries=2.2e-9, value_formatted="2.2nH"),
        ),
        "C1": Component(
            reference="C1", value="1p", footprint="",
            component_type=ComponentType.CAPACITOR,
            pins={"1": "RF_ANT", "2": "GND"},
            specs=CapacitorSpecs(value_farads=1e-12, value_formatted="1pF"),
        ),
        "C2": Component(
            reference="C2", value="1p", footprint="",
            component_type=ComponentType.CAPACITOR,
            pins={"1": "ANT_MID", "2": "GND"},
            specs=CapacitorSpecs(value_farads=1e-12, value_formatted="1pF"),
        ),
        "ANT1": Component(
            reference="ANT1", value="PCB_ANT", footprint="",
            component_type=ComponentType.CONNECTOR,
            pins={"1": "ANT_MID", "2": "GND"},
        ),
    }
    nets = {
        "RF_ANT": Net(
            name="RF_ANT", net_type=NetType.SIGNAL,
            pins=[
                PinConnection(component_ref="U1", pin_number="1"),
                PinConnection(component_ref="L1", pin_number="1"),
                PinConnection(component_ref="C1", pin_number="1"),
            ],
        ),
        "ANT_MID": Net(
            name="ANT_MID", net_type=NetType.SIGNAL,
            pins=[
                PinConnection(component_ref="L1", pin_number="2"),
                PinConnection(component_ref="C2", pin_number="1"),
                PinConnection(component_ref="ANT1", pin_number="1"),
            ],
        ),
        "GND": Net(
            name="GND", net_type=NetType.GROUND,
            pins=[
                PinConnection(component_ref="U1", pin_number="2"),
                PinConnection(component_ref="C1", pin_number="2"),
                PinConnection(component_ref="C2", pin_number="2"),
                PinConnection(component_ref="ANT1", pin_number="2"),
            ],
        ),
    }
    return DesignGraph(components=components, nets=nets)


def test_verify_finds_matching_path_to_ant_footprint():
    report = build_antenna_report(_graph_with_pi_match())
    assert report.verify
    row = report.verify[0]
    assert row.ic_ref == "U1"
    assert row.topology in ("pi", "LC", "series_L", "unknown")
    assert "L1" in row.parts
    assert row.status == "ok"
    assert row.marker_ref == "ANT1"


def test_verify_missing_matching_is_warning():
    components = {
        "U1": Component(
            reference="U1", value="RFIC", footprint="",
            component_type=ComponentType.IC,
            component_subtype="ic.rf.transceiver",
            pins={"1": "RF_OUT", "2": "GND"},
        ),
    }
    nets = {
        "RF_OUT": Net(
            name="RF_OUT", net_type=NetType.SIGNAL,
            pins=[PinConnection(component_ref="U1", pin_number="1")],
        ),
        "GND": Net(
            name="GND", net_type=NetType.GROUND,
            pins=[PinConnection(component_ref="U1", pin_number="2")],
        ),
    }
    report = build_antenna_report(DesignGraph(components=components, nets=nets))
    assert report.verify
    assert report.verify[0].topology == "missing"
    assert report.verify[0].status == "warning"


def test_design_recipe_needs_marker_without_ant():
    g = DesignGraph(components={}, nets={})
    layout = LayoutGraph(
        stackup=LayoutStackup(
            copper_layers=["F.Cu", "B.Cu"],
            dielectrics=[LayoutDielectric(name="FR4", er=4.5, height_mm=0.2)],
            copper_thickness_mm=0.035,
        ),
    )
    recipe = build_design_recipe(g, layout, f0_mhz=2440.0)
    assert recipe.status == "need_marker"


def test_design_recipe_ready_with_ant_and_stackup():
    g = DesignGraph(
        components={
            "ANT1": Component(
                reference="ANT1", value="feed", footprint="",
                component_type=ComponentType.CONNECTOR,
                pins={"1": "ANT_FEED"},
            ),
        },
        nets={
            "ANT_FEED": Net(
                name="ANT_FEED", net_type=NetType.SIGNAL,
                pins=[PinConnection(component_ref="ANT1", pin_number="1")],
            ),
        },
    )
    layout = LayoutGraph(
        footprints={
            "ANT1": LayoutFootprint(
                reference="ANT1", x=10.0, y=20.0, layer="F.Cu",
                pads=[LayoutPad(number="1", x=10.0, y=20.0, net="ANT_FEED")],
            ),
        },
        nets={"ANT_FEED": 1, "antenna": 2},
        stackup=LayoutStackup(
            copper_layers=["F.Cu", "B.Cu"],
            dielectrics=[LayoutDielectric(name="FR4", er=4.5, height_mm=0.2)],
            copper_thickness_mm=0.035,
        ),
        zones=[
            LayoutZone(
                net="antenna",
                layer="F.Cu",
                outlines=[[(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]],
            ),
        ],
    )
    recipe = build_design_recipe(g, layout, f0_mhz=2440.0, target_z_ohm=50.0)
    assert recipe.status == "ready"
    assert recipe.feed_line is not None
    assert recipe.feed_line.w_mm is not None and recipe.feed_line.w_mm > 0
    assert recipe.radiator is not None
    assert recipe.radiator.length_mm_suggest is not None
    assert recipe.zone is not None
    assert recipe.zone.bbox_mm is not None
    assert recipe.geometry is not None
    assert recipe.geometry.template == "ifa"
    assert recipe.geometry.fit in ("ok", "scaled")
    assert len(recipe.geometry.segments) >= 2
    assert recipe.geometry.svg and "<svg" in recipe.geometry.svg
    assert recipe.geometry.kicad_mod and '(footprint "' in recipe.geometry.kicad_mod
    assert "fp_line" in recipe.geometry.kicad_mod


def test_geometry_templates_produce_export():
    from backend.periscopex.antenna_geometry import build_geometry

    for tmpl in ("ifa", "meander", "stub"):
        geo = build_geometry(tmpl, f0_mhz=2440.0, w_mm=0.4, er=4.5)
        assert geo.fit == "ok"
        assert geo.total_length_mm is not None and geo.total_length_mm > 10
        assert geo.length_ideal_mm is not None
        assert geo.total_length_mm >= geo.length_ideal_mm * 0.9
        assert geo.svg and "path" in geo.svg
        assert geo.kicad_mod and '(pad "1"' in geo.kicad_mod
        assert len(geo.segments) >= 1


def test_geometry_overflow_tiny_zone():
    from backend.periscopex.antenna_geometry import build_geometry

    geo = build_geometry(
        "ifa",
        f0_mhz=2440.0,
        w_mm=0.4,
        er=4.5,
        zone_bbox_mm=(0.0, 0.0, 2.0, 1.0),
        feed_xy=(0.0, 0.0),
    )
    assert geo.fit == "overflow"
    assert not geo.segments


def test_design_recipe_meander_template():
    g = DesignGraph(
        components={
            "ANT1": Component(
                reference="ANT1", value="feed", footprint="",
                component_type=ComponentType.CONNECTOR,
                pins={"1": "ANT_FEED"},
            ),
        },
        nets={
            "ANT_FEED": Net(
                name="ANT_FEED", net_type=NetType.SIGNAL,
                pins=[PinConnection(component_ref="ANT1", pin_number="1")],
            ),
        },
    )
    layout = LayoutGraph(
        footprints={
            "ANT1": LayoutFootprint(
                reference="ANT1", x=0.0, y=0.0, layer="F.Cu",
                pads=[LayoutPad(number="1", x=0.0, y=0.0, net="ANT_FEED")],
            ),
        },
        nets={"ANT_FEED": 1},
        stackup=LayoutStackup(
            copper_layers=["F.Cu", "B.Cu"],
            dielectrics=[LayoutDielectric(name="FR4", er=4.5, height_mm=0.2)],
            copper_thickness_mm=0.035,
        ),
    )
    recipe = build_design_recipe(
        g, layout, f0_mhz=2440.0, template="meander",
    )
    assert recipe.status == "ready"
    assert recipe.geometry is not None
    assert recipe.geometry.template == "meander"
    assert recipe.geometry.fit == "ok"
    assert recipe.geometry.kicad_mod is not None
