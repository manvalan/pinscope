"""D1 impedance — ImpedenceFinder closed forms, no second formula set.

Favor: Pinscope Z0 equals vendored ImpedenceFinder bit-for-bit; classic
3 mm / 1.6 mm FR4 is ~50 Ω; solve_width round-trips.
Against: h<=0 invents nothing; CPWG stays unimplemented; stripline t=0
raises; calculator emits no findings. OpenEMS is not imported.
"""

from __future__ import annotations

import pytest

from impedancefinder import zsolver as ifz
from backend.pinscopex.impedance import (
    GeometryError,
    TraceGeometry,
    coupled_diff_z,
    cpw_z0,
    export_kicad_dru,
    microstrip_z0,
    solve_width,
    stackup_targets,
    stripline_z0,
)


def test_microstrip_matches_impedancefinder_bit_for_bit():
    geo = TraceGeometry(h=0.15, er=4.3, t=0.035, w=0.30)
    ours = microstrip_z0(geo)
    theirs = ifz.microstrip_z0(0.30, 0.15, 4.3, 0.035)
    assert ours == theirs


def test_classic_fr4_50ohm_rule_of_thumb():
    z = microstrip_z0(TraceGeometry(h=1.6, er=4.5, t=0.035, w=3.0))
    assert z == pytest.approx(50.0, rel=0.05)


def test_microstrip_zero_height_does_not_invent_z():
    with pytest.raises(GeometryError):
        microstrip_z0(TraceGeometry(h=0.0, er=4.5, t=0.035, w=0.35))


def test_stripline_matches_impedancefinder():
    geo = TraceGeometry(h=0.5, er=4.4, t=0.035, w=0.15)
    assert stripline_z0(geo) == ifz.stripline_z0(0.15, 0.5, 4.4, 0.035)


def test_stripline_zero_thickness_does_not_invent_z():
    with pytest.raises(GeometryError):
        stripline_z0(TraceGeometry(h=0.4, er=4.5, t=0.0, w=0.12))


def test_stripline_missing_width_is_invalid():
    with pytest.raises(GeometryError):
        stripline_z0(TraceGeometry(h=0.4, er=4.5, t=0.035, w=None))


def test_diff_matches_impedancefinder():
    geo = TraceGeometry(h=0.15, er=4.3, t=0.035, w=0.20, s=0.20)
    _, _, zdiff = coupled_diff_z(geo)
    assert zdiff == ifz.diff_microstrip_z0(0.20, 0.15, 0.20, 4.3, 0.035)


def test_wider_gap_raises_zdiff():
    tight = coupled_diff_z(TraceGeometry(h=0.15, er=4.3, t=0.035, w=0.20, s=0.08))
    loose = coupled_diff_z(TraceGeometry(h=0.15, er=4.3, t=0.035, w=0.20, s=0.40))
    assert loose[2] > tight[2]


def test_coupled_diff_without_gap_is_invalid():
    with pytest.raises(GeometryError):
        coupled_diff_z(TraceGeometry(h=0.15, er=4.3, t=0.035, w=0.20, s=None))


def test_cpwg_is_not_invented():
    with pytest.raises(GeometryError, match="not implemented"):
        cpw_z0(TraceGeometry(h=0.15, er=4.3, t=0.035, w=0.20, s=0.15))


def test_solve_width_roundtrips_50_ohm_microstrip():
    w = solve_width("microstrip", target_z=50.0, h=1.6, er=4.5, t=0.035)
    z = microstrip_z0(TraceGeometry(h=1.6, er=4.5, t=0.035, w=w))
    assert z == pytest.approx(50.0, rel=0.01)
    assert w > 0


def test_solve_width_rejects_non_positive_target():
    with pytest.raises(GeometryError):
        solve_width("microstrip", target_z=0.0, h=1.6, er=4.5, t=0.035)


def test_stackup_suggests_50_90_100_without_findings():
    out = stackup_targets(h=0.20, er=4.5, t=0.035, s=0.20)
    assert out["microstrip_50"].z0 == pytest.approx(50.0, rel=0.02)
    assert out["diff_90"].zdiff == pytest.approx(90.0, rel=0.02)
    assert out["diff_100"].zdiff == pytest.approx(100.0, rel=0.02)
    assert "finding" not in out


def test_stackup_rejects_non_positive_h():
    with pytest.raises(GeometryError):
        stackup_targets(h=0.0, er=4.5, t=0.035, s=0.2)


def test_kicad_dru_is_advice_not_a_finding():
    dru = export_kicad_dru(stackup_targets(h=0.20, er=4.5, t=0.035, s=0.20))
    assert "(rule PINSCOPE_50OHM" in dru
    assert "PS-Z" not in dru


def _impedance_client():
    from fastapi.testclient import TestClient
    from backend.main import app

    return TestClient(app)


def test_api_microstrip_equals_impedancefinder():
    res = _impedance_client().post("/api/impedance", json={
        "mode": "trace",
        "kind": "microstrip",
        "h": 1.6, "er": 4.5, "t": 0.035, "w": 3.0,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["z0"] == ifz.microstrip_z0(3.0, 1.6, 4.5, 0.035)
    assert "findings" not in body


def test_api_zero_height_is_400():
    res = _impedance_client().post("/api/impedance", json={
        "mode": "trace",
        "kind": "microstrip",
        "h": 0, "er": 4.5, "t": 0.035, "w": 0.35,
    })
    assert res.status_code == 400


def test_api_cpw_is_400_not_a_fake_number():
    res = _impedance_client().post("/api/impedance", json={
        "mode": "trace",
        "kind": "cpw",
        "h": 0.15, "er": 4.3, "t": 0.035, "w": 0.2, "s": 0.15,
    })
    assert res.status_code == 400


def test_openems_is_not_on_the_impedancefinder_package():
    import impedancefinder
    import pkgutil

    names = {m.name for m in pkgutil.iter_modules(impedancefinder.__path__)}
    assert "gerber2ems_export" not in names
    assert "board_model" not in names


def test_api_stackup_returns_dru_not_findings():
    res = _impedance_client().post("/api/impedance", json={
        "mode": "stackup",
        "h": 0.20, "er": 4.5, "t": 0.035, "s": 0.20,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["targets"]["microstrip_50"]["z0"] == pytest.approx(50.0, rel=0.02)
    assert "(rule PINSCOPE_50OHM" in body["kicad_dru"]
    assert "findings" not in body
