"use client";

import { use, useState, useCallback, useEffect, useLayoutEffect, useMemo, useRef, Suspense } from "react";
import { Download } from "lucide-react";
import { useOptionalUser } from "@/hooks/use-optional-auth";
import { useReport } from "@/hooks/use-report";
import { useReviewedFindings } from "@/hooks/use-reviewed-findings";
import { ReportSummary } from "@/components/report/report-summary";
import { FindingsList } from "@/components/report/findings-list";
import { FindingFocusView } from "@/components/report/finding-focus-view";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Toast, useToast } from "@/components/ui/toast";
import { FeedbackDialog } from "@/components/feedback/feedback-dialog";
import { fetchCollaborators, fetchProject, fetchMyFeedback } from "@/lib/api";
import { exportReportToExcel } from "@/lib/report-export";
import { cn, getFindingKey } from "@/lib/utils";
import type { Finding, FindingComment, Collaborator } from "@/lib/types";

interface FocusState {
  key: string;
  finding: Finding;
  mpn: string;
  page: number;
  quote?: string;
}

function ReportContent({ projectId }: { projectId: string }) {
  const { report, graph, loading, error } = useReport(projectId);
  const { user } = useOptionalUser();
  const [focus, setFocus] = useState<FocusState | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const savedScrollRef = useRef(0);
  const prevInFocusRef = useRef(false);
  const [collaborators, setCollaborators] = useState<Collaborator[]>([]);
  const [comments, setComments] = useState<Record<string, FindingComment[]>>({});
  const [creditsSpent, setCreditsSpent] = useState<number | undefined>();
  const [projectName, setProjectName] = useState<string>("");
  const [feedbackFinding, setFeedbackFinding] = useState<Finding | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [reportedFindingIds, setReportedFindingIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetchMyFeedback()
      .then((tickets) => {
        const ids = new Set<string>();
        for (const t of tickets) {
          if (t.finding_id && t.project_id === projectId) ids.add(t.finding_id);
        }
        setReportedFindingIds(ids);
      })
      .catch(() => {});
  }, [projectId]);

  useEffect(() => {
    fetchProject(projectId)
      .then((p) => {
        setCreditsSpent(p.creditsSpent);
        setProjectName(p.name);
      })
      .catch(() => {});
  }, [projectId]);

  const { reviewedCount, isReviewed, toggleReviewed } = useReviewedFindings(
    projectId,
    report?.findings ?? []
  );

  const { toast, show: showToast } = useToast();

  const findingByKey = useMemo(() => {
    const map = new Map<string, Finding>();
    (report?.findings ?? []).forEach((f, i) => map.set(getFindingKey(f, i), f));
    return map;
  }, [report?.findings]);

  const keyByFinding = useMemo(() => {
    const map = new Map<Finding, string>();
    (report?.findings ?? []).forEach((f, i) => map.set(f, getFindingKey(f, i)));
    return map;
  }, [report?.findings]);

  const handleToggleReviewed = useCallback(
    (key: string) => {
      const wasReviewed = isReviewed(key);
      toggleReviewed(key);
      if (!wasReviewed) {
        const finding = findingByKey.get(key);
        const label = finding?.finding_id ?? finding?.designator ?? "Rule";
        showToast(`${label} marked as reviewed`);
      }
    },
    [isReviewed, toggleReviewed, findingByKey, showToast]
  );

  // Load collaborators
  useEffect(() => {
    fetchCollaborators(projectId)
      .then((data) => setCollaborators(data.collaborators))
      .catch(() => {});
  }, [projectId]);

  // Sync comments from report
  useEffect(() => {
    if (report?.comments) {
      setComments(report.comments);
    }
  }, [report]);

  const handleCommentAdded = useCallback((comment: FindingComment) => {
    setComments((prev) => ({
      ...prev,
      [comment.finding_id]: [...(prev[comment.finding_id] ?? []), comment],
    }));
  }, []);

  const handleCommentDeleted = useCallback((commentId: string, findingId: string) => {
    setComments((prev) => {
      const list = (prev[findingId] ?? []).filter((c) => c.comment_id !== commentId);
      const next = { ...prev };
      if (list.length === 0) {
        delete next[findingId];
      } else {
        next[findingId] = list;
      }
      return next;
    });
  }, []);

  const handleReportFinding = useCallback((finding: Finding) => {
    setFeedbackFinding(finding);
    setFeedbackOpen(true);
  }, []);

  const handleViewReference = useCallback(
    (finding: Finding) => {
      if (!graph) return;
      // source_page/source_quote may cite a *connected* component's datasheet
      // (evidence pulled from a neighbor's excerpt during review). Open that
      // datasheet, not the component under review — else the page is in the
      // wrong PDF (and often past its end, so nothing renders).
      const sourceDesignator = finding.source_designator ?? finding.designator;
      const mpn =
        graph.components[sourceDesignator]?.mpn ??
        graph.components[finding.designator]?.mpn;
      if (!mpn) {
        showToast(`No datasheet on file for ${sourceDesignator}`);
        return;
      }
      // Save the list scroll position only when entering focus mode, not when
      // swapping between findings while already focused.
      if (!focus) {
        savedScrollRef.current = rootRef.current?.closest("main")?.scrollTop ?? 0;
      }
      setFocus({
        key: keyByFinding.get(finding) ?? finding.finding_id ?? finding.designator,
        finding,
        mpn,
        page: finding.source_page ?? 1,
        quote: finding.source_quote,
      });
    },
    [graph, focus, keyByFinding, showToast]
  );

  const inFocus = focus !== null;
  useLayoutEffect(() => {
    const wasInFocus = prevInFocusRef.current;
    prevInFocusRef.current = inFocus;
    if (wasInFocus === inFocus) return;
    const scroller = rootRef.current?.closest("main");
    if (!scroller) return;
    scroller.scrollTop = inFocus ? 0 : savedScrollRef.current;
  }, [inFocus]);

  if (loading) {
    return (
      <div className="p-6 max-w-5xl mx-auto w-full space-y-4">
        <div className="grid grid-cols-5 gap-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-8 rounded" />
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-32 rounded-lg" />
        ))}
      </div>
    );
  }

  if (error || !report || !graph) {
    return (
      <div className="p-6 max-w-5xl mx-auto w-full text-center py-12 text-sm text-muted-foreground">
        {error ?? "Report not found."}
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      className={cn("p-6 mx-auto w-full", focus ? "max-w-[1920px]" : "max-w-5xl")}
    >
      <div className={cn("space-y-6", focus && "hidden")}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold">Validation Report</h1>
            <p className="text-sm text-muted-foreground">
              {report.findings.length} findings &middot;{" "}
              {new Date(report.timestamp).toLocaleDateString()}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => exportReportToExcel(report, graph, projectName)}
            disabled={report.findings.length === 0}
          >
            <Download /> Export Excel
          </Button>
        </div>
        <ReportSummary
          summary={report.summary}
          reviewedCount={reviewedCount}
          creditsSpent={creditsSpent}
        />
        {report.review_errors && Object.keys(report.review_errors).length > 0 && (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 space-y-2">
            <p className="text-sm font-medium text-amber-700 dark:text-amber-300">
              {Object.keys(report.review_errors).length} IC review{Object.keys(report.review_errors).length === 1 ? "" : "s"} failed
            </p>
            <p className="text-xs text-muted-foreground">
              These ICs could not be reviewed against their datasheets. Re-run the pipeline to retry; if the failure repeats, share the error with support.
            </p>
            <ul className="space-y-1 text-xs">
              {Object.entries(report.review_errors).map(([ref, err]) => (
                <li key={ref} className="flex gap-2">
                  <span className="font-mono font-medium text-amber-700 dark:text-amber-300 shrink-0">{ref}</span>
                  <span className="text-muted-foreground break-all">{err}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {report.not_reviewed && report.not_reviewed.length > 0 && (
          <div className="rounded-lg border border-border bg-muted/30 p-4 space-y-2">
            <p className="text-sm font-medium">
              {report.not_reviewed.length} component{report.not_reviewed.length === 1 ? "" : "s"} not reviewed
            </p>
            <p className="text-xs text-muted-foreground">
              These components have no datasheet on file, so they were not checked against one. A reversed or mis-wired pin on an unreviewed part (e.g. a DNP footprint with no BOM entry) cannot be caught here — verify these manually.
            </p>
            <ul className="space-y-1 text-xs">
              {report.not_reviewed.map((nr) => (
                <li key={nr.designator} className="flex gap-2">
                  <span className="font-mono font-medium shrink-0">{nr.designator}</span>
                  <span className="text-muted-foreground break-all">{nr.reason}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {report.summary.total === 0 ? (
          <div className="rounded-lg border border-border bg-card p-8 text-center space-y-2">
            <p className="text-sm font-medium">No findings</p>
            <p className="text-xs text-muted-foreground">
              No IC datasheets were available for review. Upload datasheets for each IC
              on the project page and re-run the pipeline to get findings.
            </p>
          </div>
        ) : (
          <FindingsList
            findings={report.findings}
            graph={graph}
            onViewReference={handleViewReference}
            projectId={projectId}
            isReviewed={isReviewed}
            toggleReviewed={handleToggleReviewed}
            comments={comments}
            collaborators={collaborators}
            currentUserId={user?.id}
            currentUserName={user?.name ?? user?.email ?? "User"}
            onCommentAdded={handleCommentAdded}
            onCommentDeleted={handleCommentDeleted}
            onReportFinding={handleReportFinding}
            reportedFindingIds={reportedFindingIds}
          />
        )}
      </div>
      {focus && (
        <FindingFocusView
          finding={focus.finding}
          component={graph.components[focus.finding.designator]}
          mpn={focus.mpn}
          page={focus.page}
          quote={focus.quote}
          projectId={projectId}
          onExit={() => setFocus(null)}
          escapeDisabled={feedbackOpen}
          onViewReference={handleViewReference}
          checked={isReviewed(focus.key)}
          onCheckedChange={() => handleToggleReviewed(focus.key)}
          comments={focus.finding.finding_id ? comments[focus.finding.finding_id] : undefined}
          collaborators={collaborators}
          currentUserId={user?.id}
          currentUserName={user?.name ?? user?.email ?? "User"}
          onCommentAdded={handleCommentAdded}
          onCommentDeleted={handleCommentDeleted}
          onReportFinding={handleReportFinding}
          isReported={!!(focus.finding.finding_id && reportedFindingIds.has(focus.finding.finding_id))}
        />
      )}
      <Toast toast={toast} />
      <FeedbackDialog
        open={feedbackOpen}
        onOpenChange={(open) => {
          setFeedbackOpen(open);
          if (!open) setFeedbackFinding(null);
        }}
        projectId={projectId}
        projectName={projectName}
        findingContext={
          feedbackFinding?.finding_id
            ? {
                finding_id: feedbackFinding.finding_id,
                finding_text: feedbackFinding.finding,
                designator: feedbackFinding.designator,
                mpn: feedbackFinding.mpn,
                status: feedbackFinding.status,
              }
            : undefined
        }
        onSubmitted={() => {
          showToast("Feedback submitted");
          if (feedbackFinding?.finding_id) {
            setReportedFindingIds((prev) => new Set(prev).add(feedbackFinding.finding_id!));
          }
        }}
      />
    </div>
  );
}

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <div className="flex-1 w-full">
      <Suspense>
        <ReportContent projectId={id} />
      </Suspense>
    </div>
  );
}
