"""Integration: deterministic findings are seeded into report.json and skipped
ICs surface under not_reviewed — driven through validate_design_async with no
datasheet PDFs (so the LLM review loop is empty and no API call is made)."""

from __future__ import annotations

import json

import pytest

from backend.pinscopex.models import (
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Net,
    NetType,
    Pin,
    PinConnection,
)
from backend.services import validation as val


@pytest.mark.asyncio
async def test_deterministic_findings_seeded_and_not_reviewed(tmp_path):
    proj = tmp_path / "proj"
    extracted = proj / "extracted"
    extracted.mkdir(parents=True)
    pdf_dir = proj / "pdfs"
    pdf_dir.mkdir()

    # U3: a UART5_TX net on the RX-only pin (pin-mux ERROR). U6: no MPN -> no
    # datasheet -> not_reviewed. No PDFs anywhere -> review loop is empty.
    graph = DesignGraph(
        components={
            "U3": Component(reference="U3", value="", footprint="",
                            component_type=ComponentType.IC, mpn="MCUX",
                            pins={"54": "MCU-UART5-TX"}),
            "U6": Component(reference="U6", value="", footprint="",
                            component_type=ComponentType.IC, mpn=None,
                            pins={"1": "I2C1-SCL-3V3"}),
        },
        nets={
            "MCU-UART5-TX": Net(name="MCU-UART5-TX", net_type=NetType.SIGNAL,
                                pins=[PinConnection(component_ref="U3", pin_number="54")]),
            "I2C1-SCL-3V3": Net(name="I2C1-SCL-3V3", net_type=NetType.SIGNAL,
                                pins=[PinConnection(component_ref="U6", pin_number="1")]),
        },
    )
    graph_path = proj / "design_graph.json"
    graph_path.write_text(graph.model_dump_json())

    cons = ComponentConstraints(
        mpn="MCUX",
        pintable=[Pin(number=54, name="PD2", functions=["UART5_RX"])],
        absolute_maximum_ratings=[], rules=[],
    )
    (extracted / "MCUX.json").write_text(cons.model_dump_json())

    report_path = proj / "report.json"
    await val.validate_design_async(
        graph_path=str(graph_path),
        output_path=str(report_path),
        datasheets_dir=str(extracted),
        pdf_dir=str(pdf_dir),
        storage=None,
    )

    data = json.loads(report_path.read_text())

    det = [f for f in data["findings"] if f.get("source") == "pin_mux_check"]
    assert len(det) == 1
    assert det[0]["status"] == "ERROR"
    assert det[0]["designator"] == "U3"
    assert det[0]["finding_id"]            # assign_finding_ids ran over it
    assert det[0]["source_page"] is None
    assert data["summary"]["ERROR"] >= 1

    assert {x["designator"] for x in data["not_reviewed"]} == {"U3", "U6"}
