"""Tests for get_datasheet_excerpt: safety guards, budget cap, cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

from backend.pinscopex.models import DesignGraph
from backend.pinscopex.utils import safe_mpn
from backend.pinscopex.validation_tools import (
    EXCERPT_TOPICS,
    ExcerptState,
    execute_tool,
    get_datasheet_excerpt,
)
from backend.services.llm.types import PdfBlock
from backend.services.validation import _signal_neighbors

GRAPH = Path(__file__).resolve().parent.parent / "simple_project" / "design_graph.json"
IC_MPNS = {
    "U1": "SPX3819M5-L-3-3/TR",
    "U2": "CH340E",
    "U3": "MSPM0G3507SPTR",
}


def _blank_pdf(path: Path, pages: int = 3) -> None:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    with path.open("wb") as fh:
        w.write(fh)


@pytest.fixture
def graph_and_pdfs(tmp_path):
    graph = DesignGraph.model_validate(json.loads(GRAPH.read_text()))
    pdf_dir = tmp_path / "datasheets"
    pdf_dir.mkdir()
    for mpn in IC_MPNS.values():
        _blank_pdf(pdf_dir / f"{safe_mpn(mpn)}.pdf")
    return graph, pdf_dir


def _state(graph, current_ic, pdf_dir, **kwargs):
    return ExcerptState(
        current_ic=current_ic,
        connected_designators=_signal_neighbors(graph, current_ic),
        graph=graph,
        pdf_dir=pdf_dir,
        storage=None,
        **kwargs,
    )


def test_rejects_non_neighbor(graph_and_pdfs):
    """U1's review cannot fetch U2/U3 — they're not signal neighbors."""
    graph, pdf_dir = graph_and_pdfs
    state = _state(graph, "U1", pdf_dir)
    text, attachment = get_datasheet_excerpt(
        graph, {}, "U2", "absolute_max", state,
    )
    assert attachment is None
    assert "not a signal neighbor" in text
    assert state.fetch_count == 0


def test_rejects_self(graph_and_pdfs):
    """The IC under review can't excerpt its own datasheet — it's already in
    context. Steer the model to use the existing PDF."""
    graph, pdf_dir = graph_and_pdfs
    state = _state(graph, "U2", pdf_dir)
    text, attachment = get_datasheet_excerpt(
        graph, {}, "U2", "absolute_max", state,
    )
    assert attachment is None
    assert "already reviewing" in text


def test_rejects_unknown_topic(graph_and_pdfs):
    """Bad topic enum returns a clean error listing valid topics."""
    graph, pdf_dir = graph_and_pdfs
    state = _state(graph, "U2", pdf_dir)
    text, attachment = get_datasheet_excerpt(
        graph, {}, "U3", "bogus_topic", state,
    )
    assert attachment is None
    assert "Unknown topic" in text
    assert "absolute_max" in text


def test_returns_pdfblock_for_valid_neighbor(graph_and_pdfs):
    """U2's review can fetch U3 (a signal neighbor) — returns PdfBlock."""
    graph, pdf_dir = graph_and_pdfs
    state = _state(graph, "U2", pdf_dir)
    text, attachment = get_datasheet_excerpt(
        graph, {}, "U3", "pin_voltage_levels", state,
    )
    assert isinstance(attachment, PdfBlock)
    assert attachment.cacheable is True
    assert "U3" in text
    assert "pin_voltage_levels" in text
    assert state.fetch_count == 1
    assert state.page_count > 0


def test_budget_cap_fetch_count(graph_and_pdfs):
    """After fetch_budget hits, further fetches are rejected with budget message."""
    graph, pdf_dir = graph_and_pdfs
    # Tight budget so the test is fast
    state = _state(graph, "U3", pdf_dir, fetch_budget=2, page_budget=100)
    # Two valid fetches succeed
    for topic in ("absolute_max", "pin_voltage_levels"):
        _, att = get_datasheet_excerpt(graph, {}, "U2", topic, state)
        assert att is not None, f"topic {topic} should have succeeded"
    # Third fetch should be budget-blocked
    text, attachment = get_datasheet_excerpt(
        graph, {}, "U2", "electrical_characteristics", state,
    )
    assert attachment is None
    assert "budget exhausted" in text


def test_per_neighbor_page_budget_blocks_third_topic_on_same_neighbor(graph_and_pdfs):
    """A single neighbor can be fetched up to its per-neighbor page budget,
    then further topics on it are blocked — bounds one interface's cost
    without touching the global budget."""
    graph, pdf_dir = graph_and_pdfs
    # Blank PDFs fall back to 3 pages/fetch; budget of 4 admits the first two
    # fetches (0→3, 3→6) and blocks the third once the neighbor is over.
    state = _state(
        graph, "U3", pdf_dir,
        fetch_budget=10, page_budget=1000, per_neighbor_page_budget=4,
    )
    _, a1 = get_datasheet_excerpt(graph, {}, "U2", "absolute_max", state)
    assert a1 is not None
    _, a2 = get_datasheet_excerpt(graph, {}, "U2", "pin_voltage_levels", state)
    assert a2 is not None
    text, a3 = get_datasheet_excerpt(graph, {}, "U2", "electrical_characteristics", state)
    assert a3 is None
    assert "Per-neighbor" in text and "U2" in text


def test_global_page_budget_still_bounds_total_fanout(graph_and_pdfs):
    """The global page budget is a hard ceiling even when a neighbor is under
    its per-neighbor sub-budget — bounds hub-IC fan-out."""
    graph, pdf_dir = graph_and_pdfs
    state = _state(
        graph, "U3", pdf_dir,
        fetch_budget=10, page_budget=2, per_neighbor_page_budget=100,
    )
    _, a1 = get_datasheet_excerpt(graph, {}, "U2", "absolute_max", state)
    assert a1 is not None  # capped to 2 pages → page_count hits global budget
    text, a2 = get_datasheet_excerpt(graph, {}, "U2", "pin_voltage_levels", state)
    assert a2 is None
    assert "page budget exhausted" in text


def test_cache_shared_across_states(graph_and_pdfs):
    """Same (designator, topic, ds_md5) → second fetch reuses trimmed PDF.

    Mirrors the cross-IC case: U2's review fetches U3@abs_max, then U3's
    review fetches U3@abs_max from a separate ExcerptState sharing the same
    cache dict.
    """
    graph, pdf_dir = graph_and_pdfs
    shared_cache: dict = {}
    s1 = _state(graph, "U2", pdf_dir, cache=shared_cache)
    s2 = _state(graph, "U3", pdf_dir, cache=shared_cache)
    _, att1 = get_datasheet_excerpt(graph, {}, "U3", "absolute_max", s1)
    assert att1 is not None
    path1 = att1.path
    # U3's review reusing the same neighbor (loopback: U3 fetching itself is
    # blocked, so use a different IC). We assert path identity for the U2
    # case from s1's perspective is preserved across a fresh state with the
    # same cache by re-fetching from s1's neighbor U3 with state s_other.
    s_other = _state(graph, "U2", pdf_dir, cache=shared_cache)
    _, att2 = get_datasheet_excerpt(graph, {}, "U3", "absolute_max", s_other)
    assert att2 is not None
    assert att2.path == path1  # cache hit — same trimmed file


def test_no_state_returns_clean_error(graph_and_pdfs):
    """Calling without state (regression guard) returns an explicit message."""
    graph, _ = graph_and_pdfs
    text, attachment = get_datasheet_excerpt(graph, {}, "U3", "absolute_max", None)
    assert attachment is None
    assert "without per-review state" in text


def test_execute_tool_dispatcher_routes_correctly(graph_and_pdfs):
    """The dispatcher recognises the new tool name and forwards state."""
    graph, pdf_dir = graph_and_pdfs
    state = _state(graph, "U2", pdf_dir)
    text, attachment = execute_tool(
        graph, {}, "get_datasheet_excerpt",
        {"designator": "U3", "topic": "absolute_max"},
        state=state,
    )
    assert isinstance(attachment, PdfBlock)
    assert state.fetch_count == 1


def test_existing_tools_return_tuple_with_none_attachment(graph_and_pdfs):
    """Backward compat: existing tools now return (text, None)."""
    graph, _ = graph_and_pdfs
    text, att = execute_tool(graph, {}, "get_pintable", {"designator": "U2"})
    assert isinstance(text, str) and text
    assert att is None


def test_topics_cover_u2_001_use_case():
    """The U2-001 false positive needs to verify MSPM0G3507 PA1's 5V-tolerance
    — assert that the pin_voltage_levels topic regex matches typical
    datasheet phrasings for this."""
    pat = EXCERPT_TOPICS["pin_voltage_levels"]
    assert pat.search("This pin is 5V-tolerant under normal operating conditions.")
    assert pat.search("VIH = 0.7 × VDD")
    assert pat.search("Input voltage range: -0.3V to VDD+0.3V")
