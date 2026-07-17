import type {
  ApiLogEntry,
  AutoTopupConfig,
  BomSummaryRow,
  Collaborator,
  ComponentMpnBuckets,
  CostEstimate,
  CreditGrant,
  CreditLedgerEntry,
  CreditSnapshot,
  DesignGraph,
  DeratingRow,
  EdifSubDesign,
  FindingComment,
  LcscPayload,
  NetlistPreviewDesignator,
  PauseCheckpoint,
  Project,
  SkippedComponent,
  ValidationReport,
} from "./types";

// Mirror of backend.pinscopex.utils.safe_mpn — used to match MPNs
// against datasheet filename stems returned by the server.
export function safeMpn(mpn: string): string {
  return mpn.replace(/\//g, "_").replace(/:/g, "_");
}

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Auth token getter — set by useAuthApi hook
let _getToken: (() => Promise<string | null>) | null = null;

export function setTokenGetter(getter: () => Promise<string | null>) {
  _getToken = getter;
}

async function authHeaders(): Promise<HeadersInit> {
  if (!_getToken) return {};
  const token = await _getToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

async function authFetch(url: string, init?: RequestInit): Promise<Response> {
  const headers = {
    ...init?.headers,
    ...(await authHeaders()),
  };
  return fetch(url, { ...init, headers });
}

// --- Projects ---

function mapProject(p: Record<string, unknown>): Project {
  return {
    id: p.id as string,
    name: p.name as string,
    created: p.created as string,
    status: p.status as Project["status"],
    summary: p.summary as Record<string, number> | undefined,
    hasNetlist: p.has_netlist as boolean,
    hasBom: p.has_bom as boolean,
    datasheetCount: p.datasheet_count as number,
    skippedComponents: (p.skipped_components as SkippedComponent[] | null) ?? undefined,
    userId: p.user_id as string | undefined,
    collaborators: (p.collaborators as string[] | null) ?? undefined,
    creditsSpent: (p.credits_spent as number | undefined) ?? undefined,
    pauseCheckpoint: (p.pause_checkpoint as PauseCheckpoint | null) ?? null,
    pauseReason: (p.pause_reason as string | null | undefined) ?? null,
    bomColumns: (p.bom_columns as { reference: string; mpn: string } | null) ?? null,
    pipelineError: (() => {
      const ps = p.pipeline_state as Record<string, unknown> | null | undefined;
      const err = ps?.error;
      return typeof err === "string" ? err : null;
    })(),
    lcscToMpn: (p.lcsc_to_mpn as Record<string, string> | null) ?? null,
    lcscPayloads: (p.lcsc_payloads as Record<string, LcscPayload> | null) ?? null,
    componentMpns: (p.component_mpns as ComponentMpnBuckets | null) ?? null,
    pinscopeVersion: (p.pinscope_version as string | null | undefined) ?? null,
    netlistFormat: (p.netlist_format as "pads" | "edif" | null | undefined) ?? null,
    netlistSubdesigns: (p.netlist_subdesigns as string[] | null) ?? null,
  };
}

export async function fetchProjects(): Promise<Project[]> {
  const res = await authFetch(`${BASE}/api/projects`);
  if (!res.ok) throw new Error("Failed to fetch projects");
  const data = await res.json();
  return data.map(mapProject);
}

export async function createPortalSession(returnUrl: string): Promise<string> {
  const res = await authFetch(`${BASE}/api/billing/portal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ return_url: returnUrl }),
  });
  if (!res.ok) throw new Error("Failed to create portal session");
  const data = await res.json();
  return data.url;
}

export async function createProject(name: string): Promise<Project> {
  const res = await authFetch(`${BASE}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error("Failed to create project");
  return mapProject(await res.json());
}

export async function fetchProject(projectId: string): Promise<Project> {
  const res = await authFetch(`${BASE}/api/projects/${projectId}`);
  if (!res.ok) throw new Error("Failed to fetch project");
  return mapProject(await res.json());
}

export async function deleteProject(projectId: string): Promise<void> {
  const res = await authFetch(`${BASE}/api/projects/${projectId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to delete project");
}

export async function reopenProject(projectId: string): Promise<Project> {
  const res = await authFetch(`${BASE}/api/projects/${projectId}/reopen`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to reopen" }));
    throw new Error(err.detail || "Failed to reopen project");
  }
  return mapProject(await res.json());
}

export async function renameProject(
  projectId: string,
  name: string,
): Promise<Project> {
  const res = await authFetch(`${BASE}/api/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to rename" }));
    throw new Error(err.detail || "Failed to rename project");
  }
  return mapProject(await res.json());
}

export async function downloadProjectBom(projectId: string): Promise<File> {
  const res = await authFetch(`${BASE}/api/projects/${projectId}/files/bom`);
  if (!res.ok) throw new Error("Failed to download BOM");
  const blob = await res.blob();
  return new File([blob], "bom.csv", { type: "text/csv" });
}

export async function downloadProjectNetlist(projectId: string): Promise<File> {
  const res = await authFetch(`${BASE}/api/projects/${projectId}/files/netlist`);
  if (!res.ok) throw new Error("Failed to download netlist");
  const blob = await res.blob();
  return new File([blob], "netlist.asc", { type: "text/plain" });
}

export async function fetchProjectDatasheets(projectId: string): Promise<Set<string>> {
  const res = await authFetch(
    `${BASE}/api/projects/${projectId}/files/datasheets`,
  );
  if (!res.ok) return new Set();
  const data = await res.json();
  return new Set((data.stems as string[]) ?? []);
}

// --- Collaborators ---

export async function fetchCollaborators(
  projectId: string,
): Promise<{ owner_user_id: string; collaborators: Collaborator[] }> {
  const res = await authFetch(`${BASE}/api/projects/${projectId}/collaborators`);
  if (!res.ok) return { owner_user_id: "", collaborators: [] };
  return res.json();
}

export async function addCollaborator(
  projectId: string,
  email: string,
): Promise<Collaborator> {
  const res = await authFetch(`${BASE}/api/projects/${projectId}/collaborators`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to add collaborator" }));
    throw new Error(err.detail || "Failed to add collaborator");
  }
  return res.json();
}

export async function removeCollaborator(
  projectId: string,
  userId: string,
): Promise<void> {
  const res = await authFetch(
    `${BASE}/api/projects/${projectId}/collaborators/${userId}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to remove collaborator" }));
    throw new Error(err.detail || "Failed to remove collaborator");
  }
}

export async function makeCollaboratorOwner(
  projectId: string,
  userId: string,
): Promise<void> {
  const res = await authFetch(
    `${BASE}/api/projects/${projectId}/collaborators/${userId}/make-owner`,
    { method: "POST" },
  );
  if (!res.ok) {
    const err = await res
      .json()
      .catch(() => ({ detail: "Failed to transfer ownership" }));
    throw new Error(err.detail || "Failed to transfer ownership");
  }
}

// --- File uploads ---

export interface UploadBomResponse {
  path: string;
  components: number;
  lcsc_resolved: number;
  lcsc_detected: boolean;
  lcsc_to_mpn: Record<string, string>;
}

export async function uploadBom(
  projectId: string,
  file: File,
  referenceColumn?: string,
  mpnColumn?: string,
): Promise<UploadBomResponse> {
  const form = new FormData();
  form.append("file", file);
  const params = new URLSearchParams();
  if (referenceColumn) params.set("reference_column", referenceColumn);
  if (mpnColumn) params.set("mpn_column", mpnColumn);
  const qs = params.toString();
  const res = await authFetch(
    `${BASE}/api/projects/${projectId}/upload/bom${qs ? `?${qs}` : ""}`,
    { method: "POST", body: form },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error(err.detail || "Failed to upload BOM");
  }
  return res.json();
}

export interface UploadNetlistResult {
  path: string;
  parts: number;
  nets: number;
  format: "pads" | "edif";
  sub_designs: EdifSubDesign[]; // empty for PADS / single-sub-design EDIF
  // EDIF only: server-built designator→pins preview, same shape as the
  // browser-side PADS parser produces. Empty list for PADS uploads (the
  // browser parses those locally).
  designator_pins: NetlistPreviewDesignator[];
}

export async function uploadNetlist(
  projectId: string, file: File,
): Promise<UploadNetlistResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await authFetch(
    `${BASE}/api/projects/${projectId}/upload/netlist`,
    { method: "POST", body: form },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error(err.detail || "Failed to upload netlist");
  }
  const data = await res.json();
  return {
    path: data.path as string,
    parts: data.parts as number,
    nets: data.nets as number,
    format: data.format as "pads" | "edif",
    sub_designs: (data.sub_designs as EdifSubDesign[] | undefined) ?? [],
    designator_pins:
      (data.designator_pins as NetlistPreviewDesignator[] | undefined) ?? [],
  };
}

export async function fetchNetlistSubdesigns(
  projectId: string,
): Promise<{ sub_designs: EdifSubDesign[]; selected: string[] | null }> {
  const res = await authFetch(
    `${BASE}/api/projects/${projectId}/netlist/subdesigns`,
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Fetch failed" }));
    throw new Error(err.detail || "Failed to fetch sub-designs");
  }
  const data = await res.json();
  return {
    sub_designs: (data.sub_designs as EdifSubDesign[] | undefined) ?? [],
    selected: (data.selected as string[] | null | undefined) ?? null,
  };
}

export async function updateNetlistSubdesigns(
  projectId: string,
  selected: string[] | null,
): Promise<Project> {
  const res = await authFetch(
    `${BASE}/api/projects/${projectId}/netlist/subdesigns`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected }),
    },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Update failed" }));
    throw new Error(err.detail || "Failed to update sub-designs");
  }
  return mapProject(await res.json());
}

export async function uploadDatasheet(
  projectId: string,
  mpn: string,
  file: File,
  alsoFor?: string[],
) {
  const form = new FormData();
  form.append("file", file);
  let url = `${BASE}/api/projects/${projectId}/upload/datasheets?mpn=${encodeURIComponent(mpn)}`;
  if (alsoFor?.length) {
    url += `&also_for=${alsoFor.map(encodeURIComponent).join(",")}`;
  }
  const res = await authFetch(url, { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Upload failed" }));
    throw new Error(err.detail || "Failed to upload datasheet");
  }
  return res.json();
}

// --- Library check ---

export async function checkLibrary(
  icMpns: string[],
  passiveMpns: string[],
  simpleMpns: string[] = [],
): Promise<{ ic_resolved: string[]; passive_resolved: string[]; simple_resolved: string[]; datasheets_available: string[] }> {
  const res = await authFetch(`${BASE}/api/library/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ic_mpns: icMpns, passive_mpns: passiveMpns, simple_mpns: simpleMpns }),
  });
  if (!res.ok) return { ic_resolved: [], passive_resolved: [], simple_resolved: [], datasheets_available: [] };
  return res.json();
}

// --- Pipeline ---

export async function startPipeline(projectId: string) {
  const res = await authFetch(`${BASE}/api/pipeline/${projectId}/start`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to start" }));
    throw new Error(err.detail || "Failed to start pipeline");
  }
  return res.json();
}

export async function cancelPipeline(projectId: string) {
  const res = await authFetch(`${BASE}/api/pipeline/${projectId}/cancel`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to cancel" }));
    throw new Error(err.detail || "Failed to cancel pipeline");
  }
  return res.json();
}

export async function restartPipeline(projectId: string) {
  const res = await authFetch(`${BASE}/api/pipeline/${projectId}/restart`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to restart" }));
    throw new Error(err.detail || "Failed to restart pipeline");
  }
  return res.json();
}

export async function regenPipeline(projectId: string, stages: string[]) {
  const res = await authFetch(`${BASE}/api/pipeline/${projectId}/regen`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stages }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to start regen" }));
    throw new Error(err.detail || "Failed to start regen");
  }
  return res.json();
}

export async function adminMarkProjectComplete(projectId: string) {
  const res = await authFetch(
    `${BASE}/api/admin/projects/${projectId}/mark-complete`,
    { method: "POST" },
  );
  if (!res.ok) {
    const err = await res
      .json()
      .catch(() => ({ detail: "Failed to mark project complete" }));
    throw new Error(err.detail || "Failed to mark project complete");
  }
  return res.json();
}

export function pipelineEventsUrl(projectId: string): string {
  return `${BASE}/api/pipeline/${projectId}/events`;
}

export async function fetchPipelineStatus(projectId: string) {
  const res = await authFetch(`${BASE}/api/pipeline/${projectId}/status`);
  if (!res.ok) throw new Error("Failed to fetch pipeline status");
  return res.json();
}

// --- Logs ---

export async function fetchProjectLogs(
  projectId: string,
): Promise<ApiLogEntry[]> {
  const res = await authFetch(`${BASE}/api/projects/${projectId}/logs`);
  if (!res.ok) return [];
  return res.json();
}

// --- Results ---

export async function fetchBomSummary(
  projectId: string,
): Promise<BomSummaryRow[]> {
  const res = await authFetch(`${BASE}/api/bom/${projectId}`);
  if (!res.ok) return [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const raw: any[] = await res.json();
  return raw.map((r) => ({
    mpn: r.mpn,
    designators: r.designators,
    value: r.value,
    category: r.category,
    specs: r.specs,
    description: r.description ?? null,
    hasDatasheet: r.has_datasheet ?? false,
  }));
}

export async function fetchDerating(
  projectId: string,
): Promise<DeratingRow[]> {
  const res = await authFetch(`${BASE}/api/derating/${projectId}`);
  if (!res.ok) return [];
  return res.json();
}

export async function fetchReport(
  projectId: string,
): Promise<ValidationReport> {
  const res = await authFetch(`${BASE}/api/report/${projectId}`);
  if (!res.ok) throw new Error("Failed to fetch report");
  return res.json();
}

export async function addComment(
  projectId: string,
  findingId: string,
  text: string,
  userName: string,
  mentions: string[],
): Promise<FindingComment> {
  const res = await authFetch(`${BASE}/api/report/${projectId}/comments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ finding_id: findingId, text, user_name: userName, mentions }),
  });
  if (!res.ok) throw new Error("Failed to add comment");
  return res.json();
}

export async function deleteComment(
  projectId: string,
  commentId: string,
): Promise<void> {
  const res = await authFetch(`${BASE}/api/report/${projectId}/comments/${commentId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to delete comment");
}

export async function fetchGraph(projectId: string): Promise<DesignGraph> {
  const res = await authFetch(`${BASE}/api/graph/${projectId}`);
  if (!res.ok) throw new Error("Failed to fetch graph");
  return res.json();
}

// --- Admin ---

export interface AdminIC {
  mpn: string;
  type: "ic";
  subtype: string;
  pin_count: number;
  has_ratings: boolean;
}

export interface AdminPassive {
  mpn: string;
  type: "passive";
  subtype: string;
  description: string;
  regex: string;
}

export interface AdminSimple {
  mpn: string;
  type: "simple";
  specs_type: string;
  subtype: string;
  param_count: number;
}

export interface AdminComponents {
  ics: AdminIC[];
  passives: AdminPassive[];
  simple: AdminSimple[];
}

export interface AdminUser {
  user_id: string;
  project_count: number;
  balance: number;
  name: string | null;
  email: string | null;
  image_url: string | null;
}

// --- Admin: Usage ---

export interface AdminUsageProject {
  id: string;
  name: string;
  status: string;
  cost_usd: number;
  created: string;
}

export interface AdminUsageUser {
  user_id: string;
  name: string | null;
  email: string | null;
  project_count: number;
  total_cost_usd: number;
  projects: AdminUsageProject[];
}

export interface AdminUsage {
  grand_total_usd: number;
  users: AdminUsageUser[];
}

export async function fetchAdminUsage(): Promise<AdminUsage> {
  const res = await authFetch(`${BASE}/api/admin/usage`);
  if (!res.ok) throw new Error("Failed to fetch usage data");
  return res.json();
}

// --- Admin: Projects ---

export interface AdminProject {
  id: string;
  name: string;
  user_id: string;
  status: string;
  created: string;
  updated: string;
  has_bom: boolean;
  has_netlist: boolean;
  datasheet_count: number;
  total_cost_usd: number | null;
  pipeline_state: Record<string, unknown> | null;
  summary: Record<string, number> | null;
  owner_name: string | null;
  owner_email: string | null;
}

export async function fetchAdminProjects(): Promise<AdminProject[]> {
  const res = await authFetch(`${BASE}/api/admin/projects`);
  if (!res.ok) throw new Error("Failed to fetch projects");
  return res.json();
}

// --- Admin: Pipeline Runs ---

export interface AdminPipelineRun {
  project_id: string;
  user_id: string;
  project_name: string;
  started_at: string;
  duration_seconds: number;
  current_stage: string | null;
  current_substep: string | null;
  owner_name: string | null;
  owner_email: string | null;
}

export async function fetchAdminRuns(): Promise<AdminPipelineRun[]> {
  const res = await authFetch(`${BASE}/api/admin/runs`);
  if (!res.ok) throw new Error("Failed to fetch running pipelines");
  return res.json();
}

// --- Admin: Components ---

export async function fetchAdminComponents(): Promise<AdminComponents> {
  const res = await authFetch(`${BASE}/api/admin/components`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch components");
  return res.json();
}

export async function fetchAdminComponentJson(
  componentType: "ic" | "passive" | "simple",
  name: string,
): Promise<Record<string, unknown>> {
  const res = await authFetch(
    `${BASE}/api/admin/components/${componentType}/${encodeURIComponent(name)}`,
  );
  if (!res.ok) throw new Error("Failed to fetch component JSON");
  return res.json();
}

export async function deleteAdminComponent(
  componentType: "ic" | "passive" | "simple",
  name: string,
): Promise<void> {
  const res = await authFetch(
    `${BASE}/api/admin/components/${componentType}/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `Failed to delete component (${res.status})`);
  }
}

export async function deleteAdminFinding(
  projectId: string,
  findingId: string,
): Promise<{
  deleted: string;
  project_id: string;
  remaining: number;
  summary: Record<string, number>;
}> {
  const res = await authFetch(
    `${BASE}/api/admin/projects/${encodeURIComponent(projectId)}/findings/${encodeURIComponent(findingId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail || `Failed to delete finding (${res.status})`);
  }
  return res.json();
}

export async function fetchAdminUsers(): Promise<AdminUser[]> {
  const res = await authFetch(`${BASE}/api/admin/users`);
  if (!res.ok) throw new Error("Failed to fetch users");
  return res.json();
}

export async function searchAdminUsers(email: string): Promise<AdminUser[]> {
  const res = await authFetch(
    `${BASE}/api/admin/users/search?email=${encodeURIComponent(email)}`,
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Search failed" }));
    throw new Error(err.detail || "Search failed");
  }
  return res.json();
}

export async function adminAdjustCredits(
  userId: string,
  delta: number,
  note: string,
): Promise<void> {
  const res = await authFetch(`${BASE}/api/credits/admin/${userId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ delta, note }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to adjust" }));
    throw new Error(err.detail || "Failed to adjust credits");
  }
}

// --- Admin: Settings ---

export interface AdminSettings {
  default_model_version: string;
  min_model_version: string;
}

export async function fetchAdminSettings(): Promise<AdminSettings> {
  const res = await authFetch(`${BASE}/api/admin/settings`);
  if (!res.ok) throw new Error("Failed to fetch admin settings");
  return res.json();
}

export async function setMinModelVersion(
  version: string,
): Promise<{ min_model_version: string }> {
  const res = await authFetch(`${BASE}/api/admin/settings/min-model-version`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ min_model_version: version }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to save" }));
    throw new Error(err.detail || "Failed to update model version setting");
  }
  return res.json();
}

// --- DigiKey auto-fetch ---

// --- DigiKey auto-resolve ---

export interface AutoResolveResult {
  mpn: string;
  status: "resolved" | "failed";
  error?: string;
}

export async function autoResolveSimple(
  items: { mpn: string; component_type: string }[],
): Promise<{ results: AutoResolveResult[] }> {
  const res = await authFetch(`${BASE}/api/digikey/auto-resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Auto-resolve failed" }));
    throw new Error(err.detail || "Auto-resolve failed");
  }
  return res.json();
}

// --- LCSC per-row passive resolve (wizard, post-BOM-upload) ---

export interface LcscResolvePassiveResponse {
  mpn: string;
  safe_mpn: string;
  // Shape mirrors the persisted passive specs model in
  // backend/pinscopex/models.py — a discriminated union over `specs_type`.
  // Keep loose at this layer; the wizard surfaces a small summary.
  model: Record<string, unknown>;
  cached: boolean;
  lcsc_id: string;
}

export class LcscResolveError extends Error {
  status: number;
  required: number | null;
  available: number | null;
  constructor(
    message: string,
    status: number,
    required: number | null,
    available: number | null,
  ) {
    super(message);
    this.name = "LcscResolveError";
    this.status = status;
    this.required = required;
    this.available = available;
  }
}

export async function resolveLcscPassive(
  projectId: string,
  lcscId: string,
): Promise<LcscResolvePassiveResponse> {
  const res = await authFetch(
    `${BASE}/api/projects/${projectId}/lcsc/resolve-passive`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lcsc_id: lcscId }),
    },
  );
  if (!res.ok) {
    // Backend returns 402 with structured detail on insufficient credits.
    // 404 / 502 surface as plain string detail.
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    if (
      res.status === 402 &&
      detail &&
      typeof detail === "object" &&
      detail.reason === "insufficient_credits"
    ) {
      throw new LcscResolveError(
        "Out of credits",
        402,
        typeof detail.required === "number" ? detail.required : null,
        typeof detail.available === "number" ? detail.available : null,
      );
    }
    const msg =
      typeof detail === "string"
        ? detail
        : `Resolve failed (${res.status})`;
    throw new LcscResolveError(msg, res.status, null, null);
  }
  return res.json();
}

export class DigiKeyFetchError extends Error {
  url: string | null;
  constructor(message: string, url: string | null) {
    super(message);
    this.name = "DigiKeyFetchError";
    this.url = url;
  }
}

export async function fetchDigikeyDatasheet(
  mpn: string,
): Promise<{ file: File; url: string | null }> {
  const res = await authFetch(
    `${BASE}/api/digikey/datasheet?mpn=${encodeURIComponent(mpn)}`,
  );
  if (!res.ok) {
    const err = await res
      .json()
      .catch(() => ({ detail: "Fetch failed", url: null }));
    throw new DigiKeyFetchError(
      err.detail || "Failed to fetch datasheet from DigiKey",
      err.url ?? null,
    );
  }
  const url = res.headers.get("X-Datasheet-Url");
  const blob = await res.blob();
  return { file: new File([blob], `${mpn}.pdf`, { type: "application/pdf" }), url };
}

// --- Datasheets ---

export async function fetchDatasheetUrl(
  projectId: string,
  mpn: string,
): Promise<string | null> {
  // Fetch the PDF bytes directly via the authenticated proxy endpoint,
  // then return a blob URL that react-pdf can load without auth headers.
  const res = await authFetch(
    `${BASE}/api/projects/${projectId}/datasheet/${encodeURIComponent(mpn)}`,
  );
  if (!res.ok) return null;
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// ---------------------------------------------------------------------------
// Credits + estimate + resume
// ---------------------------------------------------------------------------

export async function fetchCredits(): Promise<CreditSnapshot> {
  const res = await authFetch(`${BASE}/api/credits`);
  if (!res.ok) throw new Error("Failed to fetch credits");
  return res.json();
}

export interface CreditLedgerPage {
  entries: CreditLedgerEntry[];
  total: number;
  limit: number;
  offset: number;
}

export async function fetchCreditLedger(
  limit = 50,
  offset = 0,
): Promise<CreditLedgerPage> {
  const res = await authFetch(
    `${BASE}/api/credits/ledger?limit=${limit}&offset=${offset}`,
  );
  if (!res.ok) throw new Error("Failed to fetch ledger");
  return res.json();
}

export async function reconcileCheckoutSession(
  sessionId: string,
): Promise<{ ok: boolean; kind: "topup" | "unknown"; payment_status: string | null }> {
  const res = await authFetch(`${BASE}/api/billing/reconcile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? "Failed to reconcile checkout");
  }
  return res.json();
}

export async function fetchCreditGrants(): Promise<CreditGrant[]> {
  const res = await authFetch(`${BASE}/api/credits/grants`);
  if (!res.ok) return [];
  const data = await res.json();
  return (data.grants as CreditGrant[]) ?? [];
}

export async function createTopUpCheckout(
  amountUsd: number,
  successUrl: string,
  cancelUrl: string,
): Promise<string> {
  const res = await authFetch(`${BASE}/api/billing/topup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      amount_usd: amountUsd,
      success_url: successUrl,
      cancel_url: cancelUrl,
    }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? "Failed to start top-up");
  }
  const data = await res.json();
  return data.url as string;
}

export async function fetchPipelineEstimate(
  projectId: string,
): Promise<CostEstimate> {
  const res = await authFetch(
    `${BASE}/api/pipeline/${projectId}/estimate`,
    { method: "POST" },
  );
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? "Failed to fetch estimate");
  }
  return res.json();
}

export async function fetchAutoTopup(): Promise<AutoTopupConfig> {
  const res = await authFetch(`${BASE}/api/credits/autotopup`);
  if (!res.ok) throw new Error("Failed to load auto top-up config");
  return res.json();
}

export async function updateAutoTopup(
  cfg: { enabled: boolean; threshold_credits: number; amount_usd: number },
): Promise<AutoTopupConfig> {
  const res = await authFetch(`${BASE}/api/credits/autotopup`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? "Failed to update auto top-up");
  }
  return res.json();
}

export async function resumePipeline(projectId: string): Promise<void> {
  const res = await authFetch(
    `${BASE}/api/pipeline/${projectId}/resume`,
    { method: "POST" },
  );
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? "Failed to resume pipeline");
  }
}

// ---------------------------------------------------------------------------
// Onboarding survey
// ---------------------------------------------------------------------------

export async function fetchSurveyStatus(): Promise<{ completed: boolean }> {
  const res = await authFetch(`${BASE}/api/survey/status`);
  if (!res.ok) return { completed: true };
  return res.json();
}

export async function submitSurvey(payload: {
  referral_source: string;
  user_profile: string;
}): Promise<{ ok: boolean }> {
  const res = await authFetch(`${BASE}/api/survey`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) return { ok: false };
  return res.json();
}

// ---------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------

export interface FeedbackTicket {
  ticket_id: string;
  user_id: string;
  user_name: string | null;
  user_email: string | null;
  project_id: string | null;
  project_name: string | null;
  type: "bug" | "rule_feedback" | "feature_request";
  status: "open" | "acknowledged" | "resolved";
  finding_id: string | null;
  finding_text: string | null;
  finding_designator: string | null;
  finding_mpn: string | null;
  finding_status: string | null;
  message: string;
  admin_notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateFeedbackPayload {
  type: "bug" | "rule_feedback" | "feature_request";
  message: string;
  project_id?: string;
  project_name?: string;
  user_name?: string;
  user_email?: string;
  finding_id?: string;
  finding_text?: string;
  finding_designator?: string;
  finding_mpn?: string;
  finding_status?: string;
}

export async function submitFeedback(
  payload: CreateFeedbackPayload,
): Promise<FeedbackTicket> {
  const res = await authFetch(`${BASE}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? "Failed to submit feedback");
  }
  return res.json();
}

export async function fetchMyFeedback(
  status?: string,
): Promise<FeedbackTicket[]> {
  const params = status ? `?status=${status}` : "";
  const res = await authFetch(`${BASE}/api/feedback${params}`);
  if (!res.ok) throw new Error("Failed to load feedback");
  return res.json();
}

export async function fetchAdminFeedback(params?: {
  project_id?: string;
  status?: string;
  type?: string;
}): Promise<FeedbackTicket[]> {
  const qs = new URLSearchParams();
  if (params?.project_id) qs.set("project_id", params.project_id);
  if (params?.status) qs.set("status", params.status);
  if (params?.type) qs.set("type", params.type);
  const q = qs.toString();
  const res = await authFetch(`${BASE}/api/admin/feedback${q ? `?${q}` : ""}`);
  if (!res.ok) throw new Error("Failed to load admin feedback");
  return res.json();
}

export async function updateAdminFeedback(
  ticketId: string,
  update: { status?: string; admin_notes?: string },
): Promise<FeedbackTicket> {
  const res = await authFetch(`${BASE}/api/admin/feedback/${ticketId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? "Failed to update feedback");
  }
  return res.json();
}
