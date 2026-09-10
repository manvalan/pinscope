"""DC-bias C_eff stima — not a Murata lot curve.

Favor: C0G stays at C; X7R at 50% Vr loses ~30%; C_eff formatted.
Against: tantalum uses C_nom (no MLCC model); missing Vr or C skips C_eff;
C0G is not treated as X7R.
"""

from __future__ import annotations

from backend.pinscopex.derating import (
    build_derating_table,
    dc_bias_remaining,
)
from backend.pinscopex.models import (
    CapacitorSpecs,
    Component,
    ComponentType,
    DesignGraph,
    Net,
    NetType,
    PinConnection,
)


def test_c0g_keeps_full_capacitance():
    assert dc_bias_remaining("C0G", v_op=16.0, rated_v=16.0) == 1.0
    assert dc_bias_remaining("NP0", v_op=10.0, rated_v=16.0) == 1.0


def test_x7r_at_half_rated_is_about_70_percent():
    f = dc_bias_remaining("X7R", v_op=8.0, rated_v=16.0)
    assert f is not None
    assert 0.65 <= f <= 0.75


def test_x7r_at_zero_bias_is_nominal():
    assert dc_bias_remaining("X7R", v_op=0.0, rated_v=16.0) == 1.0


def test_tantalum_has_no_mlcc_bias_model():
    assert dc_bias_remaining("tantalum", v_op=8.0, rated_v=16.0) is None


def test_missing_voltage_or_value_skips_c_eff():
    assert dc_bias_remaining("X7R", v_op=None, rated_v=16.0) is None
    assert dc_bias_remaining("X7R", v_op=8.0, rated_v=None) is None


def _cap(ref, dielectric, farads, rated, net="3V3"):
    return Component(
        reference=ref, value="", footprint="",
        component_type=ComponentType.CAPACITOR,
        component_subtype="passive.capacitor.ceramic",
        mpn=ref,
        pins={"1": net, "2": "GND"},
        specs=CapacitorSpecs(
            value_farads=farads,
            value_formatted="10uF",
            voltage_rating_v=f"{rated}V",
            dielectric=dielectric,
        ),
    )


def test_derating_row_includes_c_eff_stima():
    c1 = _cap("C1", "X7R", 10e-6, 16)
    g = DesignGraph(
        components={
            "C1": c1,
        },
        nets={
            "3V3": Net(
                name="3V3", net_type=NetType.POWER, voltage=8.0,
                pins=[PinConnection(component_ref="C1", pin_number="1")],
            ),
            "GND": Net(
                name="GND", net_type=NetType.GROUND, voltage=0.0,
                pins=[PinConnection(component_ref="C1", pin_number="2")],
            ),
        },
    )
    rows = build_derating_table(g)
    assert len(rows) == 1
    row = rows[0]
    assert row["dc_bias_model"] == "stima"
    assert row["c_nominal_f"] == 10e-6
    assert row["c_eff_f"] is not None
    assert 6.5e-6 <= row["c_eff_f"] <= 7.5e-6
    assert "uF" in (row["c_eff_formatted"] or "")


def test_c0g_row_c_eff_equals_nominal():
    c1 = _cap("C9", "C0G", 18e-12, 50)
    g = DesignGraph(
        components={"C9": c1},
        nets={
            "3V3": Net(
                name="3V3", net_type=NetType.POWER, voltage=3.3,
                pins=[PinConnection(component_ref="C9", pin_number="1")],
            ),
            "GND": Net(
                name="GND", net_type=NetType.GROUND,
                pins=[PinConnection(component_ref="C9", pin_number="2")],
            ),
        },
    )
    row = build_derating_table(g)[0]
    assert row["c_eff_f"] == 18e-12
    assert row["dc_bias_factor"] == 1.0
