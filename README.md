# Pinscope (DeepSeek)

Pinscope reviews schematics the way a good senior engineer does: with the datasheets open.

This tree is adapted from [manvalan/pinscope](https://github.com/manvalan/pinscope) so the pipeline talks to the **DeepSeek API** (`deepseek-v4-flash`, `deepseek-v4-pro`, and `deepseek-v4-flash-vision-exp`) instead of requiring an Anthropic Console skill upload. Anthropic and Gemini remain optional fallbacks.

Give it a netlist, a BOM, and your datasheet PDFs. It builds a graph of your design, reads each IC's datasheet, and checks the circuit around every part against what the manufacturer actually specifies — reference application, pin functions, absolute maximums, recommended operating conditions. Every finding points at the datasheet page that backs it up.

## What changed for DeepSeek

DeepSeek's Chat Completions API is OpenAI-compatible but **does not accept native PDF documents**. Pinscope therefore:

1. **Extracts datasheet text** with `pypdf` (page-marked) and sends it as chat content.
2. **Renders pages to JPEG** with PyMuPDF when the stage uses a vision model, so pin diagrams and tables survive.
3. **Runs extraction skills locally.** `skills/*/SKILL.md` is inlined as the system prompt; `validate.py` runs in-process. You do not need Anthropic Console Skills.
4. **Round-trips `reasoning_content`** when DeepSeek thinking mode is on, so multi-turn review and tool calls do not 400.

Default routing:

| Stage | Model |
| --- | --- |
| Pintable / pattern / specs extraction | `deepseek-v4-flash-vision-exp` |
| Per-IC datasheet review | `deepseek-v4-pro` |
| Auto-resolve / normalize | `deepseek-v4-flash` |

Override with `PROVIDER_*` and `MODEL_*_DEEPSEEK` in `backend/.env`. See `backend/.env.example`.

## How it works

<p align="center">
  <img src="docs/how-it-works.svg" width="920" alt="Pipeline: the netlist and BOM are parsed into a design graph; datasheet PDFs are extracted into pin tables and specs; a per-IC review reads both and files findings cited to datasheet pages; the derating table and BOM roll-up are computed straight from the graph, no model involved.">
</p>

1. **Parse** the BOM (CSV/XLSX) and netlist (PADS-PCB `.asc` or EDIF 2.0.0 `.edn`) into a queryable bipartite graph of components and nets.
2. **Extract** pin tables and specs from the PDFs. Large datasheets are trimmed to the relevant pages first, and every extraction is cached in a shared library.
3. **Review** each IC in isolation. The model gets the datasheet plus that IC's circuit neighborhood, can query the graph, and files findings with severity, reasoning, and page citations. Extraction now also stores absolute-maximum ratings so the reviewer does not have to rediscover supply limits from a 300-page PDF.
4. **Compute** the deterministic parts deterministically — BOM roll-up and a capacitor voltage-derating table come straight from the graph.

## Try it on the bundled design

`simple_project/` is a small MSPM0G3507 board with a CH340E USB-UART bridge and an SPX3819 LDO.

You need Python 3.12+, Node 20+, and a [DeepSeek API key](https://platform.deepseek.com/):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env   # set DEEPSEEK_API_KEY

python3 -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 18741

# in another terminal
cd frontend && npm install
NEXT_PUBLIC_API_URL=http://127.0.0.1:18741 npm run dev -- --port 18742 --hostname 127.0.0.1
```

Open the frontend URL, create a project, and feed it the netlist and BOM from `simple_project/`. Datasheets are fetched automatically (LCSC, TI, optional DigiKey); you can still drop in PDFs by hand. Fetched PDFs and extracted pin tables land in the **Library** (sidebar) and are reused on later projects. Everything runs locally against your own key; projects and the extraction library live in `data/`.

Anthropic Console Skills (`python3 scripts/upload_skills.py --update`) are optional and only needed if you set `PROVIDER_DEFAULT=anthropic`.

## Docker

```bash
cp backend/.env.example .env   # set DEEPSEEK_API_KEY
docker compose up --build
```

Backend on port 8080, frontend on port 3000.

### Update a live instance (e.g. pinscope.michelebigi.it)

On the server, from the Pinscope checkout:

```bash
./scripts/update-pinscope.sh
```

The script pulls the current branch, writes `NEXT_PUBLIC_API_URL` / `CORS_ORIGINS` for `https://pinscope.michelebigi.it`, rebuilds both Docker images, and leaves `data/` alone. First run: put `DEEPSEEK_API_KEY` in `.env` at the repo root (compose reads that file). `--no-pull` skips git. `SITE=https://other.host ./scripts/update-pinscope.sh` overrides the public URL.

Do not set `ENVIRONMENT=production` unless Clerk auth is configured — that flag refuses to boot with auth disabled.

## License

AGPL-3.0, same as upstream Pinscope. For commercial licensing of the original, write to dev@faradworks.com.
