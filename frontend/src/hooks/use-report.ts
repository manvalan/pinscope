"use client";

import { useState, useEffect } from "react";
import type { ValidationReport, DesignGraph } from "@/lib/types";
import { fetchReport, fetchGraph } from "@/lib/api";

export function useReport(projectId: string) {
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [graph, setGraph] = useState<DesignGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchReport(projectId), fetchGraph(projectId)])
      .then(([r, g]) => {
        setReport(r);
        setGraph(g);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [projectId]);

  return { report, graph, loading, error };
}
