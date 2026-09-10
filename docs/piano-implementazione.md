# Piano di implementazione — Pinscope

Documento di lavoro **prima dello sviluppo**. La lista dell’utente è il minimo; sotto c’è anche ciò che serve perché quella lista non resti un insieme di moduli scollegati.

**Questo piano copre due prodotti.** Pinscope originale resta il primo. Layout/plugin/placement mm sono il secondo. Non mescolare i changelog né vendere il secondo come “un po’ di Pinscope in più”.

Stato del codice di riferimento: branch `cursor/deepseek-71c5` (post DeepSeek V4.1, costi USD, replace BOM/netlist, parser KiCad, fingerprint review).

---

## 0. Due prodotti (stesso repo, due promesse)

| | **Pinscope** (oggi + wave A–B, C schema, F/H leggere) | **Pinscope Layout** (wave D parziale, G, C4+G2, plugin pcbnew) |
| --- | --- | --- |
| Promessa | Lo schema rispetta il datasheet | Il rame rispetta datasheet + geometria |
| File | BOM, netlist, `.kicad_sch` gerarchico | + `.kicad_pcb` |
| Output | Finding su pin/net, derating, power tree | Distanze mm, 3W, creepage, skew, via EP |
| Utente | Chi chiude lo schema | Chi sbroglia |

Farli nello stesso codebase (`pinscopex` + `LayoutGraph`) è ragionevole. Farli **nella stessa run obbligatoria** no: senza PCB il progetto deve restare un Pinscope completo, non “incompleto perché manca il gerber”.

Nome in UI: tab **Layout** o prodotto “Layout checks” gated dal file `.kicad_pcb`. Il report schema non deve riempirsi di `PS-PLC` se il PCB non c’è.

Non serve un fork oggi. Serve disciplina: ogni PR dichiara se è Core o Layout.

---

## 0. Contratto di prodotto (non negoziabile)

Pinscope oggi è un **validatore di schema**: BOM + netlist → grafo bipartito → check deterministici + review LLM con citazione datasheet. Non legge il PCB.

Molti punti della lista (larghezza traccia, 3W, creepage, CPW clearance, length matching) **non esistono senza geometria**. Il piano li tiene, ma li mette **dopo** un ingest layout. Se li si forza sullo schema si producono finding inventati.

### Ingresso ufficiale: progetto KiCad (non EasyEDA)

Coppia da chiedere all’utente (e da salvare insieme):

| File | Ruolo |
| --- | --- |
| **Root `.kicad_sch`** (+ fogli figli) | Connettività, ref, uuid simbolo/pin, MPN nei property |
| **`.kicad_pcb`** | Rame, net, coppie, courtyard, stackup |

BOM CSV resta utile se i property MPN nello schema sono vuoti; se lo schema è completo, la BOM è opzionale.

**EasyEDA è fuori scope.** Niente plugin, niente parser JSON EasyEDA, niente DRC sull’editor cloud. Chi arriva da EasyEDA resta sul percorso già documentato (export PADS), senza lavoro nuovo.

**Gerber** non è l’ingresso primario: con `.kicad_pcb` i net ci sono già. I Gerber restano un eventuale piano B, non lo sprint 1 del layout.

### Schema gerarchico (più file) — attenzione

Oggi `parse_kicad_sch` legge **un solo foglio**. I `(sheet …)` che puntano ad altri `.kicad_sch` **non vengono seguiti**. L’errore attuale chiede di esportare la netlist.

Da fare (Wave A1), in ordine:

1. Upload: cartella progetto o zip, **oppure** il `.kicad_pro` + root schematic. Non un solo foglio figlio.
2. Dal root: camminare ogni `(sheet (property "Sheetfile" "power.kicad_sch") …)` (KiCad 6–10: `Sheetfile` / path relativo al foglio padre).
3. Path traversal: solo file sotto la root del progetto; rifiutare `../`.
4. Unire la connettività:
   - **local label** restano nel foglio;
   - **hierarchical_label** sul figlio ↔ **sheet pin** sul padre (stesso nome);
   - **global_label** e power symbol (`#PWR`) sono globali su tutto il progetto.
5. Path gerarchico KiCad (`/power/U1` vs `U1`): normalizzare i **Reference** come li vede il PCB (di solito già unici; se duplicati, è un errore dello schema).
6. Uuid: `(uuid …)` su symbol e pin, più `sheet` uuid, per il plugin (pan-and-zoom sul foglio giusto).
7. Fixture di test: root + 2 figli (alimentazione + analog), label gerarchiche su un net, power flag GND condiviso. Deve coincidere col netlist XML esportato da KiCad 9.

**Done when:** progetto a 3 fogli senza export netlist produce gli stessi `(ref, pin, net)` del file `*.xml` di KiCad.

`.kicad_pcb` è un file solo (il board non è gerarchico come lo schema). I net name nel PCB devono matchare i net risolti dallo schema dopo il flatten gerarchico.

Regole:

1. Ogni nuovo check è una funzione pura in `backend/pinscopex/` che legge `DesignGraph` (+ opzionale layout). Niente SDK LLM dentro `pinscopex/`.
2. I finding usano lo stesso schema (`Finding` in `models.py` / `frontend/src/lib/types.ts`). Campo `source` già distingue check automatici vs review.
3. Finding normalization resta **downgrade-only**.
4. Libreria condivisa: MPN exact-match. Niente fuzzy sul die.
5. Plugin CAD **consumano** il report; non duplicano la pipeline.

### Estensione schema finding (fare per prima, una volta)

Oggi: `designator`, `mpn`, `aspect`, `finding`, `why`, `status`, `source_page`, `recommendation`, `source`.

Aggiungere (backward compatible):

| Campo | Serve a |
| --- | --- |
| `net` | Telemetry CAD, filtri, SI |
| `pins[]` | Pan-and-zoom su U1.4 |
| `rule_id` | Plugin DRC (`PS-DEC-001`) |
| `cad_sheet` / `cad_uuid` | Sync plugin KiCad |
| `variant` | DNP / ECO |
| `severity_calibrated` | già implicito; non alzare in post |

Passi:

1. Estendere `Finding` in `backend/pinscopex/models.py` e `frontend/src/lib/types.ts`.
2. Aggiornare `assign_finding_ids`, export Excel, report UI (campi opzionali nascosti se null).
3. Test su `simple_project/` che i check esistenti ancora serializzano.

**Done when:** un finding di decoupling ha `rule_id` + `pins` e il report non rompe i finding LLM vecchi.

---

## Mappa lista ↔ codice attuale

| Blocco utente | Già c’è | Buco |
| --- | --- | --- |
| 1 Plugin / telemetry / BOM match | Parser KiCad XML/sexp/`.kicad_sch` **singolo foglio**; wizard BOM | Hierarchie multi-file; uuid; plugin; **EasyEDA fuori scope** |
| 2 Datasheet / errata / OCR blocchi | Pintable+abs-max, PDF text+vision, excerpt per topic, quote_verify | Nessun RAG vendor; niente errata; vision non estrae clamp/ESD dal block diagram in schema strutturato |
| 3 Impedenze / stackup | — | Motore assente; niente PCB |
| 4 Filtri | Review LLM può parlarne | Nessun matcher topologico, niente \(f_c\) |
| 5 Capacità PI | Decoupling check **sulla net**; derating V | No DC bias; ESR/ESL; Cin/Cout; **niente distanza cap↔pin sul PCB** |
| 6 Elettrico | Pin mux; I2C/reset pull-up; power tree UI; LED current | Sequencing assente; pull-up non dimensionati (solo presenza); drop IR assente |
| 7 RF | Review “ruolo parte” / bias-T | Nessun matching 50 Ω, niente clearance |
| 8 HV / isolation | — | Serve layout + profilo normativo |
| 9 Termico | Excerpt topic thermal | Nessun \(T_j\), niente P=I²R resistori |
| 10 SI / DNP | Fingerprint skip IC; skipped/not_reviewed | DNP non è un modello; niente length/crosstalk |
| 11 Lifecycle | DigiKey/LCSC/Mouser per datasheet e passivi | Nessun EOL/NRND/RoHS in report |
| 13 Placement da datasheet | Review può citare “place close to pin”; vision su application pages | Nessuna misura mm sul `.kicad_pcb`; nessuna struttura `layout_rules` estratta |

---

## Extra obbligatori (non nella lista, ma bloccanti)

Senza questi i moduli 1–12 non si misurano e i plugin mentono.

**E0. Eval harness.** Golden findings su `simple_project/` + 1–2 board interni. Precision/recall, % Unverified, citation hit-rate (`quote_verify`). Ogni check nuovo deve aggiungere fixture.

**E1. KiCad gerarchico GA.** Obbligatorio. Vedi “Schema gerarchico” sopra. Senza flatten dei fogli il plugin e il `.kicad_pcb` non allineano i net.

**E2. Protocollo plugin KiCad** (`pinscope-cad-bridge` JSON). Un file per progetto:

```json
{
  "version": 1,
  "project_id": "...",
  "findings": [
    {
      "rule_id": "PS-MUX-001",
      "ref": "U3",
      "pins": ["12"],
      "sheet": "...",
      "uuid": "...",
      "severity": "error",
      "message": "...",
      "url": "https://pinscope.../report?finding=U3-001"
    }
  ]
}
```

Un solo adapter: **KiCad 9/10**. Niente secondo plugin EasyEDA.


**E3. Smoke DeepSeek V4.1** su `simple_project` (vision + finding count vs run precedente). Già in coda P0.

---

## Wave A — Fondamenta CAD e finding (sblocca 1 e 6)

Obiettivo: schema KiCad fidato, finding indirizzabili, sync unidirezionale.

### A1. KiCad nativo “GA” (schema gerarchico)

Passi: quelli in “Schema gerarchico (più file)” + file-guide riscritta (niente “esporta PADS da KiCad” come percorso principale) + `cad_index.json` (ref → uuid → sheetfile).

**Done when:** fixture a più fogli = netlist XML KiCad; upload zip/progetto senza chiedere l’export.

### A3. Bridge KiCad (punto 1)

Passi:

1. Pacchetto `plugins/kicad/` (action plugin Python, KiCad 9/10).
2. `pinscope-findings.json` dal report (E2).
3. Marcatori / focus `uuid` su **eeschema** (foglio figlio corretto) e, per finding layout, su **pcbnew**.
4. Pan-and-zoom: `FocusOnItem` / select symbol; se l’API 10 differisce, adapter sottile.

**Done when:** clic su finding centra U3 sul foglio `power.kicad_sch`, non sul root vuoto.

EasyEDA: **non si fa.**

### A2. BOM-to-schematic matching (punto 1)

Non è uno script a parte: è il grafo.

Passi:

1. Da campi KiCad (`MPN`, `mpn`, `PN`, `lcsc`) già letti in `parsers_kicad.py` / `graph.py`.
2. Tabella conflitti: ref in schema senza MPN, MPN in BOM senza ref, mismatch Value.
3. Finding `source=bom_match` con `rule_id=PS-BOM-001`.
4. UI wizard: riga rossa nel matching, non solo colonne.

**Done when:** U1 in schema e U1 in BOM con MPN diversi → ERROR citabile.

---

## Wave B — Check deterministici schema (sblocca 4, 5, 6, 9, 10 DNP)

Obiettivo: meno LLM, più numeri. Ogni item = modulo `pinscopex` + test su grafo sintetico + riga in eval.

### B1. Power tree & drop (6)

Passi:

1. Riuse UI power tree esistente.
2. Per ogni IC: somma IQ + load stimato da specs se c’è; confronta con `Iout_max` LDO/Buck se estratto.
3. IR drop **solo se** esiste Rseries esplicito (shunt/ferrite) — niente stima di pista.
4. Finding `PS-PWR-001` margin fail.

### B2. Sequencing (6)

Passi:

1. Estrarre da datasheet (skill specs o tabella) `power_sequence` se presente; altrimenti skip.
2. Sul grafo: enable pin, RC delay, PG (power good) concatenati.
3. WARNING se sequenza dichiarata e PG non collega l’enable del rail successivo; niente invenzione di millisecondi.

### B3. Pull-up dimensionamento (6)

Passi:

1. Estendere `check_i2c_pullups`: presenza **e** valore vs Vrail e Cf (formula NXP UM10204, bound largo).
2. Reset: stesso, più pull-down vietato se datasheet dice active-low con pull-up interno assente.

### B4. Mux (6)

Già `pin_mux_check.py`. Passi residui: coprire SPI/UART con gli stessi token; test su MSPM0 del `simple_project`.

### B5. Decoupling audit (5)

Già `check_supply_decoupling`. Passi:

1. Distinguere Cin LDO vs Cout (pin IN/OUT dal pintable, non solo VDD).
2. Contare valore se specs passive ci sono (100 nF vs 10 µF) come WARNING non ERROR se il vendor non è esplicito.

### B6. Derating DC bias (5)

Passi:

1. Tabella empirica C0G/X7R/X5R (loss % vs V/Vrated) in `derating.py` — etichettare “stima”, non misura.
2. UI derating: colonna C_eff.
3. Non spacciare per curva Murata del lotto.

### B7. ESR/ESL parallelo (5)

Passi:

1. Modello grezzo: C + ESL package (0402/0603 lookup) + ESR da specs se c’è.
2. Z(f) somma parallelo 10µF+100nF; confronta con f_sw buck se nota.
3. Senza f_sw: INFO “copertura HF dipende da 100nF vicino al pin” senza fingere un target Ω.

### B8. Filtri (4)

Passi:

1. Matcher topologico sul grafo: RC serie-shunt, LC, ferrite+C, π (C-L-C), T (L-C-L).
2. \(f_c\) per RC (`1/2πRC`) e LC (`1/2π√LC`); Pi/T solo se L e C noti.
3. Confronto con `adc_sample_rate` / `data_rate` **solo se** in specs IC.
4. Insertion loss: WARNING se ferrite DCR alta su rail ADC analog (soglia da datasheet o skip).

### B9. Termico schema (9)

Passi:

1. \(P \approx I_{load} \times (Vin-Vout)\) per LDO se I e V noti; \(T_j \approx T_a + P \theta_{JA}\) con \(T_a=25\) default e campo UI.
2. Resistori: \(P=I^2R\) se I dal LED check o da shunt + V; vs `power_rating_w`.
3. Senza θJA: non inventare; INFO “manca theta_ja”.

### B10. DNP / varianti (10)

Passi:

1. Campo BOM `DNP` / `fitted` / `variant`.
2. Grafo per variante: pin Enable senza pull e senza driver → ERROR.
3. Non è layout.

---

## Wave C — Datasheet intelligence (punto 2)

Restare su DeepSeek Flash vision; niente secondo vendor di default.

### C1. Parsing adattivo (già in parte)

Passi:

1. Eval pintable su 10 PDF: TI, STM, NXP, Espressif, Winbond, GigaDevice, Holtek, Silergy, 3 PEAK.
2. Dove fallisce: skill `extract-pintable` + pagine vision (già keyword). Non RAG vettoriale finché l’eval non lo chiede.
3. Se serve RAG: chunk per pagina in libreria, retrieval per pin/abs-max — **dopo** C1 eval.

### C2. Errata

Passi:

1. Non scraping indiscriminato (TOS, HTML instabile).
2. Catalogo URL noti (TI `lit/er`, STM `errata`, Microchip).
3. DeepSeek `web_search` **opzionale** gated, citazione obbligatoria, stesso `quote_verify`.
4. Finding `PS-ERRATA-001` se il workaround (pull-up, bond-out) non è nello schema.

**Done when:** un MPN con errata nota in fixture produce finding; vendor senza URL → skip silenzioso loggato.

### C3. OCR / vision block diagram (2)

Passi:

1. Non un modello CV a parte: riusare pagine “block diagram” già in `_PAGE_KEYWORDS`.
2. Tool extraction: `internal_features: {esd_clamp_pins[], pullup_pins[], analog_switch[]}`.
3. Check: pin dichiarato open-drain senza pull visibile.

### C4. Layout rules dal datasheet (placement proposto)

La pagina “Typical application / PCB layout” non è solo uno schema: spesso dice *quanto vicino*, *quanti via*, *da che lato del package*. Va estratto in **struttura**, non lasciato in prosa al reviewer.

Skill o estensione specs (libreria per MPN, versionata come il pintable):

```json
{
  "layout_rules": [
    {
      "kind": "decoupling_proximity",
      "pin": "VDD",
      "cap_value_hint": "100nF",
      "max_distance_mm": 2.0,
      "same_layer": true,
      "source_page": 14
    },
    {
      "kind": "thermal_via",
      "pin": "EP",
      "min_via_count": 4
    },
    {
      "kind": "keepout",
      "net_class": "analog",
      "note": "no digital return under analog pin"
    }
  ]
}
```

Passi:

1. Pagine già keyword-matched (`application`, `layout`, `decoupling`, `PCB`). Vision obbligatoria (disegni).
2. `validate.py` sulla lista: `kind` enum chiuso; distanze solo se il testo/figura ha un numero; altrimenti `max_distance_mm` null e il check G2 usa una default **dichiarata** (es. 3 mm) con severity WARNING.
3. Citazione `source_page` + quote_verify sul testo se c’è (“place within 2 mm”).
4. Non inventare una land pattern JEDEC se il PDF non la dà.

**Done when:** MSPM0 o LDO del `simple_project` ha almeno una `decoupling_proximity` in library JSON, o skip esplicito `layout_rules: []` con log.

---

---

## Wave D — Impedenza analitica (punto 3) *senza* PCB

Motore **standalone**, stile calcolatrice. I vincoli CAD sono export, non verità sul board.

### D1. Motore Wheeler/Schneider

Passi:

1. `pinscopex/impedance.py`: microstrip, stripline, coupled diff, CPW — formule documentate + test numerici vs 3 valori ImpedanceFinder.
2. Input: `h`, `er`, `t`, `w`, `s`, `target_z`.
3. UI tab progetto “Impedance” (non LLM).

### D2. Stackup → regole CAD

Passi:

1. Form stackup (N layer, h, er).
2. Output: w/s per 50 / 90 USB / 100 diff.
3. Export KiCad `.kicad_dru` o netclass — **consigli**, l’utente applica.
4. Nessun finding “traccia troppo stretta” finché non c’è `.kicad_pcb`.

---

## Wave E — RF schema (punto 7, parte schema)

Passi:

1. Topologia π/T tra pin ANT e connettore/antenna (stesso matcher filtri).
2. Target 50 Ω come **intento**, non misura: WARNING se manca rete e datasheet mostra matching.
3. CPW clearance: **Wave G** (layout).

---

## Wave F — Supply chain (punto 11)

Passi:

1. Estendere DigiKey/Mouser/LCSC: campi `lifecycle`, `rohs`, `stock`, `lead_time` già spesso nel payload.
2. Job periodico (non ogni review): `lifecycle.json` per MPN libreria.
3. Finding INFO/WARNING EOL/NRND; RoHS fail solo se il flag è esplicito.
4. Cross-ref: **solo** se il distributore dà `replacement` / family; niente LLM “equivalente”.

---

## Wave G — Layout (punti 3 residui, 7 CPW, 8, 10 SI)

Due file per progetto: **schema KiCad flatten** + **`.kicad_pcb`**.

**G0. Ingest layout**

Passi:

1. Parser `.kicad_pcb`: tracce, via, zone, layer, **net name**, footprint, courtyard, differential pairs se presenti.
2. I net del PCB devono combaciare con quelli dello schema dopo il flatten gerarchico (stesso `U1.4` ↔ pad).
3. Modello `LayoutGraph` affiancato a `DesignGraph`.
4. Gerber: non in questa wave. Solo se un utente non può dare il `.kicad_pcb`.

**G1. Check (solo se net e geometria sono legati)**

Passi:

1. Length matching / intra-pair skew vs limite datasheet (USB/HDMI/PCIe).
2. 3W: distanza centro-centro vs W aggressore.
3. Creepage/clearance: profilo IEC 62368 (pollution, RMS V dai net).
4. Isolation barrier: bbox isolator + divieto piste LV nel courtyard HV.
5. CPW: gap verso GND copper vs valore del calcolatore D2.

**G2. Placement vs datasheet (piste, decoupling, thermal)**

Lo schema dice se C12 è sul net VDD. Il PCB dice se C12 è a 8 mm dal pad. Questo check è **solo layout + layout_rules**.

Passi:

1. Per ogni pin alimentazione del pintable: footprint pad xy sul `.kicad_pcb`; condensatori sul medesimo net (grafo); distanza euclidea pad-cap (pin cap verso GND/VDD).
2. Se `max_distance_mm` estratto: ERROR/WARNING se oltre. Se assente: WARNING oltre default configurabile, testo “datasheet non specifica mm; usato default 3 mm”.
3. Via in pad / via sotto EP: contare via nel courtyard del thermal pad vs `min_via_count`.
4. Stesso layer: se `same_layer: true` e il cap è sull’altro lato senza via sotto il pin → WARNING.
5. Piste: lunghezza net VDD dal pin al cap (somma segmenti) come proxy di “loop area”; se >> distanza euclidea, c’è un giro largo.
6. Crystal: cap load vs pin XIN/XOUT (stessa metrica di distanza), se X1 è nel grafo.
7. Finding `PS-PLC-001` con `pins`, `net`, pagina datasheet. Plugin: focus footprint su pcbnew.

Non confrontare una foto del layout TI con il board pixel-a-pixel. Solo vincoli numerici/topologici.

**Done when:** fixture PCB con C di decoupling a 15 mm da VDD (regola 2 mm) → `PS-PLC-001`; cap a 1 mm → niente finding.

**Done when (G1+G2):** un `.kicad_pcb` di test (USB diff pair volutamente sbagliata) produce `PS-SI-001`. I net coincidono con lo schema gerarchico della stessa repo.

---

## Wave H — Enterprise (punto 12)

Passi:

1. Stato finding: `open | false_positive | accepted | wontfix` + motivo obbligatorio.
2. Firma rilascio: hash report + user + timestamp (OSS: locale; cloud: già Clerk).
3. ECO: export `eco.json` / CSV: rule_id, ref, before/after raccomandazione, finding_id.
4. Dashboard: filtri già URL; aggiungere coda “da approvare”.

Commenti esistono: non rifarli, agganciarli allo stato.

---

## Ordine di esecuzione (sviluppo)

Non parallelizzare A0 schema finding con G.

| Sprint (indicativo) | Contenuto | Dipende da |
| --- | --- | --- |
| 0 | Schema finding + eval + smoke V4.1 | — |
| 1 | A1 KiCad GA + A2 BOM match | 0 |
| 2 | B3–B5 pull-up/decoupling/Cin-Cout | 0 |
| 3 | B6–B9 DC bias, ESR, filtri, termico | 2 |
| 4 | B1–B2 power drop/sequencing + B10 DNP | 1 |
| 5 | A3 plugin KiCad + E2 JSON | 1, 0 |
| 6 | C2 errata + C3 internal features + **C4 layout_rules** | eval C1 |
| 7 | D1–D2 calcolatrice impedenza + export netclass | — |
| 8 | F lifecycle | APIs già presenti |
| 9 | H review workflow / ECO | 0 |
| 10+ | G layout `.kicad_pcb` + G2 placement datasheet | A1 + C4 |

Stima onesta: Wave A–B (schema) sono il ritorno; G è un secondo prodotto. Non promettere creepage nel plugin schema.

---

## Passi operativi per **ogni** check nuovo

1. Fixture grafo minimo in `tests/test_<nome>.py` (non solo `simple_project`).
2. Funzione in `pinscopex/` senza I/O.
3. Registrare in `services/validation.py` accanto a pin_mux/LED.
4. `rule_id` + `source`.
5. Una riga changelog.
6. Se tocca UI: tab o badge “Automated check” già usato.
7. Browser solo se UI; senno pytest.

---

## Fuori scope (esplicito)

- Plugin o parser **EasyEDA**.
- Agente che **progetta** lo sbroglio.
- Simulazione SPICE/IBIS.
- Sostituti pin-to-pin inventati dall’LLM.
- Scraping errata senza whitelist.
- Layout da netlist PADS dump (`!PADS-POWERPCB`) — già rifiutato.

---

## Prossimo passo concreto (quando si apre lo sviluppo)

Sprint 0, in quest’ordine:

1. Estensione `Finding` (`rule_id`, `pins`, `net`).
2. Eval script `simple_project` (finding count + citation).
3. Flatten `.kicad_sch` gerarchico (fixture multi-foglio).
4. Upload `.kicad_pcb` accanto allo schema (parse, ancora senza check SI).
5. BOM mismatch finding.

Il plugin KiCad aspetta uuid + sheet path. Placement decoupling in mm e 3W aspettano `.kicad_pcb` + `layout_rules`.
