"use client";

import { useState, useEffect } from "react";

/**
 * Lightweight hook that reads the reviewed-findings count from localStorage
 * without needing the full findings array. Used on the dashboard where
 * individual reports aren't loaded.
 */
export function useReviewedCount(projectId: string): number {
  const [count, setCount] = useState(0);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(`pinscopex:reviewed-findings:${projectId}`);
      if (stored) {
        const arr: unknown[] = JSON.parse(stored);
        setCount(arr.length);
      }
    } catch {
      // ignore
    }
  }, [projectId]);

  return count;
}
