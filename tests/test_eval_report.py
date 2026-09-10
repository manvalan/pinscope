"""Eval harness — finding count, citation hit-rate, precision/recall.

Favor: perfect golden match; citation rate ignores deterministic findings;
simple_project graph still has U1/U2/U3 and the two I2C pull-up keys.
Against: extra finding drops precision; missing golden key drops recall;
Unverified quotes are citation misses, not hits.
"""

from __future__ import annotations

from pathlib import Path

from backend.pinscopex.eval_report import (
    citation_hit_rate,
    eval_simple_project,
    finding_key,
    score_report,
)
from backend.pinscopex.models import Finding


def _f(**kwargs) -> Finding:
    defaults = dict(designator="U1", finding="x", status="WARNING")
    defaults.update(kwargs)
    return Finding(**defaults)


def test_perfect_match_is_precision_and_recall_one():
    f = _f(rule_id="PS-I2C-001", designator="U3", net="/I2C0.SDA")
    scores = score_report([f], {finding_key(f)})
    assert scores.precision == 1.0
    assert scores.recall == 1.0
    assert scores.finding_count == 1
    assert scores.extra_keys == []
    assert scores.missing_keys == []


def test_extra_finding_drops_precision_not_recall():
    gold = _f(rule_id="PS-I2C-001", designator="U3", net="/I2C0.SDA")
    extra = _f(rule_id="PS-BOM-001", designator="U1", net="")
    scores = score_report([gold, extra], {finding_key(gold)})
    assert scores.recall == 1.0
    assert scores.precision == 0.5
    assert scores.extra_keys == ["PS-BOM-001|U1|"]


def test_missing_golden_key_drops_recall():
    gold_a = "PS-I2C-001|U3|/I2C0.SDA"
    gold_b = "PS-I2C-001|U3|/I2C0.SCL"
    produced = [_f(rule_id="PS-I2C-001", designator="U3", net="/I2C0.SDA")]
    scores = score_report([produced[0]], {gold_a, gold_b})
    assert scores.precision == 1.0
    assert scores.recall == 0.5
    assert scores.missing_keys == [gold_b]


def test_citation_rate_ignores_deterministic_and_counts_unverified():
    det = _f(
        source="i2c_pullup_check",
        rule_id="PS-I2C-001",
        source_quote="ignored because deterministic",
        why="no pull-up",
    )
    ok = _f(
        source="review",
        source_quote="Connect a 100 nF capacitor close to VDD.",
        why="missing cap",
        status="ERROR",
    )
    bad = _f(
        source="review",
        source_quote="This quote is long enough to count.",
        why="Unverified: cited text not found in the datasheet.",
        status="WARNING",
    )
    assert citation_hit_rate([det, ok, bad]) == 0.5
    scores = score_report([det, ok, bad], {finding_key(det)})
    assert scores.unverified_pct == 100.0 / 3
    assert scores.citation_hit_rate == 0.5


def test_simple_project_eval_matches_committed_golden():
    root = Path(__file__).resolve().parents[1] / "simple_project"
    scores = eval_simple_project(root)
    assert scores.graph_ok, scores.graph_errors
    assert scores.precision == 1.0
    assert scores.recall == 1.0
    assert scores.finding_count == 2
    assert scores.by_status["WARNING"] == 2


def test_simple_project_eval_rejects_truncated_graph(tmp_path: Path):
    import json
    import shutil

    src = Path(__file__).resolve().parents[1] / "simple_project"
    dest = tmp_path / "simple_project"
    shutil.copytree(src, dest)
    g = json.loads((dest / "design_graph.json").read_text())
    g["components"] = {"R1": g["components"]["R1"]}
    (dest / "design_graph.json").write_text(json.dumps(g))
    scores = eval_simple_project(dest)
    assert scores.graph_ok is False
    assert any("U1" in e or "missing ref" in e for e in scores.graph_errors)
