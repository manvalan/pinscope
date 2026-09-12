#!/usr/bin/env python3
"""P0-5 smoke: deepseek-flash defaults + simple_project offline checks.

Usage:
  python3 scripts/smoke_simple_project.py           # offline (no API)
  python3 scripts/smoke_simple_project.py --live    # needs DEEPSEEK_API_KEY

Offline asserts: model defaults, vision gate, graph+eval golden, shortest_path.
Live (optional): PDF ingest attaches page images under vision.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SIMPLE = ROOT / "simple_project"
BASELINE = SIMPLE / "smoke_baseline.json"


def _check_model_defaults() -> list[str]:
    from backend.config import settings

    errs: list[str] = []
    if settings.deepseek_model != "deepseek-flash":
        errs.append(f"deepseek_model={settings.deepseek_model!r}, want deepseek-flash")
    # Mirror deepseek_provider._is_vision_model without importing openai.
    name = settings.deepseek_model.strip().lower()
    vision_ok = (
        name in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}
        or "vision" in name
        or name.startswith("deepseek-flash")
    )
    if not vision_ok:
        errs.append("deepseek-flash not treated as vision model")
    effort = (settings.deepseek_reasoning_effort or "").lower()
    if effort not in {"low", "high", "max"}:
        errs.append(f"deepseek_reasoning_effort={effort!r}, want low|high|max")
    return errs


def _check_simple_project_offline() -> list[str]:
    from backend.pinscopex.models import DesignGraph
    from backend.pinscopex.validation_tools import shortest_path

    errs: list[str] = []
    graph_path = SIMPLE / "design_graph.json"
    if not graph_path.is_file():
        return ["simple_project/design_graph.json missing"]
    g = DesignGraph.model_validate_json(graph_path.read_text(encoding="utf-8"))

    golden_path = SIMPLE / "eval_golden.json"
    if not golden_path.is_file():
        return ["simple_project/eval_golden.json missing"]
    golden_doc = json.loads(golden_path.read_text(encoding="utf-8"))
    for ref in golden_doc.get("required_refs") or []:
        if ref not in g.components:
            errs.append(f"missing ref {ref}")
    min_c = golden_doc.get("min_components")
    if min_c and len(g.components) < int(min_c):
        errs.append(f"components {len(g.components)} < {min_c}")
    min_n = golden_doc.get("min_nets")
    if min_n and len(g.nets) < int(min_n):
        errs.append(f"nets {len(g.nets)} < {min_n}")

    if "U3" in g.components and "X1" in g.components:
        pin = next(iter(g.components["U3"].pins), None)
        xpin = next(iter(g.components["X1"].pins), None)
        if pin and xpin:
            msg = shortest_path(g, {}, "U3", pin, "X1", xpin)
            print(f"note: shortest_path U3–X1 → {msg}")

    baseline = {
        "component_count": len(g.components),
        "net_count": len(g.nets),
        "graph_ok": not errs,
    }
    if BASELINE.is_file():
        prev = json.loads(BASELINE.read_text(encoding="utf-8"))
        for k in ("component_count", "net_count"):
            if prev.get(k) != baseline.get(k):
                errs.append(f"{k} {baseline.get(k)} != baseline {prev.get(k)}")
    else:
        BASELINE.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote baseline {BASELINE}")

    print(json.dumps({"offline": baseline, "errors": errs}, indent=2))
    return errs


def _check_live() -> list[str]:
    from backend.config import settings
    from backend.services.llm.deepseek_provider import _is_vision_model
    from backend.services.llm.pdf_ingest import pdf_to_openai_content

    if not settings.deepseek_api_key:
        return ["DEEPSEEK_API_KEY not set"]
    pdfs = list((ROOT / "library" / "datasheets").rglob("*.pdf"))[:1]
    if not pdfs:
        pdfs = list(SIMPLE.rglob("*.pdf"))
    if not pdfs:
        return ["no PDF found for live vision check"]
    pdf = pdfs[0]
    parts = pdf_to_openai_content(
        pdf, vision=_is_vision_model(settings.deepseek_model), max_images=4,
    )
    n_img = sum(1 for p in parts if p.get("type") == "image_url")
    print(json.dumps({"live_pdf": str(pdf), "content_parts": len(parts), "images": n_img}))
    if n_img == 0 and _is_vision_model(settings.deepseek_model):
        return ["vision model produced 0 page images"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="Also exercise PDF page images")
    args = ap.parse_args()
    errs = _check_model_defaults() + _check_simple_project_offline()
    if args.live:
        errs += _check_live()
    if errs:
        print("SMOKE FAIL:", *errs, sep="\n  - ")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
