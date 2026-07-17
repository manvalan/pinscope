# Pinscope

**Agentic schematic validation — catch hardware design errors before you fab.**

Pinscope reviews your schematic against every component datasheet. It parses your netlist and BOM into a queryable design graph, extracts pin tables and specs from datasheet PDFs with Claude, and runs an agentic per-IC review that compares your circuit neighborhood to the datasheet's reference application — flagging wrong pull-ups, missing decoupling, voltage-domain violations, swapped signals, and derating failures, each finding cited back to the datasheet page that backs it up.

> Pinscope is the open-source core of [pinscope.ai](https://pinscope.ai) (same code, hosted, with team accounts). Self-hosting it like this is fully supported: everything runs locally against your own Anthropic API key, no account or cloud services required.

## How it works

```
Upload BOM + netlist + datasheets
        │
        ▼
Parse BOM ─ Extract IC pintables ─ Extract passives ─ DigiKey/value fallback
        │
        ▼
Build design graph  (bipartite: components ⇄ nets, queryable)
        │
        ▼
Per-IC direct datasheet review  (Claude reads the PDF + circuit neighborhood,
        │                        queries the graph, cites pages for findings)
        ▼
Report + BOM summary + capacitor derating table
```

- **Netlists**: PADS-PCB ASCII (`.asc`) and EDIF 2.0.0 (`.edn`) — exportable from KiCad, Altium, OrCAD, Cadence, Xpedition, EasyEDA, EAGLE
- **BOM**: CSV or XLSX
- **Datasheets**: PDF per MPN (DigiKey auto-fetch supported with API keys)
- **Deterministic where possible**: graph build, BOM collation, and capacitor voltage derating are exact computations, no AI
- **Extraction is cached**: every extracted pintable/spec lands in a shared `library/` so an MPN is only ever paid for once

## Quickstart

Prereqs: Python 3.12+, Node 20+, an [Anthropic API key](https://console.anthropic.com/).

```bash
git clone https://github.com/Faradworks/Pinscope.git
cd Pinscope

# 1. Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example .env          # then set ANTHROPIC_API_KEY

# 2. Extraction skills (one-time): uploads the three extraction prompts in
#    skills/ to YOUR Anthropic Console account and writes their IDs into
#    backend/skills_manifest.json
python3 scripts/upload_skills.py --update

# 3. Run
python3 -m uvicorn backend.main:app --reload   # http://localhost:8000
cd frontend && npm install && npm run dev      # http://localhost:3000
```

Open http://localhost:3000, create a project, and upload the files from `simple_project/` (an MSPM0G3507 + CH340E reference design) to see a full run end-to-end. Projects and the extraction library are stored in `data/`; you run as a local admin user — no login.

## What's in the box

| Layer | Where | What |
|---|---|---|
| Core library | `backend/pinscopex/` | Pydantic models, netlist/BOM parsers, graph builder, agentic validator, passive resolvers, taxonomy, derating |
| Backend | `backend/` | FastAPI — async pipeline with SSE progress, project + library storage, per-call API cost logging |
| Frontend | `frontend/` | Next.js 16 — dashboard, pipeline progress, report viewer with datasheet citations, derating table, admin console |
| Extraction skills | `skills/` | Claude Console Skills for pintable / passive-pattern / specs extraction |
| Taxonomy | `taxonomy/` | Living component taxonomy with per-subtype specs schemas |

See [CLAUDE.md](CLAUDE.md) for architecture details and development guidelines.

## Configuration

Everything is env-driven (see `backend/.env.example`):

- `ANTHROPIC_API_KEY` — required; extraction and review models are configurable per stage
- `DIGIKEY_CLIENT_ID` / `DIGIKEY_CLIENT_SECRET` — optional; enables datasheet auto-fetch and parameter-based passive auto-resolve
- `GCS_BUCKET` — optional; swaps local `data/` storage for Google Cloud Storage
- `GEMINI_API_KEY` + `PROVIDER_*` — optional; route individual stages to Gemini

## Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -q
```

Tests run against `simple_project/` — it's the ground-truth reference design.

## License

[AGPL-3.0](LICENSE). Commercial licensing is available — contact dev@faradworks.com.
