---
skill_name: extract-pintable
description: Extract pin table, package info, absolute-maximum ratings, layout_rules, and component subtype from an IC datasheet PDF. Returns structured data via the save_pintable tool.
---

# Extract Pin Table & Variant Info

Extract structured data from an IC datasheet and return it via the `save_pintable` tool.

**Priority order:** (1) complete pin table for the MPN package, (2) `layout_rules` from PCB / typical-application pages, (3) package + abs-max + subtype.

## Steps

### 1. Read the datasheet PDF

Focus on these sections (figures count as evidence):
- **Pin configuration / pin assignment table** — primary target
- **Ordering information / part number decoder**
- **Package information**
- **PCB layout / layout guidelines / land pattern notes**
- **Typical application / reference design** (placement callouts near caps, vias, keepouts)
- **Absolute maximum ratings**

### 2. Extract the pin table

For every pin:
- `number` (int or str) — pin number, or BGA ball like `"A3"`
- `name` (str) — verbatim from the datasheet (e.g. `"VDD"`, `"PA0/SPI0_CLK"`)
- `description` (str or null)
- `functions` (list[str] or null) — alternate/mux functions

Rules:
- Include ALL pins — power, ground, NC, exposed pad / EP
- Names verbatim — do not rename or normalize
- Multiplexed pins: primary in `name`, alternates in `functions`
- If the datasheet has per-package tables, use the package matching the MPN
- Off-by-one pin numbers break everything downstream — double-check

**Modules vs bare die (critical).** MPNs containing `WROOM`, `WROVER`, `MODULE`, `MOD-`, or `SIP` are *modules*. Extract the **module landing-pad table** (schematic pins). Do **not** extract the SoC/QFN ball map from a nested chip chapter.
- Espressif WROOM: pin 1 is GND. Pin 1 named `ANT`, `CHIP_PU`, or `XTAL_*` means you grabbed the die table — invalid.
- Crystal, RF antenna, and flash on a WROOM module are **inside the can**; they must not appear as schematic pin numbers.

Optional extras (omit if absent):
- `internal_features.pullup_pins` / `esd_clamp_pins` / `analog_switch` from the **block diagram** only.

### 3. Extract layout_rules (required scan — empty OK)

You **must** look for layout guidance. Emit `layout_rules` as a list. Use `[]` only after scanning layout / application / thermal pages and finding no placement guidance.

#### Where to look
- Headings: “PCB Layout”, “Layout Guidelines”, “Layout Considerations”, “Board Layout”, “Land Pattern”
- “Typical Application”, “Application Circuit”, “Reference Design”
- Thermal / EP / exposed-pad via recommendations
- Callouts on application figures (“place CIN within 2 mm of VIN”)

#### Allowed `kind` (closed set)
| kind | Use when |
| --- | --- |
| `decoupling_proximity` | Bypass / decoupling / input / output cap near a supply or pin |
| `thermal_via` | Vias under exposed pad / thermal pad / EP |
| `keepout` | Keep foreign nets, digital return, or copper out of a region |
| `length_match` | Intra-pair skew / matched length limit in mm |

#### Fields
- `pin` — number or name as printed (`"5"`, `"VIN"`, `"VDD"`, `"EP"`)
- `cap_value_hint` — only if shown (`"100nF"`, `"10µF"`)
- `max_distance_mm` — **number only if the PDF states millimetres**
  - OK: “within 2 mm”, “< 5 mm”, “no more than 3 mm from the pin” → `2` / `5` / `3`
  - NOT OK as a number: “as close as possible”, “close to the pin”, “adjacent”, “nearby” → set `max_distance_mm: null` and keep the rule with a `note`
  - **Never invent** JEDEC, USB, IPC, or “standard 3 mm / 5 mm” distances
- `same_layer` — `true`/`false` only if text says same side / opposite side of the board; else null
- `min_via_count` — integer only if stated (“at least 4 vias”)
- `net_class` / `note` — short quote of the guidance
- `source_page` — 1-based page of the guidance (required when you emit a rule)

#### Examples

Numeric proximity (copy the millimetre from the PDF):

```json
{
  "kind": "decoupling_proximity",
  "pin": "VIN",
  "cap_value_hint": "10uF",
  "max_distance_mm": 2.0,
  "same_layer": true,
  "note": "Place CIN within 2 mm of VIN",
  "source_page": 14
}
```

Proximity without a millimetre (still emit the rule):

```json
{
  "kind": "decoupling_proximity",
  "pin": "VDD",
  "cap_value_hint": "100nF",
  "max_distance_mm": null,
  "note": "Place decoupling capacitor as close as possible to VDD",
  "source_page": 22
}
```

Thermal vias:

```json
{
  "kind": "thermal_via",
  "pin": "EP",
  "min_via_count": 4,
  "note": "Use at least 4 thermal vias in the exposed pad",
  "source_page": 18
}
```

#### Hard negatives
- Do not invent land-pattern pad sizes from the mechanical drawing alone
- Do not emit `length_match` for USB/HDMI/PCIe unless the **this** datasheet states a skew/length number
- Do not use kinds outside the closed set
- One rule per distinct pin/guidance; prefer supply pins that show caps in the application figure

### 4. Extract package info

- `base_family` — e.g. `"MSPM0G3507"` from `"MSPM0G3507SPTR"`
- `package` — e.g. `"LQFP-48"`, `"SOT-23-5"`
- `pin_count` (int)
- `description` — human-readable MPN decode

Prefer “Ordering Information” / “Device Information” tables.

### 5. Extract absolute maximum ratings

Copy the **Absolute Maximum Ratings** table (not Recommended Operating Conditions):

- `parameter`, `min` / `max`, `unit`, `source_page` (1-based)

Include supply voltages, pin/input voltages, input current, temperature. Skip HBM/IEC kV ESD rows unless they are the only voltage limit. Do not invent numbers.

**ESD / TVS (`ic.protection.esd` and similar):** also from Electrical Characteristics:
- Vrwm / operating voltage as signed min/max in volts
- One row for polarity/topology as printed (`bidirectional`, …), `unit: "—"`

### 6. Assign component subtype

Pick the best dotted subtype from `EXISTING IC TAXONOMY SUBTYPES` (e.g. `ic.mcu`, `ic.power.ldo`). If none fit, propose `ic.{category}.{specific}`.

### 7. Quality checks

Before output:
- Pin count matches the package for this MPN
- No duplicate / missing pin numbers
- `layout_rules` scanned (list present; `[]` only if truly no guidance)
- Every emitted rule has a valid `kind`; every numeric `max_distance_mm` comes from the PDF text/figure
- Pin names are not OCR garbage

### 8. Validate and output

```bash
python3 /skills/extract-pintable/validate.py '<your JSON here>'
```

If validation passes, call `save_pintable`. Do NOT write files to disk — use the tool.
