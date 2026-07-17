"use client";

import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { useCallback, useMemo } from "react";
import { ComponentGroup } from "./component-group";
import { ReportFilters } from "./report-filters";
import { ReviewedSection } from "./reviewed-section";
import type { Finding, FindingComment, FindingStatus, DesignGraph, Collaborator } from "@/lib/types";
import { groupBy, getFindingKey } from "@/lib/utils";

interface FindingsListProps {
  findings: Finding[];
  graph: DesignGraph;
  onViewReference: (finding: Finding) => void;
  projectId: string;
  isReviewed: (key: string) => boolean;
  toggleReviewed: (key: string) => void;
  comments?: Record<string, FindingComment[]>;
  collaborators?: Collaborator[];
  currentUserId?: string;
  currentUserName?: string;
  onCommentAdded?: (comment: FindingComment) => void;
  onCommentDeleted?: (commentId: string, findingId: string) => void;
  onReportFinding?: (finding: Finding) => void;
  reportedFindingIds?: Set<string>;
}

export function FindingsList({ findings, graph, onViewReference, projectId, isReviewed, toggleReviewed, comments, collaborators, currentUserId, currentUserName, onCommentAdded, onCommentDeleted, onReportFinding, reportedFindingIds }: FindingsListProps) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const findingKeyMap = useMemo(() => {
    const map = new Map<Finding, string>();
    findings.forEach((f, i) => map.set(f, getFindingKey(f, i)));
    return map;
  }, [findings]);

  const statusParam = searchParams.get("status");
  const componentParam = searchParams.get("component");
  const searchParam = searchParams.get("q") ?? "";

  const statusFilters = useMemo(() => {
    if (!statusParam) return new Set<FindingStatus>(["ERROR", "WARNING", "INFO"]);
    return new Set(statusParam.split(",") as FindingStatus[]);
  }, [statusParam]);

  const updateParams = useCallback(
    (updates: Record<string, string | null>) => {
      const params = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(updates)) {
        if (value === null || value === "") params.delete(key);
        else params.set(key, value);
      }
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [searchParams, router, pathname]
  );

  const toggleStatus = useCallback(
    (status: FindingStatus) => {
      const next = new Set(statusFilters);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      const isDefault = next.size === 3 && next.has("ERROR") && next.has("WARNING") && next.has("INFO");
      const value = isDefault ? null : Array.from(next).join(",");
      updateParams({ status: value });
    },
    [statusFilters, updateParams]
  );

  const matchesFilters = useCallback(
    (f: Finding) => {
      if (!statusFilters.has(f.status)) return false;
      if (componentParam && componentParam !== "all" && f.designator !== componentParam)
        return false;
      const q = searchParam.toLowerCase();
      if (q && !f.finding.toLowerCase().includes(q) && !(f.why ?? "").toLowerCase().includes(q))
        return false;
      return true;
    },
    [statusFilters, componentParam, searchParam]
  );

  const filtered = useMemo(() => {
    return findings.filter((f) => {
      if (!matchesFilters(f)) return false;
      const key = findingKeyMap.get(f)!;
      return !isReviewed(key);
    });
  }, [findings, matchesFilters, findingKeyMap, isReviewed]);

  const reviewedFindings = useMemo(() => {
    return findings.filter((f) => {
      if (!matchesFilters(f)) return false;
      const key = findingKeyMap.get(f)!;
      return isReviewed(key);
    });
  }, [findings, matchesFilters, findingKeyMap, isReviewed]);

  const grouped = useMemo(() => groupBy(filtered, (f) => f.designator), [filtered]);
  const designators = useMemo(() => {
    const all = [...new Set(findings.map((f) => f.designator))];
    const byDesignator = groupBy(findings, (f) => f.designator);
    const worst = (d: string): number => {
      const group = byDesignator[d] ?? [];
      if (group.some((f) => f.status === "ERROR")) return 0;
      if (group.some((f) => f.status === "WARNING")) return 1;
      return 2;
    };
    return all.sort((a, b) => worst(a) - worst(b) || a.localeCompare(b));
  }, [findings]);

  return (
    <div className="space-y-4">
      <ReportFilters
        statusFilters={statusFilters}
        onToggleStatus={toggleStatus}
        componentFilter={componentParam ?? "all"}
        onComponentChange={(v) => updateParams({ component: v === "all" ? null : v })}
        search={searchParam}
        onSearchChange={(v) => updateParams({ q: v || null })}
        designators={designators}
      />
      <div className="space-y-1">
        {designators
          .filter((d) => grouped[d])
          .map((d) => (
            <ComponentGroup
              key={d}
              designator={d}
              findings={grouped[d]}
              component={graph.components[d]}
              onViewReference={onViewReference}
              findingKeys={findingKeyMap}
              isReviewed={isReviewed}
              onToggleReviewed={toggleReviewed}
              comments={comments}
              projectId={projectId}
              collaborators={collaborators}
              currentUserId={currentUserId}
              currentUserName={currentUserName}
              onCommentAdded={onCommentAdded}
              onCommentDeleted={onCommentDeleted}
              onReportFinding={onReportFinding}
              reportedFindingIds={reportedFindingIds}
            />
          ))}
        {filtered.length === 0 && reviewedFindings.length === 0 && findings.length > 0 && (
          <p className="text-sm text-muted-foreground text-center py-12">
            No findings match your filters.
          </p>
        )}
      </div>
      {reviewedFindings.length > 0 && (
        <ReviewedSection
          findings={reviewedFindings}
          findingKeys={findingKeyMap}
          onToggleReviewed={toggleReviewed}
          onViewReference={onViewReference}
        />
      )}
    </div>
  );
}
