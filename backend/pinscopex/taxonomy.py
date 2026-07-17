"""Living component taxonomy: load, query, and grow the subtype tree.

Storage: one JSON file per top-level type in ``taxonomy/``.
Each file is a self-contained document that maps 1:1 to a Firestore
document, so only the relevant branch needs to be fetched/injected
into extraction prompts.

::

    taxonomy/
    ├── ic.json          # all IC subtypes
    ├── passive.json     # all passive subtypes
    ├── discrete.json    # diodes, transistors, LEDs
    ├── connector.json
    ├── crystal.json
    └── ...
"""

from __future__ import annotations

import json
import re
from pathlib import Path

TAXONOMY_DIR = Path(__file__).resolve().parent.parent.parent / "taxonomy"

# Reference-designator prefix -> taxonomy top-level type.
# Used by extraction skills: "I see 'U' so I only need the ic branch."
REF_PREFIX_TO_TYPE: dict[str, str] = {
    "U": "ic",
    "IC": "ic",
    "R": "passive",
    "C": "passive",
    "L": "passive",
    "FB": "passive",
    "J": "connector",
    "X": "crystal",
    "Y": "crystal",
    "D": "discrete",
    "LED": "discrete",
    "Q": "discrete",
    "T": "transformer",
    "F": "fuse",
    "SW": "switch",
    "TP": "test_point",
    "FM": "fiducial",
    "MH": "mechanical",
}

# Canonical format for dotted subtype keys.
SUBTYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

# All valid top-level taxonomy types (derived from ref-prefix mapping).
KNOWN_TYPES: frozenset[str] = frozenset(REF_PREFIX_TO_TYPE.values())


def validate_subtype(value: str) -> str:
    """Validate and normalize a component_subtype string.

    Lowercases, replaces hyphens/spaces with underscores, then checks
    the dotted format and that the top-level segment is a known type.

    Returns the normalized value.  Raises ``ValueError`` if invalid.
    """
    v = value.strip().lower().replace("-", "_").replace(" ", "_")
    if not SUBTYPE_PATTERN.match(v):
        raise ValueError(
            f"Invalid component_subtype format: {value!r}. "
            f"Expected dotted lowercase path like 'ic.mcu' or 'passive.resistor'"
        )
    top = v.split(".")[0]
    if top not in KNOWN_TYPES:
        raise ValueError(
            f"Unknown top-level taxonomy type: {top!r} (from {value!r}). "
            f"Known types: {sorted(KNOWN_TYPES)}"
        )
    return v


def type_for_ref(ref: str) -> str | None:
    """Map a reference designator (e.g. 'U3', 'C12') to a taxonomy type."""
    prefix = re.match(r"^[A-Za-z]+", ref)
    if not prefix:
        return None
    return REF_PREFIX_TO_TYPE.get(prefix.group().upper())


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_type_file(top_type: str, directory: Path = TAXONOMY_DIR) -> dict:
    """Load a single type file, returning its raw JSON."""
    path = directory / f"{top_type}.json"
    if not path.exists():
        return {"type": top_type, "subtypes": {}}
    return json.loads(path.read_text())


def _save_type_file(top_type: str, data: dict, directory: Path = TAXONOMY_DIR) -> None:
    """Write a type file back to disk."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{top_type}.json"
    path.write_text(json.dumps(data, indent=2) + "\n")


def load_subtypes(
    top_type: str | None = None,
    directory: Path = TAXONOMY_DIR,
) -> dict[str, dict]:
    """Return subtypes as ``{dotted_key: {description, example_mpn?}}``.

    If *top_type* is given (e.g. ``"ic"``), only that file is loaded —
    keeping prompt injection small.  If ``None``, all files are merged.
    """
    if top_type is not None:
        return dict(_load_type_file(top_type, directory).get("subtypes", {}))

    merged: dict[str, dict] = {}
    for f in sorted(directory.glob("*.json")):
        data = json.loads(f.read_text())
        merged.update(data.get("subtypes", {}))
    return merged


def list_subtypes(
    prefix: str | None = None,
    directory: Path = TAXONOMY_DIR,
) -> list[str]:
    """List subtype keys, optionally filtered by dotted prefix.

    Efficient: if *prefix* starts with a known top-level type, only that
    single file is loaded.

    Examples::

        list_subtypes()                 # all subtypes (loads every file)
        list_subtypes("ic")            # only ic.json loaded
        list_subtypes("ic.power")      # only ic.json loaded, filtered
        list_subtypes("passive")       # only passive.json loaded
    """
    # Determine which top-level type file to load
    top_type: str | None = None
    if prefix is not None:
        top_type = prefix.split(".")[0]

    subtypes = load_subtypes(top_type, directory)

    if prefix is None:
        return sorted(subtypes.keys())

    prefix_dot = prefix if prefix.endswith(".") else prefix + "."
    return sorted(k for k in subtypes if k == prefix or k.startswith(prefix_dot))


def get_subtype(key: str, directory: Path = TAXONOMY_DIR) -> dict | None:
    """Get a single subtype entry by its dotted key, or None."""
    top_type = key.split(".")[0]
    subtypes = load_subtypes(top_type, directory)
    return subtypes.get(key)


def set_type_specs(
    top_type: str,
    specs: list[dict],
    directory: Path = TAXONOMY_DIR,
) -> None:
    """Set type-level specs on a taxonomy file."""
    data = _load_type_file(top_type, directory)
    data["specs"] = specs
    _save_type_file(top_type, data, directory)


def set_extra_specs(
    subtype_key: str,
    extra_specs: list[dict],
    directory: Path = TAXONOMY_DIR,
) -> None:
    """Set extra_specs on an existing subtype entry."""
    top_type = subtype_key.split(".")[0]
    data = _load_type_file(top_type, directory)
    subtypes = data.get("subtypes", {})
    if subtype_key not in subtypes:
        return
    subtypes[subtype_key]["extra_specs"] = extra_specs
    _save_type_file(top_type, data, directory)


def has_specs(top_type: str, directory: Path = TAXONOMY_DIR) -> bool:
    """Check if a taxonomy type has any specs defined (type-level or extra)."""
    data = _load_type_file(top_type, directory)
    if data.get("specs"):
        return True
    for entry in data.get("subtypes", {}).values():
        if entry.get("extra_specs"):
            return True
    return False


def add_subtype(
    key: str,
    description: str,
    example_mpn: str | None = None,
    directory: Path = TAXONOMY_DIR,
) -> None:
    """Add a new subtype. Creates the type file if needed. No-op if exists."""
    key = validate_subtype(key)
    top_type = key.split(".")[0]
    data = _load_type_file(top_type, directory)
    subtypes = data.setdefault("subtypes", {})

    if key in subtypes:
        return

    entry: dict[str, str] = {"description": description}
    if example_mpn:
        entry["example_mpn"] = example_mpn
    subtypes[key] = entry

    data["type"] = top_type
    _save_type_file(top_type, data, directory)


def get_specs_schema(
    top_type: str,
    subtype_key: str | None = None,
    directory: Path = TAXONOMY_DIR,
) -> list[dict]:
    """Return merged specs list: type-level ``specs`` + subtype ``extra_specs``."""
    data = _load_type_file(top_type, directory)
    specs = list(data.get("specs", []))
    if subtype_key:
        entry = data.get("subtypes", {}).get(subtype_key, {})
        specs.extend(entry.get("extra_specs", []))
    return specs


def format_specs_for_prompt(top_type: str, directory: Path = TAXONOMY_DIR) -> str:
    """Format type-level + all subtype extra_specs as prompt text.

    Includes all possible parameters across subtypes so the extraction
    skill knows the full set of fields it might encounter.
    """
    data = _load_type_file(top_type, directory)
    base_specs = data.get("specs", [])
    # Collect all extra_specs across subtypes (deduplicate by name)
    all_extra: dict[str, dict] = {}
    for entry in data.get("subtypes", {}).values():
        for s in entry.get("extra_specs", []):
            all_extra[s["name"]] = s
    all_specs = list(base_specs) + list(all_extra.values())
    if not all_specs:
        return ""
    lines = [
        "PARAMETERS TO EXTRACT (include all that are relevant to this component):",
        "",
        "Use SPICE multiplier prefixes for values: "
        "T=1e12, G=1e9, M=1e6, k=1e3, m=1e-3, u=1e-6, n=1e-9, p=1e-12.",
        "Examples: 30V, 240mV, 500mA, 47mohm, 18pF, 8MHz, 10nC.",
        "Always include the unit with the multiplier in the value string.",
        "",
    ]
    for s in all_specs:
        req = " (REQUIRED)" if s.get("required") else ""
        unit = f" [{s['unit']}]" if s.get("unit") else ""
        lines.append(f"- {s['name']}{unit}: {s['description']}{req}")
    return "\n".join(lines)


def format_for_prompt(top_type: str, directory: Path = TAXONOMY_DIR) -> str:
    """Format a type's subtypes as a compact string for LLM prompt injection.

    Returns something like::

        ic.mcu — Microcontroller (e.g. MSPM0G3507SPTR)
        ic.power.ldo — Low-dropout voltage regulator (e.g. SPX3819M5-L-3-3)
        ic.power.switching_regulator — Switching voltage regulator (buck, boost, buck-boost)
        ...
    """
    subtypes = load_subtypes(top_type, directory)
    lines: list[str] = []
    for key in sorted(subtypes):
        entry = subtypes[key]
        line = f"{key} — {entry['description']}"
        if "example_mpn" in entry:
            line += f" (e.g. {entry['example_mpn']})"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Simple types (taxonomy-driven specs extraction via PDF)
# ---------------------------------------------------------------------------


def _compute_simple_types(directory: Path = TAXONOMY_DIR) -> frozenset[str]:
    """Types that have a ``specs`` schema and use PDF-based extraction.

    Excludes ``ic`` (pintable + rules) and ``passive`` (pattern-based).
    """
    result: set[str] = set()
    if not directory.is_dir():
        return frozenset(result)
    for f in directory.glob("*.json"):
        data = json.loads(f.read_text())
        t = data.get("type", "")
        if t not in ("ic", "passive") and data.get("specs"):
            result.add(t)
    return frozenset(result)


SIMPLE_TYPES: frozenset[str] = _compute_simple_types()
