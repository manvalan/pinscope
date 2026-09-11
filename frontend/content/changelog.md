# Changelog

What's new in Pinscope.

## 2.26.3 — 2026-09-11 — KiCad project zip upload

One zip (or every `.kicad_sch`) covers hierarchical sheets. If the zip has `bom.csv` and `.kicad_pcb`, those are taken too. Pipeline re-reads companions from storage.

- [New] Zip → sheets + optional BOM/PCB. Nested `Sheetfile` paths kept.
- [Test] Upload → workspace re-parse finds child sheet parts.

## 2.26.2 — 2026-09-11 — Three file boxes

New project: BOM, schematic, and PCB on the first screen. PCB is optional.

- [Changed] No extra wizard step for the board.

## 2.26.1 — 2026-09-11 — Ask for the KiCad board

The create-project wizard has a dedicated PCB step (skip still allowed). Report load errors show the API detail instead of a generic fetch failure.

- [New] Wizard step for `.kicad_pcb`. Upload later from Settings or Impedance.
- [Improved] Report page retries briefly and links back to pipeline progress when `report.json` is missing.
- [New] Drop the KiCad project folder (or a zip). Extra library junk is ignored; the board is picked up if present.

## 2.26.0 — 2026-09-11 — ImpedenceFinder Z0 on PCB nets

A pipeline run with `.kicad_pcb` + stackup samples routed **signal** nets (power/ground skipped). Extra net names can be analyzed from the Impedance tab. Z0 is ImpedenceFinder net_walk/zsolver; no invented εr.

- [New] `impedance_nets.json` after graph build. GET/POST `/api/projects/{id}/impedance/nets`.

## 2.25.0 — 2026-09-10 — Keepout courtyard

`layout_rules` `keepout` flags a foreign net whose track endpoint is inside the KiCad courtyard. Own net and missing courtyard skip. No invented analog/digital classes.

- [New] `PS-PLC-004` WARNING. Tests use `X1` / `/HFXIN` / `GND` from `simple_project`.

## 2.24.0 — 2026-09-10 — Crystal load caps and track path

Load caps on XIN/XOUT use the same `max_distance_mm` as decoupling. If the PCB has segments on the net, the limit is shortest-path length, not a guessed “loop is too big” ratio.

- [New] Crystals (`X1` / C9 / C10 on `simple_project`) run `PS-PLC-001` when `layout_rules` has millimetres.
- [New] Detour tracks: path along segments vs the same `max_distance_mm`. No segments → euclidean pad distance.

## 2.23.0 — 2026-09-10 — same_layer decoupling

If `layout_rules` sets `same_layer: true`, a decoupling cap on the opposite copper from the IC is a WARNING. A via inside the courtyard (calculated) is enough. Unset flag → skip.

- [New] `PS-PLC-003` WARNING when every placed cap on the net is on F vs B opposite the IC.

## 2.22.0 — 2026-09-10 — Thermal vias vs min_via_count

Via count is calculated inside the KiCad courtyard. The limit is the `min_via_count` parameter from `layout_rules`. No courtyard or no count → skip. No pad radius default.

- [New] `PS-PLC-002` when vias in courtyard < `min_via_count`. `simple_project` has no PCB so it stays silent.

## 2.21.0 — 2026-09-10 — Layout SI skew (datasheet mm only)

Intra-pair skew is measured on the PCB only when `layout_rules` `length_match` has a number. 3W, creepage, and CPWG are not guessed.

- [New] `PS-SI-001` ERROR when a named pair (_DP/_DM, _P/_N) exceeds that millimetre. No mm in the datasheet → skip.

## 2.20.0 — 2026-09-10 — Placement vs datasheet (PCB)

Decoupling distance is measured on the `.kicad_pcb` against `layout_rules`. No board → no `PS-PLC-001`. A null millimetre skips — no 3 mm default.

- [New] `PS-PLC-001` when a decoupling cap is farther than datasheet `max_distance_mm`. Empty `layout_rules` and missing caps skip.

## 2.19.0 — 2026-09-10 — Finding review and ECO

Findings can be accepted, marked false-positive, or wontfix with a required reason. Accepted rows export as ECO; OpenEMS-style layout SI is still not this.

- [New] Review state on the report (`open` / `accepted` / `false_positive` / `wontfix`). Empty reason is rejected except when returning to open.
- [New] ECO CSV/JSON of accepted findings only. False-positive and wontfix stay off the ECO.
- [New] Release signature: SHA-256 of findings + user + timestamp. Needs-review filter `?review=open`.

## 2.18.0 — 2026-09-10 — ImpedenceFinder calculator

The Impedance tab uses the closed-form engine from ImpedenceFinder (Hammerstad–Jensen / Cohn), not a second formula set and not OpenEMS.

- [New] Project tab Impedance: microstrip, stripline, coupled-diff Z0, stackup → 50/90/100 Ω widths, download `.kicad_dru` advice.
- [New] Vendored ImpedenceFinder core under `vendor/impedancefinder/` (no gerber2ems, no pcbnew).
- CPWG stays unimplemented — no invented number.

## 2.17.0 — 2026-09-10 — Lifecycle and datasheet extras

Distributor lifecycle is a cached check, not a review scrape. Errata and layout_rules stay structured and skip when the catalog or the PDF has no number.

- [New] `PS-LF-001` EOL, `PS-LF-002` NRND, `PS-LF-003` explicit RoHS fail. Replacement only if the distributor lists it. Active / RoHS N/A / missing cache row are silent.
- [New] `PS-ERRATA-001` when a catalogued workaround pull-up is missing. No URL → skip.
- [New] `PS-INT-001` when `internal_features.pullup_pins` has no rail resistor.
- [New] `layout_rules` closed kinds; non-numeric `max_distance_mm` is stored as null.

## 2.16.0 — 2026-09-10 — KiCad cad-bridge

Pinscope writes `pinscope-findings.json` next to the report so a KiCad 9/10 action plugin can pan to the symbol uuid on the right sheet.

- [New] E2 cad-bridge JSON (`version`, `ref`, `pins`, `sheet`, `uuid`, `severity`). Layout rule ids target pcbnew; others target eeschema.
- [New] `.kicad_sch` symbols keep `cad_uuid` + `cad_sheet` in `cad_index` (child sheet, not the empty root).
- [New] Action plugin in `plugins/kicad/` reads the JSON beside `.kicad_pro` / `.kicad_pcb`.

## 2.15.0 — 2026-09-10 — Power margin, sequencing, DNP enable

Schema checks now compare regulator load to Iout_max, look at PG→EN when a sequence is declared, and treat DNP as a fitted-variant graph.

- [New] `PS-PWR-001` when specified IQ+I_load exceeds Iout_max, or an explicit series R/ferrite DCR drops >5% of the rail. Missing IQ and PCB traces are not guessed.
- [New] `PS-SEQ-001` WARNING if `power_sequence` is in IC specs and upstream PG does not net to downstream EN.
- [New] BOM `DNP`/`Fitted`/`Variant` on `bom_fields`. Fitted enable with only a DNP pull is `PS-DNP-001` ERROR; no DNP column skips the check.

## 2.14.0 — 2026-09-10 — Filtri e termico schema

Deterministic checks now match RC/LC/π/T filters and estimate LDO/resistor dissipation without inventing missing numbers.

- [New] Filter topology `PS-FLT-001` (fc INFO) / `PS-FLT-002` vs `adc_sample_rate` only when that spec exists. Ferrite DCR `PS-FLT-003` only with a datasheet limit.
- [New] LDO `P = I_load×(Vin−Vout)` and `Tj = 25 + P·θJA`. Missing θJA is `PS-TH-001` INFO. `Iout_max` is not treated as load.
- [New] Resistor `I²R` vs `power_rating_w` on LED paths and shunts with known ΔV (`PS-TH-003`).

## 2.13.0 — 2026-09-10 — DC-bias C_eff stima

The derating table now shows an effective capacitance under DC bias for C0G/X7R/X5R ceramics. It is labelled *stima* — not a vendor lot curve.

- [New] `C_eff` column from an empirical V/Vrated table. Tantalum/electrolytic and unknown dielectrics are left blank.
- [Improved] C0G/NP0 stays at nominal C; X7R at 50% of rated V is about 70% of C.
- [New] Bulk C without a ~100 nF ceramic is `PS-ESR-001` INFO (no invented Z(f) target).

## 2.12.0 — 2026-09-10 — Pull-up sizing and LDO Cout

Deterministic schema checks now size I2C pull-ups, flag NRST pull-downs, and look at LDO VOUT capacitance — still WARNING, never a invented datasheet µF ERROR.

- [New] I2C pull-up value vs a wide NXP UM10204 band (`PS-I2C-002`). 4.7 kΩ is in-band; missing values are not sized.
- [New] Active-low reset with a resistor to ground is `PS-RST-002`.
- [New] Regulator VOUT needs Cout (`PS-DEC-001`); 100 nF-only on VOUT is `PS-DEC-002`. MCU VDD 100 nF is not flagged.
- [Improved] Pin-mux UART0 on the `simple_project` MSPM0 nets is covered in tests (SPI PICO/POCI already was).

## 2.11.0 — 2026-09-10 — DeepSeek V4.1 and re-analyze

Pinscope now defaults to DeepSeek-V4.1-Flash (`deepseek-flash`) for every LLM stage, shows API cost in dollars, and lets you replace the BOM and netlist on an existing project without deleting it.

- [New] Default model is `deepseek-flash` (native vision). Legacy `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` names still work; they route to V4.1.
- [New] Replace BOM & netlist on a finished project and re-run the analysis. History, library cache, and prior spend stay on the same project.
- [Improved] API cost uses V4.1 Flash peak rates and is shown on the report, logs tab, and run estimate (not only credits).

## 2.10.0 — 2026-08-27 — Deeper datasheet review

Each IC review now sees more of the datasheet and starts from a structured abs-max table, so voltage, decoupling, and interface checks are less likely to stop at "Unverified".

- [Improved] Extraction pulls Absolute Maximum Ratings (supplies, pin voltages, current, temperature) alongside the pin table, and the reviewer gets those numbers in context.
- [Improved] DeepSeek ingest uses PyMuPDF text (tables survive better than pypdf) and, on vision models, renders pin / abs-max / electrical / application pages instead of always the first 24.
- [Improved] Review checklist now includes recommended operating conditions, VIH/VIL, crystal load caps, and datasheet-named external parts. Turn budget 16, more excerpt pages, three follow-ups per concern.

## 2.9.0 — 2026-08-27 — Component library

Chips and passives stay in a shared library after the first look, so the next board does not re-download datasheets or re-extract pin tables.

- [New] Library page in the sidebar: ICs (pin tables), passive series, discrete specs, and saved PDFs.
- [New] Datasheets are written to the library as soon as they are fetched or uploaded, not only after a full review.
- [Improved] The create-project wizard already skipped parts that were extracted once; that reuse now covers the PDF itself as well.

## 2.8.0 — 2026-08-27 — Automatic datasheets

Pinscope now finds datasheet PDFs on its own. You can still upload a file, but you no longer need DigiKey keys for the common case.

- [New] Auto-fetch from LCSC (no API key) by manufacturer part number or LCSC code, with exact-MPN matching so a CH340E never silently becomes a CH340G.
- [New] Direct Texas Instruments datasheet URLs (`ti.com/lit/ds/symlink/…`) as a second source for TI parts.
- [New] Pipeline fallback: if a datasheet was not uploaded in the wizard, extraction and review try the same lookup before skipping the IC.
- [Improved] DigiKey remains an optional third source when `DIGIKEY_CLIENT_ID` / `DIGIKEY_CLIENT_SECRET` are set.

## 2.7.0 — 2026-08-27 — DeepSeek API

Pinscope now talks to DeepSeek by default. Extraction skills run locally; datasheet PDFs are converted to text (and page images on the vision model) because DeepSeek does not accept native PDF documents.

- [New] DeepSeek provider (`deepseek-v4-flash`, `deepseek-v4-pro`, `deepseek-v4-flash-vision-exp`) via the OpenAI-compatible Chat Completions API.
- [New] Local skill runner: `skills/*/SKILL.md` is inlined and `validate.py` runs in-process — no Anthropic Console upload required.
- [New] PDF ingest for DeepSeek: pypdf text extraction plus optional PyMuPDF page renders on vision models.
- [Improved] Anthropic and Gemini remain optional fallbacks via `PROVIDER_*` / `FALLBACK_PROVIDER_*`.

## 2.6.0 — 2026-07-12 — Export Report to Excel

Download a project's findings as an Excel spreadsheet straight from the report — one click, ready to share, filter, or archive outside Pinscope.

- [New] "Export Excel" button on the validation report. Every finding becomes a spreadsheet row — designator, part number, ID, severity, title, description, recommendation, and its datasheet source (page included) — sorted most-severe first.

## 2.5.1 — 2026-07-04 — More Thorough Reviews

Schematic review now works through every functional area of a component before finishing, so a part with several independent issues has all of them surfaced in one pass instead of just the first.

- [Improved] For each IC, the review covers power and decoupling, every signal interface, absolute-maximum ratings, and reset/boot/configuration and unused pins before reporting — catching multiple issues on the same component that could previously be missed.

## 2.5.0 — 2026-07-02 — Light Mode

Pinscope now has a light theme. Toggle between light and dark with the sun/moon button — in the sidebar next to your account menu, or in the header on the website.

- [New] Theme toggle. Switch between light and dark mode anywhere in the app; your choice is remembered on this device. Everything defaults to dark, exactly as before, until you flip it.
- [Improved] Every status color — error, warning, and pass badges, finding cards, the progress view, billing — is tuned for both themes, so reports stay legible either way.
- [Improved] The sign-in page and account menu now follow the app theme instead of always rendering light.

## 2.4.0 — 2026-07-01 — Automatic Pin & LED Current Checks

Two datasheet-grounded checks now run on every project, independent of the schematic review — catching a swapped-peripheral pin or an over-driven LED — plus a clear list of any components that had no datasheet to review against.

- [New] Pin-function feasibility check. Pinscope now flags when a net assigns an IC pin a peripheral function its silicon can't route — for example a `UART5_TX` net on a pin whose alternate-function table only offers `UART5_RX`. It's reported as an error straight from the datasheet's pin table and names the likely swap (TX↔RX, SDA↔SCL). It deliberately does not judge signal *direction* across an interface — a direct UART crosses TX↔RX while a transceiver runs straight through — so it only fires on physically impossible pin assignments, never on wiring style.
- [New] LED forward-current check. For each LED, Pinscope computes the forward current from the supply rail, the series resistor, and the LED's rated forward voltage, and flags any channel whose current exceeds the LED's rated maximum. Each color of an RGB LED is checked separately, and a leg with no current-limiting resistor at all is called out as a caution.
- [New] "Not reviewed" list on the report. Components with no datasheet on file — for instance a do-not-populate footprint that isn't in the BOM — are now called out explicitly, so a mis-wired pin on an unreviewed part shows up as a known gap instead of being silently absent.
- [New] Findings from these automatic checks carry an "Automated check" badge, so they're easy to tell apart from datasheet-review findings.

## 2.3.3 — 2026-06-08 — Faster Reviews

Multi-chip designs now review several times faster — Pinscope works through ICs in parallel instead of one at a time.

- [Improved] Datasheet extraction and schematic review now process multiple ICs at once, so reports on multi-IC projects come back substantially faster. The findings are unchanged — only the wait is shorter.

## 2.3.2 — 2026-05-26 — Smarter RF Topology Review

Schematic review now reasons about *what each external part is for* before flagging it — catching valid bias, coupling, and matching circuits that previously looked like errors.

- [Improved] The reviewer states the role of every external part on an IC pin (choke, blocking cap, divider, decoupling, matching) before judging the connection. Common RF topologies like bias-T (DC injected onto a coax through a choke, with the chip protected by an internal DC block and a downstream load doing the actual draw) are no longer flagged as errors against the chip.
- [Improved] Stricter absolute-maximum-rating checks: the cited limit must come from the same pin under stress (a Vdd abs-max no longer counts against an RF or signal pin), and the inequality must be a strict exceed — equal-to-abs-max is at most a Warning.
- [Improved] Single-concern deep dives are capped at two follow-up queries; concerns that can't be resolved in that budget are reported as Warnings with the unresolved question stated, so one suspect finding can't starve the rest of the IC review.
- [Improved] Inferred rail voltages from the power-tree pass are no longer treated as ground truth by the schematic reviewer. Voltages set by net name (`+5V`, `+3V3`) or by user power-source hints are trusted as before; voltages the power-tree LLM guessed for an adjustable regulator output or propagated through inference are kept on the power-tree view for reference but excluded from review reasoning, so a single misread rail can't anchor a false-positive Error.
- [Improved] After each IC's review, a second pass normalizes findings against a fixed Error/Warning/Info rubric and merges any two findings that share a single root cause (e.g. "series resistor drops VIN" and "VOUT setpoint exceeds available VIN" are one defect, not two). Cuts run-to-run severity drift and avoids inflating the error count when one defect can be described from multiple angles.

## 2.3.1 — 2026-05-25 — EDIF Netlist Support

EDIF 2.0.0 netlists upload alongside PADS-PCB, with a sub-design picker for files that contain more than one design. Schematic review is also more cautious about polarity / direction-control findings.

- [New] Upload EDIF 2.0.0 (`.edn`) netlists directly. Format is auto-detected from the file contents — no need to convert to PADS-PCB first. Verified against Siemens xDX Designer exports.
- [New] When an EDIF file contains multiple sub-designs, project setup shows a picker so you can choose which one to review. The picker auto-confirms when there's a single clean match against your BOM and only asks when it's ambiguous; unselected sub-designs are filtered out of the design graph.
- [Improved] Stronger verification of differential and polarity pin assignments (USB D+/D−, TX/RX, IN+/IN−, anode/cathode) directly against the datasheet.
- [Improved] Improved support for bidirectional buffers and level translators (74xx245 and friends) — direction-control truth tables are factored into bus-contention analysis.
- [Improved] Findings that share a single root cause on the same chip are grouped into one combined finding.

## 2.3.0 — 2026-05-24 — LCSC Part Number Support

JLCPCB-style BOMs with LCSC part numbers (e.g. `C12044`) now work out of the box — Pinscope auto-detects the column, resolves each id to the real manufacturer part number, and shows you what it resolved to before the pipeline runs.

- [New] LCSC part numbers in the manufacturer part number column are auto-detected at BOM upload and converted to real MPNs. Works with JLCPCB / EasyEDA exports without any column renaming.
- [New] Project setup now shows the LCSC → MPN mapping on each IC row in the datasheet step (e.g. `C12044 → TP4057-42-SOT26-R`), so you can see what each LCSC id became before the pipeline starts.
- [New] Passive specs (value, voltage, tolerance, dielectric, package) are resolved from the LCSC catalog during project setup, with per-row progress and status — you see what's resolved before spending credits on the full pipeline.
- [Improved] Datasheet auto-fetch hit rate is dramatically higher on LCSC BOMs, because DigiKey now sees real MPNs instead of `C…` ids.

## 2.2.1 — 2026-05-22 — Easier Netlist Uploads & Xpedition Support

Tabbed file upload guide with per-tool instructions, Xpedition coverage, and direct `.net` / `.txt` uploads.

- [New] Documentation for exporting a PADS-PCB netlist from Siemens Xpedition Designer / DxDesigner (VX.2.x, including VX.2.14).
- [Improved] File upload guide reorganized into tabs — KiCad, Altium, OrCAD/Allegro, Xpedition, EasyEDA, and Eagle each get their own panel.
- [Improved] Netlist uploads now accept `.asc`, `.net`, `.NET`, and `.txt` directly — no more renaming required before upload.
- [Improved] File guide now calls out the difference between the PADS-PCB schematic netlist Pinscope needs and the `!PADS-POWERPCB` PCB-layout dump that some EDA tools also save as `.asc`.

## 2.2.0 — 2026-05-20 — Cross-chip Datasheet Review

The reviewer now reads neighbor-chip datasheets to verify cross-chip constraints, with fewer false errors when a spec can't be confirmed.

- [Improved] Schematic review now cross-references connected chips: when an issue depends on a neighbor's spec (5V tolerance, absolute-max, drive strength), the reviewer pulls the relevant pages from that chip's datasheet before flagging it.
- [Improved] Fewer false errors on cross-chip findings: if a counterpart spec can't be confirmed from the datasheet, the issue is reported as a Warning with the unverified assumption stated — instead of being overstated as an Error.
- [Fixed] Some review findings could occasionally fail to appear in the report.

## 2.1.0 — 2026-05-19 — Datasheet Reference Highlighting

Datasheet citations now highlight the exact supporting sentence on the PDF page, with more reliable page numbers on large datasheets.

- [Improved] Datasheet references now highlight the exact supporting sentence on the PDF page, not just the page number.
- [Fixed] Datasheet citations landing on the wrong page for large (multi-hundred-page) datasheets.
- [Fixed] Reviewed findings losing their checked state on page refresh.

## 2.0.1 — 2026-05-01 — Flagging & Onboarding

One-click flags on finding cards, an onboarding survey for new users, and small UI polish.

- [New] Report findings with one click via the flag button on any finding card.
- [New] Onboarding survey for new users to help us improve the product.
- [Improved] Comment input box now fills available width.

## 2.0.0 — 2026-04-29 — Public Changelog

- [New] Initial public changelog.
