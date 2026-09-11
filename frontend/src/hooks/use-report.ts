"use client";

import { useState, useEffect } from "react";
import type { ValidationReport, DesignGraph } from "@/lib/types";
import { fetchReport, fetchGraph } from "@/lib/api";

function describeLoadError(e: unknown): string {
  if (e instanceof TypeError) {
    return "Could not reach the Pinscope API. Check that the backend is running and that /api is proxied to it.";
  }
  return e instanceof Error ? e.message : "Failed to load report";
}

export function useReport(projectId: string) {
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [graph, setGraph] = useState<DesignGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      let lastErr: unknown;
      for (let attempt = 0; attempt < 4; attempt++) {
        try {
          const [r, g] = await Promise.all([
            fetchReport(projectId),
            fetchGraph(projectId),
          ]);
          if (!cancelled) {
            setReport(r);
            setGraph(g);
          }
          return;
        } catch (e) {
          lastErr = e;
          const msg = e instanceof Error ? e.message : "";
          const retryable = msg.includes("not found") || e instanceof TypeError;
          if (!retryable || attempt === 3) break;
          await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
        }
      }
      if (!cancelled) setError(describeLoadError(lastErr));
    })().finally(() => {
      if (!cancelled) setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [projectId]);

  return { report, graph, loading, error };
}
