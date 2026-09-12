"""Placement pipeline smoke — topology only, no LLM."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.pinscopex.functional_groups import (
    FunctionalGroupsReport,
    build_placement_plan,
)
from backend.pinscopex.models import DesignGraph


ROOT = Path(__file__).resolve().parents[1]
SIMPLE = ROOT / "simple_project"


@pytest.fixture
def graph() -> DesignGraph:
    path = SIMPLE / "design_graph.json"
    return DesignGraph.model_validate_json(path.read_text(encoding="utf-8"))


def test_placement_plan_writes_domains_and_groups(graph: DesignGraph, tmp_path: Path):
    plan = build_placement_plan(graph)
    assert plan.objective == "routing"
    assert plan.domains
    assert plan.groups
    out = tmp_path / "placement_plan.json"
    out.write_text(plan.model_dump_json(indent=2) + "\n")
    loaded = FunctionalGroupsReport.model_validate_json(out.read_text())
    assert len(loaded.groups) == len(plan.groups)


def test_placement_busy_helpers():
    from backend.services.projects import ProjectMeta, STATUS_QUEUED, STATUS_RUNNING

    # Mirror placement_pipeline helpers without importing the worker stack
    # (that pulls Anthropic via services.pipeline in lean test envs).
    active = frozenset({"queued", "running"})
    analysis = frozenset({STATUS_QUEUED, STATUS_RUNNING})

    draft = ProjectMeta(id="p", name="t", created="2026-01-01", user_id="u")
    assert (draft.placement_status or "draft") not in active
    assert draft.status not in analysis

    draft.placement_status = "queued"
    assert (draft.placement_status or "draft") in active
    draft.status = STATUS_RUNNING
    assert draft.status in analysis
