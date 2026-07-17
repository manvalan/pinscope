"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { PipelineStepper } from "@/components/progress/pipeline-stepper";
import { PausedRunBanner } from "@/components/billing/paused-run-banner";
import { usePipelineProgress } from "@/hooks/use-pipeline-progress";
import { cancelPipeline, fetchProject, resumePipeline } from "@/lib/api";
import type { PauseCheckpoint } from "@/lib/types";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Coffee,
  Coins,
  Loader2,
  OctagonX,
  Ban,
} from "lucide-react";

export default function ProgressPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { steps, done, cancelled, error, summary, autoTopupFailure, credits, started, paused } =
    usePipelineProgress(id);
  const [topupDismissed, setTopupDismissed] = useState(false);

  const [projectName, setProjectName] = useState<string>("");
  const [confirmInput, setConfirmInput] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [projectPaused, setProjectPaused] = useState(false);
  const [projectCheckpoint, setProjectCheckpoint] = useState<PauseCheckpoint | null>(null);
  const [resuming, setResuming] = useState(false);

  // Fetch project name + initial paused state. The SSE stream only reports
  // `pipeline_paused` if the page is open when it fires; landing on the
  // progress page later, we need to read the persisted project status.
  useEffect(() => {
    fetchProject(id)
      .then((p) => {
        setProjectName(p.name);
        if (p.status === "paused_insufficient_credits") {
          setProjectPaused(true);
          setProjectCheckpoint(p.pauseCheckpoint ?? null);
        }
      })
      .catch(() => {});
  }, [id]);

  // Live `pipeline_paused` event also flips the paused state.
  useEffect(() => {
    if (paused) {
      setProjectPaused(true);
      setProjectCheckpoint({
        paused_at: paused.unit_id,
        paused_stage: paused.stage,
        last_completed_label: paused.last_completed,
        completed_review_refs: paused.completed_review_refs ?? [],
        pending_review_refs: paused.pending_review_refs ?? [],
      });
    }
  }, [paused]);

  const handleResume = async () => {
    setResuming(true);
    try {
      await resumePipeline(id);
      // Refresh the page so a new SSE connection picks up the resumed run
      // from a clean slate.
      window.location.reload();
    } catch (e) {
      setResuming(false);
      alert(e instanceof Error ? e.message : "Failed to resume pipeline");
    }
  };

  // Auto-navigate to report when pipeline completes successfully
  useEffect(() => {
    if (done && !error && !cancelled && !projectPaused) {
      const timer = setTimeout(() => {
        router.push(`/project/${id}/report`);
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [done, error, cancelled, projectPaused, id, router]);

  // Auto-navigate to dashboard when pipeline is cancelled
  useEffect(() => {
    if (cancelled) {
      const timer = setTimeout(() => {
        router.push("/dashboard");
      }, 1500);
      return () => clearTimeout(timer);
    }
  }, [cancelled, router]);

  const handleCancel = async () => {
    setCancelling(true);
    try {
      await cancelPipeline(id);
    } catch {
      // Pipeline may have already finished
    } finally {
      setCancelling(false);
      setDialogOpen(false);
      setConfirmInput("");
    }
  };

  const isRunning = !done && !projectPaused;
  const isQueued = isRunning && !started;

  return (
    <div className="flex-1 p-6 max-w-3xl mx-auto w-full space-y-6">
      <div>
        <h1 className="text-lg font-semibold">Pipeline Progress</h1>
        <p className="text-sm text-muted-foreground">
          {cancelled
            ? "Pipeline cancelled"
            : projectPaused
              ? "Pipeline paused — out of credits"
              : done
                ? "Validation complete"
                : isQueued
                  ? "Project queued, starting pipeline..."
                  : "Running validation pipeline..."}
        </p>
      </div>

      {projectPaused && (
        <PausedRunBanner
          projectId={id}
          checkpoint={projectCheckpoint}
          resuming={resuming}
          onResume={handleResume}
        />
      )}

      {isQueued && (
        <div className="relative flex items-center gap-4 p-5 rounded-xl border border-blue-500/30 bg-gradient-to-r from-blue-500/10 via-blue-500/[0.04] to-transparent overflow-hidden">
          <div className="relative shrink-0">
            <span
              aria-hidden
              className="absolute inset-0 -m-2 rounded-full bg-blue-400/30 blur-xl animate-pulse"
            />
            <Loader2
              className="relative h-8 w-8 text-blue-700 dark:text-blue-300 animate-spin drop-shadow-[0_0_10px_rgba(96,165,250,0.75)]"
              strokeWidth={2.25}
            />
          </div>
          <div className="flex-1">
            <p className="text-base font-medium text-foreground">
              Project queued, starting pipeline…
            </p>
            <p className="text-sm text-muted-foreground mt-0.5">
              Waiting for a worker to pick this run up. Usually takes a few seconds.
            </p>
          </div>
        </div>
      )}

      {isRunning && !isQueued && (
        <div className="relative flex items-center gap-4 p-5 rounded-xl border border-amber-500/30 bg-gradient-to-r from-amber-500/10 via-amber-500/[0.04] to-transparent overflow-hidden">
          <div className="relative shrink-0">
            <span
              aria-hidden
              className="absolute inset-0 -m-2 rounded-full bg-amber-400/30 blur-xl animate-pulse"
            />
            <Coffee
              className="relative h-8 w-8 text-amber-700 dark:text-amber-300 drop-shadow-[0_0_10px_rgba(251,191,36,0.75)]"
              strokeWidth={2.25}
            />
          </div>
          <div className="flex-1">
            <p className="text-base font-medium text-foreground">
              Feel free to grab a coffee
            </p>
            <p className="text-sm text-muted-foreground mt-0.5">
              You can close this tab — we&apos;ll email you when it&apos;s ready.
            </p>
          </div>
        </div>
      )}

      {autoTopupFailure && !topupDismissed && (
        <div className="flex items-start gap-3 p-4 rounded-lg border border-amber-500/30 bg-amber-500/5">
          <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
          <div className="flex-1 text-sm">
            <p className="font-medium text-amber-800 dark:text-amber-200">Auto top-up failed</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {autoTopupFailure.amount_usd
                ? `The $${autoTopupFailure.amount_usd.toFixed(0)} charge`
                : "Your scheduled top-up"}{" "}
              was declined ({autoTopupFailure.reason}). Auto top-up has been
              disabled — top up manually to avoid pausing.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/credits">
              <Button variant="outline" size="sm">
                Top up
              </Button>
            </Link>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setTopupDismissed(true)}
            >
              Dismiss
            </Button>
          </div>
        </div>
      )}

      {(credits || isRunning) && (
        <div className="flex items-center justify-between gap-4 px-3 py-1.5 rounded-md border border-border/40 bg-muted/20 text-xs text-muted-foreground">
          <div className="flex items-center gap-2 min-w-0">
            <Coins
              key={credits?.ts ?? 0}
              className="h-3.5 w-3.5 text-amber-600/60 dark:text-amber-400/60 shrink-0 data-[bump=true]:animate-pulse"
              data-bump={credits ? "true" : "false"}
            />
            <span>Spent this run</span>
            <span className="font-mono tabular-nums text-foreground/80">
              {credits ? credits.credits_spent.toFixed(2) : "0.00"}
            </span>
            {credits?.stage && (
              <span className="hidden sm:inline text-muted-foreground/70 truncate">
                · {credits.stage}
                {credits.unit_id ? ` · ${credits.unit_id}` : ""}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span>Balance</span>
            <span className="font-mono tabular-nums text-foreground/80">
              {credits ? credits.balance_after.toFixed(2) : "—"}
            </span>
          </div>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Steps</CardTitle>
        </CardHeader>
        <CardContent>
          <PipelineStepper steps={steps} />
        </CardContent>
      </Card>

      {isRunning && (
        <AlertDialog open={dialogOpen} onOpenChange={(open) => {
          setDialogOpen(open);
          if (!open) setConfirmInput("");
        }}>
          <AlertDialogTrigger
            render={
              <Button variant="outline" size="sm" className="text-rose-600 dark:text-rose-400 border-rose-500/30 hover:bg-rose-500/10">
                <OctagonX className="h-4 w-4 mr-1.5" />
                Cancel Project
              </Button>
            }
          />
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Cancel pipeline?</AlertDialogTitle>
              <AlertDialogDescription>
                This will stop the validation pipeline. The project will still
                count toward your project limit.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="space-y-2 py-2">
              <p className="text-sm text-muted-foreground">
                Type <span className="font-semibold font-mono text-foreground">{projectName}</span> to
                confirm:
              </p>
              <Input
                value={confirmInput}
                onChange={(e) => setConfirmInput(e.target.value)}
                placeholder={projectName}
                autoFocus
              />
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel>Go back</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                disabled={confirmInput !== projectName || cancelling}
                onClick={(e) => {
                  e.preventDefault();
                  handleCancel();
                }}
              >
                {cancelling ? "Cancelling..." : "Cancel project"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}

      {done && !error && !cancelled && (
        <div className="flex items-center gap-3 p-4 rounded-lg border border-emerald-500/30 bg-emerald-500/5">
          <CheckCircle2 className="h-5 w-5 text-emerald-600 dark:text-emerald-400 shrink-0" />
          <span className="text-sm flex-1">
            All checks complete.{" "}
            {summary &&
              `${summary.total} findings: ${summary.PASS} pass, ${summary.WARNING} warnings, ${summary.ERROR} errors.`}
          </span>
          <Link href={`/project/${id}/report`}>
            <Button size="sm">
              View Report
              <ArrowRight className="h-4 w-4 ml-1" />
            </Button>
          </Link>
        </div>
      )}

      {done && cancelled && (
        <div className="flex items-center gap-3 p-4 rounded-lg border border-amber-500/30 bg-amber-500/5">
          <Ban className="h-5 w-5 text-amber-600 dark:text-amber-400 shrink-0" />
          <span className="text-sm text-amber-600 dark:text-amber-400 flex-1">
            Pipeline was cancelled. This project still counts toward your project
            limit.
          </span>
          <Link href="/dashboard">
            <Button variant="outline" size="sm">
              Back to Dashboard
            </Button>
          </Link>
        </div>
      )}

      {done && error && !cancelled && (
        <div className="flex items-center gap-3 p-4 rounded-lg border border-rose-500/30 bg-rose-500/5">
          <span className="text-sm text-rose-600 dark:text-rose-400">{error}</span>
        </div>
      )}
    </div>
  );
}
