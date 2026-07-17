"""Concurrency of the gated review path in validate_design_async.

The pipeline drives validate_design_async with a ``before_ic`` credit gate,
which (since the parallelism change) runs up to ``settings.ic_concurrency``
IC reviews at once. These tests build a minimal synthetic graph (no real
datasheets, no network — review_ic_async is faked) and assert:

  * in-flight reviews are bounded by the single ``ic_concurrency`` knob,
  * each IC's API calls land in its *own* private ApiLogger (no cross-billing
    between concurrent ICs — the bug the private-logger design prevents),
  * a gate that trips mid-run stops *new* reviews (pause is honoured).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

from backend.config import settings
from backend.pinscopex.models import Component, ComponentType, DesignGraph, Finding
from backend.pinscopex.utils import safe_mpn
from backend.pinscopex.validate import ReviewResult
from backend.services import validation as val
from backend.services.api_logs import ApiLogger
from backend.services.storage import LocalStorageBackend

PREFIX = "users/local/projects/test"
# Distinct MPNs so each IC maps to its own datasheet PDF + private logger.
IC_MPNS = {f"U{i}": f"MPN-{i}" for i in range(1, 6)}  # 5 ICs


def _blank_pdf(path: Path) -> None:
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    with path.open("wb") as fh:
        w.write(fh)


@pytest.fixture
def workspace(tmp_path):
    data = tmp_path / "data"
    proj = data / PREFIX
    proj.mkdir(parents=True)

    # Minimal graph: one IC component per MPN (plus their datasheet PDFs).
    graph = DesignGraph(
        components={
            ref: Component(
                reference=ref,
                value=mpn,
                footprint="LQFP",
                component_type=ComponentType.IC,
                mpn=mpn,
            )
            for ref, mpn in IC_MPNS.items()
        }
    )
    graph_path = proj / "design_graph.json"
    graph_path.write_text(graph.model_dump_json())

    extracted = proj / "extracted"
    extracted.mkdir()
    ds_dir = proj / "uploads" / "datasheets"
    ds_dir.mkdir(parents=True)
    for mpn in IC_MPNS.values():
        _blank_pdf(ds_dir / f"{safe_mpn(mpn)}.pdf")

    return dict(
        data=data,
        graph=graph_path,
        report=proj / "report.json",
        extracted=extracted,
        ds_dir=ds_dir,
        storage=LocalStorageBackend(data),
    )


def _finding(ref: str) -> Finding:
    return Finding(designator=ref, finding=f"{ref} note", status="INFO")


def _make_fake_review(tracker: dict, *, log_entries):
    """Fake review_ic_async: logs ``log_entries(ref)`` calls to the *private*
    logger it's handed, records peak concurrency, and yields so reviews truly
    overlap."""

    async def fake_review_ic_async(graph, cmap, ic_ref, pdf_path, *,
                                   api_logger=None, **kw):
        tracker["in_flight"] += 1
        tracker["peak"] = max(tracker["peak"], tracker["in_flight"])
        try:
            # Real suspension point so the event loop can interleave reviews.
            await asyncio.sleep(0.02)
            # Log this IC's API calls into ITS OWN private logger. Each IC logs
            # a distinct number of entries so cross-billing would be visible.
            for k in range(log_entries(ic_ref)):
                api_logger.log(
                    stage="validation", identifier=ic_ref, model="fake",
                    input_tokens=100, output_tokens=50, duration_ms=1,
                    stop_reason="end_turn", turns=1,
                )
            return ReviewResult(findings=[_finding(ic_ref)], checked_areas=["power"]), {}
        finally:
            tracker["in_flight"] -= 1

    return fake_review_ic_async


@pytest.mark.asyncio
async def test_concurrency_bounded_by_knob_and_charging_isolated(workspace, monkeypatch):
    # One unique entry-count per IC (U1->1, U2->2, ... U5->5).
    entries_for = {ref: i + 1 for i, ref in enumerate(IC_MPNS)}
    tracker = {"in_flight": 0, "peak": 0}
    monkeypatch.setattr(
        val, "review_ic_async",
        _make_fake_review(tracker, log_entries=lambda ref: entries_for[ref]),
    )
    monkeypatch.setattr(settings, "ic_concurrency", 3)

    shared = ApiLogger()
    charged: dict[str, int] = {}

    async def before_ic(ref):
        return True

    async def on_ic_done(ref, result, private):
        # Mirror _charge_private_logger: the private logger must hold EXACTLY
        # this IC's entries — never another concurrent IC's.
        assert all(e["identifier"] == ref for e in private.entries), \
            f"{ref}'s private logger leaked another IC's entries: {private.entries}"
        charged[ref] = len(private.entries)
        shared.entries.extend(private.entries)

    await val.validate_design_async(
        str(workspace["graph"]),
        str(workspace["report"]),
        str(workspace["extracted"]),
        pdf_dir=str(workspace["ds_dir"]),
        api_logger=shared,
        storage=workspace["storage"],
        before_ic=before_ic,
        on_ic_done=on_ic_done,
    )

    # Knob bounds parallelism: 5 ICs, knob=3 -> peak exactly 3.
    assert tracker["peak"] == 3, f"expected peak 3, got {tracker['peak']}"
    # Per-IC charging isolated and exact: each IC charged its own entry count.
    assert charged == entries_for
    # Shared log accumulated every IC's calls once (1+2+3+4+5 = 15).
    assert len(shared.entries) == sum(entries_for.values()) == 15

    report = json.loads(workspace["report"].read_text())
    assert report["summary"]["total"] == len(IC_MPNS)  # all 5 reviewed


@pytest.mark.asyncio
async def test_one_knob_scales_to_one_is_sequential(workspace, monkeypatch):
    tracker = {"in_flight": 0, "peak": 0}
    monkeypatch.setattr(
        val, "review_ic_async",
        _make_fake_review(tracker, log_entries=lambda ref: 1),
    )
    monkeypatch.setattr(settings, "ic_concurrency", 1)

    async def before_ic(ref):
        return True

    await val.validate_design_async(
        str(workspace["graph"]),
        str(workspace["report"]),
        str(workspace["extracted"]),
        pdf_dir=str(workspace["ds_dir"]),
        api_logger=ApiLogger(),
        storage=workspace["storage"],
        before_ic=before_ic,
    )
    # ic_concurrency=1 -> never more than one review in flight (sequential).
    assert tracker["peak"] == 1


@pytest.mark.asyncio
async def test_gate_trip_stops_new_reviews(workspace, monkeypatch):
    tracker = {"in_flight": 0, "peak": 0}
    monkeypatch.setattr(
        val, "review_ic_async",
        _make_fake_review(tracker, log_entries=lambda ref: 1),
    )
    monkeypatch.setattr(settings, "ic_concurrency", 2)

    reviewed: list[str] = []
    gate_calls = {"n": 0}
    LIMIT = 2  # allow exactly the first 2 gate checks, then "out of credits"

    async def before_ic(ref):
        # Synchronous (no await) -> atomic counter, so exactly LIMIT pass.
        gate_calls["n"] += 1
        return gate_calls["n"] <= LIMIT

    async def on_ic_done(ref, result, private):
        reviewed.append(ref)

    report_obj = await val.validate_design_async(
        str(workspace["graph"]),
        str(workspace["report"]),
        str(workspace["extracted"]),
        pdf_dir=str(workspace["ds_dir"]),
        api_logger=ApiLogger(),
        storage=workspace["storage"],
        before_ic=before_ic,
        on_ic_done=on_ic_done,
    )

    # Exactly LIMIT ICs got past the gate and were reviewed; the rest were
    # stopped without starting work.
    assert len(reviewed) == LIMIT, f"reviewed={reviewed}"
    # Report is marked partial because a gate tripped.
    report = json.loads(workspace["report"].read_text())
    assert report.get("partial") is True
    assert report["summary"]["total"] == LIMIT
