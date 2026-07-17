"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import type { Finding } from "@/lib/types";
import { getFindingKey } from "@/lib/utils";

function storageKey(projectId: string) {
  return `pinscopex:reviewed-findings:${projectId}`;
}

export function useReviewedFindings(projectId: string, findings: Finding[]) {
  const validKeys = useMemo(() => {
    const keys = new Set<string>();
    findings.forEach((f, i) => keys.add(getFindingKey(f, i)));
    return keys;
  }, [findings]);

  const [reviewedIds, setReviewedIds] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const stored = localStorage.getItem(storageKey(projectId));
      if (!stored) return new Set();
      const arr: string[] = JSON.parse(stored);
      // Load all stored keys as-is. Pruning here would wipe everything
      // on refresh, since findings load asynchronously and validKeys is
      // empty on first render.
      return new Set(arr);
    } catch {
      return new Set();
    }
  });

  // Skip the first persistence write so we never clobber stored state
  // before the report's findings have loaded.
  const hydrated = useRef(false);
  useEffect(() => {
    if (!hydrated.current) {
      hydrated.current = true;
      return;
    }
    localStorage.setItem(
      storageKey(projectId),
      JSON.stringify(Array.from(reviewedIds))
    );
  }, [reviewedIds, projectId]);

  // Prune stale keys only once findings are actually loaded.
  useEffect(() => {
    if (findings.length === 0) return;
    setReviewedIds((prev) => {
      const next = new Set(
        Array.from(prev).filter((k) => validKeys.has(k))
      );
      return next.size === prev.size ? prev : next;
    });
  }, [validKeys, findings.length]);

  const toggleReviewed = useCallback((key: string) => {
    setReviewedIds((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const isReviewed = useCallback(
    (key: string) => reviewedIds.has(key),
    [reviewedIds]
  );

  return { reviewedIds, toggleReviewed, isReviewed, reviewedCount: reviewedIds.size };
}
