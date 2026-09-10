"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { setFindingReview } from "@/lib/api";
import type { FindingReview, FindingReviewState } from "@/lib/types";

const STATES: { value: FindingReviewState; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "accepted", label: "Accepted (ECO)" },
  { value: "false_positive", label: "False positive" },
  { value: "wontfix", label: "Won't fix" },
];

export function FindingReviewControls({
  projectId,
  findingId,
  review,
  userName,
  onSaved,
}: {
  projectId: string;
  findingId: string;
  review?: FindingReview;
  userName: string;
  onSaved: (findingId: string, review: FindingReview) => void;
}) {
  const [state, setState] = useState<FindingReviewState>(review?.state ?? "open");
  const [reason, setReason] = useState(review?.reason ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const saved = await setFindingReview(projectId, findingId, state, reason, userName);
      onSaved(findingId, saved.state === "open" ? { ...saved, state: "open", reason: "" } : saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 space-y-2" onClick={(e) => e.stopPropagation()}>
      <div className="flex flex-wrap items-center gap-2">
        <select
          className="h-8 rounded-lg border border-input bg-transparent px-2 text-xs"
          value={state}
          onChange={(e) => setState(e.target.value as FindingReviewState)}
        >
          {STATES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <Input
          className="h-8 min-w-[160px] flex-1 text-xs"
          placeholder={state === "open" ? "Reason optional when open" : "Reason (required)"}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <Button size="sm" variant="outline" onClick={save} disabled={busy}>
          Save
        </Button>
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
