# File Upload Guide

Pinscope needs two files from your EDA tool to review a design:

- A **PADS-PCB ASCII netlist** or an **EDIF 2.0.0 netlist** — the circuit's connectivity. Pinscope accepts `.asc`, `.net`, `.NET`, `.txt` (PADS-PCB) and `.edn`, `.edif`, `.edf` (EDIF); the format is auto-detected from the file's first bytes.
- A **Bill of Materials** (CSV or XLSX) — mapping each reference designator to a manufacturer part number.

## Example files

New to Pinscope? Here's a complete set of files from [Phil's Lab](https://www.youtube.com/@PhilsLab)' KiCad 9 TI MSPM0 tutorial you can download and upload as a starter project:

- [TI-MSP-KICAD9-TUTORIAL.asc](/examples/TI-MSP-KICAD9-TUTORIAL.asc) — netlist
- [TI-MSP-KICAD9-TUTORIAL.csv](/examples/TI-MSP-KICAD9-TUTORIAL.csv) — BOM
- [TI-MSP-KICAD9-TUTORIAL-SCHEMATIC.pdf](/examples/TI-MSP-KICAD9-TUTORIAL-SCHEMATIC.pdf) — schematic (for your reference; Pinscope doesn't need this)

The BOM below shows the shape Pinscope is looking for.

## The BOM

Pinscope auto-detects BOM columns by header name. After upload you'll confirm which column holds designators and which holds part numbers, so the exact header names don't matter — only that these columns exist.

**Required**

- **Designator / Reference** — one row per part, or grouped references like `C1,C2,C5` in a single row (Pinscope expands these automatically).
- **Manufacturer Part Number (MPN)** — the full orderable part number. Pinscope uses this to look up datasheets, so `10uF 0805` on its own is **not** enough — it needs e.g. `GRM21BR61C106KE15L`.

**Recommended**

- **Value** or **Comment** — passive values (`10uF`, `4.7k`, `8MHz`). Used for passive value resolution and mismatch detection when the MPN can't be matched.
- **Footprint** — used to enrich the design graph.

CSV and XLSX both work. For XLSX, the first worksheet is used.

## The Netlist

Pinscope accepts either a **PADS-PCB ASCII netlist** or an **EDIF 2.0.0 netlist** — the upload form auto-detects which one you sent based on the file's first bytes, so you don't have to pick a format.

### PADS-PCB ASCII

This is the default format most EDA tools can export. It looks like this:

```text
*PADS-PCB*
*PART*
U1 LQFP48
C1 0402
...
*NET*
*SIGNAL* GND
U1.1 U1.48 C1.2
*SIGNAL* VCC
U1.24 C1.1
...
*END*
```

If your file starts with `*PADS-PCB*` and ends with `*END*`, you're good. Reference designators may contain spaces (e.g. `CV GND`) — Pinscope resolves them against your BOM.

**Heads up — two different `.asc` files exist.** PADS (and tools that interop with PADS, like Xpedition) use the `.asc` extension for two unrelated things:

- The **schematic-exported netlist** starts with `*PADS-PCB*` and lists `*PART*` / `*NET*` sections. **This is what Pinscope wants.**
- The **full PCB layout dump** starts with `!PADS-POWERPCB-V…` and contains routing/footprint geometry. Pinscope cannot parse this.

If your upload errors with "No components found", check the first line of the file.

### EDIF 2.0.0

EDIF is a vendor-neutral s-expression format. The first form is `(edif …`, with an `(edifVersion 2 0 0)` declaration near the top, libraries that define each cell's pin list, and a design library that lists `(instance …)` and `(net …)` forms. Pinscope has been verified against **Siemens xDX Designer / DxDesigner** exports; other EDIF 2.0.0 exporters (OrCAD, Altium, KiCad, Eagle) follow the same grammar and should work, but haven't been broadly tested. If your EDIF file doesn't parse, [contact us](/contact) and send a snippet — most fixes are small.

If your tool exports both PADS-PCB and EDIF, PADS-PCB is the path more users have validated; use EDIF when it's the only option.

## Exporting from your EDA tool

### KiCad

Works with KiCad 7.x, 8.x, and 9.x.

**Netlist**

1. Open the schematic in eeschema.
2. **File → Export → Netlist…**
3. Select the **PADS** tab → **Export Netlist**.
4. Save the resulting `.net` file and upload it directly.

**BOM**

1. In eeschema: **Tools → Generate BOM…**
2. Use the built-in `bom_csv_grouped_by_value_with_fp` plugin (or similar) — it produces a CSV with Reference, Value, Footprint, and any custom MPN field on your symbols.
3. If your symbols don't have an MPN field yet, add one via **Edit → Edit Symbol Fields** before generating the BOM.

### Altium Designer

**Netlist**

1. In the Schematic Editor: **Design → Netlist for Project → PADS**.
2. The `.NET` file lands in `Project Outputs for …`. Upload it directly.

**BOM**

1. **Reports → Bill of Materials**.
2. In the template, include `Designator`, `Manufacturer Part Number` (or your equivalent parameter), `Comment` (= value), and `Footprint`.
3. **Export** → CSV or Excel.

### OrCAD / Allegro

For OrCAD Capture and Allegro Design Entry.

**Netlist**

1. **Tools → Create Netlist** → format **PADS-PCB (.asc)**.
2. Save to the project output directory.

**BOM**

1. **Tools → Bill of Materials** → configure columns to include `Reference`, `Manufacturer Part Number`, and `Value`.
2. Export CSV.

### Xpedition

Works in **Xpedition Designer / DxDesigner** (VX.2.x, including VX.2.14). Xpedition and PADS are both Siemens EDA tools and share a netlist exchange format — but the PADS netlist export is only available in projects created with the **Netlist** project type. Projects on the integrated Xpedition flow (which forward directly into Xpedition Layout) don't expose PADS as a layout target.

**Netlist**

1. Open the schematic in Xpedition Designer / DxDesigner.
2. **Setup → Settings → Layout Tool** → confirm **PADS** (or PADS Professional) is selected. If this option isn't available, the project was created as an Integrated/Expedition project — create a new Netlist-type project and import the schematics into it.
3. **Tools → PCB Interface** → choose the **PADS** template (`pads2007.cfg` or equivalent).
4. Run the export. The output's first line should be `*PADS-PCB*` with `*PART*` and `*NET*` sections below. The file extension (`.txt`, `.net`, or `.asc`) doesn't matter — upload it as-is.

If your project is locked to the integrated Xpedition flow, **export EDIF instead** — DxDesigner's EDIF Exporter runs against any project type and Pinscope accepts the resulting `.edn` as an equivalent input. See **Netlist (EDIF alternative)** below.

**Netlist (EDIF alternative)**

1. Open the schematic in Xpedition Designer / DxDesigner.
2. **File → Export → EDIF…** (older builds: **Tools → Run Tool → Edif Exporter**).
3. In the export dialog:
   - **EDIF version**: **2.0.0** (Pinscope only supports 2.0.0)
   - **EDIF level**: **0** (the default)
   - **Output format**: **Netlist view** — make sure cells, instances, nets, and the `viewMap` back-annotation block are all included
   - **Designator source**: include back-annotated designators (otherwise instances export as templates like `U?` / `R?` and Pinscope drops them)
4. Save as `<design>.edn` and upload it as the netlist. The file should start with `(edif …)` and contain `(edifVersion 2 0 0)` near the top.

If neither PADS nor EDIF works for your project setup, send us the BOM and schematic PDF via [Contact](/contact) — we can usually help unblock the export.

**BOM**

1. **Reports → Bill of Materials** in Xpedition Designer, or run the Variant Manager BOM export.
2. Include `Reference Designator`, `Part Number` (manufacturer part), `Value`, and `Footprint` columns.
3. Export CSV or XLSX.

### EasyEDA

For both EasyEDA Standard and EasyEDA Pro.

**Netlist**

1. EasyEDA Std: **File → Export → PADS-PCB Netlist (.asc)**.
2. EasyEDA Pro: **Design → Output → Netlist → PADS-PCB**.

**BOM**

1. **Fabrication → BOM** → export CSV.
2. Confirm the Manufacturer Part Number column is populated — it comes from the LCSC supplier data or from a custom MPN attribute you've set on each part.

### Autodesk Eagle

**Netlist**

1. In the schematic editor: **File → Run ULP → `pads-pcb.ulp`** (or **File → Export → Netlist → PADS** in newer builds).
2. Save and upload as-is.

**BOM**

1. **File → Export → Bill of Materials** → CSV.
2. Ensure your parts carry an `MPN` or `MFR_PART` attribute so the column ends up in the export.

## Troubleshooting

- **"No components found"** — your netlist is missing the `*PART*` section. Re-export specifically in PADS-PCB format (not Spice, Protel, or a generic text netlist). If the first line is `!PADS-POWERPCB-V…`, you uploaded the PCB layout dump instead of the schematic netlist — re-export from the schematic side.
- **"No ground net found"** — your netlist has no net named `GND`, `VSS`, `AGND`, `DGND`, or similar. If you exported a sub-sheet, re-export the top sheet instead.
- **Unresolved parts after the pipeline runs** — a BOM row has no MPN, or the MPN wasn't found on DigiKey. Add the MPN, or rely on Pinscope's value fallback (fills in from the `Value` / `Comment` column).

Still stuck? [Contact us](/contact) with your netlist and BOM attached and we'll take a look.
