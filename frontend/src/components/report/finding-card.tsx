"use client";

import { useState } from "react";
import { ChevronDown, FileText, Check, MessageSquare, Star, Flag, Cpu } from "lucide-react";
import { Checkbox } from "@base-ui/react/checkbox";
import { Button } from "@/components/ui/button";

import { StatusBadge } from "./status-badge";
import { FindingComments } from "./finding-comments";
import type { Finding, FindingComment, Collaborator } from "@/lib/types";
import { cn } from "@/lib/utils";

const BORDER_COLOR: Record<string, string> = {
  ERROR: "border-l-rose-500",
  WARNING: "border-l-amber-500",
  INFO: "border-l-blue-500",
};

interface FindingCardProps {
  finding: Finding;
  onViewReference: (finding: Finding) => void;
  checked?: boolean;
  onCheckedChange?: () => void;
  comments?: FindingComment[];
  projectId?: string;
  collaborators?: Collaborator[];
  currentUserId?: string;
  currentUserName?: string;
  onCommentAdded?: (comment: FindingComment) => void;
  onCommentDeleted?: (commentId: string, findingId: string) => void;
  onReportFinding?: (finding: Finding) => void;
  isReported?: boolean;
  defaultOpen?: boolean;
}

export function FindingCard({
  finding,
  onViewReference,
  checked,
  onCheckedChange,
  comments,
  projectId,
  collaborators,
  currentUserId,
  currentUserName,
  onCommentAdded,
  onCommentDeleted,
  onReportFinding,
  isReported,
  defaultOpen,
}: FindingCardProps) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  const commentCount = comments?.length ?? 0;
  const hasCommentSupport = !!(projectId && collaborators && onCommentAdded && onCommentDeleted);
  const expandable = !!finding.recommendation || (hasCommentSupport && !!finding.finding_id);

  return (
    <div
      role={expandable ? "button" : undefined}
      tabIndex={expandable ? 0 : undefined}
      aria-expanded={expandable ? open : undefined}
      onClick={expandable ? () => setOpen((o) => !o) : undefined}
      onKeyDown={
        expandable
          ? (e) => {
              if (e.currentTarget !== e.target) return;
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setOpen((o) => !o);
              }
            }
          : undefined
      }
      className={cn(
        "rounded-lg border border-border bg-card p-4 border-l-4 transition-colors",
        expandable && "cursor-pointer hover:bg-accent/30",
        BORDER_COLOR[finding.status]
      )}
    >
      <div className="flex items-start gap-3">
        {onCheckedChange && (
          <div onClick={(e) => e.stopPropagation()}>
            <Checkbox.Root
              checked={checked}
              onCheckedChange={() => onCheckedChange()}
              aria-label="Mark as reviewed"
              className="mt-0.5 h-5 w-5 shrink-0 rounded border border-border hover:border-muted-foreground data-[checked]:bg-emerald-500 data-[checked]:border-emerald-500 flex items-center justify-center cursor-pointer transition-colors"
            >
              <Checkbox.Indicator>
                <Check className="h-3.5 w-3.5 text-white" />
              </Checkbox.Indicator>
            </Checkbox.Root>
          </div>
        )}
        <StatusBadge status={finding.status} />
        <div className="flex-1 min-w-0 space-y-1.5">
          <p className="text-sm font-semibold leading-snug flex items-center gap-1.5">
            {commentCount > 0 && (
              <Star
                className="h-3.5 w-3.5 shrink-0 text-amber-600 fill-amber-600 dark:text-amber-400 dark:fill-amber-400"
                aria-label="Has comments"
              />
            )}
            <span>{finding.finding}</span>
          </p>
          {finding.why && (
            <p className="text-sm text-muted-foreground leading-relaxed">{finding.why}</p>
          )}

          <div className="flex items-center gap-2 pt-1">
            {finding.recommendation && (
              <span className="inline-flex items-center h-7 px-2 text-xs text-muted-foreground">
                <ChevronDown
                  className={cn("h-3.5 w-3.5 mr-1 transition-transform", open && "rotate-180")}
                />
                Recommendation
              </span>
            )}
            {hasCommentSupport && finding.finding_id && (
              <span className="inline-flex items-center h-7 px-2 text-xs text-muted-foreground gap-1">
                <MessageSquare className="h-3.5 w-3.5" />
                {commentCount > 0
                  ? `${commentCount} comment${commentCount > 1 ? "s" : ""}`
                  : "Comment"}
              </span>
            )}
            {onReportFinding && finding.finding_id && (
              <button
                type="button"
                className={cn(
                  "inline-flex items-center h-7 px-2 text-xs transition-colors",
                  isReported
                    ? "text-rose-500"
                    : "text-muted-foreground hover:text-amber-500"
                )}
                onClick={(e) => {
                  e.stopPropagation();
                  onReportFinding(finding);
                }}
                aria-label={isReported ? "Reported" : "Report this finding"}
              >
                <Flag className={cn("h-3.5 w-3.5", isReported && "fill-rose-500")} />
              </button>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
              onClick={(e) => {
                e.stopPropagation();
                onViewReference(finding);
              }}
            >
              <FileText className="h-3.5 w-3.5 mr-1" />
              {finding.source_page ? `p.${finding.source_page}` : "ref"}
            </Button>
            {finding.finding_id && (
              <span className="font-mono text-[11px] text-muted-foreground">
                {finding.finding_id}
              </span>
            )}
            {finding.source && finding.source !== "review" && (
              <span
                title="Found by a deterministic rule check, not the datasheet review"
                className="inline-flex items-center gap-1 rounded border border-blue-500/30 bg-blue-500/10 px-1.5 py-0.5 text-[10px] font-medium text-blue-700 dark:text-blue-300"
              >
                <Cpu className="h-3 w-3" />
                Automated check
              </span>
            )}
          </div>

          {open && (
            <div onClick={(e) => e.stopPropagation()}>
              {finding.recommendation && (
                <p className="text-sm text-muted-foreground mt-2 pl-1 border-l-2 border-muted leading-relaxed">
                  {finding.recommendation}
                </p>
              )}
              {hasCommentSupport && finding.finding_id && (
                <FindingComments
                  findingId={finding.finding_id}
                  comments={comments ?? []}
                  projectId={projectId}
                  collaborators={collaborators}
                  currentUserId={currentUserId ?? ""}
                  currentUserName={currentUserName ?? "User"}
                  onCommentAdded={onCommentAdded}
                  onCommentDeleted={onCommentDeleted}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
