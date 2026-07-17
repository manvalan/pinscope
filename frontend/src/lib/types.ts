export type FindingStatus = "ERROR" | "WARNING" | "INFO";

export interface Finding {
  finding_id: string | null;
  designator: string;
  mpn: string;
  aspect: string | null;
  finding: string;
  why: string;
  source_page: number | null;
  source_quote?: string;
  source_designator?: string | null; // designator whose datasheet source_page refers to; null = this finding's own `designator` (evidence from a connected component's datasheet excerpt)
  status: FindingStatus;
  recommendation?: string;
  reference: string;
  source?: string | null; // "pin_mux_check"/"led_current_check" = deterministic; null/"review" = LLM
}

export interface FindingComment {
  comment_id: string;
  finding_id: string;
  user_id: string;
  user_name: string;
  text: string;
  mentions: string[];
  created_at: string;
}

export interface ValidationReport {
  project: string;
  timestamp: string;
  findings: Finding[];
  summary: Record<string, number>;
  coverage: Record<string, string[]>;
  review_errors?: Record<string, string>;
  not_reviewed?: { designator: string; reason: string }[];
  comments?: Record<string, FindingComment[]>;
}

export type NetType = "power" | "ground" | "signal" | "unknown";
export type ComponentType =
  | "resistor"
  | "capacitor"
  | "inductor"
  | "ic"
  | "connector"
  | "crystal"
  | "discrete"
  | "transformer"
  | "fuse"
  | "switch"
  | "test_point"
  | "fiducial"
  | "mechanical"
  | "unknown";

export interface ComponentSpecs {
  specs_type: string;
  value_formatted?: string;
  values?: Record<string, string | number | null>;
  [key: string]: unknown;
}

export interface Component {
  reference: string;
  value: string;
  footprint: string;
  component_type: ComponentType;
  component_subtype: string | null;
  mpn: string | null;
  pins: Record<string, string>;
  specs: ComponentSpecs | null;
}

export interface PinConnection {
  component_ref: string;
  pin_number: string;
  pin_name: string | null;
}

export interface Net {
  name: string;
  net_type: NetType;
  voltage: number | null;
  pins: PinConnection[];
}

export interface DesignGraph {
  components: Record<string, Component>;
  nets: Record<string, Net>;
}

export interface BomSummaryRow {
  mpn: string | null;
  designators: string[];
  value: string;
  category: string | null;
  specs: Record<string, string | number> | null;
  description: string | null;
  hasDatasheet: boolean;
}

export type ProjectStatus =
  | "draft"
  | "running"
  | "complete"
  | "error"
  | "cancelled"
  | "paused_insufficient_credits"
  | "paused_by_user";

export interface PauseCheckpoint {
  paused_at?: string | null;
  paused_stage?: string | null;
  last_completed_label?: string | null;
  completed_review_refs?: string[];
  pending_review_refs?: string[];
}

export interface CreditSnapshot {
  user_id: string;
  balance: number;
  plan: string; // always "payg" post-migration; kept for backward compat
  last_entry_ts?: string | null;
  next_expiry?: string | null;
}

export interface CreditGrant {
  grant_id: string;
  user_id: string;
  amount_usd: number;
  remaining: number;
  granted_at: string;
  expires_at: string | null;
  source:
    | "top_up"
    | "trial_grant"
    | "admin_adjust"
    | "refund_system_error"
    | "pre_migration";
  stripe_event_id?: string | null;
  expired?: boolean;
  note?: string | null;
}

export interface AutoTopupConfig {
  enabled: boolean;
  threshold_credits: number;
  amount_usd: number;
  has_payment_method: boolean;
  last_attempt_ts?: string | null;
  last_attempt_status?: "ok" | "failed" | "pending" | "";
  last_failure_reason?: string | null;
}

export interface CreditLedgerEntry {
  user_id: string;
  timestamp: string;
  delta: number;
  balance_after: number;
  reason: string;
  run_id?: string | null;
  unit_id?: string | null;
  stripe_event_id?: string | null;
  note?: string | null;
}

export interface CostItem {
  identifier: string;
  kind: string;
  api_cost_usd: number;
  source: "cache_hit" | "api_call" | "api_call_estimated";
  note?: string | null;
}

export interface CostEstimate {
  api_cost_low: number;
  api_cost_high: number;
  api_cost_mid: number;
  credits_low: number;
  credits_high: number;
  credits_mid: number;
  breakdown: CostItem[];
  ic_count: number;
  simple_count: number;
  passive_count: number;
  cached_ic_count: number;
  cached_simple_count: number;
  cached_passive_count: number;
  review_ic_count: number;
}

export interface SkippedComponent {
  identifier: string;
  stage: string;
  error: string;
}

/**
 * Cached purple-parts payload for a single LCSC id. Populated server-side at
 * BOM upload when the MPN column was detected as entirely LCSC ids; consumed
 * by the wizard's LCSC passive-resolve step to render a description summary
 * and by the backend to synthesize the auto-resolve call.
 */
export interface LcscPayload {
  mpn?: string | null;
  manufacturer?: string | null;
  package?: string | null;
  description?: string | null;
  category?: string | null;
  subcategory?: string | null;
}

export interface ComponentMpnBuckets {
  ic: string[];
  passive: string[];
  simple: string[];
}

export interface Project {
  id: string;
  name: string;
  created: string;
  status: ProjectStatus;
  summary?: Record<string, number>;
  hasNetlist: boolean;
  hasBom: boolean;
  datasheetCount: number;
  skippedComponents?: SkippedComponent[];
  userId?: string;
  collaborators?: string[];
  creditsSpent?: number;
  pauseCheckpoint?: PauseCheckpoint | null;
  pauseReason?: string | null;
  bomColumns?: { reference: string; mpn: string } | null;
  pipelineError?: string | null;
  // LCSC mapping captured at BOM upload time. `lcscToMpn` is the resolved
  // LCSC id → MPN map used by the wizard to render "C12044 → STM32F103C8T6";
  // `lcscPayloads` is the full purple-parts payload keyed by LCSC id used by
  // the per-row passive resolve step.
  lcscToMpn?: Record<string, string> | null;
  lcscPayloads?: Record<string, LcscPayload> | null;
  componentMpns?: ComponentMpnBuckets | null;
  pinscopeVersion?: string | null;
  // "pads" | "edif" — what kind of netlist file the user uploaded. null on
  // projects predating EDIF support; treat null as PADS for rendering.
  netlistFormat?: "pads" | "edif" | null;
  // Sub-design IDs (e.g. ["&0441"]) the user chose to include in the review.
  // null means "include every sub-design found in the file" — the default
  // for single-sub-design EDIFs and all PADS netlists.
  netlistSubdesigns?: string[] | null;
}

// One entry per EDIF sub-design (`&NNNN` ID prefix). Returned by the upload
// endpoint and the subdesigns inspection endpoint; consumed by the wizard's
// sub-design picker step.
export interface EdifSubDesign {
  id: string | null; // null → bare-named cells with no prefix
  instance_count: number;
  designators: string[];
}

export interface Collaborator {
  user_id: string;
  name: string | null;
  email: string | null;
  image_url: string | null;
  role: "owner" | "collaborator";
}

export interface ApiLogEntry {
  timestamp: string;
  stage: string;
  identifier: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  duration_ms: number;
  stop_reason: string;
  skill_id?: string | null;
  turns?: number | null;
  error?: string | null;
  cost_usd?: number | null;
}

export interface DeratingRow {
  designator: string;
  mpn: string | null;
  value_formatted: string | null;
  rated_voltage_v: number | null;
  operating_voltage_v: number | null;
  operating_voltage_source: string | null;
  net_plus: string | null;
  net_minus: string | null;
  dielectric_category: "ceramic" | "tantalum" | "electrolytic" | null;
}

export interface DeratingSettings {
  ceramic: number;
  tantalum: number;
  electrolytic: number;
}

export interface NetlistPreviewDesignator {
  ref: string;
  pins: { number: string; net_name: string }[];
}

export interface PipelineSubstep {
  key: string;
  label: string;
  status: "pending" | "running" | "complete";
  cached?: boolean;
}

export interface PipelineStep {
  title: string;
  description: string;
  substeps: PipelineSubstep[];
  status: "pending" | "running" | "complete";
  totalNew?: number;
}
