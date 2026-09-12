"""Tests for shortest_path, library_gate, cache_stats."""

from __future__ import annotations

from backend.pinscopex.library_gate import pintable_checksum, should_promote_extraction
from backend.pinscopex.models import Component, ComponentType, DesignGraph, Net, NetType, PinConnection
from backend.pinscopex.validation_tools import shortest_path
from backend.services.api_logs import cache_stats_by_stage


def _graph_r_chain() -> DesignGraph:
    """U1.1 — NET_A — R1 — NET_B — U2.2"""
    return DesignGraph(
        components={
            "U1": Component(
                reference="U1", value="IC", footprint="",
                component_type=ComponentType.IC, pins={"1": "NET_A"},
            ),
            "R1": Component(
                reference="R1", value="10k", footprint="",
                component_type=ComponentType.RESISTOR,
                pins={"1": "NET_A", "2": "NET_B"},
            ),
            "U2": Component(
                reference="U2", value="IC", footprint="",
                component_type=ComponentType.IC, pins={"2": "NET_B"},
            ),
        },
        nets={
            "NET_A": Net(
                name="NET_A", net_type=NetType.SIGNAL,
                pins=[
                    PinConnection(component_ref="U1", pin_number="1"),
                    PinConnection(component_ref="R1", pin_number="1"),
                ],
            ),
            "NET_B": Net(
                name="NET_B", net_type=NetType.SIGNAL,
                pins=[
                    PinConnection(component_ref="R1", pin_number="2"),
                    PinConnection(component_ref="U2", pin_number="2"),
                ],
            ),
        },
    )


def test_shortest_path_two_hops():
    g = _graph_r_chain()
    msg = shortest_path(g, {}, "U1", "1", "U2", "2")
    assert "hop" in msg.lower()
    assert "NET_A" in msg and "NET_B" in msg
    assert "U1.1" in msg and "U2.2" in msg


def test_shortest_path_same_net():
    g = _graph_r_chain()
    msg = shortest_path(g, {}, "U1", "1", "R1", "1")
    assert "same net" in msg.lower() or "Direct" in msg


def test_library_gate_rejects_empty():
    ok, reason = should_promote_extraction({"pintable": []})
    assert not ok
    assert "empty" in reason


def test_library_gate_accepts_named_pins():
    data = {
        "pintable": [
            {"number": "1", "name": "VDD"},
            {"number": "2", "name": "GND"},
        ],
    }
    ok, checksum = should_promote_extraction(data)
    assert ok
    assert checksum == pintable_checksum(data["pintable"])


def test_cache_stats_by_stage():
    entries = [
        {"stage": "pintable", "input_tokens": 100, "cache_read_input_tokens": 40},
        {"stage": "pintable", "input_tokens": 100, "cache_read_input_tokens": 60},
        {"stage": "validation", "input_tokens": 50, "cache_read_input_tokens": 0},
    ]
    stats = cache_stats_by_stage(entries)
    assert stats["pintable"]["calls"] == 2
    assert stats["pintable"]["hit_ratio"] == 0.5
    assert stats["validation"]["hit_ratio"] == 0.0
