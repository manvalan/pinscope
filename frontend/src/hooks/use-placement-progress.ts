"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type { PipelineStep } from "@/lib/types";
import { placementEventsUrl } from "@/lib/api";
import { useOptionalAuth } from "@/hooks/use-optional-auth";

const PLACEMENT_STAGES = [
  {
    id: "ensure_graph",
    title: "Ensure design graph",
    description: "Reuse or build design_graph.json",
  },
  {
    id: "classify",
    title: "Classify topology",
    description: "Domains, IC groups, satellite roles",
  },
  {
    id: "write_plan",
    title: "Write placement plan",
    description: "Save placement_plan.json (no millimetres)",
  },
] as const;

const STAGE_INDEX: Record<string, number> = Object.fromEntries(
  PLACEMENT_STAGES.map((s, i) => [s.id, i]),
);

function createInitialSteps(): PipelineStep[] {
  return PLACEMENT_STAGES.map((s) => ({
    title: s.title,
    description: s.description,
    status: "pending" as const,
    substeps: [],
  }));
}

export function usePlacementProgress(projectId: string | null, enabled = true) {
  const [steps, setSteps] = useState<PipelineStep[]>(createInitialSteps);
  const [done, setDone] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<{ domains?: number; groups?: number } | null>(null);
  const [started, setStarted] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const terminalRef = useRef(false);
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

    if (eventType === "placement_complete") {
      setSummary({
        domains: Number(data.domains) || 0,
        groups: Number(data.groups) || 0,
      });
      setDone(true);
      terminalRef.current = true;
      esRef.current?.close();
      return;
    }

    if (eventType === "placement_cancelled") {
      setCancelled(true);
      setDone(true);
      terminalRef.current = true;
      esRef.current?.close();
      return;
    }

    if (eventType === "placement_error") {
      setError((data.error as string) || "Placement failed");
      setDone(true);
      terminalRef.current = true;
      esRef.current?.close();
      return;
    }

    if (eventType !== "placement_step_update") return;

    setStarted(true);
    const stage = data.stage as string;
    const status = data.status as "pending" | "running" | "complete" | "failed";
    const detail = data.detail as string | undefined;

    setSteps((prev) => {
      const next = prev.map((s) => ({ ...s, substeps: [...s.substeps] }));
      const idx = STAGE_INDEX[stage];
      if (idx === undefined) return next;
      const step = next[idx];
      if (status === "running") {
        step.status = "running";
        if (detail) step.description = detail;
      } else if (status === "complete") {
        step.status = "complete";
        if (detail) step.description = detail;
      }
      return next;
    });
  }, []);

  useEffect(() => {
    if (!projectId || !enabled) return;

    let es: EventSource | null = null;
    let retries = 0;
    const MAX_RETRIES = 50;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    async function connect() {
      if (closed) return;
      if (es) {
        es.close();
        es = null;
      }

      const token = await getToken();
      const baseUrl = placementEventsUrl(projectId!);
      const url = token ? `${baseUrl}?token=${token}` : baseUrl;

      es = new EventSource(url);
      esRef.current = es;

      for (const eventName of [
        "placement_step_update",
        "placement_complete",
        "placement_error",
        "placement_cancelled",
        "heartbeat",
      ]) {
        es.addEventListener(eventName, (event: MessageEvent) => {
          retries = 0;
          handleEvent(event);
        });
      }

      es.onerror = () => {
        if (closed || terminalRef.current) return;
        es?.close();
        es = null;
        esRef.current = null;
        if (retries >= MAX_RETRIES) {
          setError("Lost connection to placement pipeline. Refresh to reconnect.");
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
  }, [projectId, enabled, getToken, handleEvent]);

  return { steps, done, cancelled, error, summary, started };
}
