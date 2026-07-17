"use client";

import { useEffect, useRef } from "react";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PdfViewerPanel } from "@/components/pdf/pdf-viewer-panel";
import { FindingCard } from "./finding-card";
import type { Finding, FindingComment, Collaborator, Component } from "@/lib/types";
import { subtypeLabel } from "@/lib/utils";

interface FindingFocusViewProps {
  finding: Finding;
  component?: Component;
  mpn: string;
  page: number;
  quote?: string;
  projectId: string;
  onExit: () => void;
  escapeDisabled?: boolean;
  onViewReference: (finding: Finding) => void;
  checked: boolean;
  onCheckedChange: () => void;
  comments?: FindingComment[];
  collaborators?: Collaborator[];
  currentUserId?: string;
  currentUserName?: string;
  onCommentAdded?: (comment: FindingComment) => void;
  onCommentDeleted?: (commentId: string, findingId: string) => void;
  onReportFinding?: (finding: Finding) => void;
  isReported?: boolean;
}

export function FindingFocusView({
  finding,
  component,
  mpn,
  page,
  quote,
  projectId,
  onExit,
  escapeDisabled,
  onViewReference,
  checked,
  onCheckedChange,
  comments,
  collaborators,
  currentUserId,
  currentUserName,
  onCommentAdded,
  onCommentDeleted,
  onReportFinding,
  isReported,
}: FindingFocusViewProps) {
  const backRef = useRef<HTMLButtonElement>(null);

  // The reference button that opened this view just got hidden — without this,
  // keyboard focus drops to <body>.
  useEffect(() => {
    backRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || escapeDisabled || e.defaultPrevented) return;
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      )
        return;
      onExit();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [escapeDisabled, onExit]);

  return (
    <div className="flex flex-col lg:flex-row lg:items-start gap-6">
      <div className="flex-1 min-w-0 w-full space-y-4">
        <Button ref={backRef} variant="ghost" size="sm" onClick={onExit}>
          <ArrowLeft className="h-4 w-4 mr-1" />
          All findings
        </Button>
        <div className="flex items-center gap-3">
          <Badge variant="secondary" className="font-mono text-sm">
            {finding.designator}
          </Badge>
          <span className="text-sm text-muted-foreground font-mono">{mpn}</span>
          {component?.component_subtype && (
            <span className="text-xs text-muted-foreground">
              {subtypeLabel(component.component_subtype)}
            </span>
          )}
        </div>
        <FindingCard
          finding={finding}
          onViewReference={onViewReference}
          checked={checked}
          onCheckedChange={onCheckedChange}
          comments={comments}
          projectId={projectId}
          collaborators={collaborators}
          currentUserId={currentUserId}
          currentUserName={currentUserName}
          onCommentAdded={onCommentAdded}
          onCommentDeleted={onCommentDeleted}
          onReportFinding={onReportFinding}
          isReported={isReported}
          defaultOpen
        />
      </div>
      <div className="w-full lg:w-auto lg:shrink-0 lg:sticky lg:top-6 h-[70vh] lg:h-[calc(100vh-3rem)] rounded-lg border border-border bg-card overflow-hidden">
        <PdfViewerPanel
          projectId={projectId}
          mpn={mpn}
          initialPage={page}
          highlightQuote={quote}
          onClose={onExit}
        />
      </div>
    </div>
  );
}
