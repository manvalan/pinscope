---
skill_name: extract-pattern
description: Extract passive component MPN pattern (resistor, capacitor, inductor) from a datasheet PDF. Returns structured data via the save_pattern tool.
---

# Extract Passive Component Pattern

Extract the part numbering system from a passive component datasheet (resistor, capacitor, inductor) and return it as structured JSON via the `save_pattern` tool.

## Steps

### 1. Read the datasheet PDF

The datasheet PDF is provided in the user message. Focus on finding the **Part Numbering System**, **Ordering Information**, or **Explanation of Part No.** section — every passive component datasheet has one. This section shows:
- A diagram or table breaking the MPN into positional fields
- The meaning of each field position
- Lookup tables mapping codes to values (sizes, tolerances, voltage ratings, etc.)
- An example part number with decoded fields

Also identify from the front page:
- **Manufacturer name** (e.g., "Uniroyal", "Samsung Electro-Mechanics")
- **Component type** — must be one of: `resistor`, `capacitor`, `inductor`
- **Series/product name** (e.g., "Thick Film Chip Resistors", "CL Series MLCC")

### 2. Extract each field

For every field in the part number format, extract:
- **name** — a short snake_case identifier matching the regex group name. Use these standard names where applicable:
  - `size` — package size code
  - `tolerance` — value tolerance
  - `resistance` — resistance value digits (for resistors)
  - `capacitance` — capacitance value digits (for capacitors)
  - `inductance` — inductance value digits (for inductors)
  - `voltage` — rated voltage
  - `wattage` — power rating (resistors)
  - `dielectric` — temperature characteristic / dielectric type (capacitors)
  - `packing_type` — tape/reel vs bulk
  - `packing_qty` — quantity per reel
  - `series` — product series prefix
  - `special` — special features
  - `thickness` — component thickness
  - `reserved` — reserved/unused codes
- **position** — 0-based character offset in the MPN string
- **length** — number of characters
- **description** — human-readable description from the datasheet
- **lookup** — complete mapping of code -> meaning extracted from the datasheet. For the primary value field (resistance/capacitance/inductance), leave lookup as `{}` since it's decoded algorithmically.

### 3. Determine the value decoder

Based on the component type and how the value field works, select the decoder type:

**For capacitors** using 3-digit EIA code in picofarads (e.g., "106" = 10x10^6 pF = 10uF):
```json
{
  "type": "eia3_pf",
  "base_unit": "pF",
  "output_unit": "F",
  "letter_multipliers": {},
  "zero_code": null,
  "conditional_on": null
}
```

**For resistors** using 4-digit code where the digit layout depends on tolerance:
```json
{
  "type": "eia4_ohm_conditional",
  "base_unit": "ohm",
  "output_unit": "ohm",
  "letter_multipliers": {"J": -1, "K": -2, "L": -3, "M": -4, "N": -5, "P": -6},
  "zero_code": "0000",
  "conditional_on": {
    "field": "tolerance",
    "high_tolerance": ["J"],
    "high_tolerance_layout": {
      "significant_start": 1,
      "significant_count": 2,
      "multiplier_index": 3
    },
    "low_tolerance_layout": {
      "significant_start": 0,
      "significant_count": 3,
      "multiplier_index": 3
    }
  }
}
```

Read the datasheet carefully for:
- Which tolerance codes use 3 vs 2 significant digits (the `high_tolerance` list)
- Whether letter multiplier codes are supported (J, K, L, etc.) and their exponent values
- Whether there's a special zero/jumper code

If the datasheet describes a different encoding scheme, adapt the decoder accordingly.

### 4. Build the regex pattern

Build a Python regex with named capture groups, one per field. The regex must:
- Start with `^` and end with `$` (full MPN match)
- Use `(?P<name>...)` syntax for each field
- Be as specific as possible — enumerate known codes in alternation groups (e.g., `(?P<size>0603|0805|1206)`) rather than broad patterns like `\d{4}`
- Handle the value field with appropriate character classes (digits + any letter multiplier codes)

### 5. Assign component subtype (taxonomy)

The existing passive taxonomy subtypes are provided in the system prompt under `EXISTING PASSIVE TAXONOMY SUBTYPES`. Pick the most specific matching subtype.

If no existing subtype fits, propose a new one following the dot-notation convention (`passive.{type}.{specific}`).

### 6. Quality checks

Before producing output, verify:
- The regex matches ALL example MPNs (provided in the system prompt as BOM MPNs)
- Every field has position + length that sum correctly across the full MPN
- No field positions overlap
- The primary value field (resistance/capacitance) has an empty `lookup` dict (it's decoded algorithmically)
- All other fields have non-empty lookup dicts with codes extracted from the datasheet
- The value decoder type is appropriate for the component type

### 7. Validate and output

Validate your extraction against the output schema:

```bash
python3 /skills/extract-pattern/validate.py '<your JSON here>'
```

If validation passes, call the `save_pattern` tool with the structured result.
Do NOT write files to disk — use the tool.
