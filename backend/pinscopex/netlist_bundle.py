"""Unpack a netlist upload: one file, several KiCad sheets, or a zip.

The hierarchical ``.kicad_sch`` parser needs sibling files on disk. A single
temp file named ``tmpXXXX.kicad_sch`` cannot see ``Sheetfile`` children.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from backend.pinscopex.parsers import detect_netlist_format

MAX_BUNDLE_BYTES = 30 * 1024 * 1024
_MAX_ZIP_MEMBERS = 400

_KIND = str  # pads | edif | kicad_* | zip | kicad_pcb | unknown


@dataclass
class NetlistUpload:
    root: Path
    work_dir: Path
    pcb: Path | None
    extra_sch: list[Path]
    bom: Path | None = None


def sniff_netlist_kind(content: bytes) -> str:
    if content[:2] == b"PK":
        return "zip"
    head = content[:2048].decode("utf-8", errors="replace").lstrip("\ufeff").lstrip()
    low = head[:40].lower()
    if low.startswith("(kicad_pcb"):
        return "kicad_pcb"
    if low.startswith("(kicad_sch"):
        return "kicad_sch"
    if low.startswith("(edif"):
        return "edif"
    if low.startswith("(export"):
        return "kicad_sexp"
    if low.startswith("<?xml") or low.startswith("<export"):
        return "kicad_xml"
    if "*PADS-PCB*" in head.upper() or head.lstrip().startswith("*PART*"):
        return "pads"
    return "unknown"


def _safe_rel(name: str) -> str:
    rel = name.replace("\\", "/").strip()
    if not rel or rel.startswith("/") or rel.startswith("\\"):
        raise ValueError(f"Rejected path: {name}")
    parts = Path(rel).parts
    if ".." in parts or (parts and parts[0] == ".."):
        raise ValueError(f"Rejected path: {name}")
    return rel


_KEEP_SUFFIX = {
    ".kicad_sch",
    ".kicad_pcb",
    ".kicad_pro",
    ".kicad_net",
    ".xml",
    ".edn",
    ".edif",
    ".edf",
    ".asc",
    ".net",
    ".csv",
    ".xlsx",
}


def _keep_zip_member(rel: str) -> bool:
    parts = Path(rel).parts
    if any(
        p.endswith("-backups") or p.endswith(".pretty") or p.lower() in {"3dmodels", "__macosx"}
        for p in parts
    ):
        return False
    return Path(rel).suffix.lower() in _KEEP_SUFFIX


def _extract_zip(data: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    kept = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = _safe_rel(info.filename)
            if not _keep_zip_member(rel):
                continue
            kept += 1
            if kept > _MAX_ZIP_MEMBERS:
                raise ValueError("Zip has too many schematic files")
            total += max(info.file_size, 0)
            if total > MAX_BUNDLE_BYTES:
                raise ValueError("Zip is too large")
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src:
                payload = src.read()
            if len(payload) > MAX_BUNDLE_BYTES:
                raise ValueError("Zip member is too large")
            out.write_bytes(payload)


def _write_named(name: str, data: bytes, dest: Path) -> None:
    rel = _safe_rel(name)
    kind = sniff_netlist_kind(data)
    if kind == "zip":
        _extract_zip(data, dest)
        return
    out = dest / Path(rel).name
    # Keep a single subdirectory when the client sent webkitRelativePath.
    if "/" in rel:
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)


def find_kicad_pcb(work: Path) -> Path | None:
    hits = sorted(p for p in work.rglob("*.kicad_pcb") if p.is_file())
    return hits[0] if hits else None


def find_bom(work: Path) -> Path | None:
    """Prefer a shallow BOM path (KiCad project root over nested copies)."""
    hits = [
        p for p in work.rglob("*")
        if p.is_file() and p.suffix.lower() in {".csv", ".xlsx"}
    ]
    if not hits:
        return None
    hits.sort(key=lambda p: (len(p.relative_to(work).parts), p.name.lower()))
    return hits[0]


def _sheetfiles_of(path: Path) -> list[str]:
    from backend.pinscopex.parsers_kicad import _parse_sexp, _sheetfiles, _tag

    text = path.read_text(encoding="utf-8", errors="replace")
    tree = _parse_sexp(text)
    if _tag(tree) != "kicad_sch":
        return []
    return _sheetfiles(tree)


def _pick_root(work: Path) -> Path:
    schs: list[Path] = []
    exported: list[Path] = []
    for p in work.rglob("*"):
        if not p.is_file():
            continue
        kind = sniff_netlist_kind(p.read_bytes()[:2048])
        if kind == "kicad_sch":
            schs.append(p)
        elif kind in ("kicad_xml", "kicad_sexp", "edif", "pads"):
            exported.append(p)

    if exported:
        pref = [
            p for p in exported
            if sniff_netlist_kind(p.read_bytes()[:2048]) in (
                "kicad_xml", "kicad_sexp", "edif",
            )
        ]
        return (pref or exported)[0]

    if not schs:
        raise ValueError(
            "No schematic found. Drop the KiCad project folder or a netlist."
        )

    referenced: set[Path] = set()
    for p in schs:
        for rel in _sheetfiles_of(p):
            try:
                child = (p.parent / rel.replace("\\", "/")).resolve()
            except ValueError:
                continue
            referenced.add(child)
    roots = [p for p in schs if p.resolve() not in referenced]
    if not roots:
        raise ValueError("Cyclic sheet includes — upload an exported KiCad netlist instead.")

    pro = list(work.rglob("*.kicad_pro"))
    if len(roots) > 1 and pro:
        stems = {p.stem for p in pro}
        matched = [r for r in roots if r.stem in stems]
        if len(matched) == 1:
            return matched[0]
    if len(roots) > 1:
        names = ", ".join(sorted(r.name for r in roots))
        raise ValueError(
            f"Multiple root sheets ({names}). Upload a zip of the project, "
            "or the top-level .kicad_sch together with every Sheetfile child."
        )
    return roots[0]


def materialize_netlist_upload(
    files: list[tuple[str, bytes]],
    dest: Path,
) -> NetlistUpload:
    """Write uploaded bytes into ``dest`` and return the file to parse."""
    if not files:
        raise ValueError("No netlist file uploaded")
    dest.mkdir(parents=True, exist_ok=True)
    total = sum(len(b) for _n, b in files)
    if total > MAX_BUNDLE_BYTES:
        raise ValueError("Upload is too large")

    if len(files) == 1:
        name, data = files[0]
        kind = sniff_netlist_kind(data)
        if kind == "kicad_pcb":
            raise ValueError(
                "This is a board file. Drop the KiCad project folder, or put "
                "the .kicad_pcb on the optional board step."
            )
        if kind == "unknown" and not name.lower().endswith(".zip"):
            raise ValueError(
                "Not a netlist. Drop the KiCad project folder, a zip, or a "
                "PADS / EDIF / KiCad netlist."
            )

    for name, data in files:
        _write_named(name, data, dest)

    root = _pick_root(dest)
    pcb = find_kicad_pcb(dest)
    bom = find_bom(dest)
    extras = [
        p for p in work_sch_files(dest)
        if p.resolve() != root.resolve()
    ]
    return NetlistUpload(
        root=root, work_dir=dest, pcb=pcb, extra_sch=extras, bom=bom,
    )


def work_sch_files(dest: Path) -> list[Path]:
    return sorted(p for p in dest.rglob("*.kicad_sch") if p.is_file())
