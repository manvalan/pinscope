@AGENTS.md

# Pinscope Frontend

Next.js 16 app (App Router, Turbopack) providing a web UI for Pinscope schematic validation. Talks to the FastAPI backend at `localhost:8000`.

## Architecture

- **Next.js 16** with App Router, Tailwind CSS v4, shadcn/ui (Base UI primitives, not Radix)
- **Route groups**: `(app)` for app routes (dashboard, projects, admin), `(marketing)` for public pages (landing, contact, privacy, terms)
- **Backend integration**: `src/lib/api.ts` fetches from `NEXT_PUBLIC_API_URL` (defaults to `http://localhost:8000`)
- **SSE for pipeline progress**: Streams events from `GET /api/pipeline/{id}/events`
- **Sidebar navigation**: Project pages use sidebar nav with tabs via URL query params (`?tab=bom|derating|power|logs|settings`)
- **Power tree visualization**: Interactive graph via React Flow (`@xyflow/react`) + dagre layout
- **Project collaborators**: Email-based invites, shared access badges, owner/member roles
- **Comments on findings**: Reviewers can leave threaded comments with `@mention` support on any finding card

## Open-core seams

A handful of files are gateway-owned stubs the hosted-cloud repo replaces
with Clerk/billing implementations. Keep their export signatures stable and
never import auth/billing SDKs elsewhere:

- `src/proxy.ts` — pass-through middleware here
- `src/hooks/use-optional-auth.ts` — always the local admin user here
- `src/components/theme/clerk-theme-provider.tsx` — pass-through here
- `src/components/billing/credits-context.tsx` — `useCredits()` always null here
- `src/components/billing/paused-run-banner.tsx` — renders null here
- `src/components/layout/sidebar-auth.tsx` — renders null here
- `src/components/marketing/pricing-section.tsx` — renders null here
- `src/components/analytics/*` — render null here
- `src/lib/csp-hosts.ts` — empty allow-lists here

Read auth state only through `useOptionalAuth()`/`useOptionalUser()`, and
credit state only through `useCredits()` — both are inert in this repo.

## Key Paths

| Path | Purpose |
|---|---|
| `src/lib/types.ts` | TS types mirroring `pinscopex/models.py` |
| `src/lib/api.ts` | All data fetching — single integration point with backend |
| `src/lib/mock-data.ts` | Pipeline step definitions for progress UI |
| `src/components/report/` | Report viewer components + power tree React Flow graph + derating table + finding comments |
| `src/components/progress/` | Pipeline progress stepper |
| `src/components/dashboard/` | Project card + create-project dialog |
| `src/components/upload/` | File upload components |
| `src/components/layout/` | Sidebar and layout shells |
| `src/components/pdf/` | PDF viewer (uses `react-pdf` for in-browser datasheet viewing) |
| `src/components/legal/` | Shared footer / legal-page scaffolding |
| `src/components/ui/` | shadcn primitives — Base UI, not Radix |
| `src/hooks/` | `use-auth-api`, `use-pipeline-progress`, `use-report`, `use-reviewed-count`, `use-reviewed-findings` |

## Routes

| Route | Page |
|---|---|
| `/` | Marketing landing page |
| `/contact`, `/privacy`, `/terms` | Marketing static pages |
| `/dashboard` | Project grid |
| `/project/[id]` | Project detail — tabbed: Project (uploads), BOM, Derating, Power, Logs, Settings |
| `/project/[id]/report` | Report viewer — findings grouped by component, filters in URL params, threaded comments |
| `/project/[id]/progress` | Pipeline progress — SSE-driven stepper |
| `/admin` | Admin dashboard — tabbed: Components, Users, Usage, Projects, Runs, Settings |

## shadcn/ui: Base UI, Not Radix

This project uses **Base UI** primitives (`@base-ui/react`), not Radix. Key differences:
- No `asChild` prop — use `render={<Component />}` instead for composition
- `Select.onValueChange` signature is `(value: string | null, details) => void`
- `CollapsibleTrigger` renders its children directly, no slot forwarding needed

Always check `src/components/ui/*.tsx` for the actual component API before using a shadcn component.

## Data Flow

1. Client components call functions from `src/lib/api.ts`
2. `api.ts` calls the FastAPI backend (`/api/projects`, `/api/report/{id}`, `/api/graph/{id}`, etc.)
3. File uploads (BOM, netlist, datasheets) use multipart form-data with MPN query param for datasheets; datasheet uploads support `also_for` for multi-MPN sharing
4. Pipeline progress streams via SSE; polling fallback at `/api/pipeline/{id}/status`; cancel via `cancelPipeline()`
5. BOM summary: `fetchBomSummary()`; derating: `fetchDerating()`; power tree: `fetchPowerTree()`; logs: `fetchProjectLogs()`
6. Comments: `addComment()`, `deleteComment()` on finding cards
7. Collaborators: `fetchCollaborators()`, `addCollaborator()`, `removeCollaborator()`
8. DigiKey: `autoResolveSimple()`, `fetchDigikeyDatasheet()`

## Development

```bash
cd frontend
npm run dev    # starts on localhost:3000
npm run build  # production build (verifies types)
```

Requires the backend running at `localhost:8000` (or set `NEXT_PUBLIC_API_URL`).

## Guidelines

- Keep all data fetching in `src/lib/api.ts` — don't scatter fetch calls across components
- Report filters persist in URL search params (`?status=ERROR&component=U3&q=decoupling`)
- When modifying types, keep `src/lib/types.ts` in sync with `backend/pinscopex/models.py`
- Use `font-mono` for technical values: designators (U1), MPNs, pin names, component values
- Status colors: emerald = PASS, amber = WARNING, rose = ERROR, blue = accent/active
