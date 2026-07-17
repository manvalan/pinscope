"use client";

import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { FileUploadZone } from "@/components/upload/file-upload-zone";
import {
  Plus,
  Upload,
  Loader2,
  ChevronRight,
  ChevronLeft,
  Cpu,
  AlertCircle,
  X,
  CheckCircle2,
  Zap,
  ExternalLink,
} from "lucide-react";
import { authEnabled } from "@/lib/auth";
import { cn } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  createProject,
  deleteProject,
  uploadBom,
  uploadNetlist,
  uploadDatasheet,
  startPipeline,
  checkLibrary,
  fetchDigikeyDatasheet,
  autoResolveSimple,
  resolveLcscPassive,
  DigiKeyFetchError,
  LcscResolveError,
  reopenProject,
  renameProject,
  downloadProjectBom,
  downloadProjectNetlist,
  fetchProject,
  fetchProjectDatasheets,
  fetchPipelineEstimate,
  fetchCredits,
  safeMpn,
  updateNetlistSubdesigns,
} from "@/lib/api";
import type {
  CostEstimate,
  EdifSubDesign,
  LcscPayload,
  NetlistPreviewDesignator,
  Project,
} from "@/lib/types";
import Link from "next/link";
import * as XLSX from "xlsx";

// ---------------------------------------------------------------------------
// CSV / XLSX parsing (client-side, for column detection + IC classification)
// ---------------------------------------------------------------------------

function parseCsvLine(line: string): string[] {
  const fields: string[] = [];
  let current = "";
  let inQuotes = false;
  for (const ch of line) {
    if (ch === '"') {
      inQuotes = !inQuotes;
    } else if (ch === "," && !inQuotes) {
      fields.push(current.trim());
      current = "";
    } else if (ch !== "\r") {
      current += ch;
    }
  }
  fields.push(current.trim());
  return fields;
}

function parseCsv(text: string) {
  const lines = text.split("\n").filter((l) => l.trim());
  if (lines.length === 0) return { headers: [] as string[], rows: [] as Record<string, string>[] };
  const headers = parseCsvLine(lines[0]);
  const rows = lines.slice(1).map((line) => {
    const values = parseCsvLine(line);
    const row: Record<string, string> = {};
    headers.forEach((h, i) => {
      row[h] = values[i] || "";
    });
    return row;
  });
  return { headers, rows };
}

function parseXlsx(buffer: ArrayBuffer) {
  const wb = XLSX.read(buffer, { type: "array" });
  const ws = wb.Sheets[wb.SheetNames[0]];
  const raw = XLSX.utils.sheet_to_json<string[]>(ws, { header: 1, defval: "" });
  if (raw.length === 0) return { headers: [] as string[], rows: [] as Record<string, string>[] };
  const headers = raw[0].map(String);
  const rows = raw.slice(1).map((r) => {
    const row: Record<string, string> = {};
    headers.forEach((h, i) => { row[h] = r[i] != null ? String(r[i]) : ""; });
    return row;
  });
  return { headers, rows };
}

// ---------------------------------------------------------------------------
// Column auto-detection
// ---------------------------------------------------------------------------

const REF_CANDIDATES = ["Reference", "Designator", "Ref Des", "RefDes", "Ref"];
const MPN_CANDIDATES = [
  "Manufacturer Part Number",
  "MPN",
  "Part Number",
  "Mfr Part",
  "Mfg Part Number",
];

function autoDetect(headers: string[], candidates: string[]): string | null {
  for (const c of candidates) {
    if (headers.includes(c)) return c;
  }
  const lower = candidates.map((c) => c.toLowerCase());
  for (const h of headers) {
    if (lower.includes(h.toLowerCase())) return h;
  }
  return null;
}

// ---------------------------------------------------------------------------
// BOM classification — mirrors pinscopex/taxonomy.py REF_PREFIX_TO_TYPE
// ---------------------------------------------------------------------------

interface IcMpnEntry {
  mpn: string;
  refs: string[];
}

interface PassiveGroup {
  prefix: string;
  label: string;
  mpns: { mpn: string; refs: string[] }[];
}

interface SimpleGroup {
  prefix: string;
  label: string;
  mpns: { mpn: string; refs: string[]; refPrefix: string }[];
}

const PASSIVE_LABELS: Record<string, string> = {
  R: "Resistors",
  C: "Capacitors",
  L: "Inductors",
  FB: "Ferrite Beads",
};

const SIMPLE_LABELS: Record<string, string> = {
  D: "Discrete Semiconductors",
  Q: "Discrete Semiconductors",
  LED: "Discrete Semiconductors",
  J: "Connectors",
  X: "Crystals",
  Y: "Crystals",
  T: "Transformers",
  F: "Fuses",
  SW: "Switches",
};

const SIMPLE_PREFIXES = new Set(Object.keys(SIMPLE_LABELS));

const SIMPLE_PREFIX_TO_TYPE: Record<string, string> = {
  D: "discrete", Q: "discrete", LED: "discrete",
  J: "connector", X: "crystal", Y: "crystal",
  T: "transformer", F: "fuse", SW: "switch",
};

/**
 * Extract a series prefix from an MPN for datasheet auto-sharing.
 *
 * Includes leading alpha chars, an optional dash, then up to 4 alphanumeric
 * series-code characters.  This distinguishes series like ERJ-2RKF vs
 * ERJ-3RQF vs ERJ-U02 that share leading letters but need different
 * datasheets.
 *
 * Examples:
 *   ERJ-2RKF10R0X  → ERJ-2RKF
 *   ERJ-3RQF2R2V   → ERJ-3RQF
 *   ERJ-U02F6R20X  → ERJ-U02F
 *   GRM155R60J475  → GRM155R
 *   04023D104KAT2A → 0402   (digit-leading MPNs)
 */
function getSeriesPrefix(mpn: string): string {
  const m = mpn.match(/^[A-Za-z]+-?[A-Za-z0-9]{1,4}/);
  return (m?.[0] || mpn.slice(0, 4)).toUpperCase();
}

function classifyBom(
  rows: Record<string, string>[],
  refCol: string,
  mpnCol: string,
) {
  const icMap = new Map<string, string[]>();
  const passiveMap = new Map<string, { refs: string[]; prefix: string }>();
  const simpleMap = new Map<string, { refs: string[]; prefix: string }>();
  let passiveCount = 0;
  let totalRefs = 0;

  for (const row of rows) {
    const refsRaw = row[refCol] || "";
    const mpn = row[mpnCol] || "";
    const refs = refsRaw
      .split(",")
      .map((r) => r.trim())
      .filter(Boolean);

    for (const ref of refs) {
      totalRefs++;
      const prefix = (ref.match(/^[A-Za-z]+/)?.[0] || "").toUpperCase();
      if (!mpn) continue;
      if (prefix === "U") {
        const existing = icMap.get(mpn) || [];
        existing.push(ref);
        icMap.set(mpn, existing);
      } else if (["R", "C", "L", "FB"].includes(prefix)) {
        passiveCount++;
        const entry = passiveMap.get(mpn) || { refs: [], prefix };
        entry.refs.push(ref);
        passiveMap.set(mpn, entry);
      } else if (SIMPLE_PREFIXES.has(prefix)) {
        const entry = simpleMap.get(mpn) || { refs: [], prefix };
        entry.refs.push(ref);
        simpleMap.set(mpn, entry);
      }
    }
  }

  const icMpns: IcMpnEntry[] = Array.from(icMap.entries())
    .map(([mpn, refs]) => ({ mpn, refs: refs.sort() }))
    .sort((a, b) => a.mpn.localeCompare(b.mpn));

  // Group passive MPNs by component type prefix
  const groupMap = new Map<string, { mpn: string; refs: string[] }[]>();
  for (const [mpn, { refs, prefix }] of passiveMap.entries()) {
    const group = groupMap.get(prefix) || [];
    group.push({ mpn, refs: refs.sort() });
    groupMap.set(prefix, group);
  }
  const passiveGroups: PassiveGroup[] = Array.from(groupMap.entries())
    .map(([prefix, mpns]) => ({
      prefix,
      label: PASSIVE_LABELS[prefix] || prefix,
      mpns: mpns.sort((a, b) => a.mpn.localeCompare(b.mpn)),
    }))
    .sort((a, b) => a.prefix.localeCompare(b.prefix));

  // Group simple component MPNs by label (merge D/Q/LED into one group, etc.)
  const simpleLabelMap = new Map<string, { mpn: string; refs: string[]; refPrefix: string }[]>();
  for (const [mpn, { refs, prefix }] of simpleMap.entries()) {
    const label = SIMPLE_LABELS[prefix] || prefix;
    const group = simpleLabelMap.get(label) || [];
    group.push({ mpn, refs: refs.sort(), refPrefix: prefix });
    simpleLabelMap.set(label, group);
  }
  const simpleGroups: SimpleGroup[] = Array.from(simpleLabelMap.entries())
    .map(([label, mpns]) => ({
      prefix: label,
      label,
      mpns: mpns.sort((a, b) => a.mpn.localeCompare(b.mpn)),
    }))
    .sort((a, b) => a.label.localeCompare(b.label));

  return {
    icMpns,
    icCount: icMpns.reduce((sum, e) => sum + e.refs.length, 0),
    passiveCount,
    passiveGroups,
    simpleGroups,
    totalRefs,
  };
}

// ---------------------------------------------------------------------------
// LCSC detection (mirror of backend.services.purple_parts.detect_lcsc_column).
// Run on the client so we know during the columns step whether the BOM upload
// will trigger an LCSC → MPN rewrite. A single non-LCSC entry disqualifies
// the column, matching the backend's column-level all-or-nothing rule.
// ---------------------------------------------------------------------------

const LCSC_ID_RE = /^C\d+$/;

function detectLcscColumn(
  rows: Record<string, string>[],
  mpnCol: string,
): boolean {
  let seenAny = false;
  for (const r of rows) {
    const v = (r[mpnCol] || "").trim();
    if (!v) continue;
    if (!LCSC_ID_RE.test(v)) return false;
    seenAny = true;
  }
  return seenAny;
}

/**
 * Build a one-line summary like "10kΩ · 0603 · 50V" from the auto-resolve
 * response's `model` field (a serialized ResistorSpecs / CapacitorSpecs /
 * InductorSpecs / SimpleComponentSpecs). Shown next to the resolved row.
 */
function summarizeLcscModel(model: Record<string, unknown>): string {
  const parts: string[] = [];
  const value = model.value_formatted;
  if (typeof value === "string" && value) parts.push(value);
  const pkg = model.package;
  if (typeof pkg === "string" && pkg) parts.push(pkg);
  const v = model.voltage_rating_v ?? model.voltage_rating;
  if (typeof v === "string" && v) parts.push(v);
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------
// Wizard step definitions
// ---------------------------------------------------------------------------

type WizardStep =
  | "details"
  | "columns"
  | "subdesigns"
  | "lcsc-passives"
  | "datasheets"
  | "simple"
  | "passives";

const ALL_STEPS: { key: WizardStep; label: string }[] = [
  { key: "details", label: "Project Details" },
  { key: "columns", label: "BOM Columns" },
  { key: "subdesigns", label: "Sub-designs" },
  { key: "lcsc-passives", label: "Resolving Passive Specs" },
  { key: "datasheets", label: "IC Datasheets" },
  { key: "simple", label: "Component Datasheets (Optional)" },
  { key: "passives", label: "Passive Datasheets (Optional)" },
];

// Sentinel for sub-design id `null` (bare-named cells with no &NNNN prefix).
// Sets only carry strings, so we tag this case with an empty-string key.
const NULL_SUBDESIGN_KEY = "";
const _subKey = (id: string | null): string => id ?? NULL_SUBDESIGN_KEY;

// Concurrency cap for the per-row LCSC passive resolve. Backend charges one
// credit-billing API call per request; small batch keeps latency reasonable
// without overloading the resolve endpoint.
const LCSC_RESOLVE_CONCURRENCY = 5;

// Cap on parallel DigiKey datasheet fetches per step. DigiKey will rate-limit
// at higher concurrency on large BOMs.
const AUTO_FETCH_CONCURRENCY = 8;

function naturalSortKey(s: string): (string | number)[] {
  const parts: (string | number)[] = [];
  const re = /(\d+)|(\D+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s)) !== null) {
    parts.push(m[1] !== undefined ? parseInt(m[1], 10) : m[2].toLowerCase());
  }
  return parts;
}

function naturalCompare(a: string, b: string): number {
  const ka = naturalSortKey(a);
  const kb = naturalSortKey(b);
  const n = Math.min(ka.length, kb.length);
  for (let i = 0; i < n; i++) {
    const x = ka[i];
    const y = kb[i];
    if (typeof x === typeof y) {
      if (x < y) return -1;
      if (x > y) return 1;
    } else {
      return typeof x === "number" ? -1 : 1;
    }
  }
  return ka.length - kb.length;
}

/**
 * Parse a PADS-PCB netlist directly in the browser to produce the
 * designator→pins mapping. Avoids a backend round-trip + cold-start latency
 * on the Create Project flow.
 *
 * Tolerant of the common format: splits on `*SIGNAL*` delimiters, then for
 * each block reads the net name (first line) and tokenises the remaining
 * lines as `REF.PIN` pairs until the next `*`-prefixed section marker.
 */
function parseNetlistPreview(text: string): NetlistPreviewDesignator[] {
  const byRef = new Map<string, Map<string, string>>();
  const blocks = text.split(/\*SIGNAL\*/);
  for (let i = 1; i < blocks.length; i++) {
    const lines = blocks[i].split(/\r?\n/);
    const netName = lines[0].trim();
    if (!netName) continue;
    for (let j = 1; j < lines.length; j++) {
      const line = lines[j].trim();
      if (!line) continue;
      if (line.startsWith("*")) break;
      for (const tok of line.split(/\s+/)) {
        const dot = tok.indexOf(".");
        if (dot <= 0 || dot === tok.length - 1) continue;
        const ref = tok.slice(0, dot);
        const pin = tok.slice(dot + 1);
        let pins = byRef.get(ref);
        if (!pins) {
          pins = new Map();
          byRef.set(ref, pins);
        }
        if (!pins.has(pin)) pins.set(pin, netName);
      }
    }
  }
  const refs = [...byRef.keys()].sort(naturalCompare);
  return refs.map((ref) => {
    const pinsMap = byRef.get(ref)!;
    const pins = [...pinsMap.entries()]
      .sort((a, b) => naturalCompare(a[0], b[0]))
      .map(([number, net_name]) => ({ number, net_name }));
    return { ref, pins };
  });
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface CreateProjectDialogProps {
  disabled?: boolean;
  onCreateProject: (project: Project) => void;
  rerunProject?: Project | null;
  onRerunDone?: () => void;
  cloneAsNewProject?: Project | null;
  onCloneAsNewDone?: () => void;
}

function countNetsInNetlist(text: string): number {
  return (text.match(/\*SIGNAL\*/g) || []).length;
}

// EDIF files are s-expression Lisp-like — backend handles them, but the
// PADS-shape preview functions don't, so detect early and skip them.
function isEdifNetlist(text: string): boolean {
  return text.slice(0, 1024).trimStart().slice(0, 5).toLowerCase() === "(edif";
}

export function CreateProjectDialog({
  disabled,
  onCreateProject,
  rerunProject,
  onRerunDone,
  cloneAsNewProject,
  onCloneAsNewDone,
}: CreateProjectDialogProps) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<WizardStep>("details");

  // Rerun mode: operate on an existing project rather than creating new.
  const [existingProjectId, setExistingProjectId] = useState<string | null>(null);
  const [initialBomFile, setInitialBomFile] = useState<File | null>(null);
  const [initialNetlistFile, setInitialNetlistFile] = useState<File | null>(null);
  const [existingDatasheetStems, setExistingDatasheetStems] = useState<Set<string>>(
    new Set(),
  );
  const [prefillLoading, setPrefillLoading] = useState(false);
  const [prefillError, setPrefillError] = useState<string | null>(null);

  // Pre-flight credit estimate (shown under Run button on the final step).
  const [estimate, setEstimate] = useState<CostEstimate | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [estimateLoading, setEstimateLoading] = useState(false);

  // Step 1
  const [name, setName] = useState("");
  const [bomFile, setBomFile] = useState<File | null>(null);
  const [netlistFile, setNetlistFile] = useState<File | null>(null);
  const [netlistNetCount, setNetlistNetCount] = useState<number | null>(null);
  const [netlistError, setNetlistError] = useState<string | null>(null);

  // Step 2
  const [csvData, setCsvData] = useState<ReturnType<typeof parseCsv> | null>(
    null,
  );
  const [refCol, setRefCol] = useState<string | null>(null);
  const [mpnCol, setMpnCol] = useState<string | null>(null);

  // Step 3
  const [datasheetFiles, setDatasheetFiles] = useState(
    new Map<string, File>(),
  );

  // Step 3.5 — simple component datasheets (per-MPN, optional)
  const [simpleDatasheetFiles, setSimpleDatasheetFiles] = useState(
    new Map<string, File>(),
  );

  // Step 4
  const [passiveDatasheetFiles, setPassiveDatasheetFiles] = useState(
    new Map<string, File>(),
  );

  // Step 5 — power sources (optional)
  const [netlistPreview, setNetlistPreview] = useState<
    NetlistPreviewDesignator[] | null
  >(null);
  const [netlistPreviewError, setNetlistPreviewError] = useState<string | null>(
    null,
  );

  // Creation state
  const [creating, setCreating] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Library resolution state
  const [resolvedIcMpns, setResolvedIcMpns] = useState<Set<string>>(new Set());
  const [resolvedPassiveMpns, setResolvedPassiveMpns] = useState<Set<string>>(
    new Set(),
  );
  const [resolvedSimpleMpns, setResolvedSimpleMpns] = useState<Set<string>>(
    new Set(),
  );
  const [libraryDatasheets, setLibraryDatasheets] = useState<Set<string>>(
    new Set(),
  );
  const [checkingLibrary, setCheckingLibrary] = useState(false);

  // Auto-resolve state: per-MPN status
  type ResolveStatus = "resolving" | "resolved" | "failed";
  const [resolveStatus, setResolveStatus] = useState<Map<string, ResolveStatus>>(new Map());
  const [resolveErrors, setResolveErrors] = useState<Map<string, string>>(new Map());
  const [autoResolving, setAutoResolving] = useState(false);

  // Auto-fetch state: per-MPN status
  type FetchStatus = "fetching" | "failed";
  const [fetchStatus, setFetchStatus] = useState<Map<string, FetchStatus>>(new Map());
  const [fetchErrors, setFetchErrors] = useState<Map<string, string>>(new Map());
  const [fetchUrls, setFetchUrls] = useState<Map<string, string>>(new Map());
  const [autoFetching, setAutoFetching] = useState(false);

  // ---- LCSC bridging state ----
  // Populated either by an early BOM upload (new project path) or by rerun
  // prefill (existing project with cached LCSC data). `lcscToMpn` keys are
  // the original LCSC ids the user uploaded; values are real MPNs the BOM
  // was rewritten to. `mpnToLcsc` is the reverse — letting per-row UI find
  // the LCSC id given the resolved MPN. Treated as null when the BOM had
  // no LCSC ids at all (suppresses the LCSC passive-resolve step entirely).
  const [lcscToMpn, setLcscToMpn] = useState<Record<string, string> | null>(null);
  const [lcscPayloads, setLcscPayloads] = useState<Record<string, LcscPayload> | null>(null);
  // True once the BOM has been pushed to the server in early-upload mode.
  // Used by handleCreate to skip the redundant upload at submit time.
  const [bomUploadedEarly, setBomUploadedEarly] = useState(false);
  // Same pattern for EDIF netlists: we need the sub-design list before the
  // user reaches the picker step, so we eagerly upload on Details → Next.
  const [netlistUploadedEarly, setNetlistUploadedEarly] = useState(false);
  const [earlyUploading, setEarlyUploading] = useState(false);
  const [earlyUploadError, setEarlyUploadError] = useState<string | null>(null);

  // EDIF sub-design state, populated from the early netlist upload response.
  // `selectedSubdesignIds = null` means "include every sub-design" (default
  // for single-design EDIFs and pre-confirmation state). String ids include
  // "" as a sentinel for sub-design id `null` (bare-named cells).
  const [edifSubDesigns, setEdifSubDesigns] = useState<EdifSubDesign[]>([]);
  const [selectedSubdesignIds, setSelectedSubdesignIds] = useState<
    Set<string> | null
  >(null);
  // True when the selected netlist's first bytes start with `(edif`. Detected
  // in handleNetlistChange so step transitions don't have to re-read the file.
  const [netlistIsEdif, setNetlistIsEdif] = useState(false);

  // Per-LCSC-id resolve status for the lcsc-passives step.
  type LcscStatus = "pending" | "resolving" | "resolved" | "failed";
  const [lcscStatus, setLcscStatus] = useState<Map<string, LcscStatus>>(new Map());
  const [lcscErrors, setLcscErrors] = useState<Map<string, string>>(new Map());
  const [lcscSummaries, setLcscSummaries] = useState<Map<string, string>>(new Map());
  const [lcscResolving, setLcscResolving] = useState(false);
  const [lcscOutOfCredits, setLcscOutOfCredits] = useState(false);

  // ---- Derived ----

  const classification = useMemo(() => {
    if (!csvData || !refCol || !mpnCol) return null;
    return classifyBom(csvData.rows, refCol, mpnCol);
  }, [csvData, refCol, mpnCol]);

  // True when the user's chosen MPN column is entirely LCSC ids. Recomputed
  // whenever the CSV / column choice changes. Cleared once the early upload
  // has run because at that point csvData rows have been rewritten to real
  // MPNs (lcscToMpn is the post-upload source of truth).
  const lcscDetectedClient = useMemo(() => {
    if (!csvData || !mpnCol) return false;
    if (bomUploadedEarly) return false;
    return detectLcscColumn(csvData.rows, mpnCol);
  }, [csvData, mpnCol, bomUploadedEarly]);

  // Reverse map MPN → LCSC for per-row LCSC-id display in the IC datasheets
  // step. Built from lcscToMpn (post-upload state); empty otherwise.
  const mpnToLcsc = useMemo(() => {
    const m = new Map<string, string>();
    if (lcscToMpn) {
      for (const [lcsc, mpn] of Object.entries(lcscToMpn)) {
        if (mpn) m.set(mpn, lcsc);
      }
    }
    return m;
  }, [lcscToMpn]);

  // Check library whenever classification changes
  useEffect(() => {
    if (!classification) {
      setResolvedIcMpns(new Set());
      setResolvedPassiveMpns(new Set());
      setResolvedSimpleMpns(new Set());
      return;
    }
    const icMpns = classification.icMpns.map((e) => e.mpn);
    const passiveMpns = classification.passiveGroups.flatMap((g) =>
      g.mpns.map((e) => e.mpn),
    );
    const simpleMpns = classification.simpleGroups.flatMap((g) =>
      g.mpns.map((e) => e.mpn),
    );
    if (icMpns.length === 0 && passiveMpns.length === 0 && simpleMpns.length === 0) return;

    setCheckingLibrary(true);
    checkLibrary(icMpns, passiveMpns, simpleMpns).then((res) => {
      setResolvedIcMpns(new Set(res.ic_resolved));
      setResolvedPassiveMpns(new Set(res.passive_resolved));
      setResolvedSimpleMpns(new Set(res.simple_resolved));
      setLibraryDatasheets(new Set(res.datasheets_available ?? []));
    }).finally(() => {
      setCheckingLibrary(false);
    });
  }, [classification]);

  // Filter to only unresolved MPNs
  const unresolvedIcMpns = useMemo(
    () =>
      classification?.icMpns.filter((e) => !resolvedIcMpns.has(e.mpn)) ?? [],
    [classification, resolvedIcMpns],
  );

  const unresolvedPassiveGroups = useMemo(() => {
    if (!classification) return [];
    return classification.passiveGroups
      .map((g) => ({
        ...g,
        mpns: g.mpns.filter((e) => !resolvedPassiveMpns.has(e.mpn)),
      }))
      .filter((g) => g.mpns.length > 0);
  }, [classification, resolvedPassiveMpns]);

  const unresolvedSimpleGroups = useMemo(() => {
    if (!classification) return [];
    return classification.simpleGroups
      .map((g) => ({
        ...g,
        mpns: g.mpns.filter((e) => !resolvedSimpleMpns.has(e.mpn)),
      }))
      .filter((g) => g.mpns.length > 0);
  }, [classification, resolvedSimpleMpns]);

  const hasIcs = unresolvedIcMpns.length > 0;
  const hasPassives = unresolvedPassiveGroups.length > 0;
  const hasSimple = unresolvedSimpleGroups.length > 0;

  // List of passives that originated as LCSC ids — these are the candidates
  // for the lcsc-passives step. We resolve via the LCSC payload (cached on
  // the project) rather than via the MPN, because the payload already
  // carries the description we need.
  //
  // Filtering rules:
  //   - the passive MPN must appear in mpnToLcsc (came from an LCSC id)
  //   - skip if already resolved via library
  //   - skip if already resolved during this wizard session
  const lcscPassives = useMemo(() => {
    if (!classification || mpnToLcsc.size === 0) return [];
    const out: { mpn: string; lcsc: string; refs: string[]; description: string | null }[] = [];
    const seen = new Set<string>();
    for (const g of classification.passiveGroups) {
      for (const e of g.mpns) {
        const lcsc = mpnToLcsc.get(e.mpn);
        if (!lcsc || seen.has(lcsc)) continue;
        if (resolvedPassiveMpns.has(e.mpn)) continue;
        if (lcscStatus.get(lcsc) === "resolved") continue;
        seen.add(lcsc);
        const payload = lcscPayloads?.[lcsc];
        out.push({
          mpn: e.mpn,
          lcsc,
          refs: e.refs,
          description: payload?.description ?? null,
        });
      }
    }
    return out;
  }, [classification, mpnToLcsc, lcscPayloads, resolvedPassiveMpns, lcscStatus]);

  const hasLcscPassives = lcscPassives.length > 0;

  const hasSubdesignChoice = edifSubDesigns.length >= 2;

  // Per-sub-design BOM coverage (how many of its designators appear in the
  // BOM the user picked). Cheap recompute on csvData / refCol / sub-designs.
  const subdesignCoverage = useMemo(() => {
    if (!csvData || !refCol) return new Map<string, number>();
    const bomRefs = new Set<string>();
    for (const row of csvData.rows) {
      const cell = (row[refCol] || "").trim();
      for (const r of cell.split(",").map((s) => s.trim()).filter(Boolean)) {
        bomRefs.add(r);
      }
    }
    const out = new Map<string, number>();
    for (const sd of edifSubDesigns) {
      let n = 0;
      for (const d of sd.designators) if (bomRefs.has(d)) n++;
      out.set(_subKey(sd.id), n);
    }
    return out;
  }, [csvData, refCol, edifSubDesigns]);

  // Smart auto-pick decision. Returns the case kind plus the set of
  // sub-design keys to pre-select. "clean" = exactly one sub-design has
  // BOM coverage (auto-confirm banner UX); "ambiguous" = multiple covered
  // or none covered (explicit picker UX).
  const subdesignSuggestion = useMemo(() => {
    if (!hasSubdesignChoice) return null;
    const covered: string[] = [];
    for (const sd of edifSubDesigns) {
      if ((subdesignCoverage.get(_subKey(sd.id)) ?? 0) > 0) {
        covered.push(_subKey(sd.id));
      }
    }
    if (covered.length === 1) {
      return { kind: "clean" as const, preselect: new Set(covered) };
    }
    // Pre-select either all covered (split case) or all sub-designs (none
    // covered) so the user has a sane starting point in the picker.
    return {
      kind: "ambiguous" as const,
      preselect:
        covered.length > 1
          ? new Set(covered)
          : new Set(edifSubDesigns.map((sd) => _subKey(sd.id))),
    };
  }, [hasSubdesignChoice, edifSubDesigns, subdesignCoverage]);

  // Seed selectedSubdesignIds once the suggestion is known and the user
  // hasn't already touched it. Re-runs when the suggestion changes (e.g.,
  // column choice flips coverage), but only when selection is still null.
  useEffect(() => {
    if (selectedSubdesignIds === null && subdesignSuggestion) {
      setSelectedSubdesignIds(subdesignSuggestion.preselect);
    }
  }, [subdesignSuggestion, selectedSubdesignIds]);

  const activeSteps = useMemo(() => {
    return ALL_STEPS.filter((s) => {
      // Always include the step the user is currently on so the
      // "Step N of M" counter stays valid mid-transition (e.g. when
      // lcsc-passives drains to zero before the auto-advance effect
      // moves us off the step on the next tick).
      if (s.key === step) return true;
      if (s.key === "subdesigns") return hasSubdesignChoice;
      if (s.key === "lcsc-passives") return hasLcscPassives;
      if (s.key === "datasheets") return hasIcs;
      if (s.key === "simple") return hasSimple;
      if (s.key === "passives") return hasPassives;
      return true;
    });
  }, [step, hasSubdesignChoice, hasLcscPassives, hasIcs, hasSimple, hasPassives]);

  const stepIndex = activeSteps.findIndex((s) => s.key === step);

  // ---- Handlers ----

  const handleBomChange = useCallback((files: File[]) => {
    const file = files[0] || null;
    setBomFile(file);
    if (!file) {
      setCsvData(null);
      setRefCol(null);
      setMpnCol(null);
      return;
    }
    const isXlsx = file.name.toLowerCase().endsWith(".xlsx");
    if (isXlsx) {
      file.arrayBuffer().then((buf) => {
        const parsed = parseXlsx(buf);
        setCsvData(parsed);
        setRefCol(autoDetect(parsed.headers, REF_CANDIDATES));
        setMpnCol(autoDetect(parsed.headers, MPN_CANDIDATES));
      });
    } else {
      file.text().then((text) => {
        const parsed = parseCsv(text);
        setCsvData(parsed);
        setRefCol(autoDetect(parsed.headers, REF_CANDIDATES));
        setMpnCol(autoDetect(parsed.headers, MPN_CANDIDATES));
      });
    }
  }, []);

  const handleNetlistChange = useCallback((files: File[]) => {
    const file = files[0] || null;
    setNetlistFile(file);
    setNetlistNetCount(null);
    setNetlistError(null);
    // Invalidate any cached netlist preview, since it was relative to the
    // previous file.
    setNetlistPreview(null);
    setNetlistPreviewError(null);
    // Re-uploads invalidate any prior EDIF sub-design data + selection.
    setEdifSubDesigns([]);
    setSelectedSubdesignIds(null);
    setNetlistUploadedEarly(false);
    setNetlistIsEdif(false);
    if (!file) return;
    file.text().then((text) => {
      // EDIF: skip browser-side preview — the net-count badge will populate
      // after the server upload parses the file.
      if (isEdifNetlist(text)) {
        setNetlistNetCount(null);
        setNetlistPreview([]);
        setNetlistIsEdif(true);
        return;
      }
      setNetlistIsEdif(false);
      setNetlistNetCount(countNetsInNetlist(text));
      try {
        setNetlistPreview(parseNetlistPreview(text));
      } catch (e) {
        setNetlistPreviewError(
          e instanceof Error ? e.message : "Failed to parse netlist",
        );
      }
    });
  }, []);

  const resetAndClose = useCallback(() => {
    setOpen(false);
    setStep("details");
    setName("");
    setBomFile(null);
    setNetlistFile(null);
    setNetlistNetCount(null);
    setNetlistError(null);
    setCsvData(null);
    setRefCol(null);
    setMpnCol(null);
    setDatasheetFiles(new Map());
    setSimpleDatasheetFiles(new Map());
    setPassiveDatasheetFiles(new Map());
    setResolvedIcMpns(new Set());
    setResolvedPassiveMpns(new Set());
    setResolvedSimpleMpns(new Set());
    setFetchStatus(new Map());
    setFetchErrors(new Map());
    setFetchUrls(new Map());
    setAutoFetching(false);
    setResolveStatus(new Map());
    setResolveErrors(new Map());
    setAutoResolving(false);
    setNetlistPreview(null);
    setNetlistPreviewError(null);
    setCreating(false);
    setProgress("");
    setError(null);
    setExistingProjectId(null);
    setInitialBomFile(null);
    setInitialNetlistFile(null);
    setExistingDatasheetStems(new Set());
    setPrefillLoading(false);
    setPrefillError(null);
    setEstimate(null);
    setBalance(null);
    setEstimateLoading(false);
    setLcscToMpn(null);
    setLcscPayloads(null);
    setBomUploadedEarly(false);
    setNetlistUploadedEarly(false);
    setEdifSubDesigns([]);
    setSelectedSubdesignIds(null);
    setNetlistIsEdif(false);
    setEarlyUploading(false);
    setEarlyUploadError(null);
    setLcscStatus(new Map());
    setLcscErrors(new Map());
    setLcscSummaries(new Map());
    setLcscResolving(false);
    setLcscOutOfCredits(false);
    autoFetchedSteps.current = new Set();
    onRerunDone?.();
    onCloneAsNewDone?.();
  }, [onRerunDone, onCloneAsNewDone]);


  // Prefill state from a cancelled/complete project when entering rerun mode.
  useEffect(() => {
    if (!rerunProject) return;
    let cancelled = false;
    setOpen(true);
    setPrefillLoading(true);
    setPrefillError(null);
    setExistingProjectId(rerunProject.id);
    setName(rerunProject.name);
    setStep("details");

    (async () => {
      try {
        // Drafts are already editable; only non-drafts need a status reset
        // to clear pipeline artifacts before the rerun.
        if (rerunProject.status !== "draft") {
          await reopenProject(rerunProject.id);
        }

        // Pull LCSC bridging data off the existing project meta. The BOM
        // already-stored on the server has resolved MPNs; we just need the
        // map for per-row LCSC-id display and the payloads for the
        // lcsc-passives step.
        if (rerunProject.lcscToMpn && Object.keys(rerunProject.lcscToMpn).length > 0) {
          setLcscToMpn(rerunProject.lcscToMpn);
        }
        if (rerunProject.lcscPayloads && Object.keys(rerunProject.lcscPayloads).length > 0) {
          setLcscPayloads(rerunProject.lcscPayloads);
        }

        const [bom, netlist, dsStems] = await Promise.all([
          rerunProject.hasBom
            ? downloadProjectBom(rerunProject.id).catch(() => null)
            : Promise.resolve(null),
          rerunProject.hasNetlist
            ? downloadProjectNetlist(rerunProject.id).catch(() => null)
            : Promise.resolve(null),
          fetchProjectDatasheets(rerunProject.id).catch(() => new Set<string>()),
        ]);
        if (cancelled) return;

        // BOM: parse + restore column mappings.
        if (bom) {
          const isXlsx = bom.name.toLowerCase().endsWith(".xlsx");
          const parsed = isXlsx
            ? parseXlsx(await bom.arrayBuffer())
            : parseCsv(await bom.text());
          if (cancelled) return;
          setBomFile(bom);
          setInitialBomFile(bom);
          setCsvData(parsed);
          const saved = rerunProject.bomColumns;
          setRefCol(
            (saved?.reference && parsed.headers.includes(saved.reference)
              ? saved.reference
              : autoDetect(parsed.headers, REF_CANDIDATES)) ?? null,
          );
          setMpnCol(
            (saved?.mpn && parsed.headers.includes(saved.mpn)
              ? saved.mpn
              : autoDetect(parsed.headers, MPN_CANDIDATES)) ?? null,
          );
        }

        // Netlist: parse preview + count nets.
        if (netlist) {
          const netlistText = await netlist.text();
          if (cancelled) return;
          setNetlistFile(netlist);
          setInitialNetlistFile(netlist);
          if (isEdifNetlist(netlistText)) {
            setNetlistIsEdif(true);
            // EDIF: PADS-shape browser preview doesn't apply.
            setNetlistNetCount(null);
            setNetlistPreview([]);
          } else {
            setNetlistIsEdif(false);
            setNetlistNetCount(countNetsInNetlist(netlistText));
            try {
              setNetlistPreview(parseNetlistPreview(netlistText));
            } catch (e) {
              setNetlistPreviewError(
                e instanceof Error ? e.message : "Failed to parse netlist",
              );
            }
          }
        }

        // Existing datasheets (server-stored; kept as stems).
        setExistingDatasheetStems(dsStems);
      } catch (e) {
        if (!cancelled) {
          setPrefillError(
            e instanceof Error ? e.message : "Failed to load project data",
          );
        }
      } finally {
        if (!cancelled) setPrefillLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [rerunProject]);

  // Prefill state for "rerun as new project" — admin clone flow.
  // Mirrors the rerun effect but does NOT set existingProjectId, so submit
  // creates a brand new project. Skips datasheet stems (clone re-extracts them).
  useEffect(() => {
    if (!cloneAsNewProject) return;
    let cancelled = false;
    setOpen(true);
    setPrefillLoading(true);
    setPrefillError(null);
    setName(`[Auto-update] ${cloneAsNewProject.name}`);
    setStep("details");

    (async () => {
      try {
        const [bom, netlist] = await Promise.all([
          cloneAsNewProject.hasBom
            ? downloadProjectBom(cloneAsNewProject.id).catch(() => null)
            : Promise.resolve(null),
          cloneAsNewProject.hasNetlist
            ? downloadProjectNetlist(cloneAsNewProject.id).catch(() => null)
            : Promise.resolve(null),
        ]);
        if (cancelled) return;

        if (bom) {
          const isXlsx = bom.name.toLowerCase().endsWith(".xlsx");
          const parsed = isXlsx
            ? parseXlsx(await bom.arrayBuffer())
            : parseCsv(await bom.text());
          if (cancelled) return;
          setBomFile(bom);
          setCsvData(parsed);
          const saved = cloneAsNewProject.bomColumns;
          setRefCol(
            (saved?.reference && parsed.headers.includes(saved.reference)
              ? saved.reference
              : autoDetect(parsed.headers, REF_CANDIDATES)) ?? null,
          );
          setMpnCol(
            (saved?.mpn && parsed.headers.includes(saved.mpn)
              ? saved.mpn
              : autoDetect(parsed.headers, MPN_CANDIDATES)) ?? null,
          );
        }

        if (netlist) {
          const netlistText = await netlist.text();
          if (cancelled) return;
          setNetlistFile(netlist);
          if (isEdifNetlist(netlistText)) {
            setNetlistIsEdif(true);
            setNetlistNetCount(null);
            setNetlistPreview([]);
          } else {
            setNetlistIsEdif(false);
            setNetlistNetCount(countNetsInNetlist(netlistText));
            try {
              setNetlistPreview(parseNetlistPreview(netlistText));
            } catch (e) {
              setNetlistPreviewError(
                e instanceof Error ? e.message : "Failed to parse netlist",
              );
            }
          }
        }
      } catch (e) {
        if (!cancelled) {
          setPrefillError(
            e instanceof Error ? e.message : "Failed to load project data",
          );
        }
      } finally {
        if (!cancelled) setPrefillLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [cloneAsNewProject]);

  // Fetch a pre-flight credit estimate when entering the final step of
  // an edit/rerun. Requires a BOM already on the server — drafts where
  // the user just uploaded a new BOM via the modal haven't been pushed
  // to the backend yet, so skip until there's something to estimate.
  useEffect(() => {
    if (!authEnabled) return; // OSS mode: no credits — no estimate UI
    if (!existingProjectId) return;
    if (step !== activeSteps[activeSteps.length - 1]?.key) return;
    if (!initialBomFile) return;
    let cancelled = false;
    setEstimateLoading(true);
    Promise.all([
      fetchPipelineEstimate(existingProjectId),
      fetchCredits(),
    ])
      .then(([est, cr]) => {
        if (cancelled) return;
        setEstimate(est);
        setBalance(cr.balance);
      })
      .catch(() => {
        /* Estimate is best-effort; swallow so the Run button still works. */
      })
      .finally(() => {
        if (!cancelled) setEstimateLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [existingProjectId, step]);

  const handleAutoFetch = useCallback(
    async (
      mpns: string[],
      setter: React.Dispatch<React.SetStateAction<Map<string, File>>>,
      existing: Map<string, File>,
    ) => {
      // Filter to MPNs that don't already have a file, library entry,
      // or server-side upload from an earlier session.
      const toFetch = mpns.filter(
        (m) =>
          !existing.has(m) &&
          !libraryDatasheets.has(m) &&
          !existingDatasheetStems.has(safeMpn(m)),
      );
      if (toFetch.length === 0) return;

      setAutoFetching(true);
      // Spinner on every row immediately, before any await.
      setFetchStatus((prev) => {
        const next = new Map(prev);
        for (const mpn of toFetch) next.set(mpn, "fetching");
        return next;
      });

      const fetchOne = async (mpn: string) => {
        try {
          const { file, url } = await fetchDigikeyDatasheet(mpn);
          setter((prev) => new Map(prev).set(mpn, file));
          if (url) setFetchUrls((prev) => new Map(prev).set(mpn, url));
          setFetchStatus((prev) => {
            const next = new Map(prev);
            next.delete(mpn);
            return next;
          });
          setFetchErrors((prev) => {
            const next = new Map(prev);
            next.delete(mpn);
            return next;
          });
        } catch (e) {
          const msg = e instanceof Error ? e.message : "Fetch failed";
          setFetchStatus((prev) => new Map(prev).set(mpn, "failed"));
          setFetchErrors((prev) => new Map(prev).set(mpn, msg));
          if (e instanceof DigiKeyFetchError && e.url) {
            setFetchUrls((prev) => new Map(prev).set(mpn, e.url!));
          }
        }
      };

      // Worker pool: up to AUTO_FETCH_CONCURRENCY in flight, cursor pulls next.
      let cursor = 0;
      const workers = Array.from(
        { length: Math.min(AUTO_FETCH_CONCURRENCY, toFetch.length) },
        async () => {
          while (cursor < toFetch.length) {
            const i = cursor++;
            await fetchOne(toFetch[i]);
          }
        },
      );
      await Promise.all(workers);

      setAutoFetching(false);
    },
    [libraryDatasheets, existingDatasheetStems],
  );

  // Track which datasheet steps already auto-fetched in this dialog session
  // so navigating back-and-forth doesn't refire. Reset in resetAndClose.
  const autoFetchedSteps = useRef<Set<WizardStep>>(new Set());

  useEffect(() => {
    if (checkingLibrary || autoFetching) return;
    if (
      step === "datasheets" &&
      !autoFetchedSteps.current.has("datasheets") &&
      unresolvedIcMpns.length > 0
    ) {
      autoFetchedSteps.current.add("datasheets");
      handleAutoFetch(
        unresolvedIcMpns.map((e) => e.mpn),
        setDatasheetFiles,
        datasheetFiles,
      );
    } else if (
      step === "simple" &&
      !autoFetchedSteps.current.has("simple") &&
      unresolvedSimpleGroups.length > 0
    ) {
      autoFetchedSteps.current.add("simple");
      // Skip connectors — their datasheets are truly optional and rarely
      // contribute anything the reviewer can act on, so we don't burn the
      // DigiKey budget on them.
      const toFetch = unresolvedSimpleGroups
        .flatMap((g) => g.mpns)
        .filter((e) => e.refPrefix !== "J")
        .map((e) => e.mpn);
      if (toFetch.length > 0) {
        handleAutoFetch(toFetch, setSimpleDatasheetFiles, simpleDatasheetFiles);
      }
    }
  }, [
    step,
    checkingLibrary,
    autoFetching,
    unresolvedIcMpns,
    unresolvedSimpleGroups,
    handleAutoFetch,
    datasheetFiles,
    simpleDatasheetFiles,
  ]);

  const handleAutoResolve = useCallback(
    async (groups: SimpleGroup[]) => {
      // Collect MPNs not already in library and not already resolved
      const toResolve = groups.flatMap((g) =>
        g.mpns.filter(
          (e) =>
            !resolvedSimpleMpns.has(e.mpn) &&
            resolveStatus.get(e.mpn) !== "resolved",
        ),
      );
      if (toResolve.length === 0) return;

      setAutoResolving(true);

      // Mark all as resolving
      setResolveStatus((prev) => {
        const next = new Map(prev);
        for (const { mpn } of toResolve) next.set(mpn, "resolving");
        return next;
      });

      // Build items with component type from ref prefix
      const items = toResolve.map((e) => ({
        mpn: e.mpn,
        component_type: SIMPLE_PREFIX_TO_TYPE[e.refPrefix] || "discrete",
      }));

      try {
        const { results } = await autoResolveSimple(items);

        const newResolved = new Set<string>();
        setResolveStatus((prev) => {
          const next = new Map(prev);
          for (const r of results) {
            next.set(r.mpn, r.status);
            if (r.status === "resolved") newResolved.add(r.mpn);
          }
          return next;
        });
        setResolveErrors((prev) => {
          const next = new Map(prev);
          for (const r of results) {
            if (r.error) next.set(r.mpn, r.error);
            else next.delete(r.mpn);
          }
          return next;
        });
        // Add resolved MPNs to library set so they persist across steps
        if (newResolved.size > 0) {
          setResolvedSimpleMpns((prev) => new Set([...prev, ...newResolved]));
        }
      } catch {
        // Batch-level failure: mark all as failed
        setResolveStatus((prev) => {
          const next = new Map(prev);
          for (const { mpn } of toResolve) next.set(mpn, "failed");
          return next;
        });
      }

      setAutoResolving(false);
    },
    [resolvedSimpleMpns, resolveStatus],
  );

  const handleAutoResolvePassives = useCallback(
    async (groups: PassiveGroup[]) => {
      const toResolve = groups.flatMap((g) =>
        g.mpns.filter(
          (e) =>
            !resolvedPassiveMpns.has(e.mpn) &&
            resolveStatus.get(e.mpn) !== "resolved",
        ),
      );
      if (toResolve.length === 0) return;

      setAutoResolving(true);

      setResolveStatus((prev) => {
        const next = new Map(prev);
        for (const { mpn } of toResolve) next.set(mpn, "resolving");
        return next;
      });

      const items = toResolve.map((e) => ({
        mpn: e.mpn,
        component_type: "passive",
      }));

      try {
        const { results } = await autoResolveSimple(items);

        const newResolved = new Set<string>();
        setResolveStatus((prev) => {
          const next = new Map(prev);
          for (const r of results) {
            next.set(r.mpn, r.status);
            if (r.status === "resolved") newResolved.add(r.mpn);
          }
          return next;
        });
        setResolveErrors((prev) => {
          const next = new Map(prev);
          for (const r of results) {
            if (r.error) next.set(r.mpn, r.error);
            else next.delete(r.mpn);
          }
          return next;
        });
        if (newResolved.size > 0) {
          setResolvedPassiveMpns((prev) => new Set([...prev, ...newResolved]));
        }
      } catch {
        setResolveStatus((prev) => {
          const next = new Map(prev);
          for (const { mpn } of toResolve) next.set(mpn, "failed");
          return next;
        });
      }

      setAutoResolving(false);
    },
    [resolvedPassiveMpns, resolveStatus],
  );

  // ---- Early BOM upload (LCSC-triggered) ----
  //
  // When the user has chosen the columns and the MPN column is detected as
  // entirely LCSC ids, we cannot just continue with client-side state —
  // every downstream step (IC datasheet fetch, passive specs) needs real
  // MPNs. We do an early createProject + uploadBom so the backend can run
  // its purple-parts rewrite. After this we:
  //   - update local csvData rows so the chosen MPN column holds resolved MPNs
  //   - stash the LCSC → MPN map and full payloads for downstream rendering
  //   - mark bomUploadedEarly so handleCreate doesn't re-upload at submit
  //
  // Returns true on success along with the resolved map and the rewritten
  // csv rows, so the caller can route to the right next step without
  // waiting for React state to re-derive (state updates queued here only
  // take effect on the next render).
  //
  // Failures leave bomUploadedEarly = false so handleCreate retries the
  // upload in its normal slot. The orphan draft project is cleaned up so
  // failed early-upload attempts don't pile up.
  const runEarlyUpload = useCallback(async (): Promise<{
    ok: boolean;
    lcscMap: Record<string, string>;
    rewrittenRows: Record<string, string>[];
  }> => {
    if (!csvData || !bomFile || !refCol || !mpnCol) {
      return { ok: false, lcscMap: {}, rewrittenRows: [] };
    }
    setEarlyUploading(true);
    setEarlyUploadError(null);
    let createdProjectId: string | null = null;
    try {
      const proj = await createProject(name.trim() || "Untitled");
      createdProjectId = proj.id;
      const uploadRes = await uploadBom(proj.id, bomFile, refCol, mpnCol);
      // Pull the full project meta back so we get lcsc_payloads (the upload
      // response only carries lcsc_to_mpn).
      const fullProj = await fetchProject(proj.id);

      setExistingProjectId(proj.id);
      setBomUploadedEarly(true);
      setInitialBomFile(bomFile);
      // Treat the netlist as fresh so it still uploads in handleCreate.
      setInitialNetlistFile(null);
      const map = uploadRes.lcsc_to_mpn ?? {};
      if (Object.keys(map).length > 0) setLcscToMpn(map);
      if (fullProj.lcscPayloads && Object.keys(fullProj.lcscPayloads).length > 0) {
        setLcscPayloads(fullProj.lcscPayloads);
      }

      // Rewrite local csvData mpn column so client-side classification
      // sees real MPNs from here on. This keeps the existing IC / passive /
      // simple step plumbing unchanged.
      const rewrittenRows = csvData.rows.map((r) => {
        const lcsc = (r[mpnCol] || "").trim();
        const mpn = map[lcsc];
        if (mpn) return { ...r, [mpnCol]: mpn };
        return r;
      });
      setCsvData({ headers: csvData.headers, rows: rewrittenRows });

      return { ok: true, lcscMap: map, rewrittenRows };
    } catch (e) {
      // Clean up the orphan draft on failure to avoid littering the user's
      // dashboard with empty retry projects.
      if (createdProjectId) {
        try {
          await deleteProject(createdProjectId);
        } catch {
          // ignore — surface the original error
        }
      }
      setEarlyUploadError(
        e instanceof Error ? e.message : "Failed to resolve LCSC parts",
      );
      return { ok: false, lcscMap: {}, rewrittenRows: [] };
    } finally {
      setEarlyUploading(false);
    }
  }, [csvData, bomFile, refCol, mpnCol, name]);

  // EDIF netlists carry sub-design info we need before the picker step
  // renders. Triggered on Details → Next when the netlist is EDIF and the
  // file hasn't already been uploaded (rerun mode skips this — the netlist
  // is already on the server). Reuses `existingProjectId` when set (e.g.,
  // LCSC early upload ran first), otherwise creates the project.
  const runEarlyNetlistUpload = useCallback(async (): Promise<{
    ok: boolean;
    subDesigns: EdifSubDesign[];
  }> => {
    if (!netlistFile) return { ok: false, subDesigns: [] };
    setEarlyUploading(true);
    setEarlyUploadError(null);
    let createdProjectIdHere: string | null = null;
    try {
      let projectId = existingProjectId;
      if (!projectId) {
        const proj = await createProject(name.trim() || "Untitled");
        projectId = proj.id;
        createdProjectIdHere = proj.id;
        setExistingProjectId(proj.id);
      }
      const result = await uploadNetlist(projectId, netlistFile);
      setEdifSubDesigns(result.sub_designs);
      if (result.designator_pins.length > 0) {
        setNetlistPreview(result.designator_pins);
      }
      setNetlistUploadedEarly(true);
      setInitialNetlistFile(netlistFile);
      return { ok: true, subDesigns: result.sub_designs };
    } catch (e) {
      // Only delete the project if we created it just now — leave LCSC's
      // earlier draft alone.
      if (createdProjectIdHere) {
        try {
          await deleteProject(createdProjectIdHere);
          setExistingProjectId(null);
        } catch {
          // swallow — surface original error
        }
      }
      setEarlyUploadError(
        e instanceof Error ? e.message : "Failed to upload netlist",
      );
      return { ok: false, subDesigns: [] };
    } finally {
      setEarlyUploading(false);
    }
  }, [netlistFile, existingProjectId, name]);

  // ---- LCSC per-row passive resolve ----
  //
  // Walks the lcscPassives list in batches of LCSC_RESOLVE_CONCURRENCY,
  // calling POST /api/projects/{id}/lcsc/resolve-passive for each. On
  // 402 we stop the queue immediately so the user can top up; partial
  // progress (already-resolved rows) is preserved. The lcsc-passives step
  // is skippable so a failure of a single MPN doesn't block the wizard.
  const handleResolveLcscPassives = useCallback(async () => {
    if (!existingProjectId) return;
    const toResolve = lcscPassives.filter(
      (p) => lcscStatus.get(p.lcsc) !== "resolved" && lcscStatus.get(p.lcsc) !== "resolving",
    );
    if (toResolve.length === 0) return;

    setLcscResolving(true);
    setLcscOutOfCredits(false);

    // Mark all as pending so the spinner appears immediately.
    setLcscStatus((prev) => {
      const next = new Map(prev);
      for (const p of toResolve) next.set(p.lcsc, "resolving");
      return next;
    });

    let cursor = 0;
    let stopped = false;

    const resolveOne = async (entry: typeof toResolve[number]) => {
      if (stopped) return;
      try {
        const res = await resolveLcscPassive(existingProjectId, entry.lcsc);
        const summary = summarizeLcscModel(res.model);
        setLcscStatus((prev) => new Map(prev).set(entry.lcsc, "resolved"));
        setLcscSummaries((prev) => new Map(prev).set(entry.lcsc, summary));
        setLcscErrors((prev) => {
          const next = new Map(prev);
          next.delete(entry.lcsc);
          return next;
        });
        // Mirror the existing pattern from handleAutoResolve: surface the
        // MPN as fully resolved so the standard passives step skips it.
        setResolvedPassiveMpns((prev) => new Set(prev).add(res.mpn));
      } catch (e) {
        if (e instanceof LcscResolveError && e.status === 402) {
          stopped = true;
          setLcscOutOfCredits(true);
          // Reset still-running rows back to pending so the user can retry
          // after topping up.
          setLcscStatus((prev) => {
            const next = new Map(prev);
            for (const [k, v] of next.entries()) {
              if (v === "resolving") next.set(k, "pending");
            }
            return next;
          });
          return;
        }
        setLcscStatus((prev) => new Map(prev).set(entry.lcsc, "failed"));
        setLcscErrors((prev) =>
          new Map(prev).set(
            entry.lcsc,
            e instanceof Error ? e.message : "Resolve failed",
          ),
        );
      }
    };

    const workers = Array.from(
      { length: Math.min(LCSC_RESOLVE_CONCURRENCY, toResolve.length) },
      async () => {
        while (cursor < toResolve.length && !stopped) {
          const i = cursor++;
          await resolveOne(toResolve[i]);
        }
      },
    );
    await Promise.all(workers);

    setLcscResolving(false);
  }, [existingProjectId, lcscPassives, lcscStatus]);

  // Auto-kick the LCSC resolve when the user lands on the step. Same
  // once-per-session pattern as the IC datasheet auto-fetch above.
  useEffect(() => {
    if (step !== "lcsc-passives") return;
    if (autoFetchedSteps.current.has("lcsc-passives")) return;
    if (lcscPassives.length === 0) return;
    if (!existingProjectId) return;
    autoFetchedSteps.current.add("lcsc-passives");
    handleResolveLcscPassives();
    // We intentionally only auto-trigger once per dialog session — back/next
    // navigation should not re-fire (and re-charge) resolved rows.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, lcscPassives.length, existingProjectId]);

  // Auto-advance off the lcsc-passives step when there's nothing left to
  // resolve. Covers two cases:
  //   1) auto-fire completed and every row landed in lcscStatus="resolved",
  //      so lcscPassives filters down to empty;
  //   2) the user navigated here but the project had no LCSC-resolved
  //      passives in the first place (rare, but the step counter goes
  //      wrong without this guard).
  useEffect(() => {
    if (step !== "lcsc-passives") return;
    if (lcscResolving) return;
    if (lcscPassives.length > 0) return;
    // Pick the next active step in display order.
    if (hasIcs) setStep("datasheets");
    else if (hasSimple) setStep("simple");
    else if (hasPassives) setStep("passives");
  }, [step, lcscResolving, lcscPassives.length, hasIcs, hasSimple, hasPassives]);

  const canAdvance = (): boolean => {
    if (step === "details") return !!(name.trim() && bomFile && netlistFile && !netlistError);
    if (step === "columns") return !!(refCol && mpnCol);
    if (step === "subdesigns")
      return !!(selectedSubdesignIds && selectedSubdesignIds.size > 0);
    return true;
  };

  // Shared "what's after columns" routing — used by goNext on the columns
  // step and by goBack from the lcsc-passives step. When an EDIF file has
  // >=2 sub-designs we slot the "subdesigns" step in first.
  const stepAfterColumns = (): WizardStep | null => {
    if (hasSubdesignChoice) return "subdesigns";
    if (hasLcscPassives) return "lcsc-passives";
    if (hasIcs) return "datasheets";
    if (hasSimple) return "simple";
    if (hasPassives) return "passives";
    return null;
  };

  const stepAfterSubdesigns = (): WizardStep | null => {
    if (hasLcscPassives) return "lcsc-passives";
    if (hasIcs) return "datasheets";
    if (hasSimple) return "simple";
    if (hasPassives) return "passives";
    return null;
  };

  const goNext = async () => {
    if (step === "details") {
      // EDIF: eagerly upload so we can show the sub-design picker before
      // the user reaches Run. Skip in rerun mode (netlist is already on
      // the server) and when already done.
      if (
        !rerunProject &&
        netlistIsEdif &&
        !netlistUploadedEarly &&
        netlistFile
      ) {
        const res = await runEarlyNetlistUpload();
        if (!res.ok) return;
      }
      setStep("columns");
    }
    else if (step === "columns") {
      // LCSC-detected BOMs need an early upload so we have the LCSC → MPN
      // map before the IC and passive steps. If the early upload fails we
      // stay on this step and surface the error rather than continuing
      // with stale LCSC ids. Rerun projects already have the BOM rewritten
      // server-side (so csvData rows have real MPNs and lcscDetectedClient
      // is false), but we guard explicitly for clarity.
      if (!rerunProject && !bomUploadedEarly && lcscDetectedClient) {
        const res = await runEarlyUpload();
        if (!res.ok) return;
        // Determine the next step from the freshly returned data because
        // React state updates queued by runEarlyUpload (lcscToMpn, csvData)
        // haven't propagated to derived memos yet. We need to re-derive
        // hasLcscPassives off `res.lcscMap` and `res.rewrittenRows`.
        if (!refCol || !mpnCol) return;
        const cls = classifyBom(res.rewrittenRows, refCol, mpnCol);
        const lcscMpns = new Set(Object.values(res.lcscMap));
        const passiveHit = cls.passiveGroups.some((g) =>
          g.mpns.some((e) => lcscMpns.has(e.mpn) && !resolvedPassiveMpns.has(e.mpn)),
        );
        if (passiveHit) setStep("lcsc-passives");
        else if (cls.icMpns.length > 0) setStep("datasheets");
        else if (cls.simpleGroups.length > 0) setStep("simple");
        else if (cls.passiveGroups.length > 0) setStep("passives");
        return;
      }
      const next = stepAfterColumns();
      if (next) setStep(next);
    } else if (step === "subdesigns") {
      // Persist the selection before advancing. ``null`` means "include
      // every sub-design"; pass it explicitly so re-confirmations clear
      // any prior partial selection.
      if (existingProjectId) {
        const sel = selectedSubdesignIds;
        const list = sel === null
          ? null
          : Array.from(sel).map((k) => (k === NULL_SUBDESIGN_KEY ? "" : k));
        try {
          await updateNetlistSubdesigns(existingProjectId, list);
        } catch (e) {
          setError(e instanceof Error ? e.message : "Failed to save sub-design selection");
          return;
        }
      }
      const next = stepAfterSubdesigns();
      if (next) setStep(next);
    } else if (step === "lcsc-passives") {
      if (hasIcs) setStep("datasheets");
      else if (hasSimple) setStep("simple");
      else if (hasPassives) setStep("passives");
    } else if (step === "datasheets") {
      // Warn if ICs are missing datasheets. In rerun/edit mode a datasheet
      // may already be stored in this project's uploads folder — match it
      // via existingDatasheetStems so we don't falsely flag them.
      const missingIcs = unresolvedIcMpns.filter(
        (e) =>
          !datasheetFiles.has(e.mpn) &&
          !libraryDatasheets.has(e.mpn) &&
          !existingDatasheetStems.has(safeMpn(e.mpn)),
      );
      if (missingIcs.length > 0) {
        const ok = window.confirm(
          `${missingIcs.length} IC${missingIcs.length !== 1 ? "s" : ""} without datasheets will not be analysed:\n\n${missingIcs.map((e) => `  ${e.mpn} (${e.refs.join(", ")})`).join("\n")}\n\nContinue anyway?`,
        );
        if (!ok) return;
      }
      if (hasSimple) setStep("simple");
      else if (hasPassives) setStep("passives");
    } else if (step === "simple") {
      if (hasPassives) setStep("passives");
    }
  };

  const goBack = () => {
    setError(null);
    if (step === "columns") setStep("details");
    else if (step === "subdesigns") setStep("columns");
    else if (step === "lcsc-passives") {
      if (hasSubdesignChoice) setStep("subdesigns");
      else setStep("columns");
    } else if (step === "datasheets") {
      if (hasLcscPassives) setStep("lcsc-passives");
      else if (hasSubdesignChoice) setStep("subdesigns");
      else setStep("columns");
    } else if (step === "simple") {
      if (hasIcs) setStep("datasheets");
      else if (hasLcscPassives) setStep("lcsc-passives");
      else if (hasSubdesignChoice) setStep("subdesigns");
      else setStep("columns");
    } else if (step === "passives") {
      if (hasSimple) setStep("simple");
      else if (hasIcs) setStep("datasheets");
      else if (hasLcscPassives) setStep("lcsc-passives");
      else if (hasSubdesignChoice) setStep("subdesigns");
      else setStep("columns");
    }
  };

  const handleCreate = async () => {
    setCreating(true);
    setError(null);
    const isRerunMode = rerunProject !== null;
    // "Early upload" projects are net-new drafts already on the server.
    // Two paths create one: the LCSC BOM resolver (Columns → Next) and the
    // EDIF sub-design picker (Details → Next). Don't reopen them, don't
    // re-create them, and skip whichever uploads already happened — the
    // per-file equality checks below decide what still needs uploading.
    const isEarlyUploaded =
      existingProjectId !== null && (bomUploadedEarly || netlistUploadedEarly);
    const projectAlreadyExists = isRerunMode || isEarlyUploaded;
    try {
      let project: Project;
      if (isRerunMode) {
        setProgress("Preparing rerun...");
        project = await reopenProject(existingProjectId!);
        const trimmed = name.trim();
        if (trimmed && trimmed !== project.name) {
          project = await renameProject(project.id, trimmed);
        }
      } else if (isEarlyUploaded) {
        setProgress("Continuing setup...");
        project = await fetchProject(existingProjectId!);
        const trimmed = name.trim();
        if (trimmed && trimmed !== project.name) {
          project = await renameProject(project.id, trimmed);
        }
      } else {
        setProgress("Creating project...");
        project = await createProject(name.trim());
      }

      // For new projects, delete the draft on BOM/netlist upload failure so
      // retries don't pile up orphan drafts. For reruns and early-upload
      // projects, the project already owns prior artifacts and should be
      // preserved on failure.
      try {
        if (!projectAlreadyExists || bomFile !== initialBomFile) {
          setProgress("Uploading BOM...");
          await uploadBom(
            project.id,
            bomFile!,
            refCol || undefined,
            mpnCol || undefined,
          );
        }

        if (!projectAlreadyExists || netlistFile !== initialNetlistFile) {
          setProgress("Uploading netlist...");
          await uploadNetlist(project.id, netlistFile!);
        }
      } catch (uploadErr) {
        if (!projectAlreadyExists) {
          try {
            await deleteProject(project.id);
          } catch {
            // ignore cleanup failure — surface the original upload error
          }
        }
        throw uploadErr;
      }

      const entries = Array.from(datasheetFiles.entries());
      for (let i = 0; i < entries.length; i++) {
        const [mpn, file] = entries[i];
        setProgress(
          `Uploading datasheet ${i + 1}/${entries.length}: ${mpn}`,
        );
        await uploadDatasheet(project.id, mpn, file);
      }

      // Upload simple component datasheets (per-MPN)
      const simpleEntries = Array.from(simpleDatasheetFiles.entries());
      for (let i = 0; i < simpleEntries.length; i++) {
        const [mpn, file] = simpleEntries[i];
        setProgress(
          `Uploading component datasheet ${i + 1}/${simpleEntries.length}: ${mpn}`,
        );
        await uploadDatasheet(project.id, mpn, file);
      }

      // Upload passive datasheets (grouped by shared File to avoid duplicate uploads)
      const fileToMpns = new Map<File, string[]>();
      for (const [mpn, file] of passiveDatasheetFiles.entries()) {
        const list = fileToMpns.get(file) || [];
        list.push(mpn);
        fileToMpns.set(file, list);
      }
      const passiveUploads = Array.from(fileToMpns.entries());
      for (let i = 0; i < passiveUploads.length; i++) {
        const [file, mpns] = passiveUploads[i];
        const [primary, ...rest] = mpns;
        setProgress(
          `Uploading passive datasheet ${i + 1}/${passiveUploads.length}: ${file.name} (${mpns.length} part${mpns.length !== 1 ? "s" : ""})`,
        );
        await uploadDatasheet(project.id, primary, file, rest.length > 0 ? rest : undefined);
      }

      setProgress("Starting pipeline...");
      await startPipeline(project.id);

      onCreateProject(project);
      resetAndClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create project");
      setCreating(false);
    }
  };

  // ---- Render ----

  const isLastStep = step === activeSteps[activeSteps.length - 1]?.key;
  // True for the rerun/clone flows where the user is acting on an existing
  // project. Note: this is NOT the same as `existingProjectId !== null` —
  // the LCSC early-upload path also sets existingProjectId on a brand-new
  // draft, but the UI should still read as a fresh "Create Project" flow
  // (CTA label, step titles, etc.).
  const isRerun = rerunProject !== null;
  const isDraftEdit = isRerun && rerunProject?.status === "draft";
  const canRunRerun =
    !estimate || balance === null ? true : balance >= estimate.credits_low;

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (creating || disabled) return;
        if (v) setOpen(true);
        else resetAndClose();
      }}
    >
      {disabled ? (
        <Tooltip>
          <TooltipTrigger
            render={
              <Button size="sm" disabled className="pointer-events-auto" />
            }
          >
            <Plus className="h-4 w-4 mr-1" />
            New Project
          </TooltipTrigger>
          <TooltipContent>Project limit reached</TooltipContent>
        </Tooltip>
      ) : (
        <DialogTrigger render={<Button size="sm" />}>
          <Plus className="h-4 w-4 mr-1" />
          New Project
        </DialogTrigger>
      )}
      <DialogContent
        className="sm:max-w-2xl"
        showCloseButton={!creating}
      >
        <DialogHeader>
          <DialogTitle>
            {isRerun && !isDraftEdit
              ? `Rerun — ${activeSteps[stepIndex]?.label}`
              : activeSteps[stepIndex]?.label}
          </DialogTitle>
          <DialogDescription>
            {isDraftEdit
              ? `Continuing draft. Step ${stepIndex + 1} of ${activeSteps.length}`
              : isRerun
                ? `Re-running existing project. Step ${stepIndex + 1} of ${activeSteps.length}`
                : `Step ${stepIndex + 1} of ${activeSteps.length}`}
          </DialogDescription>
        </DialogHeader>

        {/* Progress bar */}
        <div className="flex gap-1">
          {activeSteps.map((s, i) => (
            <div
              key={s.key}
              className={cn(
                "h-1 flex-1 rounded-full transition-colors",
                i <= stepIndex ? "bg-primary" : "bg-muted",
              )}
            />
          ))}
        </div>

        {prefillLoading && (
          <div className="flex items-center gap-2 rounded-lg border bg-muted/20 p-3 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Loading saved project data…
          </div>
        )}
        {prefillError && (
          <div className="flex items-center gap-2 rounded-lg border border-rose-500/40 bg-rose-500/5 p-3 text-sm text-rose-600 dark:text-rose-400">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {prefillError}
          </div>
        )}

        {/*
          Surfaces the LCSC → MPN rewrite happening during the columns
          step. Shown while the early upload is in flight, plus an error
          row if it fails so the user can retry from the columns step.
        */}
        {earlyUploading && (
          <div className="flex items-center gap-2 rounded-lg border bg-muted/20 p-3 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Resolving LCSC codes to manufacturer part numbers…
          </div>
        )}
        {earlyUploadError && (
          <div className="flex items-center gap-2 rounded-lg border border-rose-500/40 bg-rose-500/5 p-3 text-sm text-rose-600 dark:text-rose-400">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {earlyUploadError}
          </div>
        )}

        {/* Step content */}
        <div className="min-h-[280px] max-h-[70vh] overflow-y-auto">
          {/* ---- Step 1: Details ---- */}
          {step === "details" && (
            <div className="space-y-4">
              <div>
                <label className="text-sm font-medium mb-1.5 block">
                  Project Name
                </label>
                <Input
                  placeholder="e.g. Power Supply Rev B"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && canAdvance() && goNext()}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <FileUploadZone
                    label="BOM (.csv, .xlsx)"
                    accept=".csv,.xlsx"
                    files={bomFile ? [bomFile] : []}
                    onFilesChange={handleBomChange}
                  />
                  <p className="text-[11px] text-muted-foreground leading-tight px-1">
                    Requires columns: Designator, Manufacturer Part Number, and
                    Comment (for passive values / mismatch detection).{" "}
                    <a
                      href="/file-guide#the-bom"
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-600 dark:text-blue-500 hover:underline"
                    >
                      How to export →
                    </a>
                  </p>
                </div>
                <div className="space-y-1.5">
                  <FileUploadZone
                    label="Netlist (.asc / .net / .txt / .edn)"
                    accept=".asc,.net,.NET,.txt,.edn,.edif,.edf"
                    files={netlistFile ? [netlistFile] : []}
                    onFilesChange={handleNetlistChange}
                  />
                  {netlistError ? (
                    <p className="text-[11px] text-rose-600 dark:text-rose-400 leading-tight flex items-start gap-1 px-1">
                      <AlertCircle className="h-3 w-3 mt-0.5 shrink-0" />
                      {netlistError}
                    </p>
                  ) : netlistNetCount !== null ? (
                    <p className="text-[11px] text-muted-foreground leading-tight px-1">
                      {netlistNetCount} nets detected
                    </p>
                  ) : netlistFile ? (
                    <p className="text-[11px] text-muted-foreground leading-tight px-1">
                      EDIF detected — net count will appear after upload.
                    </p>
                  ) : (
                    <p className="text-[11px] text-muted-foreground leading-tight px-1">
                      PADS-PCB ASCII or EDIF 2.0.0. .asc / .net / .txt / .edn all work.{" "}
                      <a
                        href="/file-guide#the-netlist"
                        target="_blank"
                        rel="noreferrer"
                        className="text-blue-600 dark:text-blue-500 hover:underline"
                      >
                        How to export →
                      </a>
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* ---- Step 2: BOM Columns ---- */}
          {step === "columns" && csvData && (
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Select which BOM columns contain the reference designators and
                manufacturer part numbers.
              </p>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-sm font-medium mb-1.5 block">
                    Designator Column
                  </label>
                  <Select
                    value={refCol}
                    onValueChange={(v: string | null) => setRefCol(v)}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Select column..." />
                    </SelectTrigger>
                    <SelectContent>
                      {csvData.headers.map((h) => (
                        <SelectItem key={h} value={h}>
                          {h}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-sm font-medium mb-1.5 block">
                    MPN Column
                  </label>
                  <Select
                    value={mpnCol}
                    onValueChange={(v: string | null) => setMpnCol(v)}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Select column..." />
                    </SelectTrigger>
                    <SelectContent>
                      {csvData.headers.map((h) => (
                        <SelectItem key={h} value={h}>
                          {h}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {lcscDetectedClient && (
                <div className="rounded-lg border border-blue-500/40 bg-blue-500/5 p-3 text-xs text-blue-800/90 dark:text-blue-200/90 leading-snug">
                  <span className="font-medium text-blue-900 dark:text-blue-100">
                    LCSC part numbers detected.
                  </span>{" "}
                  We&apos;ll resolve them to manufacturer part numbers via
                  purple-parts when you click Next. Resistors, capacitors,
                  and other passives will be auto-resolved on the next step.
                </div>
              )}

              {classification && (
                <div className="rounded-lg border bg-muted/30 p-3 text-sm space-y-1">
                  <p className="font-medium">
                    {classification.totalRefs} components detected
                  </p>
                  <div className="flex gap-4 text-muted-foreground">
                    <span>
                      {classification.icMpns.length} IC MPN
                      {classification.icMpns.length !== 1 && "s"}
                      {resolvedIcMpns.size > 0 && (
                        <span className="text-emerald-600 dark:text-emerald-400">
                          {" "}
                          ({resolvedIcMpns.size} in library)
                        </span>
                      )}
                    </span>
                    <span>
                      {classification.passiveCount} passives
                      {resolvedPassiveMpns.size > 0 && (
                        <span className="text-emerald-600 dark:text-emerald-400">
                          {" "}
                          ({resolvedPassiveMpns.size} resolved)
                        </span>
                      )}
                    </span>
                  </div>
                  {classification.icMpns.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {classification.icMpns.map(({ mpn, refs }) => (
                        <span
                          key={mpn}
                          className={cn(
                            "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-xs",
                            resolvedIcMpns.has(mpn)
                              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                              : "bg-muted",
                          )}
                        >
                          <Cpu className="h-3 w-3 text-muted-foreground" />
                          {mpn}
                          <span className="text-muted-foreground">
                            ({refs.join(", ")})
                          </span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* ---- Step 2.25: EDIF Sub-design Picker ---- */}
          {step === "subdesigns" && (
            <div className="space-y-4">
              <div>
                <p className="text-sm font-medium">
                  This EDIF file contains {edifSubDesigns.length} sub-designs.
                </p>
                <p className="text-sm text-muted-foreground mt-1">
                  {subdesignSuggestion?.kind === "clean" ? (
                    <>
                      Only one sub-design contains parts from your BOM. We&apos;ve
                      pre-selected it; uncheck or include the others if needed.
                    </>
                  ) : (
                    <>
                      Pick which sub-designs to review. Sub-designs with BOM
                      coverage are pre-selected.
                    </>
                  )}
                </p>
              </div>

              <div className="space-y-2">
                {edifSubDesigns.map((sd) => {
                  const key = _subKey(sd.id);
                  const checked = selectedSubdesignIds?.has(key) ?? false;
                  const coverage = subdesignCoverage.get(key) ?? 0;
                  const preview = sd.designators.slice(0, 8).join(", ");
                  const more =
                    sd.designators.length > 8
                      ? ` … +${sd.designators.length - 8} more`
                      : "";
                  return (
                    <label
                      key={key || "(unprefixed)"}
                      className={cn(
                        "flex items-start gap-3 rounded-md border p-3 cursor-pointer",
                        checked
                          ? "border-blue-500 bg-blue-500/5"
                          : "border-border hover:border-muted-foreground/40",
                      )}
                    >
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={checked}
                        onChange={(e) => {
                          const next = new Set(selectedSubdesignIds ?? []);
                          if (e.target.checked) next.add(key);
                          else next.delete(key);
                          setSelectedSubdesignIds(next);
                        }}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-sm">
                            {sd.id ?? "(unprefixed cells)"}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {sd.instance_count} parts · {coverage} in BOM
                          </span>
                        </div>
                        <p className="text-xs text-muted-foreground mt-1 font-mono break-all">
                          {preview}
                          {more}
                        </p>
                      </div>
                    </label>
                  );
                })}
              </div>

              {selectedSubdesignIds && selectedSubdesignIds.size === 0 && (
                <p className="text-[11px] text-amber-600 dark:text-amber-500 leading-tight px-1">
                  No sub-designs selected — at least one is required.
                </p>
              )}
            </div>
          )}

          {/* ---- Step 2.5: LCSC Passive Resolve ---- */}
          {step === "lcsc-passives" && (
            <div className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm text-muted-foreground">
                  The BOM used LCSC part numbers. We&apos;re resolving each
                  passive to typed specs (value, voltage, package) from its
                  LCSC description.
                </p>
                {/* Manual retry — useful after a top-up or transient
                    failures. Disabled while a batch is mid-flight. */}
                <Button
                  variant="outline"
                  size="sm"
                  disabled={lcscResolving}
                  onClick={handleResolveLcscPassives}
                >
                  {lcscResolving ? (
                    <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                  ) : (
                    <Zap className="h-3.5 w-3.5 mr-1" />
                  )}
                  {lcscResolving ? "Resolving..." : "Retry"}
                </Button>
              </div>

              {lcscOutOfCredits && (
                <div className="rounded-lg border border-rose-500/40 bg-rose-500/5 p-3 text-xs flex items-start gap-2">
                  <AlertCircle className="h-4 w-4 shrink-0 mt-0.5 text-rose-600 dark:text-rose-400" />
                  <div className="flex-1">
                    <p className="font-medium text-rose-900 dark:text-rose-100">
                      Out of credits — top up to continue
                    </p>
                    <p className="text-rose-800/80 dark:text-rose-200/80 mt-0.5 leading-snug">
                      Already-resolved passives are saved. After topping up,
                      click <span className="font-medium">Retry</span> to
                      finish the remaining rows. You can also skip this
                      step and let the pipeline resolve them later.
                    </p>
                  </div>
                  <Link
                    href="/credits"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0"
                  >
                    <Button size="sm">Top up</Button>
                  </Link>
                </div>
              )}

              <div className="space-y-2 max-h-[320px] overflow-y-auto pr-1">
                {lcscPassives.map(({ mpn, lcsc, refs, description }) => {
                  const status = lcscStatus.get(lcsc) ?? "pending";
                  const error = lcscErrors.get(lcsc);
                  const summary = lcscSummaries.get(lcsc);
                  return (
                    <div
                      key={lcsc}
                      className={cn(
                        "flex items-start gap-3 rounded-lg border p-3 transition-colors",
                        status === "resolved"
                          ? "border-emerald-500/40 bg-emerald-500/5"
                          : status === "failed"
                            ? "border-amber-500/40 bg-amber-500/5"
                            : "border-border",
                      )}
                    >
                      <div className="flex-1 min-w-0">
                        <p className="font-mono text-sm font-medium truncate">
                          <span
                            className="text-muted-foreground font-normal text-xs mr-1"
                            title={`Resolved from LCSC id ${lcsc}`}
                          >
                            {lcsc} →
                          </span>
                          {mpn}
                        </p>
                        <p className="text-xs text-muted-foreground truncate">
                          {refs.join(", ")}
                          {description && (
                            <>
                              {" · "}
                              <span className="text-muted-foreground/80">
                                {description}
                              </span>
                            </>
                          )}
                        </p>
                        {status === "resolved" && summary && (
                          <p className="text-xs text-emerald-600 dark:text-emerald-400 mt-0.5 truncate font-mono">
                            {summary}
                          </p>
                        )}
                        {status === "failed" && error && (
                          <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5 truncate">
                            {error}
                          </p>
                        )}
                      </div>
                      <div className="shrink-0 self-center">
                        {status === "resolved" ? (
                          <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                            <Zap className="h-3.5 w-3.5" />
                            resolved
                          </span>
                        ) : status === "resolving" ? (
                          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                        ) : status === "failed" ? (
                          <span className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                            <AlertCircle className="h-3.5 w-3.5" />
                            failed
                          </span>
                        ) : (
                          <span className="text-xs text-muted-foreground">
                            pending
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              <p className="text-xs text-muted-foreground">
                {(() => {
                  const total = lcscPassives.length;
                  const resolved = lcscPassives.filter(
                    (p) => lcscStatus.get(p.lcsc) === "resolved",
                  ).length;
                  return `${resolved} of ${total} passive${total !== 1 ? "s" : ""} resolved`;
                })()}
                {" — you can proceed even with unresolved rows; the pipeline will retry them."}
              </p>
            </div>
          )}

          {/* ---- Step 3: IC Datasheets ---- */}
          {step === "datasheets" && (
            <div className="space-y-3">
              {checkingLibrary ? (
                <div className="flex flex-col items-center justify-center py-12 text-muted-foreground gap-3">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  <p className="text-sm">Checking extracted components…</p>
                </div>
              ) : (<>
              <p className="text-sm text-muted-foreground">
                Datasheets are auto-fetched from DigiKey. Upload manually for any that fail below.
              </p>
              {(() => {
                const failedCount = unresolvedIcMpns.filter(
                  (e) =>
                    fetchStatus.get(e.mpn) === "failed" &&
                    !datasheetFiles.has(e.mpn) &&
                    !libraryDatasheets.has(e.mpn) &&
                    !existingDatasheetStems.has(safeMpn(e.mpn)),
                ).length;
                if (failedCount === 0) return null;
                return (
                  <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-xs flex items-start gap-2">
                    <AlertCircle className="h-4 w-4 shrink-0 mt-0.5 text-amber-600 dark:text-amber-400" />
                    <div>
                      <p className="font-medium text-amber-900 dark:text-amber-100">
                        {failedCount} datasheet{failedCount !== 1 ? "s" : ""} couldn&apos;t download automatically.
                      </p>
                      <p className="text-amber-800/80 dark:text-amber-200/80 mt-0.5 leading-snug">
                        Vendor sites sometimes block automated downloads or return a non-PDF error page. On each failed row below: click the DigiKey link to open the datasheet (or search the manufacturer&apos;s site), save the PDF, then use the <span className="font-medium">PDF</span> upload button on that row to attach it.
                      </p>
                    </div>
                  </div>
                );
              })()}
              <div className="space-y-2 max-h-[280px] overflow-y-auto pr-1">
                {unresolvedIcMpns.map(({ mpn, refs }) => {
                  const file = datasheetFiles.get(mpn);
                  const inLibrary = !file && libraryDatasheets.has(mpn);
                  const inProject = !file && !inLibrary && existingDatasheetStems.has(safeMpn(mpn));
                  const mpnFetchStatus = fetchStatus.get(mpn);
                  const mpnFetchError = fetchErrors.get(mpn);
                  const mpnFetchUrl = fetchUrls.get(mpn);
                  // If this MPN came from an LCSC id, show the source id as
                  // a muted prefix so the user can cross-check against
                  // their BOM. Hidden when the BOM already had real MPNs.
                  const lcscSource = mpnToLcsc.get(mpn);
                  return (
                    <div
                      key={mpn}
                      className={cn(
                        "flex items-center gap-3 rounded-lg border p-3 transition-colors",
                        file || inLibrary || inProject
                          ? "border-emerald-500/40 bg-emerald-500/5"
                          : mpnFetchStatus === "failed"
                            ? "border-amber-500/40 bg-amber-500/5"
                            : "border-border",
                      )}
                    >
                      <Cpu className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <div className="flex-1 min-w-0">
                        <p className="font-mono text-sm font-medium truncate">
                          {lcscSource && (
                            <span
                              className="text-muted-foreground font-normal text-xs mr-1"
                              title={`Resolved from LCSC id ${lcscSource}`}
                            >
                              {lcscSource} →
                            </span>
                          )}
                          {mpn}
                        </p>
                        {mpnFetchUrl && (
                          <a
                            href={mpnFetchUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Open datasheet on DigiKey"
                            className="text-xs text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 hover:underline inline-flex items-center gap-1 truncate max-w-full"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <ExternalLink className="h-3 w-3 shrink-0" />
                            <span className="truncate">{mpnFetchUrl}</span>
                          </a>
                        )}
                        <p className="text-xs text-muted-foreground">
                          {refs.join(", ")}
                        </p>
                        {mpnFetchStatus === "failed" && mpnFetchError && (
                          <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5 truncate">
                            {mpnFetchError}
                          </p>
                        )}
                      </div>
                      {file ? (
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs text-emerald-600 dark:text-emerald-400 font-mono truncate max-w-[100px]">
                            {file.name}
                          </span>
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            onClick={() =>
                              setDatasheetFiles((prev) => {
                                const next = new Map(prev);
                                next.delete(mpn);
                                return next;
                              })
                            }
                          >
                            <X className="h-3 w-3" />
                          </Button>
                        </div>
                      ) : inLibrary ? (
                        <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          in library
                        </span>
                      ) : inProject ? (
                        <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          uploaded
                        </span>
                      ) : mpnFetchStatus === "fetching" ? (
                        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                      ) : (
                        <label className="cursor-pointer">
                          <Button
                            variant="outline"
                            size="sm"
                            nativeButton={false}
                            render={<span />}
                          >
                            <Upload className="h-3.5 w-3.5 mr-1" />
                            PDF
                          </Button>
                          <input
                            type="file"
                            accept=".pdf"
                            className="hidden"
                            onChange={(e) => {
                              const f = e.target.files?.[0];
                              if (f) {
                                setDatasheetFiles((prev) => {
                                  const next = new Map(prev);
                                  next.set(mpn, f);
                                  return next;
                                });
                              }
                              e.target.value = "";
                            }}
                          />
                        </label>
                      )}
                    </div>
                  );
                })}
              </div>
              <p className="text-xs text-muted-foreground">
                {datasheetFiles.size + unresolvedIcMpns.filter(e => !datasheetFiles.has(e.mpn) && (libraryDatasheets.has(e.mpn) || existingDatasheetStems.has(safeMpn(e.mpn)))).length} of {unresolvedIcMpns.length} datasheets
                ready
                {resolvedIcMpns.size > 0 && (
                  <span>
                    {" "}
                    ({resolvedIcMpns.size} fully resolved)
                  </span>
                )}
              </p>
              </>)}
            </div>
          )}

          {/* ---- Step 3.5: Simple Component Datasheets (Optional) ---- */}
          {step === "simple" && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  Resolve component specs automatically, or upload datasheets.
                  These are <strong>optional</strong>.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={autoResolving || autoFetching}
                  onClick={() => handleAutoResolve(unresolvedSimpleGroups)}
                >
                  {autoResolving ? (
                    <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                  ) : (
                    <Zap className="h-3.5 w-3.5 mr-1" />
                  )}
                  {autoResolving ? "Resolving..." : "Auto-resolve"}
                </Button>
              </div>
              {(() => {
                const failedCount = unresolvedSimpleGroups
                  .flatMap((g) => g.mpns)
                  .filter(
                    (e) =>
                      fetchStatus.get(e.mpn) === "failed" &&
                      !simpleDatasheetFiles.has(e.mpn) &&
                      !libraryDatasheets.has(e.mpn) &&
                      !existingDatasheetStems.has(safeMpn(e.mpn)) &&
                      resolveStatus.get(e.mpn) !== "resolved",
                  ).length;
                if (failedCount === 0) return null;
                return (
                  <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-xs flex items-start gap-2">
                    <AlertCircle className="h-4 w-4 shrink-0 mt-0.5 text-amber-600 dark:text-amber-400" />
                    <div>
                      <p className="font-medium text-amber-900 dark:text-amber-100">
                        {failedCount} datasheet{failedCount !== 1 ? "s" : ""} couldn&apos;t download automatically.
                      </p>
                      <p className="text-amber-800/80 dark:text-amber-200/80 mt-0.5 leading-snug">
                        Vendor sites sometimes block automated downloads or return a non-PDF error page. These components are optional, but if you want a full review: click the <ExternalLink className="inline h-3 w-3 -mt-0.5" /> link on a failed row to open the datasheet, save the PDF, then use the <span className="font-medium">PDF</span> upload button on that row.
                      </p>
                    </div>
                  </div>
                );
              })()}
              <div className="space-y-2 max-h-[280px] overflow-y-auto pr-1">
                {unresolvedSimpleGroups.map((group) => (
                  <div key={group.label} className="space-y-1.5">
                    <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                      {group.label}
                      {group.label === "Connectors" && (
                        <span className="ml-2 normal-case text-muted-foreground/80">
                          — datasheets are truly{" "}
                          <span className="font-semibold text-emerald-600 dark:text-emerald-400">
                            optional
                          </span>
                        </span>
                      )}
                    </p>
                    {group.mpns.map(({ mpn, refs }) => {
                      const file = simpleDatasheetFiles.get(mpn);
                      const inLibrary = !file && libraryDatasheets.has(mpn);
                      const rStatus = resolveStatus.get(mpn);
                      const rError = resolveErrors.get(mpn);
                      const sFetchStatus = fetchStatus.get(mpn);
                      const sFetchError = fetchErrors.get(mpn);
                      const sFetchUrl = fetchUrls.get(mpn);
                      const isResolved = rStatus === "resolved";
                      const inProject =
                        !file && !inLibrary && !isResolved && existingDatasheetStems.has(safeMpn(mpn));
                      return (
                        <div
                          key={mpn}
                          className={cn(
                            "flex items-center gap-3 rounded-lg border p-3 transition-colors",
                            file || inLibrary || isResolved || inProject
                              ? "border-emerald-500/40 bg-emerald-500/5"
                              : rStatus === "failed" || sFetchStatus === "failed"
                                ? "border-amber-500/40 bg-amber-500/5"
                                : "border-border",
                          )}
                        >
                          <div className="flex-1 min-w-0">
                            <p className="font-mono text-sm font-medium truncate flex items-center gap-1">
                              {mpn}
                              {sFetchUrl && (
                                <a
                                  href={sFetchUrl}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  title="Open datasheet on DigiKey"
                                  className="shrink-0 text-muted-foreground hover:text-foreground"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <ExternalLink className="h-3 w-3" />
                                </a>
                              )}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {refs.join(", ")}
                            </p>
                            {rStatus === "failed" && rError && (
                              <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5 truncate">
                                {rError}
                              </p>
                            )}
                            {sFetchStatus === "failed" && sFetchError && (
                              <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5 truncate">
                                {sFetchError}
                              </p>
                            )}
                          </div>
                          {file ? (
                            <div className="flex items-center gap-1.5">
                              <span className="text-xs text-emerald-600 dark:text-emerald-400 font-mono truncate max-w-[100px]">
                                {file.name}
                              </span>
                              <Button
                                variant="ghost"
                                size="icon-xs"
                                onClick={() =>
                                  setSimpleDatasheetFiles((prev) => {
                                    const next = new Map(prev);
                                    next.delete(mpn);
                                    return next;
                                  })
                                }
                              >
                                <X className="h-3 w-3" />
                              </Button>
                            </div>
                          ) : inLibrary ? (
                            <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                              <CheckCircle2 className="h-3.5 w-3.5" />
                              in library
                            </span>
                          ) : isResolved ? (
                            <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                              <Zap className="h-3.5 w-3.5" />
                              auto-resolved
                            </span>
                          ) : inProject ? (
                            <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                              <CheckCircle2 className="h-3.5 w-3.5" />
                              uploaded
                            </span>
                          ) : rStatus === "resolving" || sFetchStatus === "fetching" ? (
                            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                          ) : (
                            <label className="cursor-pointer">
                              <Button
                                variant="outline"
                                size="sm"
                                nativeButton={false}
                                render={<span />}
                              >
                                <Upload className="h-3.5 w-3.5 mr-1" />
                                PDF
                              </Button>
                              <input
                                type="file"
                                accept=".pdf"
                                className="hidden"
                                onChange={(e) => {
                                  const f = e.target.files?.[0];
                                  if (f) {
                                    setSimpleDatasheetFiles((prev) => {
                                      const next = new Map(prev);
                                      next.set(mpn, f);
                                      return next;
                                    });
                                  }
                                  e.target.value = "";
                                }}
                              />
                            </label>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                {(() => {
                  const totalUnresolved = unresolvedSimpleGroups.reduce((sum, g) => sum + g.mpns.length, 0);
                  const autoResolvedCount = Array.from(resolveStatus.values()).filter(s => s === "resolved").length;
                  const readyCount = simpleDatasheetFiles.size + autoResolvedCount;
                  return `${readyCount} of ${totalUnresolved} components ready (all optional)`;
                })()}
                {resolvedSimpleMpns.size > 0 && (
                  <span>
                    {" "}
                    ({resolvedSimpleMpns.size} already in library)
                  </span>
                )}
              </p>
            </div>
          )}

          {/* ---- Step 4: Passive Datasheets (Optional) ---- */}
          {step === "passives" && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  Resolve component specs automatically, or upload datasheets.
                  These are <strong>optional</strong>.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={autoResolving || autoFetching}
                  onClick={() => handleAutoResolvePassives(unresolvedPassiveGroups)}
                >
                  {autoResolving ? (
                    <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                  ) : (
                    <Zap className="h-3.5 w-3.5 mr-1" />
                  )}
                  {autoResolving ? "Resolving..." : "Auto-resolve"}
                </Button>
              </div>
              <div className="space-y-2 max-h-[280px] overflow-y-auto pr-1">
                {unresolvedPassiveGroups.map((group) => (
                  <div key={group.prefix} className="space-y-1.5">
                    <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                      {group.label}
                    </p>
                    {group.mpns.map(({ mpn, refs }) => {
                      const file = passiveDatasheetFiles.get(mpn);
                      const inLibrary = !file && resolvedPassiveMpns.has(mpn);
                      const rStatus = resolveStatus.get(mpn);
                      const rError = resolveErrors.get(mpn);
                      const pFetchStatus = fetchStatus.get(mpn);
                      const pFetchError = fetchErrors.get(mpn);
                      const isResolved = rStatus === "resolved";
                      const inProject =
                        !file && !inLibrary && !isResolved && existingDatasheetStems.has(safeMpn(mpn));
                      return (
                        <div
                          key={mpn}
                          className={cn(
                            "flex items-center gap-3 rounded-lg border p-3 transition-colors",
                            file || inLibrary || isResolved || inProject
                              ? "border-emerald-500/40 bg-emerald-500/5"
                              : rStatus === "failed" || pFetchStatus === "failed"
                                ? "border-amber-500/40 bg-amber-500/5"
                                : "border-border",
                          )}
                        >
                          <div className="flex-1 min-w-0">
                            <p className="font-mono text-sm font-medium truncate">
                              {mpn}
                            </p>
                            <p className="text-xs text-muted-foreground">
                              {refs.join(", ")}
                            </p>
                            {rStatus === "failed" && rError && (
                              <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5 truncate">
                                {rError}
                              </p>
                            )}
                            {pFetchStatus === "failed" && pFetchError && (
                              <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5 truncate">
                                {pFetchError}
                              </p>
                            )}
                          </div>
                          {file ? (
                            <div className="flex items-center gap-1.5">
                              <span className="text-xs text-emerald-600 dark:text-emerald-400 font-mono truncate max-w-[100px]">
                                {file.name}
                              </span>
                              <Button
                                variant="ghost"
                                size="icon-xs"
                                onClick={() =>
                                  setPassiveDatasheetFiles((prev) => {
                                    const next = new Map(prev);
                                    next.delete(mpn);
                                    return next;
                                  })
                                }
                              >
                                <X className="h-3 w-3" />
                              </Button>
                            </div>
                          ) : inLibrary ? (
                            <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                              <CheckCircle2 className="h-3.5 w-3.5" />
                              in library
                            </span>
                          ) : isResolved ? (
                            <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                              <Zap className="h-3.5 w-3.5" />
                              auto-resolved
                            </span>
                          ) : inProject ? (
                            <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                              <CheckCircle2 className="h-3.5 w-3.5" />
                              uploaded
                            </span>
                          ) : rStatus === "resolving" || pFetchStatus === "fetching" ? (
                            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                          ) : (
                            <label className="cursor-pointer">
                              <Button
                                variant="outline"
                                size="sm"
                                nativeButton={false}
                                render={<span />}
                              >
                                <Upload className="h-3.5 w-3.5 mr-1" />
                                PDF
                              </Button>
                              <input
                                type="file"
                                accept=".pdf"
                                className="hidden"
                                onChange={(e) => {
                                  const f = e.target.files?.[0];
                                  if (f) {
                                    setPassiveDatasheetFiles((prev) => {
                                      const next = new Map(prev);
                                      // Auto-share: apply to all uncovered MPNs with same alpha prefix
                                      const prefix = getSeriesPrefix(mpn);
                                      for (const g of unresolvedPassiveGroups) {
                                        for (const entry of g.mpns) {
                                          if (
                                            !next.has(entry.mpn) &&
                                            !resolvedPassiveMpns.has(entry.mpn) &&
                                            getSeriesPrefix(entry.mpn) === prefix
                                          ) {
                                            next.set(entry.mpn, f);
                                          }
                                        }
                                      }
                                      // Always set for the selected MPN (even if prefix already covered)
                                      next.set(mpn, f);
                                      return next;
                                    });
                                  }
                                  e.target.value = "";
                                }}
                              />
                            </label>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                {(() => {
                  const totalUnresolved = unresolvedPassiveGroups.reduce((sum, g) => sum + g.mpns.length, 0);
                  const autoResolvedCount = unresolvedPassiveGroups
                    .flatMap((g) => g.mpns)
                    .filter((e) => resolveStatus.get(e.mpn) === "resolved").length;
                  const readyCount = passiveDatasheetFiles.size + autoResolvedCount;
                  return `${readyCount} of ${totalUnresolved} components ready (all optional)`;
                })()}
                {resolvedPassiveMpns.size > 0 && (
                  <span>
                    {" "}
                    ({resolvedPassiveMpns.size} already in library)
                  </span>
                )}
              </p>
            </div>
          )}

          {/* ---- Step 5: Power Sources (Optional) ---- */}
        </div>

        {/* Error */}
        {error && (
          <div className="flex items-center gap-2 rounded-lg border border-rose-500/40 bg-rose-500/5 p-3 text-sm text-rose-600 dark:text-rose-400">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        {/* Footer */}
        <DialogFooter className="flex-col gap-2 sm:flex-col sm:items-stretch">
          <div className="flex items-center w-full">
            {step !== "details" && !creating && (
              <Button variant="outline" onClick={goBack}>
                <ChevronLeft className="h-4 w-4 mr-1" />
                Back
              </Button>
            )}
            <div className="flex-1" />
            {creating ? (
              <Button disabled>
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                {progress}
              </Button>
            ) : isLastStep ? (
              <Button
                onClick={handleCreate}
                disabled={!canAdvance() || prefillLoading || (isRerun && !canRunRerun)}
              >
                {isRerun ? "Run" : "Create Project"}
              </Button>
            ) : (
              <Button
                onClick={goNext}
                disabled={!canAdvance() || prefillLoading || earlyUploading}
              >
                {earlyUploading ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                    Resolving LCSC…
                  </>
                ) : (
                  <>
                    Next
                    <ChevronRight className="h-4 w-4 ml-1" />
                  </>
                )}
              </Button>
            )}
          </div>
          {isLastStep && isRerun && (
            <div className="text-xs text-muted-foreground flex items-center justify-end gap-4 w-full">
              {estimateLoading ? (
                <span className="flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Estimating…
                </span>
              ) : estimate && balance !== null ? (
                <>
                  <span>
                    Balance:{" "}
                    <span className="font-mono text-foreground">
                      {balance.toFixed(2)}
                    </span>
                  </span>
                  <span>
                    Est:{" "}
                    <span className="font-mono text-foreground">
                      {estimate.credits_low.toFixed(2)}–{estimate.credits_high.toFixed(2)}
                    </span>{" "}
                    credits
                  </span>
                  {!canRunRerun && (
                    <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
                      <AlertCircle className="h-3 w-3" />
                      Need {estimate.credits_low.toFixed(2)} to run
                    </span>
                  )}
                </>
              ) : null}
            </div>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
