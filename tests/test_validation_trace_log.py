"""Per-IC validation trace log — end-to-end on the async (gated) pipeline path.

Drives validate_design_async with a scripted fake LLM provider (no API key,
no network) against the real simple_project graph, asserting that each IC
leaves a review_traces/<safe_mpn>.json transcript with the expected schema,
and that trace failures never break the review/report.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pypdf import PdfWriter

from backend.pinscopex.utils import safe_mpn
from backend.services import validation as val
from backend.services.llm.types import Completion, ToolCall, Usage
from backend.services.storage import LocalStorageBackend

GRAPH = Path(__file__).resolve().parent.parent / "simple_project" / "design_graph.json"
IC_MPNS = {
    "U1": "SPX3819M5-L-3-3/TR",
    "U2": "CH340E",
    "U3": "MSPM0G3507SPTR",
}
PREFIX = "users/local/projects/test"


def _blank_pdf(path: Path) -> None:
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    with path.open("wb") as fh:
        w.write(fh)


def _fake_provider(script):
    """Return a provider whose session.complete() yields scripted Completions.

    `script(ic_ref, call_idx)` -> Completion. call_idx resets per session
    (i.e. per IC review attempt).
    """

    class _Session:
        def __init__(self, ic_ref):
            self._ic = ic_ref
            self._n = 0

        async def complete(self, messages, tools, tool_choice):
            c = script(self._ic, self._n)
            self._n += 1
            return c

        async def close(self):
            pass

    class _Provider:
        name = "fake"

        async def create_session(self, model, system, max_tokens, **_kwargs):
            # ic_ref is recoverable from the build_component_context text in
            # the first user message; simpler: stash via closure below.
            # **_kwargs absorbs temperature= (and any future session knobs).
            return _Session(_Provider._current_ic)

        _current_ic = None

    return _Provider


def _usage():
    return Usage(input_tokens=10, output_tokens=5,
                 cache_creation_tokens=2, cache_read_tokens=1)


def _script(ic_ref, n):
    if n == 0:
        return Completion(
            text="",
            tool_calls=[ToolCall(id="t1", name="get_pintable",
                                 input={"designator": ic_ref})],
            usage=_usage(),
            stop_reason="tool_use",
            raw_assistant_blocks=[],
        )
    return Completion(
        text="done",
        tool_calls=[ToolCall(id="t2", name="submit_review", input={
            "findings": [{
                "finding": "VCAP not connected.",
                "why": "Datasheet requires 1uF on VCAP.",
                "status": "WARNING",
                "source_page": 7,
            }],
            "checked_areas": ["power", "decoupling"],
        })],
        usage=_usage(),
        stop_reason="tool_use",
        raw_assistant_blocks=[],
    )


@pytest.fixture
def workspace(tmp_path):
    data = tmp_path / "data"
    (data / PREFIX).mkdir(parents=True)
    graph_path = data / PREFIX / "design_graph.json"
    graph_path.write_text(GRAPH.read_text())
    report_path = data / PREFIX / "report.json"
    extracted = data / PREFIX / "extracted"
    extracted.mkdir()
    ds_dir = data / PREFIX / "uploads" / "datasheets"
    ds_dir.mkdir(parents=True)
    for mpn in IC_MPNS.values():
        _blank_pdf(ds_dir / f"{safe_mpn(mpn)}.pdf")
    storage = LocalStorageBackend(data)
    return dict(data=data, graph=graph_path, report=report_path,
                extracted=extracted, ds_dir=ds_dir, storage=storage)


async def _run(ws, monkeypatch, before_ic):
    """Patch the provider seam and run the gated path."""
    prov_cls = _fake_provider(_script)

    # review_ic_async calls call_with_fallback("validation", _run); short-circuit
    # it to invoke the body directly with our fake provider, exercising the
    # real loop + trace instrumentation.
    async def fake_cwf(stage, body):
        # build_component_context is called inside body; the fake session needs
        # the ic_ref. Patch _select_review_pages to a no-op and recover ic_ref
        # via the provider's _current_ic class attr set per call below.
        return await body(prov_cls(), "fake-model")

    monkeypatch.setattr(val, "call_with_fallback", fake_cwf)

    # The fake session needs the current ic_ref; thread it through by wrapping
    # review_ic_async to set the class attr before delegating.
    orig = val.review_ic_async

    async def wrapped(graph, cmap, ic_ref, pdf_path, **kw):
        prov_cls._current_ic = ic_ref
        return await orig(graph, cmap, ic_ref, pdf_path, **kw)

    monkeypatch.setattr(val, "review_ic_async", wrapped)

    return await val.validate_design_async(
        str(ws["graph"]),
        str(ws["report"]),
        str(ws["extracted"]),
        pdf_dir=str(ws["ds_dir"]),
        storage=ws["storage"],
        before_ic=before_ic,
        project_prefix=PREFIX,
        run_meta={"git_commit": "testsha"},
    )


@pytest.mark.asyncio
async def test_trace_written_per_ic_with_schema(workspace, monkeypatch):
    async def before_ic(ref):
        return True

    await _run(workspace, monkeypatch, before_ic)

    traces_dir = workspace["data"] / PREFIX / "review_traces"
    for ref, mpn in IC_MPNS.items():
        # Keyed by safe_mpn(ic_ref) per the design — the designator, not MPN.
        f = traces_dir / f"{safe_mpn(ref)}.json"
        assert f.is_file(), f"missing trace for {ref}"
        t = json.loads(f.read_text())
        assert t["trace_version"] == 1
        assert t["ic_ref"] == ref
        assert t["mpn"] == mpn
        assert t["provider"] == "fake"
        assert t["git_commit"] == "testsha"
        assert re.fullmatch(r"[0-9a-f]{32}", t["datasheet"]["md5"])
        assert t["datasheet"]["safe_mpn"] == safe_mpn(mpn)
        assert len(t["turns"]) == 2
        tc0 = t["turns"][0]["tool_calls"][0]
        assert tc0["name"] == "get_pintable"
        assert tc0["input"] == {"designator": ref}
        assert isinstance(tc0["output"], str) and tc0["output"]
        assert t["turns"][0]["usage"]["input_tokens"] == 10
        assert t["turns"][1]["tool_calls"][0]["name"] == "submit_review"
        assert t["final_submission"]["checked_areas"] == ["power", "decoupling"]
        assert t["stop_reason"] == "submit_review"
        assert t["result"]["findings_count"] == 1
        assert t["error"] is None
        assert isinstance(t["duration_ms"], int)

    report = json.loads(workspace["report"].read_text())
    # 3 ICs x 1 finding each
    assert report["summary"]["total"] == 3


@pytest.mark.asyncio
async def test_trace_write_failure_does_not_break_review(workspace, monkeypatch):
    storage = workspace["storage"]
    real_write = storage.write_json

    def flaky(key, data):
        if "review_traces" in key:
            raise RuntimeError("simulated trace storage outage")
        return real_write(key, data)

    monkeypatch.setattr(storage, "write_json", flaky)

    async def before_ic(ref):
        return True

    # Must not raise despite every trace write failing.
    await _run(workspace, monkeypatch, before_ic)

    report = json.loads(workspace["report"].read_text())
    assert report["summary"]["total"] == 3
    assert not (workspace["data"] / PREFIX / "review_traces").exists()


@pytest.mark.asyncio
async def test_per_ic_flush_survives_pause(workspace, monkeypatch):
    seen = []

    async def before_ic(ref):
        seen.append(ref)
        return len(seen) == 1  # allow only the first IC, then pause

    await _run(workspace, monkeypatch, before_ic)

    traces_dir = workspace["data"] / PREFIX / "review_traces"
    files = sorted(p.name for p in traces_dir.glob("*.json"))
    # Exactly the first IC (U1) reviewed before the pause; its trace persisted.
    assert files == ["U1.json"]
