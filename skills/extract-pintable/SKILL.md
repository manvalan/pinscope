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
- **Modules vs bare die (critical).** MPNs containing `WROOM`, `WROVER`, `MODULE`, `MOD-`, or `SIP` are *modules*. Extract the **module landing-pad table** (connector pins the schematic uses). Do **not** extract the SoC/QFN ball map from a nested chip chapter or a sibling chip-only PDF.
  - Espressif WROOM: pin 1 is GND (often a group of GND pads). Pin 1 named `ANT`, `CHIP_PU`, or `XTAL_*` means you grabbed the bare ESP32 die table — that will mark every module GND as “antenna shorted” and is invalid.
  - Crystal, RF antenna, and flash on a WROOM module are **inside the can**; they must not appear as schematic pin numbers.

### 3. Extract package info

Decode the MPN and package details into a single `PackageInfo`:
- `base_family` (str) — The base part family (e.g., `"MSPM0G3507"` from `"MSPM0G3507SPTR"`)
- `package` (str) — Package name (e.g., `"LQFP-48"`, `"SOT-23-5"`)
- `pin_count` (int) — Number of pins
- `description` (str) — Human-readable decoding of the full MPN (e.g., `"MSPM0G3507, 48-pin LQFP, tape & reel"`)

Look for an "Ordering Information" or "Device Information" table in the datasheet — most datasheets have one.

### 4. Extract absolute maximum ratings

Copy the **Absolute Maximum Ratings** table (not Recommended Operating Conditions). For each row that a reviewer would need to compare against the schematic rails:

- `parameter` (str) — as printed (`VCC`, `VIN`, `I/O pin voltage`, `Storage temperature`, …)
- `min` / `max` (number or null) — numeric limit; omit the other side if the table only lists one
- `unit` (str) — `V`, `mA`, `°C`, …
- `source_page` (int) — 1-based datasheet page of that row

Include supply voltages, pin/input voltages, input current, and temperature. Skip ESD *human-body-model / IEC contact-discharge kV* rows unless they are the only voltage limit given. Do not invent numbers; if the table is a raster with no readable values, return an empty array.

**ESD / TVS / protection ICs (`ic.protection.esd` and similar):** also copy from Electrical Characteristics (not only abs-max):

- Working / reverse working voltage **Vrwm** (or V_RWM / "operating voltage") as a **signed** min/max in volts — e.g. bidirectional ±13 V is `min: -13`, `max: 13`, `unit: "V"`.
- One extra row whose `parameter` states polarity/topology as printed (`bidirectional`, `unidirectional`, `back-to-back`), `unit: "—"`, min/max omitted. Do **not** infer unidirectional from "IO" pins that list GND as the reference pin.

### 5. Assign component subtype (taxonomy)

The existing IC taxonomy subtypes are provided in the system prompt under `EXISTING IC TAXONOMY SUBTYPES`. Pick the best matching subtype based on the component's MPN, package info, and pin names.

If no existing subtype fits, propose a new one following the dot-notation convention (`ic.{category}.{specific}`).

Set the chosen subtype on the `component_subtype` field.

### 6. Quality checks

Before producing output, verify:
- Pin count matches what the datasheet says for this package
- No duplicate pin numbers
- No pins are missing (compare against the datasheet's stated pin count)
- Pin names look reasonable (not garbled OCR artifacts)

### 7. Validate and output

Validate your extraction against the output schema:

```bash
python3 /skills/extract-pintable/validate.py '<your JSON here>'
```

If validation passes, call the `save_pintable` tool with the structured result.
Do NOT write files to disk — use the tool.
