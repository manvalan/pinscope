---
skill_name: extract-specs
description: Extract pin table, package info, and electrical specifications from a discrete/simple component datasheet PDF. Returns structured data via the save_specs tool.
---

# Extract Component Specifications & Pin Table

Extract the pin table, package info, and key electrical specifications from a component datasheet and return them as structured JSON via the `save_specs` tool.

## Steps

### 1. Read the datasheet PDF

The datasheet PDF is provided in the user message. Focus on these sections:
- **Pin configuration / pin assignment table** — Pin number, pin name, description
- **Package information** — Pin count, package type
- **Electrical characteristics** — The primary source of parameter values
- **Absolute maximum ratings** — Maximum voltage, current, and power limits

### 2. Identify the component subtype

The system prompt provides a list of taxonomy subtypes. Choose the best match for this component. If none match, propose a new subtype following the dotted naming convention.

### 3. Extract the pin table

For every pin on the component, extract:
- `number` (int or str) — The pin number as printed in the datasheet
- `name` (str) — The pin name exactly as printed (e.g., `"A"` for anode, `"K"` for cathode, `"G"` for gate)
- `description` (str or null) — A brief description if the datasheet provides one
- `functions` (list[str] or null) — Alternate functions if the pin supports them

Rules for pin extraction:
- Include ALL pins — including pad/tab/exposed pad pins
- Use pin names verbatim from the datasheet — do not rename or normalize
- Pay careful attention to pin numbering — off-by-one errors break downstream validation
- For multi-pin packages (e.g., SOT-23 transistor), ensure the pin assignment matches the specific package variant

### 4. Extract package info

Decode the MPN and package details:
- `base_family` (str) — The base part family (e.g., `"BAT54"` from `"BAT54S"`)
- `package` (str) — Package name (e.g., `"SOT-23"`, `"SOD-123"`, `"TO-220"`)
- `pin_count` (int) — Number of pins
- `description` (str) — Human-readable decoding of the full MPN

### 5. Extract specifications

The system prompt contains a "PARAMETERS TO EXTRACT" section listing the **ONLY** parameters you should extract. These are the standardized parameters for this component type that are useful for schematic validation.

**CRITICAL: Extract ONLY the parameters listed in "PARAMETERS TO EXTRACT".** Do not add any other parameters, even if they appear in the datasheet. Parameters like contact material, insulator material, processing temperature, orientation, mounting type, plating, etc. are NOT useful for schematic validation and MUST be excluded.

For each listed parameter:

- **Search systematically**: Check electrical characteristics tables, absolute maximum ratings, and application notes
- **Prefer typical operating values** where available, but note maximums for rating parameters
- **Use SPICE multiplier prefixes** for all values with units: `T`=1e12, `G`=1e9, `M`=1e6, `k`=1e3, `m`=1e-3, `u`=1e-6, `n`=1e-9, `p`=1e-12. Pick the multiplier that gives the most readable number.
  - Good: `"30V"`, `"240mV"`, `"500mA"`, `"47mohm"`, `"18pF"`, `"8MHz"`, `"10nC"`
  - Bad: `"0.24V"`, `"0.5A"`, `"0.047ohm"`, `"0.000000000018F"`, `"8000000Hz"`
- **Always include the unit** with the multiplier in the value string
- **Use numeric values** only when the parameter is inherently unitless (e.g., turns ratio, pin count, hFE)
- **Use null** for parameters that are not applicable to this component or not found in the datasheet

Rules:
- Extract from the datasheet only — do not infer or calculate values
- If a parameter has different values at different conditions, use the value at the most common/standard condition
- For parameters with min/typ/max, prefer typical; include all in the string if they matter (e.g., `"550mV typ, 850mV max"`)
- **ONLY use parameter names from the "PARAMETERS TO EXTRACT" list** — any extra keys will be discarded

### 6. Call save_specs

Call the `save_specs` tool with:
- `component_subtype`: The dotted taxonomy path (e.g., `"discrete.diode.schottky"`)
- `component_subtype_description`: A brief description if this is a new subtype
- `package_info`: Package details (base_family, package, pin_count, description)
- `pintable`: Array of pin objects (number, name, description, functions)
- `values`: An object mapping parameter names to their extracted values
