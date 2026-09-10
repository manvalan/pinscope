"""Filter topology matcher — RC/LC/π/T fc, ADC compare only with specs."""

from __future__ import annotations

from backend.pinscopex.filter_check import check_filters
from backend.pinscopex.models import (
    CapacitorSpecs,
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    InductorSpecs,
    Net,
    NetType,
    Pin,
    PinConnection,
    ResistorSpecs,
    SimpleComponentSpecs,
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


def _res(ref, ohms, n1, n2):
    return Component(
        reference=ref, value=str(ohms), footprint="",
        component_type=ComponentType.RESISTOR, mpn=ref,
        pins={"1": n1, "2": n2},
        specs=ResistorSpecs(value_ohms=ohms, value_formatted=str(ohms)),
    )


def _cap(ref, farads, net):
    return Component(
        reference=ref, value="", footprint="",
        component_type=ComponentType.CAPACITOR, mpn=ref,
        pins={"1": net, "2": "GND"},
        specs=CapacitorSpecs(value_farads=farads, value_formatted="x"),
    )


def _l(ref, henries, n1, n2, ferrite=False, dcr=None):
    sub = "passive.ferrite_bead" if ferrite else "passive.inductor"
    kwargs = dict(value_formatted="x", component_subtype=sub, dcr_ohms=dcr)
    if ferrite:
        specs = InductorSpecs(impedance_ohm=100.0, value_henries=henries, **kwargs)
    else:
        specs = InductorSpecs(value_henries=henries, **kwargs)
    return Component(
        reference=ref, value="", footprint="",
        component_type=ComponentType.INDUCTOR, mpn=ref,
        component_subtype=sub,
        pins={"1": n1, "2": n2}, specs=specs,
    )


def _ic(ref="U1", pins=None, values=None, mpn="UTEST"):
    return Component(
        reference=ref, value="", footprint="",
        component_type=ComponentType.IC, mpn=mpn,
        pins=pins or {"1": "AIN", "2": "GND"},
        specs=SimpleComponentSpecs(
            specs_type="ic", values=values or {},
        ) if values is not None else None,
    )


def test_rc_reports_fc_info_without_adc_rate():
    # 1k * 100nF -> fc ≈ 1.59 kHz; no sample rate → INFO not WARNING.
    g = _graph(
        {
            "U1": _ic(),
            "R1": _res("R1", 1e3, "AIN", "FILT"),
            "C1": _cap("C1", 100e-9, "FILT"),
        },
        {
            "AIN": (NetType.SIGNAL, [("U1", "1"), ("R1", "1")]),
            "FILT": (NetType.SIGNAL, [("R1", "2"), ("C1", "1")]),
            "GND": (NetType.GROUND, [("U1", "2"), ("C1", "2")]),
        },
    )
    findings = check_filters(g)
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-FLT-001"
    assert findings[0].status == "INFO"
    assert findings[0].source == "filter_check"


def test_rc_vs_adc_rate_is_warning():
    g = _graph(
        {
            "U1": _ic(values={"adc_sample_rate": 1e6}),
            "R1": _res("R1", 1e3, "AIN", "FILT"),
            "C1": _cap("C1", 100e-9, "FILT"),
        },
        {
            "AIN": (NetType.SIGNAL, [("U1", "1"), ("R1", "1")]),
            "FILT": (NetType.SIGNAL, [("R1", "2"), ("C1", "1")]),
            "GND": (NetType.GROUND, [("U1", "2"), ("C1", "2")]),
        },
    )
    findings = check_filters(g)
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-FLT-002"
    assert findings[0].status == "WARNING"


def test_pullup_plus_decoupling_is_not_a_filter():
    g = _graph(
        {
            "U1": Component(
                reference="U1", value="", footprint="",
                component_type=ComponentType.IC, mpn="UTEST",
                pins={"1": "SDA", "2": "3V3", "3": "GND"},
            ),
            "R1": _res("R1", 4700, "SDA", "3V3"),
            "C1": _cap("C1", 100e-9, "3V3"),
        },
        {
            "SDA": (NetType.SIGNAL, [("U1", "1"), ("R1", "1")]),
            "3V3": (NetType.POWER, [("U1", "2"), ("R1", "2"), ("C1", "1")]),
            "GND": (NetType.GROUND, [("U1", "3"), ("C1", "2")]),
        },
    )
    assert check_filters(g) == []


def test_missing_c_value_does_not_invent_fc_warning():
    c = Component(
        reference="C1", value="", footprint="",
        component_type=ComponentType.CAPACITOR, mpn="C1",
        pins={"1": "FILT", "2": "GND"},
    )
    g = _graph(
        {
            "U1": _ic(values={"adc_sample_rate": 1e6}),
            "R1": _res("R1", 1e3, "AIN", "FILT"),
            "C1": c,
        },
        {
            "AIN": (NetType.SIGNAL, [("U1", "1"), ("R1", "1")]),
            "FILT": (NetType.SIGNAL, [("R1", "2"), ("C1", "1")]),
            "GND": (NetType.GROUND, [("U1", "2"), ("C1", "2")]),
        },
    )
    findings = check_filters(g)
    assert len(findings) == 1
    assert findings[0].rule_id == "PS-FLT-001"
    assert findings[0].status == "INFO"


def test_pi_and_t_need_l_and_c():
    g_pi = _graph(
        {
            "L1": _l("L1", 10e-6, "A", "B"),
            "C1": _cap("C1", 100e-9, "A"),
            "C2": _cap("C2", 100e-9, "B"),
        },
        {
            "A": (NetType.SIGNAL, [("L1", "1"), ("C1", "1")]),
            "B": (NetType.SIGNAL, [("L1", "2"), ("C2", "1")]),
            "GND": (NetType.GROUND, [("C1", "2"), ("C2", "2")]),
        },
    )
    pi = check_filters(g_pi)
    assert len(pi) == 1 and pi[0].rule_id == "PS-FLT-001" and "π" in pi[0].finding

    g_t = _graph(
        {
            "L1": _l("L1", 10e-6, "A", "MID"),
            "L2": _l("L2", 10e-6, "MID", "B"),
            "C1": _cap("C1", 100e-9, "MID"),
        },
        {
            "A": (NetType.SIGNAL, [("L1", "1")]),
            "MID": (NetType.SIGNAL, [("L1", "2"), ("L2", "1"), ("C1", "1")]),
            "B": (NetType.SIGNAL, [("L2", "2")]),
            "GND": (NetType.GROUND, [("C1", "2")]),
        },
    )
    t = check_filters(g_t)
    assert len(t) == 1 and "T" in t[0].finding


def test_ferrite_dcr_warns_only_with_datasheet_limit():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=1, name="VDDA"), Pin(number=2, name="GND")],
            absolute_maximum_ratings=[], rules=[],
        )
    }
    fb = _l("FB1", None, "VDDA", "3V3", ferrite=True, dcr=2.0)
    g = _graph(
        {
            "U1": _ic(pins={"1": "VDDA", "2": "GND"}, values={}),
            "FB1": fb,
            "C1": _cap("C1", 100e-9, "VDDA"),
        },
        {
            "VDDA": (NetType.POWER, [("U1", "1"), ("FB1", "1"), ("C1", "1")]),
            "3V3": (NetType.POWER, [("FB1", "2")]),
            "GND": (NetType.GROUND, [("U1", "2"), ("C1", "2")]),
        },
    )
    assert not any(f.rule_id == "PS-FLT-003" for f in check_filters(g, cons))

    g2 = _graph(
        {
            "U1": _ic(pins={"1": "VDDA", "2": "GND"}, values={"max_ferrite_dcr_ohms": 0.5}),
            "FB1": fb,
            "C1": _cap("C1", 100e-9, "VDDA"),
        },
        {
            "VDDA": (NetType.POWER, [("U1", "1"), ("FB1", "1"), ("C1", "1")]),
            "3V3": (NetType.POWER, [("FB1", "2")]),
            "GND": (NetType.GROUND, [("U1", "2"), ("C1", "2")]),
        },
    )
    dcr = [f for f in check_filters(g2, cons) if f.rule_id == "PS-FLT-003"]
    assert len(dcr) == 1 and dcr[0].status == "WARNING"
