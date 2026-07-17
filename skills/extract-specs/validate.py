#!/usr/bin/env python3
"""Validate extraction output against the specs schema."""

import json
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

    if "component_subtype" in data:
        st = data["component_subtype"]
        if not isinstance(st, str) or "." not in st:
            errors.append(f"component_subtype must be dotted path, got: {st!r}")

    if "package_info" in data:
        pkg = data["package_info"]
        for f in ["base_family", "package", "pin_count"]:
            if f not in pkg:
                errors.append(f"package_info missing required field: {f}")
        if "pin_count" in pkg and not isinstance(pkg["pin_count"], int):
            errors.append(f"package_info.pin_count must be integer, got: {type(pkg['pin_count']).__name__}")

    if "pintable" in data:
        pins = data["pintable"]
        if not isinstance(pins, list) or len(pins) == 0:
            errors.append("pintable must be a non-empty array")
        else:
            numbers = []
            for i, pin in enumerate(pins):
                if "number" not in pin:
                    errors.append(f"pintable[{i}] missing required field: number")
                if "name" not in pin:
                    errors.append(f"pintable[{i}] missing required field: name")
                if "number" in pin:
                    numbers.append(pin["number"])
            dupes = [n for n in set(numbers) if numbers.count(n) > 1]
            if dupes:
                errors.append(f"Duplicate pin numbers: {dupes}")

    if "values" in data:
        values = data["values"]
        if not isinstance(values, dict):
            errors.append(f"values must be an object, got: {type(values).__name__}")
        else:
            for k, v in values.items():
                if v is not None and not isinstance(v, (str, int, float)):
                    errors.append(f"values[{k!r}] must be string, number, or null, got: {type(v).__name__}")

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
