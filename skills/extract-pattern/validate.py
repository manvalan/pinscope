#!/usr/bin/env python3
"""Validate extraction output against the pattern schema."""

import json
import re
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.json"


def validate(data: dict) -> list[str]:
    """Return list of validation errors (empty = valid)."""
    errors = []
    schema = json.loads(SCHEMA_PATH.read_text())

    for field in schema.get("required", []):
        if field not in data:
            errors.append(f"Missing required field: {field}")

    if "component_type" in data:
        ct = data["component_type"]
        if ct not in ("resistor", "capacitor", "inductor"):
            errors.append(f"component_type must be resistor/capacitor/inductor, got: {ct!r}")

    if "regex" in data:
        try:
            pattern = re.compile(data["regex"])
        except re.error as e:
            errors.append(f"Invalid regex: {e}")
            pattern = None

        if pattern and "example_mpns" in data:
            for mpn in data["example_mpns"]:
                if not pattern.match(mpn):
                    errors.append(f"Regex does not match example MPN: {mpn!r}")

    if "fields" in data:
        fields = data["fields"]
        if not isinstance(fields, list) or len(fields) == 0:
            errors.append("fields must be a non-empty array")
        else:
            for i, field in enumerate(fields):
                for f in ["name", "position", "length", "description"]:
                    if f not in field:
                        errors.append(f"fields[{i}] missing: {f}")

    if "value_decoder" in data:
        vd = data["value_decoder"]
        if not isinstance(vd, dict) or "type" not in vd:
            errors.append("value_decoder must be an object with a 'type' field")

    return errors


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 validate.py '<json string>'")
        sys.exit(1)

    try:
        data = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(f"INVALID JSON: {e}")
        sys.exit(1)

    errors = validate(data)
    if errors:
        print("VALIDATION FAILED:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("VALIDATION PASSED")
