"""Recovery path: when a turn produces no tool calls (model wrote findings
as a JSON code block in prose instead of calling submit_review), the loop
must NOT drop the work — it should nudge and force submit_review next turn.

This regression was introduced by a system-prompt change that made Gemini
default to text output for findings. The fix is in the review loop itself
so it survives future prompt regressions.
"""

from __future__ import annotations

import json
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


def _usage():
    return Usage(input_tokens=10, output_tokens=5,
                 cache_creation_tokens=0, cache_read_tokens=0)


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


def _recovery_script(ic_ref, n):
    """Turn 0: text only, no tool calls (the failure mode).
    Turn 1: submit_review under forced tool_choice (the recovery)."""
    if n == 0:
        return Completion(
            text="I'll write up findings as JSON below: [...]",
            tool_calls=[],  # the bug: no tool calls
            usage=_usage(),
            stop_reason="end_turn",
            raw_assistant_blocks=[],
        )
    # Turn 1 — forced submit_review under the recovery
    return Completion(
        text="",
        tool_calls=[ToolCall(id="t2", name="submit_review", input={
            "findings": [{
                "finding": "Recovered finding.",
                "why": "Recovered from a no-tool-call turn.",
                "status": "INFO",
                "source_page": 1,
            }],
            "checked_areas": ["recovery"],
        })],
        usage=_usage(),
        stop_reason="tool_use",
        raw_assistant_blocks=[],
    )


@pytest.mark.asyncio
async def test_no_tool_calls_triggers_forced_submit_next_turn(workspace, monkeypatch):
    """A turn with zero tool calls should not drop the review — the next
    turn must be forced to submit_review and the resulting findings must
    land in the report."""

    seen_tool_choices: list = []

    class _Session:
        def __init__(self, ic_ref):
            self._ic = ic_ref
            self._n = 0

        async def complete(self, messages, tools, tool_choice):
            seen_tool_choices.append((self._ic, self._n, tool_choice))
            c = _recovery_script(self._ic, self._n)
            self._n += 1
            return c

        async def close(self):
            pass

    class _Provider:
        name = "fake"

        async def create_session(self, model, system, max_tokens, **_kwargs):
            return _Session(_Provider._current_ic)

        _current_ic = None

    async def fake_cwf(stage, body):
        return await body(_Provider(), "fake-model")

    monkeypatch.setattr(val, "call_with_fallback", fake_cwf)

    orig = val.review_ic_async

    async def wrapped(graph, cmap, ic_ref, pdf_path, **kw):
        _Provider._current_ic = ic_ref
        return await orig(graph, cmap, ic_ref, pdf_path, **kw)

    monkeypatch.setattr(val, "review_ic_async", wrapped)

    async def before_ic(ref):
        return True

    await val.validate_design_async(
        str(workspace["graph"]),
        str(workspace["report"]),
        str(workspace["extracted"]),
        pdf_dir=str(workspace["ds_dir"]),
        storage=workspace["storage"],
        before_ic=before_ic,
        project_prefix=PREFIX,
        run_meta={"git_commit": "testsha"},
    )

    # All three ICs should have recovered: each had a no-tool-call turn 0,
    # then submit_review under forced tool_choice on turn 1.
    report = json.loads(workspace["report"].read_text())
    assert report["summary"]["total"] == 3
    assert report["summary"]["INFO"] == 3

    # Verify the recovery actually forced submit_review on turn 1 for each IC.
    by_ic_turn = {(ic, n): tc for ic, n, tc in seen_tool_choices}
    for ic in ("U1", "U2", "U3"):
        # Turn 0 should be auto (model free to use any tool)
        assert by_ic_turn[(ic, 0)] == "auto", \
            f"turn 0 for {ic} should be auto, got {by_ic_turn[(ic, 0)]!r}"
        # Turn 1 should be forced submit_review (the recovery)
        assert by_ic_turn[(ic, 1)] == {"name": "submit_review"}, \
            f"turn 1 for {ic} should force submit_review, got {by_ic_turn[(ic, 1)]!r}"

    # Each trace should show the recovery: turn 0 has no tool calls, turn 1
    # has submit_review.
    traces_dir = workspace["data"] / PREFIX / "review_traces"
    for ref in ("U1", "U2", "U3"):
        t = json.loads((traces_dir / f"{safe_mpn(ref)}.json").read_text())
        assert len(t["turns"]) == 2
        assert t["turns"][0]["tool_calls"] == []
        assert t["turns"][1]["tool_calls"][0]["name"] == "submit_review"
        assert t["stop_reason"] == "submit_review"
