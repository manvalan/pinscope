"""Multi-file / zip KiCad schematic ingest."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from backend.pinscopex.netlist_bundle import (
    find_kicad_pcb,
    materialize_netlist_upload,
    sniff_netlist_kind,
)
from backend.pinscopex.parsers import parse_netlist_any, validate_netlist

_LIB_R = """
  (lib_symbols
    (symbol "Device:R"
      (pin passive (at 0 3.81 90) (length 2.54)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
      (pin passive (at 0 -3.81 90) (length 2.54)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27))))
      )
    )
  )
"""


def _resistor(ref: str, value: str) -> str:
    uid = "aaaaaaaa-aaaa-aaaa-aaaa-" + ref.encode().hex()[:12].ljust(12, "0")
    return f"""
  (symbol
    (lib_id "Device:R")
    (at 0 0 0)
    (unit 1)
    (uuid "{uid}")
    (property "Reference" "{ref}" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "{value}" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (pin "1" (uuid "p1"))
    (pin "2" (uuid "p2"))
  )
"""


def _sch(*body: str) -> str:
    return (
        "(kicad_sch (version 20250114) (uuid \"11111111-1111-1111-1111-111111111111\")"
        + _LIB_R
        + "".join(body)
        + "\n)\n"
    )


def _root_with_child() -> tuple[str, str]:
    child = _sch(
        _resistor("C1", "100n"),
        """
  (global_label "GND" (at 0 3.81 0) (uuid "cccccccccccccccccccccccccccccccccccc"))
""",
    )
    root = _sch(
        _resistor("R1", "10k"),
        """
  (global_label "GND" (at 0 3.81 0) (uuid "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
  (sheet
    (at 50 0)
    (size 20 20)
    (property "Sheetname" "Child" (at 50 0 0) (effects (font (size 1.27 1.27))))
    (property "Sheetfile" "child.kicad_sch" (at 50 0 0) (effects (font (size 1.27 1.27))))
  )
""",
    )
    return root, child


def test_sniff_rejects_pcb_as_netlist():
    assert sniff_netlist_kind(b"(kicad_pcb (version 1)\n") == "kicad_pcb"
    assert sniff_netlist_kind(b"PK\x03\x04rest") == "zip"
    assert sniff_netlist_kind(b"(kicad_sch (version 1)") == "kicad_sch"
    assert sniff_netlist_kind(b"*PADS-PCB*\n*PART*\n") == "pads"


def test_root_alone_missing_child_explains_multi_file(tmp_path: Path):
    from backend.pinscopex.parsers_kicad import parse_kicad

    root, _child = _root_with_child()
    parsed = materialize_netlist_upload(
        [("root.kicad_sch", root.encode())],
        tmp_path / "work",
    )
    with pytest.raises(ValueError, match="child.kicad_sch"):
        parse_kicad(parsed.root)


def test_multiple_sch_files_parse(tmp_path: Path):
    root, child = _root_with_child()
    parsed = materialize_netlist_upload(
        [
            ("root.kicad_sch", root.encode()),
            ("child.kicad_sch", child.encode()),
        ],
        tmp_path / "work",
    )
    parts, nets, fmt = parse_netlist_any(parsed.root)
    assert fmt == "kicad_sch"
    assert "R1" in parts and "C1" in parts
    assert ("R1", "1") in nets["GND"] and ("C1", "1") in nets["GND"]
    assert validate_netlist(parts, nets) == []
    assert parsed.pcb is None


def test_zip_with_nested_folder_and_pcb(tmp_path: Path):
    root, child = _root_with_child()
    pcb = b'(kicad_pcb (version 20240108) (generator pcbnew)\n  (net 0 "")\n)\n'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("board/root.kicad_sch", root)
        zf.writestr("board/child.kicad_sch", child)
        zf.writestr("board/board.kicad_pcb", pcb)
    parsed = materialize_netlist_upload(
        [("board.zip", buf.getvalue())],
        tmp_path / "work",
    )
    parts, nets, _fmt = parse_netlist_any(parsed.root)
    assert "R1" in parts and "C1" in parts
    assert parsed.pcb is not None
    assert find_kicad_pcb(parsed.work_dir) is not None


def test_zip_slip_is_rejected(tmp_path: Path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.kicad_sch", _sch(_resistor("R1", "1k")))
    with pytest.raises(ValueError, match="Rejected"):
        materialize_netlist_upload(
            [("bad.zip", buf.getvalue())],
            tmp_path / "work",
        )


def test_api_accepts_zip_and_companion_sheets(tmp_path: Path):
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.services.storage import LocalStorageBackend

    app.state.storage = LocalStorageBackend(tmp_path)
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "kicad"}).json()["id"]
    root, child = _root_with_child()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("root.kicad_sch", root)
        zf.writestr("child.kicad_sch", child)
    resp = client.post(
        f"/api/projects/{pid}/upload/netlist",
        files={"file": ("board.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["parts"] == 2
    assert body["format"] == "kicad_sch"
    storage = client.app.state.storage
    prefix = f"users/local/projects/{pid}/uploads/"
    assert storage.exists(prefix + "netlist.kicad_sch")
    assert storage.exists(prefix + "child.kicad_sch")


def test_api_accepts_multiple_sch_files(tmp_path: Path):
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.services.storage import LocalStorageBackend

    app.state.storage = LocalStorageBackend(tmp_path)
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "multi"}).json()["id"]
    root, child = _root_with_child()
    resp = client.post(
        f"/api/projects/{pid}/upload/netlist",
        files=[
            ("files", ("root.kicad_sch", root.encode(), "text/plain")),
            ("files", ("child.kicad_sch", child.encode(), "text/plain")),
        ],
        data={"paths": '["root.kicad_sch", "child.kicad_sch"]'},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["parts"] == 2


def test_zip_pipeline_workspace_reparses_hierarchy(tmp_path: Path):
    """Upload → storage → local workspace layout must still see child sheets."""
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.pinscopex.parsers import parse_netlist_any
    from backend.services.storage import LocalStorageBackend

    app.state.storage = LocalStorageBackend(tmp_path)
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "pipe"}).json()["id"]
    root, child = _root_with_child()
    root_nested = root.replace(
        'Sheetfile" "child.kicad_sch"',
        'Sheetfile" "sheets/child.kicad_sch"',
    )
    pcb = b'(kicad_pcb (version 20240108) (generator pcbnew)\n  (net 0 "")\n)\n'
    bom = (
        b"Reference,Value,Manufacturer Part Number\n"
        b"R1,10k,RC0603FR-0710KL\n"
        b"C1,100n,CL10B104KB8NNNC\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("proj/root.kicad_sch", root_nested)
        zf.writestr("proj/sheets/child.kicad_sch", child)
        zf.writestr("proj/board.kicad_pcb", pcb)
        zf.writestr("proj/bom.csv", bom)

    resp = client.post(
        f"/api/projects/{pid}/upload/netlist",
        files={"file": ("board.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["parts"] == 2
    assert body["sheets"] == 2
    assert body["pcb_saved"] is True
    assert body["bom_saved"] is True

    storage = client.app.state.storage
    prefix = f"users/local/projects/{pid}/uploads/"
    ws = tmp_path / "ws" / "uploads"
    ws.mkdir(parents=True)
    for key in storage.list_recursive(prefix):
        rel = key[len(prefix):]
        dest = ws / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(storage.read_bytes(key))

    parts, nets, fmt = parse_netlist_any(ws / "netlist.kicad_sch")
    assert fmt == "kicad_sch"
    assert "R1" in parts and "C1" in parts
    assert ("R1", "1") in nets["GND"] and ("C1", "1") in nets["GND"]
    assert (ws / "pcb.kicad_pcb").is_file()
    assert (ws / "bom.csv").is_file()
    meta = client.get(f"/api/projects/{pid}").json()
    assert meta["has_pcb"] is True
    assert meta["has_bom"] is True
    assert meta["has_netlist"] is True


def test_kicad_pcb_bytes_are_not_parsed_as_pads(tmp_path: Path):
    with pytest.raises(ValueError, match="board"):
        materialize_netlist_upload(
            [("board.kicad_pcb", b"(kicad_pcb (version 1)\n")],
            tmp_path / "work",
        )
