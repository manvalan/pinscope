"""Score a validation report against a golden key set.

Used by the simple_project eval harness: finding counts, % Unverified,
citation hit-rate among LLM quotes, precision/recall vs golden keys.
Deterministic checks without a quote are excluded from the citation
denominator so pin-mux/BOM noise cannot inflate the rate.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from backend.pinscopex.models import DesignGraph, Finding, ValidationReport
from backend.pinscopex.pin_mux_check import check_pin_mux_feasibility
from backend.pinscopex.led_current_check import check_led_current
from backend.pinscopex.passive_rail_check import (
    check_i2c_pullups,
    check_reset_pullups,
    check_supply_decoupling,
)
from backend.pinscopex.bom_match_check import check_bom_schematic_match
from backend.pinscopex.hf_coverage_check import check_hf_decoupling_coverage
from backend.pinscopex.filter_check import check_filters
from backend.pinscopex.thermal_check import check_thermal
from backend.pinscopex.power_margin_check import check_power_margin
from backend.pinscopex.sequencing_check import check_power_sequencing
from backend.pinscopex.dnp_check import check_dnp_enables
from backend.pinscopex.lifecycle import check_lifecycle
from backend.pinscopex.errata_check import check_errata
from backend.pinscopex.internal_features_check import check_internal_features


class EvalScores(BaseModel):
    finding_count: int
    by_status: dict[str, int]
    unverified_pct: float
    citation_hit_rate: float | None
    precision: float
    recall: float
    extra_keys: list[str]
    missing_keys: list[str]
    graph_ok: bool = True
    graph_errors: list[str] = []


def finding_key(f: Finding) -> str:
    if f.rule_id:
        return f"{f.rule_id}|{f.designator}|{f.net or ''}"
    return f"{f.source or 'review'}|{f.designator}|{f.net or f.finding}"


def _is_review(f: Finding) -> bool:
    return not f.source or f.source == "review"


def citation_hit_rate(findings: list[Finding]) -> float | None:
    quoted = [
        f for f in findings
        if _is_review(f) and (f.source_quote or "").strip()
    ]
    if not quoted:
        return None
    hits = sum(1 for f in quoted if not (f.why or "").startswith("Unverified:"))
    return hits / len(quoted)


def unverified_pct(findings: list[Finding]) -> float:
    if not findings:
        return 0.0
    n = sum(1 for f in findings if (f.why or "").startswith("Unverified:"))
    return 100.0 * n / len(findings)


def score_keys(produced: set[str], golden: set[str]) -> tuple[float, float, list[str], list[str]]:
    extra = sorted(produced - golden)
    missing = sorted(golden - produced)
    precision = 1.0 if not produced else len(produced & golden) / len(produced)
    recall = 1.0 if not golden else len(produced & golden) / len(golden)
    return precision, recall, extra, missing


def run_deterministic_on_graph(graph: DesignGraph) -> list[Finding]:
    cmap: dict = {}
    out: list[Finding] = []
    out.extend(check_pin_mux_feasibility(graph, cmap))
    out.extend(check_led_current(graph))
    out.extend(check_supply_decoupling(graph, cmap))
    out.extend(check_i2c_pullups(graph, cmap))
    out.extend(check_reset_pullups(graph, cmap))
    out.extend(check_bom_schematic_match(graph.schematic_fields, graph.bom_fields))
    out.extend(check_hf_decoupling_coverage(graph, cmap))
    out.extend(check_filters(graph, cmap))
    out.extend(check_thermal(graph, cmap))
    out.extend(check_power_margin(graph, cmap))
    out.extend(check_power_sequencing(graph, cmap))
    out.extend(check_dnp_enables(graph, cmap))
    out.extend(check_lifecycle(graph, {}))
    out.extend(check_errata(graph, cmap))
    out.extend(check_internal_features(graph, cmap))
    return out


def score_report(
    findings: list[Finding],
    golden_keys: set[str],
    *,
    graph: DesignGraph | None = None,
    golden_meta: dict | None = None,
) -> EvalScores:
    keys = {finding_key(f) for f in findings}
    precision, recall, extra, missing = score_keys(keys, golden_keys)
    by_status: dict[str, int] = {"ERROR": 0, "WARNING": 0, "INFO": 0}
    for f in findings:
        by_status[f.status] = by_status.get(f.status, 0) + 1
    graph_errors: list[str] = []
    if graph is not None and golden_meta:
        for ref in golden_meta.get("required_refs") or []:
            if ref not in graph.components:
                graph_errors.append(f"missing ref {ref}")
        min_c = golden_meta.get("min_components")
        if min_c and len(graph.components) < int(min_c):
            graph_errors.append(
                f"components {len(graph.components)} < {min_c}"
            )
        min_n = golden_meta.get("min_nets")
        if min_n and len(graph.nets) < int(min_n):
            graph_errors.append(f"nets {len(graph.nets)} < {min_n}")
    return EvalScores(
        finding_count=len(findings),
        by_status=by_status,
        unverified_pct=unverified_pct(findings),
        citation_hit_rate=citation_hit_rate(findings),
        precision=precision,
        recall=recall,
        extra_keys=extra,
        missing_keys=missing,
        graph_ok=not graph_errors,
        graph_errors=graph_errors,
    )


def eval_simple_project(
    root: str | Path,
    report: ValidationReport | None = None,
) -> EvalScores:
    root = Path(root)
    graph = DesignGraph.model_validate_json(
        (root / "design_graph.json").read_text()
    )
    golden = {}
    gpath = root / "eval_golden.json"
    if gpath.is_file():
        import json
        golden = json.loads(gpath.read_text())
    if report is not None:
        findings = list(report.findings)
    else:
        findings = run_deterministic_on_graph(graph)
    keys = set(golden.get("deterministic_keys") or [])
    return score_report(findings, keys, graph=graph, golden_meta=golden)
