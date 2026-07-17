"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type { PipelineStep } from "@/lib/types";
import { pipelineEventsUrl } from "@/lib/api";
import { useOptionalAuth } from "@/hooks/use-optional-auth";

// Detail values that indicate a cached/skipped extraction (not new work)
const CACHED_DETAILS = new Set([
  "already extracted",
  "from library",
  "no datasheet (optional)",
  "specs already cached",
  "specs from library",
  "already resolved",
  "all passives already resolved",
]);

// Single source of truth for stage order and display metadata.
// To add, remove, or reorder a stage, edit this array — STAGE_INDEX and
// createInitialSteps are derived from it automatically.
const PIPELINE_STAGES = [
  { id: "bom_parse",          title: "Parse BOM",                  description: "Classify components from BOM",                           initialSubsteps: [{ key: "parsing-bom",     label: "Parsing BOM"          }] },
  { id: "ic_extraction",      title: "IC Datasheet Extraction",    description: "Extract pin tables from datasheets",                     initialSubsteps: [] },
  { id: "simple_extraction",  title: "Component Specs Extraction", description: "Extract specifications from discrete/simple datasheets",  initialSubsteps: [] },
  { id: "passive_extraction", title: "Passive Pattern Extraction", description: "Resolve passive component values",                        initialSubsteps: [] },
  { id: "graph_build",        title: "Build Design Graph",         description: "Combine netlist, BOM, and extracted data",               initialSubsteps: [{ key: "building-graph",  label: "Building graph"       }] },
  { id: "validation",         title: "Review Design",              description: "Review each IC against its datasheet",                    initialSubsteps: [] },
] as const;

// Both derived — no separate maintenance needed
const STAGE_INDEX: Record<string, number> = Object.fromEntries(
  PIPELINE_STAGES.map((s, i) => [s.id, i])
);

function createInitialSteps(): PipelineStep[] {
  return PIPELINE_STAGES.map((s) => ({
    title: s.title,
    description: s.description,
    status: "pending" as const,
    substeps: s.initialSubsteps.map((ss) => ({ ...ss, status: "pending" as const })),
  }));
}

export interface AutoTopupFailure {
  reason: string;
  amount_usd?: number;
  ts: number;
}

export interface PipelinePaused {
  reason?: string;
  last_completed?: string | null;
  stage?: string | null;
  unit_id?: string | null;
  completed_review_refs?: string[];
  pending_review_refs?: string[];
  ts: number;
}

export interface CreditsUpdate {
  credits_spent: number;
  balance_after: number;
  delta: number;
  stage?: string | null;
  unit_id?: string | null;
  ts: number;
}

export function usePipelineProgress(projectId: string | null) {
  const [steps, setSteps] = useState<PipelineStep[]>(createInitialSteps);
  const [done, setDone] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<Record<string, number> | null>(null);
  const [autoTopupFailure, setAutoTopupFailure] =
    useState<AutoTopupFailure | null>(null);
  const [credits, setCredits] = useState<CreditsUpdate | null>(null);
  const [paused, setPaused] = useState<PipelinePaused | null>(null);
  // False while the project is queued (worker hasn't booted yet). Flips
  // to true on the first step_update so the UI can show a "starting…"
  // spinner instead of an empty stepper.
  const [started, setStarted] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const terminalRef = useRef(false); // true once pipeline_complete/error/cancelled received
  const { getToken } = useOptionalAuth();

  const handleEvent = useCallback((event: MessageEvent) => {
    const eventType = event.type || "message";
    if (eventType === "heartbeat") return;

    let data: Record<string, unknown>;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }

    if (eventType === "pipeline_complete" || event.lastEventId === "pipeline_complete") {
      setSummary(data.summary as Record<string, number>);
      setDone(true);
      terminalRef.current = true;
      esRef.current?.close();
      return;
    }

    if (eventType === "pipeline_cancelled" || event.lastEventId === "pipeline_cancelled") {
      setCancelled(true);
      setDone(true);
      terminalRef.current = true;
      esRef.current?.close();
      return;
    }

    if (eventType === "pipeline_error" || event.lastEventId === "pipeline_error") {
      setError(data.error as string);
      setDone(true);
      terminalRef.current = true;
      esRef.current?.close();
      return;
    }

    if (eventType === "pipeline_paused" || event.lastEventId === "pipeline_paused") {
      setPaused({
        reason: (data.reason as string | undefined) ?? "insufficient_credits",
        last_completed: (data.last_completed as string | null | undefined) ?? null,
        stage: (data.stage as string | null | undefined) ?? null,
        unit_id: (data.unit_id as string | null | undefined) ?? null,
        completed_review_refs: (data.completed_review_refs as string[] | undefined) ?? [],
        pending_review_refs: (data.pending_review_refs as string[] | undefined) ?? [],
        ts: Date.now(),
      });
      terminalRef.current = true;
      esRef.current?.close();
      return;
    }

    if (eventType === "credits_update") {
      setCredits({
        credits_spent: Number(data.credits_spent) || 0,
        balance_after: Number(data.balance_after) || 0,
        delta: Number(data.delta) || 0,
        stage: (data.stage as string | null | undefined) ?? null,
        unit_id: (data.unit_id as string | null | undefined) ?? null,
        ts: Date.now(),
      });
      return;
    }

    if (eventType === "auto_topup_failed") {
      setAutoTopupFailure({
        reason: (data.reason as string) ?? "unknown",
        amount_usd: data.amount_usd as number | undefined,
        ts: Date.now(),
      });
      return;
    }

    // step_update — first one flips `started` so the queued spinner clears.
    setStarted(true);
    const stage = data.stage as string;
    const substep = data.substep as string | undefined;
    const status = data.status as "pending" | "running" | "complete" | "failed";
    const detail = data.detail as string | undefined;

    setSteps((prev) => {
      const next = prev.map((s) => ({
        ...s,
        substeps: s.substeps.map((ss) => ({ ...ss })),
      }));

      const idx = STAGE_INDEX[stage];
      if (idx === undefined) return next;
      const step = next[idx];

      if (!substep) {
        // Stage-level update
        if (status === "running") {
          step.status = "running";
          const totalNew = data.total_new as number | undefined;
          if (typeof totalNew === "number") step.totalNew = totalNew;
        } else if (status === "complete") {
          step.status = "complete";
          // Mark all substeps as complete when the stage completes
          for (const ss of step.substeps) {
            ss.status = "complete";
          }
        }
        // Also update single substeps for simple stages
        if (step.substeps.length === 1) {
          step.substeps[0].status = status === "failed" ? "complete" : status;
        }
      } else {
        // Substep-level: find by stable key, or create
        let sub = step.substeps.find((s) => s.key === substep);
        if (!sub) {
          sub = { key: substep, label: substep, status: "pending" };
          step.substeps.push(sub);
        }
        sub.status = status === "failed" ? "complete" : status;
        if (detail) sub.label = `${substep} — ${detail}`;

        // Track cached vs new for progress counter
        if (status === "running") {
          sub.cached = false;
        } else if ((status === "complete" || status === "failed") && detail && CACHED_DETAILS.has(detail)) {
          sub.cached = true;
        }

        // Mark step as running if any substep is running
        if (step.substeps.some((s) => s.status === "running")) {
          step.status = "running";
        }
        if (step.substeps.every((s) => s.status === "complete")) {
          step.status = "complete";
        }
      }

      return next;
    });
  }, []);

  useEffect(() => {
    if (!projectId) return;

    let es: EventSource | null = null;
    let retries = 0;
    const MAX_RETRIES = 50; // ~25 minutes of reconnections at 30s intervals
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false; // true once cleanup runs or terminal event received

    async function connect() {
      if (closed) return;

      // Close previous connection if any
      if (es) {
        es.close();
        es = null;
      }

      const token = await getToken();
      const baseUrl = pipelineEventsUrl(projectId!);
      const url = token ? `${baseUrl}?token=${token}` : baseUrl;

      es = new EventSource(url);
      esRef.current = es;

      for (const eventName of ["step_update", "pipeline_complete", "pipeline_error", "pipeline_cancelled", "pipeline_paused", "auto_topup_failed", "credits_update", "heartbeat"]) {
        es.addEventListener(eventName, (event: MessageEvent) => {
          // Reset retry counter on any successful message
          retries = 0;
          handleEvent(event);
        });
      }

      es.onerror = () => {
        if (closed || terminalRef.current) return;
        // Connection lost — reconnect with a fresh token
        es?.close();
        es = null;
        esRef.current = null;

        if (retries >= MAX_RETRIES) {
          setError("Lost connection to pipeline. Please refresh the page to reconnect.");
          setDone(true);
          return;
        }

        retries++;
        reconnectTimer = setTimeout(connect, 30_000);
      };
    }

    connect();

    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
      esRef.current = null;
    };
  }, [projectId, getToken, handleEvent]);

  return { steps, done, cancelled, error, summary, autoTopupFailure, credits, started, paused };
}
