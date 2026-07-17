"""Cost estimator smoke-tests against the simple_project fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import projects as proj_svc
from backend.services.cost_estimator import (
    CostEstimate,
    estimate_pipeline_cost,
    estimate_stage_cost_usd,
)


SIMPLE_PROJECT = Path(__file__).resolve().parents[1] / "simple_project"


def _seed_project(storage, user_id: str, project_id: str) -> None:
    """Create a minimal project with the simple_project BOM uploaded."""
    proj_svc.create_project(storage, user_id, "Fixture")
    # create_project returns a new id; overwrite with a known one
    meta = proj_svc.create_project(storage, user_id, "Fixture2")

    # Use any CSV from simple_project as the BOM
    bom_candidates = list(SIMPLE_PROJECT.glob("*.csv"))
    if not bom_candidates:
        pytest.skip("simple_project/ has no BOM CSV")
    bom_data = bom_candidates[0].read_bytes()
    proj_svc.save_bom(storage, user_id, meta.id, bom_data)


def test_estimator_returns_credit_range(storage):
    """For a real BOM, estimator returns bounded credit range."""
    if not SIMPLE_PROJECT.is_dir():
        pytest.skip("simple_project/ not present in repo")
    meta = proj_svc.create_project(storage, "u1", "Fixture")
    bom_candidates = list(SIMPLE_PROJECT.glob("*.csv"))
    if not bom_candidates:
        pytest.skip("simple_project/ has no BOM CSV")
    proj_svc.save_bom(storage, "u1", meta.id, bom_candidates[0].read_bytes())

    est = estimate_pipeline_cost(storage, "u1", meta.id)

    assert isinstance(est, CostEstimate)
    assert est.api_cost_low <= est.api_cost_mid <= est.api_cost_high
    assert est.credits_low <= est.credits_high
    assert est.api_cost_mid > 0
    # Breakdown should have an IC extraction entry for the project's ICs.
    # (Review entries only land here when each IC has a datasheet uploaded;
    # this fixture doesn't upload PDFs, so they're skipped.)
    kinds = {item.kind for item in est.breakdown}
    assert "ic_extraction" in kinds


def test_estimator_counts_library_cache_hits(storage, tmp_path):
    """When an IC is in the library, its extraction cost becomes $0."""
    if not SIMPLE_PROJECT.is_dir():
        pytest.skip("simple_project/ not present")

    # Seed a project with the BOM
    meta = proj_svc.create_project(storage, "u1", "Fixture")
    bom_candidates = list(SIMPLE_PROJECT.glob("*.csv"))
    if not bom_candidates:
        pytest.skip("no BOM")
    proj_svc.save_bom(storage, "u1", meta.id, bom_candidates[0].read_bytes())

    before = estimate_pipeline_cost(storage, "u1", meta.id)

    # Plant a library extraction for the first IC MPN the estimator saw
    ic_items = [it for it in before.breakdown if it.kind == "ic_extraction"
                and it.source == "api_call_estimated"]
    if not ic_items:
        pytest.skip("fixture has no uncached IC")
    target_mpn = ic_items[0].identifier
    from backend.pinscopex.utils import safe_mpn

    storage.write_json(
        f"library/extracted/{safe_mpn(target_mpn)}.json",
        {"mpn": target_mpn, "pintable": [{"number": 1, "name": "VCC"}]},
    )

    after = estimate_pipeline_cost(storage, "u1", meta.id)
    assert after.cached_ic_count == before.cached_ic_count + 1
    assert after.api_cost_mid <= before.api_cost_mid
    # Savings should be at least the baseline IC cost
    assert before.api_cost_mid - after.api_cost_mid >= estimate_stage_cost_usd("ic_extraction") - 0.001


def test_estimator_errors_without_bom(storage):
    meta = proj_svc.create_project(storage, "u1", "No BOM")
    with pytest.raises(FileNotFoundError):
        estimate_pipeline_cost(storage, "u1", meta.id)
