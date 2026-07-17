---
skill_name: extract-pintable
description: Extract pin table, package info, and component subtype from an IC datasheet PDF. Returns structured data via the save_pintable tool.
---

# Extract Pin Table & Variant Info

Extract the pin table and variant/ordering information from a component datasheet and return it as structured JSON via the `save_pintable` tool.

## Steps

### 1. Read the datasheet PDF

The datasheet PDF is provided in the user message. Focus on these sections:
- **Pin configuration / pin assignment table** — This is the primary target. Look for tables listing pin number, pin name, description, and alternate functions.
- **Ordering information / part number decoder** — Decode what each segment of the MPN means (package, temperature grade, packing, output voltage, etc.)
- **Package information** — Pin count, package type (QFP, BGA, SOT-23, etc.)

### 2. Extract the pin table

For every pin on the component, extract:
- `number` (int or str) — The pin number, or BGA ball coordinate like `"A3"`
- `name` (str) — The pin name exactly as printed in the datasheet (e.g., `"VDD"`, `"PA0/SPI0_CLK"`)
- `description` (str or null) — A brief description if the datasheet provides one
- `functions` (list[str] or null) — Alternate/multiplexed functions if the pin supports them

Rules for pin extraction:
- Include ALL pins — power, ground, NC, and signal pins
- Use pin names verbatim from the datasheet — do not rename or normalize
- For multiplexed pins, put the primary name in `name` and alternates in `functions`
- If the datasheet has separate tables for different packages, extract for the package matching the MPN
- Pay careful attention to pin numbering — off-by-one errors here break everything downstream

### 3. Extract package info

Decode the MPN and package details into a single `PackageInfo`:
- `base_family` (str) — The base part family (e.g., `"MSPM0G3507"` from `"MSPM0G3507SPTR"`)
- `package` (str) — Package name (e.g., `"LQFP-48"`, `"SOT-23-5"`)
- `pin_count` (int) — Number of pins
- `description` (str) — Human-readable decoding of the full MPN (e.g., `"MSPM0G3507, 48-pin LQFP, tape & reel"`)

Look for an "Ordering Information" or "Device Information" table in the datasheet — most datasheets have one.

### 4. Assign component subtype (taxonomy)

The existing IC taxonomy subtypes are provided in the system prompt under `EXISTING IC TAXONOMY SUBTYPES`. Pick the best matching subtype based on the component's MPN, package info, and pin names.

If no existing subtype fits, propose a new one following the dot-notation convention (`ic.{category}.{specific}`).

Set the chosen subtype on the `component_subtype` field.

### 5. Quality checks

Before producing output, verify:
- Pin count matches what the datasheet says for this package
- No duplicate pin numbers
- No pins are missing (compare against the datasheet's stated pin count)
- Pin names look reasonable (not garbled OCR artifacts)

### 6. Validate and output

Validate your extraction against the output schema:

```bash
python3 /skills/extract-pintable/validate.py '<your JSON here>'
```

If validation passes, call the `save_pintable` tool with the structured result.
Do NOT write files to disk — use the tool.
