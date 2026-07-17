"""EDIF 2.0.0 netlist parser — Siemens xDX Designer flavor.

Verified against the client-supplied file ``edif-files/144040 (1).edn``
(128 KB, two sub-designs merged, 42 instances, 20 nets). The BOM at
``edif-files/BOM (1).xlsx`` covers 19 of those 42 designators; the rest are
orphan parts in the second sub-design and are expected to land in the graph
unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.pinscopex.parsers import (
    detect_netlist_format,
    parse_netlist_any,
    validate_netlist,
)
from backend.pinscopex.parsers_edif import (
    list_edif_subdesigns,
    parse_edif_netlist,
)


EDIF_FIXTURE = Path(__file__).resolve().parent.parent / "edif-files" / "144040 (1).edn"

# The client-supplied EDIF sample is confidential and never committed —
# these tests only run on machines that have it locally.
if not EDIF_FIXTURE.exists():
    pytest.skip(
        "edif-files/ sample netlist not present (local-only, untracked)",
        allow_module_level=True,
    )


# Designators the client's BOM lists. The parser MUST surface every one of
# these — anything else is a regression.
BOM_DESIGNATORS = frozenset({
    "C1", "C2", "C3", "C4", "C5", "C6",
    "C22", "C23", "C24", "C25",
    "R1", "R6", "R7",
    "L1", "L5",
    "FB1", "FB2",
    "U1", "U3",
})


@pytest.fixture(scope="module")
def parsed():
    parts, nets = parse_edif_netlist(EDIF_FIXTURE)
    return parts, nets


def test_format_detection():
    assert detect_netlist_format(EDIF_FIXTURE.read_bytes()) == "edif"


def test_dispatcher_routes_to_edif():
    parts, nets, fmt = parse_netlist_any(EDIF_FIXTURE)
    assert fmt == "edif"
    assert parts and nets


def test_all_bom_designators_present(parsed):
    parts, _ = parsed
    missing = BOM_DESIGNATORS - set(parts)
    assert not missing, f"BOM designators missing from parser output: {sorted(missing)}"


def test_u3_pin_count_matches_ldo(parsed):
    """U3 is the MIC5305YMLTR LDO — datasheet has 7 pins (6 + thermal EPAD)."""
    _, nets = parsed
    u3_pins = {pin for conns in nets.values() for ref, pin in conns if ref == "U3"}
    assert len(u3_pins) == 7, f"expected 7 distinct pins for U3, got {sorted(u3_pins)}"


def test_u1_pin_count_matches_rf_amp(parsed):
    """U1 is the CMD263P3 RF amp — datasheet has 17 pins (16 + thermal EPAD)."""
    _, nets = parsed
    u1_pins = {pin for conns in nets.values() for ref, pin in conns if ref == "U1"}
    assert len(u1_pins) == 17, f"expected 17 distinct pins for U1, got {sorted(u1_pins)}"


def test_ground_net_renamed(parsed):
    """The parser must rename Pin_Type=GROUND nets to ``GND`` so the existing
    validate_netlist() ground check passes."""
    _, nets = parsed
    assert "GND" in nets, f"expected a 'GND' net, found: {sorted(nets)}"
    assert len(nets["GND"]) > 5, "GND should have many endpoints in a real design"


def test_validate_netlist_accepts_edif_output(parsed):
    parts, nets = parsed
    issues = validate_netlist(parts, nets)
    assert issues == [], f"unexpected validation issues: {issues}"


def test_every_net_endpoint_resolves(parsed):
    """No dangling (ref, pin) tuples — every connection should reference a
    designator that's also in the parts dict."""
    parts, nets = parsed
    refs = set(parts)
    bad = [
        (net, ref, pin)
        for net, conns in nets.items()
        for ref, pin in conns
        if ref not in refs
    ]
    assert not bad, f"net endpoints reference unknown designators: {bad[:5]}"


def test_template_designators_skipped(parsed):
    """Instances whose designator is still a template (``R?`` / ``C?`` / ``U?``)
    should not leak into parts — they're unconfigured library symbols."""
    parts, _ = parsed
    leaked = [ref for ref in parts if ref.endswith("?")]
    assert not leaked, f"template designators leaked: {leaked}"


# ---------------------------------------------------------------------------
# Sub-design listing + filtering
# ---------------------------------------------------------------------------


def test_list_subdesigns_finds_two():
    """The client's file has two sub-designs (&0441, &0442). The lister must
    surface both with their instance counts and full designator lists."""
    subs = list_edif_subdesigns(EDIF_FIXTURE)
    ids = sorted(s["id"] for s in subs if s["id"])
    assert ids == ["&0441", "&0442"], f"unexpected sub-design ids: {ids}"
    # Each sub-design carries 21 resolved-designator instances.
    for s in subs:
        assert s["instance_count"] == len(s["designators"])
        assert s["instance_count"] > 0


def test_subdesign_filter_keeps_only_selected():
    """Filtering to ``&0441`` must yield exactly that sub-design's parts."""
    parts_a, nets_a = parse_edif_netlist(EDIF_FIXTURE, include_subdesigns={"&0441"})
    parts_b, nets_b = parse_edif_netlist(EDIF_FIXTURE, include_subdesigns={"&0442"})

    bom = BOM_DESIGNATORS
    in_a = set(parts_a) & bom
    in_b = set(parts_b) & bom
    assert in_a == bom, f"sub-design A should hold every BOM ref; missing: {bom - in_a}"
    assert in_b == set(), f"sub-design B has 0 BOM refs in the fixture; got: {in_b}"
    # Designators must not overlap across sub-designs (xDX gives each its own
    # ref namespace per board).
    assert set(parts_a).isdisjoint(parts_b)


def test_subdesign_filter_preserves_shared_ground():
    """``GND`` (renamed via Pin_Type detection) spans both sub-designs in the
    raw EDIF. After filtering to one sub-design the net survives, but only
    with endpoints from instances that survived."""
    _, nets_a = parse_edif_netlist(EDIF_FIXTURE, include_subdesigns={"&0441"})
    _, nets_all = parse_edif_netlist(EDIF_FIXTURE)
    assert "GND" in nets_a
    refs_a = {ref for ref, _ in nets_a["GND"]}
    refs_all = {ref for ref, _ in nets_all["GND"]}
    # Filtered GND is a strict subset of unfiltered GND.
    assert refs_a < refs_all, "filtered GND should drop the excluded sub-design's endpoints"


def test_subdesign_filter_empty_set_returns_empty():
    """Empty selection yields no parts (and no nets that referenced them)."""
    parts, nets = parse_edif_netlist(EDIF_FIXTURE, include_subdesigns=set())
    assert parts == {}
    assert nets == {}


def test_subdesign_filter_none_matches_unfiltered():
    """``include_subdesigns=None`` (the default) is the pre-flag behavior."""
    parts1, nets1 = parse_edif_netlist(EDIF_FIXTURE)
    parts2, nets2 = parse_edif_netlist(EDIF_FIXTURE, include_subdesigns=None)
    assert parts1 == parts2
    assert nets1 == nets2
