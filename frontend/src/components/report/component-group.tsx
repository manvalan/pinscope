"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Badge } from "@/components/ui/badge";
import { FindingCard } from "./finding-card";
import { StatusBadge } from "./status-badge";
import type { Finding, FindingComment, Collaborator, Component } from "@/lib/types";
import { cn, sortFindings, subtypeLabel } from "@/lib/utils";

interface ComponentGroupProps {
  designator: string;
  findings: Finding[];
  component?: Component;
  onViewReference: (finding: Finding) => void;
  findingKeys?: Map<Finding, string>;
  isReviewed?: (key: string) => boolean;
  onToggleReviewed?: (key: string) => void;
  comments?: Record<string, FindingComment[]>;
  projectId?: string;
  collaborators?: Collaborator[];
  currentUserId?: string;
  currentUserName?: string;
  onCommentAdded?: (comment: FindingComment) => void;
  onCommentDeleted?: (commentId: string, findingId: string) => void;
  onReportFinding?: (finding: Finding) => void;
  reportedFindingIds?: Set<string>;
}

export function ComponentGroup({ designator, findings, component, onViewReference, findingKeys, isReviewed, onToggleReviewed, comments, projectId, collaborators, currentUserId, currentUserName, onCommentAdded, onCommentDeleted, onReportFinding, reportedFindingIds }: ComponentGroupProps) {
  const [open, setOpen] = useState(true);
  const sorted = sortFindings(findings);

  const errorCount = findings.filter((f) => f.status === "ERROR").length;
  const warnCount = findings.filter((f) => f.status === "WARNING").length;
  const infoCount = findings.filter((f) => f.status === "INFO").length;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-3 w-full py-3 group">
        <ChevronRight
          className={cn("h-4 w-4 text-muted-foreground transition-transform", open && "rotate-90")}
        />
        <Badge variant="secondary" className="font-mono text-sm">
          {designator}
        </Badge>
        {component?.mpn && (
          <span className="text-sm text-muted-foreground font-mono">{component.mpn}</span>
        )}
        {component?.component_subtype && (
          <span className="text-xs text-muted-foreground">
            {subtypeLabel(component.component_subtype)}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {errorCount > 0 && <StatusBadge status="ERROR" />}
          {warnCount > 0 && <StatusBadge status="WARNING" />}
          {infoCount > 0 && <StatusBadge status="INFO" />}
          <span className="text-xs text-muted-foreground ml-1">
            {findings.length} {findings.length === 1 ? "finding" : "findings"}
          </span>
        </div>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="space-y-3 pl-7 pb-4">
          {sorted.map((f, i) => {
            const key = findingKeys?.get(f);
            return (
              <FindingCard
                key={key ?? i}
                finding={f}
                onViewReference={onViewReference}
                checked={key && isReviewed ? isReviewed(key) : undefined}
                onCheckedChange={key && onToggleReviewed ? () => onToggleReviewed(key) : undefined}
                comments={f.finding_id ? comments?.[f.finding_id] : undefined}
                projectId={projectId}
                collaborators={collaborators}
                currentUserId={currentUserId}
                currentUserName={currentUserName}
                onCommentAdded={onCommentAdded}
                onCommentDeleted={onCommentDeleted}
                onReportFinding={onReportFinding}
                isReported={!!(f.finding_id && reportedFindingIds?.has(f.finding_id))}
              />
            );
          })}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
