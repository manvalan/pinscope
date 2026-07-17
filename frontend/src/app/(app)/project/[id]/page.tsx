"use client";

import { use, useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  fetchProject,
  fetchProjectLogs,
  fetchBomSummary,
  fetchDerating,
  fetchCollaborators,
  addCollaborator,
  removeCollaborator,
  makeCollaboratorOwner,
  startPipeline,
  resumePipeline,
  fetchPipelineEstimate,
} from "@/lib/api";
import { PausedRunBanner } from "@/components/billing/paused-run-banner";
import type { Project, SkippedComponent, ApiLogEntry, BomSummaryRow, DeratingRow, DeratingSettings, Collaborator, CostEstimate } from "@/lib/types";
import {
  ArrowRight,
  Play,
  AlertTriangle,
  ExternalLink,
  X,
  UserPlus,
  Trash2,
  Crown,
  Coins,
  OctagonX,
  Copy,
  Check,
} from "lucide-react";
import { useOptionalUser } from "@/hooks/use-optional-auth";
import { PdfViewerSheet } from "@/components/pdf/pdf-viewer-sheet";

export default function ProjectDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const [project, setProject] = useState<Project | null>(null);
  const [logs, setLogs] = useState<ApiLogEntry[]>([]);
  const [bomRows, setBomRows] = useState<BomSummaryRow[]>([]);
  const [deratingRows, setDeratingRows] = useState<DeratingRow[]>([]);
  const [deratingSettings, setDeratingSettings] = useState<DeratingSettings>(() => {
    if (typeof window === "undefined") return { ceramic: 50, tantalum: 50, electrolytic: 50 };
    try {
      const stored = localStorage.getItem(`pinscopex:derating-settings:${id}`);
      return stored ? JSON.parse(stored) : { ceramic: 50, tantalum: 50, electrolytic: 50 };
    } catch {
      return { ceramic: 50, tantalum: 50, electrolytic: 50 };
    }
  });
  const [manualVoltages, setManualVoltages] = useState<Record<string, number>>(() => {
    if (typeof window === "undefined") return {};
    try {
      const stored = localStorage.getItem(`pinscopex:derating-overrides:${id}`);
      return stored ? JSON.parse(stored) : {};
    } catch {
      return {};
    }
  });
  const [pdfState, setPdfState] = useState<{
    open: boolean;
    mpn: string | null;
  }>({ open: false, mpn: null });

  // Persist derating settings to localStorage
  useEffect(() => {
    localStorage.setItem(`pinscopex:derating-settings:${id}`, JSON.stringify(deratingSettings));
  }, [deratingSettings, id]);

  // Persist manual voltage overrides to localStorage
  useEffect(() => {
    localStorage.setItem(`pinscopex:derating-overrides:${id}`, JSON.stringify(manualVoltages));
  }, [manualVoltages, id]);

  const reload = useCallback(() => {
    fetchProject(id).then(setProject);
    fetchProjectLogs(id).then(setLogs);
    fetchBomSummary(id).then(setBomRows);
    fetchDerating(id).then(setDeratingRows);
  }, [id]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Redirect to progress page if pipeline is running
  useEffect(() => {
    if (project?.status === "running") {
      router.replace(`/project/${id}/progress`);
    }
  }, [project?.status, id, router]);

  const searchParams = useSearchParams();
  const tab = searchParams.get("tab") ?? "bom";
  const [starting, setStarting] = useState(false);
  const [estimate, setEstimate] = useState<CostEstimate | null>(null);

  const canRun = Boolean(project?.hasBom && project?.hasNetlist);
  const canStartFresh = project?.status !== "complete" && project?.status !== "paused_insufficient_credits";

  // Pull a cost estimate when the project is ready to run, so we can show
  // the "~X credits" hint under the button.  No blocking — the user can
  // start the run even if the balance is below the estimate.
  useEffect(() => {
    if (!canRun || !canStartFresh) {
      setEstimate(null);
      return;
    }
    let cancelled = false;
    fetchPipelineEstimate(id)
      .then((est) => {
        if (!cancelled) setEstimate(est);
      })
      .catch(() => {
        if (!cancelled) setEstimate(null);
      });
    return () => {
      cancelled = true;
    };
  }, [id, canRun, canStartFresh]);

  if (!project) return null;

  const hasSkipped = project.skippedComponents && project.skippedComponents.length > 0;
  const isPaused = project.status === "paused_insufficient_credits";

  const handleRunPipeline = async () => {
    if (!canRun) return;
    setStarting(true);
    try {
      await startPipeline(id);
      router.push(`/project/${id}/progress`);
    } catch (e) {
      setStarting(false);
      alert(e instanceof Error ? e.message : "Failed to start pipeline");
    }
  };

  const handleResumeRun = async () => {
    setStarting(true);
    try {
      await resumePipeline(id);
      router.push(`/project/${id}/progress`);
    } catch (e) {
      setStarting(false);
      alert(e instanceof Error ? e.message : "Failed to resume pipeline");
    }
  };

  return (
    <div className="flex-1 p-6 max-w-4xl mx-auto w-full space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">{project.name}</h1>
          <p className="text-sm text-muted-foreground">
            {new Date(project.created).toLocaleDateString()}
          </p>
        </div>
        <Badge variant="outline" className="capitalize text-xs">
          {project.status}
        </Badge>
      </div>

      {isPaused && (
        <PausedRunBanner
          projectId={id}
          checkpoint={project.pauseCheckpoint ?? null}
          resuming={starting}
          onResume={handleResumeRun}
        />
      )}

      {project.status === "error" && (
        <PipelineErrorBanner
          projectId={id}
          message={project.pipelineError ?? null}
        />
      )}

      <Card>
        <CardContent className="py-3">
          {project.status === "complete" ? (
            <div className="flex items-center gap-3">
              <span className="text-sm text-emerald-600 dark:text-emerald-400">
                Validation complete
              </span>
              <Link href={`/project/${id}/report`} className="ml-auto">
                <Button size="sm">
                  View Report
                  <ArrowRight className="h-4 w-4 ml-1" />
                </Button>
              </Link>
            </div>
          ) : isPaused ? (
            <span className="text-sm text-amber-600 dark:text-amber-400">
              Paused — waiting for credits. Use the banner above to resume.
            </span>
          ) : (
            <div className="flex flex-col items-start gap-2">
              <div className="flex items-center gap-3">
                <Button size="sm" disabled={!canRun || starting} onClick={handleRunPipeline}>
                  <Play className="h-4 w-4 mr-1" />
                  {starting ? "Starting..." : "Run Pipeline"}
                </Button>
                {!canRun && (
                  <span className="text-xs text-muted-foreground">
                    Upload BOM and netlist to enable
                  </span>
                )}
              </div>
              {canRun && estimate && estimate.review_ic_count > 0 && (
                <div className="inline-flex items-center gap-1.5 text-[11px] text-amber-700/90 dark:text-amber-300/90 animate-pulse drop-shadow-[0_0_6px_rgba(251,191,36,0.55)]">
                  <Coins className="h-3 w-3" />
                  <span className="tabular-nums">
                    ≈ {(estimate.review_ic_count * 2).toFixed(0)}
                    –{(estimate.review_ic_count * 2.5).toFixed(0)} credits
                  </span>
                  <span className="text-amber-700/60 dark:text-amber-300/60">
                    · {estimate.review_ic_count} IC
                    {estimate.review_ic_count === 1 ? "" : "s"} to review
                  </span>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {tab === "bom" && (
        <BomSummaryTable
          rows={bomRows}
          onViewDatasheet={(mpn) => setPdfState({ open: true, mpn })}
        />
      )}

      {tab === "derating" && (
        <DeratingTable
          rows={deratingRows}
          settings={deratingSettings}
          onSettingsChange={setDeratingSettings}
          manualVoltages={manualVoltages}
          onManualVoltageChange={(designator, voltage) => {
            setManualVoltages((prev) => {
              if (voltage === null) {
                const next = { ...prev };
                delete next[designator];
                return next;
              }
              return { ...prev, [designator]: voltage };
            });
          }}
        />
      )}

      {tab === "logs" && (
        <ApiLogsSection logs={logs} />
      )}

      {tab === "settings" && (
        <div className="space-y-6">
          <CollaboratorsSection projectId={id} />
          <SkippedComponentsSection skipped={project.skippedComponents} />
          <ReportVersionSection pinscopeVersion={project.pinscopeVersion} />
        </div>
      )}
      <PdfViewerSheet
        open={pdfState.open}
        onOpenChange={(open) => setPdfState((s) => ({ ...s, open }))}
        projectId={id}
        mpn={pdfState.mpn}
        initialPage={1}
      />
    </div>
  );
}

const STAGE_LABELS: Record<string, string> = {
  ic_extraction: "IC Extraction",
  simple_extraction: "Specs Extraction",
  passive_extraction: "Passive Extraction",
  passive_pattern_load: "Pattern Loading",
  passive_resolve: "Passive Resolution",
  passive_specs: "Passive Specs",
  graph_build: "Graph Build",
  validation: "Datasheet Review",
  simple_digikey_resolve: "DigiKey Auto-Resolve",
};

const LOG_STAGE_LABELS: Record<string, string> = {
  pintable: "Pin Table",
  rules: "Rules",
  specs: "Specs",
  pattern: "Pattern",
  validation: "Validation",
};

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(1)}k`;
}

function ApiLogsSection({ logs }: { logs: ApiLogEntry[] }) {
  if (logs.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">API Calls</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No API call logs yet. Run the pipeline to generate logs.
          </p>
        </CardContent>
      </Card>
    );
  }

  const totalInput = logs.reduce((s, l) => s + l.input_tokens, 0);
  const totalOutput = logs.reduce((s, l) => s + l.output_tokens, 0);
  const totalDuration = logs.reduce((s, l) => s + l.duration_ms, 0);
  const totalCost = logs.reduce((s, l) => s + (l.cost_usd ?? 0), 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          API Calls
          <Badge variant="outline" className="text-xs">
            {logs.length}
          </Badge>
        </CardTitle>
        <div className="flex gap-4 text-xs text-muted-foreground">
          <span>{formatTokens(totalInput)} input tokens</span>
          <span>{formatTokens(totalOutput)} output tokens</span>
          <span>{formatDuration(totalDuration)} total</span>
          {totalCost > 0 && <span className="font-medium text-foreground">${totalCost.toFixed(4)}</span>}
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {logs.map((log, i) => (
            <div
              key={`${log.identifier}-${log.stage}-${i}`}
              className="flex items-start gap-3 rounded-md border border-border/50 p-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium font-mono">
                    {log.identifier}
                  </span>
                  <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                    {LOG_STAGE_LABELS[log.stage] ?? log.stage}
                  </Badge>
                  {log.skill_id && (
                    <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-blue-600 dark:text-blue-400 border-blue-500/40">
                      skill
                    </Badge>
                  )}
                  {log.turns && log.turns > 1 && (
                    <span className="text-[10px] text-muted-foreground">
                      {log.turns} turns
                    </span>
                  )}
                </div>
                <div className="flex gap-3 mt-1 text-xs text-muted-foreground">
                  <span>{formatTokens(log.input_tokens)} in</span>
                  <span>{formatTokens(log.output_tokens)} out</span>
                  <span>{formatDuration(log.duration_ms)}</span>
                  <span className="font-mono">{log.model}</span>
                  {log.cost_usd != null && log.cost_usd > 0 && (
                    <span className="font-medium text-foreground">${log.cost_usd.toFixed(4)}</span>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

const SPEC_LABELS: Record<string, string> = {
  value_formatted: "Value",
  tolerance: "Tolerance",
  package: "Package",
  power_rating_w: "Power",
  voltage_rating_v: "Voltage",
  dielectric: "Dielectric",
  current_rating_a: "Current",
  dcr_ohms: "DCR",
  // discrete
  forward_voltage_v: "Vf",
  reverse_voltage_v: "Vr",
  forward_current_a: "If",
  zener_voltage_v: "Vz",
  zener_impedance_ohm: "Zzt",
  standoff_voltage_v: "Vrwm",
  clamping_voltage_v: "Vclamp",
  peak_pulse_current_a: "Ipp",
  vds_max_v: "Vds",
  vce_max_v: "Vce",
  id_max_a: "Id",
  ic_max_a: "Ic",
  rds_on_ohm: "Rds(on)",
  vgs_th_v: "Vgs(th)",
  qg_c: "Qg",
  hfe: "hFE",
  color: "Color",
  power_dissipation_w: "Pd",
  // crystal
  frequency_hz: "Freq",
  load_capacitance_f: "CL",
  esr_ohm: "ESR",
  // connector
  pin_count: "Pins",
  // general
  turns_ratio: "Turns",
  voltage_primary_v: "Vpri",
  voltage_secondary_v: "Vsec",
  breaking_capacity_a: "Breaking",
};

function BomSummaryTable({
  rows,
  onViewDatasheet,
}: {
  rows: BomSummaryRow[];
  onViewDatasheet: (mpn: string) => void;
}) {
  if (rows.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Bill of Materials</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No BOM summary yet. Run the pipeline to generate.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          Bill of Materials
          <Badge variant="outline" className="text-xs">
            {rows.length} parts
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="pb-2 pr-4 font-medium">MPN</th>
                <th className="pb-2 pr-4 font-medium">Designators</th>
                <th className="pb-2 pr-4 font-medium">Value</th>
                <th className="pb-2 pr-4 font-medium">Category</th>
                <th className="pb-2 pr-4 font-medium">Specs</th>
                <th className="pb-2 font-medium">Datasheet</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {rows.map((row, i) => (
                <tr key={row.mpn ?? `row-${i}`}>
                  <td className="py-2 pr-4 font-mono text-xs">
                    {row.mpn ?? <span className="text-muted-foreground">—</span>}
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs">
                    {row.designators.join(", ")}
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs">
                    {row.value}
                  </td>
                  <td className="py-2 pr-4">
                    {row.category ? (
                      <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-mono">
                        {row.category}
                      </Badge>
                    ) : (
                      <span className="text-muted-foreground text-xs">—</span>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-xs text-muted-foreground">
                    {row.description ? (
                      <span className="text-foreground/80">{row.description}</span>
                    ) : row.specs ? (
                      <span className="flex flex-wrap gap-x-3 gap-y-0.5">
                        {Object.entries(row.specs).map(([k, v]) => (
                          <span key={k}>
                            <span className="text-muted-foreground/60">{SPEC_LABELS[k] ?? k}:</span>{" "}
                            <span className="font-mono text-foreground">{String(v)}</span>
                          </span>
                        ))}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-2 text-xs">
                    {row.hasDatasheet && row.mpn ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-2 text-xs text-blue-600 hover:text-blue-700 dark:text-blue-500 dark:hover:text-blue-400"
                        onClick={() => onViewDatasheet(row.mpn!)}
                      >
                        <ExternalLink className="h-3 w-3 mr-1" />
                        View
                      </Button>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function deratingStatus(
  row: DeratingRow,
  settings: DeratingSettings,
  manualVoltage: number | undefined,
): "pass" | "fail" | "unknown" {
  const rated = row.rated_voltage_v;
  const operating = manualVoltage ?? row.operating_voltage_v;
  if (rated == null || operating == null) return "unknown";
  const category = row.dielectric_category ?? "ceramic";
  const pct = settings[category] / 100;
  const threshold = rated * (1 - pct);
  return operating <= threshold ? "pass" : "fail";
}

const STATUS_ROW_STYLES: Record<string, string> = {
  pass: "bg-emerald-500/10",
  fail: "bg-rose-500/10",
  unknown: "",
};

const CATEGORY_LABELS: Record<string, string> = {
  ceramic: "Ceramic",
  tantalum: "Tantalum",
  electrolytic: "Electrolytic",
};

function DeratingTable({
  rows,
  settings,
  onSettingsChange,
  manualVoltages,
  onManualVoltageChange,
}: {
  rows: DeratingRow[];
  settings: DeratingSettings;
  onSettingsChange: (s: DeratingSettings) => void;
  manualVoltages: Record<string, number>;
  onManualVoltageChange: (designator: string, voltage: number | null) => void;
}) {
  const [editingCell, setEditingCell] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  if (rows.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Capacitor Voltage Derating</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No derating data yet. Run the pipeline to generate.
          </p>
        </CardContent>
      </Card>
    );
  }

  const passCount = rows.filter(
    (r) => deratingStatus(r, settings, manualVoltages[r.designator]) === "pass",
  ).length;
  const failCount = rows.filter(
    (r) => deratingStatus(r, settings, manualVoltages[r.designator]) === "fail",
  ).length;

  return (
    <div className="space-y-4">
      {/* Settings card */}
      <Card>
        <CardContent className="py-3">
          <div className="flex items-center gap-6 flex-wrap">
            <span className="text-xs font-medium text-muted-foreground">Derating %</span>
            {(["ceramic", "tantalum", "electrolytic"] as const).map((cat) => (
              <label key={cat} className="flex items-center gap-1.5">
                <span className="text-xs text-muted-foreground">{CATEGORY_LABELS[cat]}</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={settings[cat]}
                  onChange={(e) => {
                    const val = Math.max(0, Math.min(100, Number(e.target.value) || 0));
                    onSettingsChange({ ...settings, [cat]: val });
                  }}
                  className="w-14 rounded border border-border bg-background px-1.5 py-0.5 text-xs font-mono text-center focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
                <span className="text-xs text-muted-foreground">%</span>
              </label>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            Capacitor Voltage Derating
            <Badge variant="outline" className="text-xs">
              {rows.length} caps
            </Badge>
            {passCount > 0 && (
              <span className="text-[10px] text-emerald-600 dark:text-emerald-400">{passCount} pass</span>
            )}
            {failCount > 0 && (
              <span className="text-[10px] text-rose-600 dark:text-rose-400">{failCount} fail</span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="pb-2 pr-4 font-medium">Designator</th>
                  <th className="pb-2 pr-4 font-medium">MPN</th>
                  <th className="pb-2 pr-4 font-medium">Value</th>
                  <th className="pb-2 pr-4 font-medium">Type</th>
                  <th className="pb-2 pr-4 font-medium">Net+</th>
                  <th className="pb-2 pr-4 font-medium">Net−</th>
                  <th className="pb-2 pr-4 font-medium text-right">Rated V</th>
                  <th className="pb-2 pr-4 font-medium text-right">Operating V</th>
                  <th className="pb-2 pr-4 font-medium">Source</th>
                  <th className="pb-2 font-medium text-right">Margin</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {rows.map((row) => {
                  const manual = manualVoltages[row.designator];
                  const status = deratingStatus(row, settings, manual);
                  const operatingV = manual ?? row.operating_voltage_v;
                  const source = manual != null ? "user" : row.operating_voltage_source;
                  const isEditing = editingCell === row.designator;

                  // Compute margin
                  let margin: string | null = null;
                  if (row.rated_voltage_v != null && operatingV != null) {
                    const cat = row.dielectric_category ?? "ceramic";
                    const threshold = row.rated_voltage_v * (1 - settings[cat] / 100);
                    const pct = ((threshold - operatingV) / threshold) * 100;
                    margin = `${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%`;
                  }

                  return (
                    <tr
                      key={row.designator}
                      className={STATUS_ROW_STYLES[status]}
                    >
                      <td className="py-2 pr-4 font-mono text-xs font-medium">
                        {row.designator}
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs">
                        {row.mpn ?? <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs">
                        {row.value_formatted ?? <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2 pr-4 text-xs">
                        {row.dielectric_category ? (
                          <Badge variant="outline" className="text-[10px] px-1.5 py-0 capitalize">
                            {row.dielectric_category}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs">
                        {row.net_plus ?? <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs">
                        {row.net_minus ?? <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs text-right">
                        {row.rated_voltage_v != null ? (
                          `${row.rated_voltage_v}V`
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-right">
                        {isEditing ? (
                          <input
                            type="number"
                            step="0.1"
                            autoFocus
                            value={editValue}
                            onChange={(e) => setEditValue(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                const v = parseFloat(editValue);
                                if (!isNaN(v) && v >= 0) {
                                  onManualVoltageChange(row.designator, v);
                                }
                                setEditingCell(null);
                              }
                              if (e.key === "Escape") setEditingCell(null);
                            }}
                            onBlur={() => {
                              const v = parseFloat(editValue);
                              if (!isNaN(v) && v >= 0) {
                                onManualVoltageChange(row.designator, v);
                              }
                              setEditingCell(null);
                            }}
                            className="w-16 rounded border border-blue-500 bg-background px-1.5 py-0.5 text-xs font-mono text-right focus:outline-none"
                          />
                        ) : (
                          <span
                            className="inline-flex items-center gap-1 cursor-pointer group"
                            onClick={() => {
                              setEditingCell(row.designator);
                              setEditValue(
                                String(manual ?? row.operating_voltage_v ?? ""),
                              );
                            }}
                          >
                            <span className="font-mono text-xs">
                              {operatingV != null ? (
                                `${operatingV}V`
                              ) : (
                                <span className="text-muted-foreground/50 group-hover:text-muted-foreground text-[10px]">
                                  click to set
                                </span>
                              )}
                            </span>
                            {manual != null && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onManualVoltageChange(row.designator, null);
                                }}
                                className="text-muted-foreground hover:text-foreground"
                              >
                                <X className="h-3 w-3" />
                              </button>
                            )}
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-4 text-xs">
                        {source === "user" ? (
                          <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-blue-600 dark:text-blue-400 border-blue-500/40">
                            user
                          </Badge>
                        ) : source ? (
                          <span className="font-mono text-muted-foreground">{source}</span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="py-2 font-mono text-xs text-right">
                        {margin != null ? (
                          <span className={status === "pass" ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}>
                            {margin}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function CollaboratorsSection({ projectId }: { projectId: string }) {
  const { user } = useOptionalUser();
  const [collaborators, setCollaborators] = useState<Collaborator[]>([]);
  const [ownerUserId, setOwnerUserId] = useState<string>("");
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isOwner = ownerUserId ? ownerUserId === user?.id || ownerUserId === "local" : false;
  const isAdmin = user?.isAdmin ?? false;

  const reload = useCallback(() => {
    setLoading(true);
    fetchCollaborators(projectId)
      .then((data) => {
        setCollaborators(data.collaborators);
        setOwnerUserId(data.owner_user_id);
      })
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const handleAdd = async () => {
    if (!email.trim()) return;
    setAdding(true);
    setError(null);
    try {
      await addCollaborator(projectId, email.trim());
      setEmail("");
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add collaborator");
    } finally {
      setAdding(false);
    }
  };

  const handleRemove = async (userId: string) => {
    setError(null);
    try {
      await removeCollaborator(projectId, userId);
      setCollaborators((prev) => prev.filter((c) => c.user_id !== userId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to remove collaborator");
    }
  };

  const handleMakeOwner = async (userId: string) => {
    setError(null);
    try {
      await makeCollaboratorOwner(projectId, userId);
      reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to transfer ownership");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          Collaborators
          {collaborators.length > 0 && (
            <Badge variant="outline" className="text-xs">
              {collaborators.length}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {isOwner && (
          <div className="flex gap-2">
            <input
              type="email"
              placeholder="Add collaborator by email..."
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleAdd();
              }}
              className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <Button size="sm" onClick={handleAdd} disabled={adding || !email.trim()}>
              <UserPlus className="h-4 w-4 mr-1" />
              {adding ? "Adding..." : "Add"}
            </Button>
          </div>
        )}

        {error && (
          <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>
        )}

        {loading ? (
          <p className="text-sm text-muted-foreground">Loading...</p>
        ) : collaborators.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No collaborators yet.{isOwner ? " Add team members by email to give them access to this project." : ""}
          </p>
        ) : (
          <div className="space-y-2">
            {collaborators.map((collab) => (
              <div
                key={collab.user_id}
                className="flex items-center gap-3 rounded-md border border-border/50 p-3"
              >
                {collab.image_url ? (
                  <img
                    src={collab.image_url}
                    alt=""
                    className="h-8 w-8 rounded-full"
                  />
                ) : (
                  <div className="h-8 w-8 rounded-full bg-muted flex items-center justify-center">
                    <span className="text-xs text-muted-foreground">
                      {(collab.name?.[0] || collab.email?.[0] || "?").toUpperCase()}
                    </span>
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium truncate">
                      {collab.name || "Unknown"}
                    </p>
                    {collab.role === "owner" && (
                      <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                        Owner
                      </Badge>
                    )}
                  </div>
                  {collab.email && (
                    <p className="text-xs text-muted-foreground truncate">
                      {collab.email}
                    </p>
                  )}
                </div>
                {isAdmin && collab.role !== "owner" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs text-muted-foreground hover:text-amber-600 dark:hover:text-amber-500"
                    onClick={() => handleMakeOwner(collab.user_id)}
                    title="Promote to owner"
                  >
                    <Crown className="h-3.5 w-3.5 mr-1" />
                    Make owner
                  </Button>
                )}
                {(isOwner || isAdmin) && collab.role !== "owner" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0 text-muted-foreground hover:text-rose-600 dark:hover:text-rose-400"
                    onClick={() => handleRemove(collab.user_id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function SkippedComponentsSection({
  skipped,
}: {
  skipped?: SkippedComponent[];
}) {
  if (!skipped || skipped.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Skipped Components</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No components were skipped during analysis.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
          Skipped Components
          <Badge variant="outline" className="text-xs text-amber-600 dark:text-amber-400 border-amber-500/40">
            {skipped.length}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-muted-foreground mb-3">
          These components were skipped during analysis due to errors. The rest of the pipeline continued without them.
        </p>
        <div className="space-y-2">
          {skipped.map((item, i) => (
            <div
              key={`${item.identifier}-${item.stage}-${i}`}
              className="flex items-start gap-3 rounded-md border border-amber-500/20 bg-amber-500/5 p-3"
            >
              <AlertTriangle className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium font-mono">
                    {item.identifier}
                  </span>
                  <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                    {STAGE_LABELS[item.stage] ?? item.stage}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5 break-all">
                  {item.error}
                </p>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ReportVersionSection({
  pinscopeVersion,
}: {
  pinscopeVersion?: string | null;
}) {
  if (!pinscopeVersion) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Report Version</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            Generated with Pinscope
          </p>
          <span className="font-mono text-sm">v{pinscopeVersion}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function PipelineErrorBanner({
  projectId,
  message,
}: {
  projectId: string;
  message: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const detail = message ?? "Unknown error — no details were recorded.";
  const shareText = `PinscopeX project ${projectId} failed: ${detail}`;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(shareText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard blocked — fall back to no-op; user can still select the text
    }
  };

  return (
    <Card className="border-rose-500/30 bg-rose-500/5">
      <CardContent className="py-4">
        <div className="flex items-start gap-3">
          <OctagonX className="h-5 w-5 text-rose-600 dark:text-rose-400 mt-0.5 shrink-0" />
          <div className="flex-1 min-w-0 space-y-2">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium text-rose-700 dark:text-rose-300">
                Pipeline run failed
              </p>
              <Button
                size="sm"
                variant="outline"
                className="h-7 px-2 text-xs"
                onClick={handleCopy}
              >
                {copied ? (
                  <><Check className="h-3 w-3 mr-1" /> Copied</>
                ) : (
                  <><Copy className="h-3 w-3 mr-1" /> Copy details</>
                )}
              </Button>
            </div>
            <pre className="text-xs font-mono whitespace-pre-wrap break-all select-text bg-muted/60 dark:bg-black/30 rounded p-2 text-rose-800/90 dark:text-rose-200/90 border border-rose-500/20">
              {detail}
            </pre>
            <p className="text-xs text-muted-foreground">
              Project ID: <span className="font-mono">{projectId}</span>. Try
              running again — if the error persists,{" "}
              <Link href="/contact" className="underline hover:text-foreground">
                contact support
              </Link>{" "}
              and include the error above.
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
