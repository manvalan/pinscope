"use client";

import { useState } from "react";
import { useOptionalUser } from "@/hooks/use-optional-auth";
import { Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { submitFeedback } from "@/lib/api";
import type { FindingStatus } from "@/lib/types";

const STATUS_STYLES: Record<string, string> = {
  ERROR: "bg-rose-500/15 text-rose-600 dark:text-rose-400 border-rose-500/30",
  WARNING: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30",
  INFO: "bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30",
};

interface FindingContext {
  finding_id: string;
  finding_text: string;
  designator: string;
  mpn: string;
  status: FindingStatus;
}

interface FeedbackDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId?: string;
  projectName?: string;
  findingContext?: FindingContext;
  onSubmitted?: () => void;
}

export function FeedbackDialog({
  open,
  onOpenChange,
  projectId,
  projectName,
  findingContext,
  onSubmitted,
}: FeedbackDialogProps) {
  const { user } = useOptionalUser();
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  function reset() {
    setMessage("");
    setError(null);
    setSuccess(false);
    setSubmitting(false);
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  async function handleSubmit() {
    if (!message.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await submitFeedback({
        type: findingContext ? "rule_feedback" : "bug",
        message: message.trim(),
        project_id: projectId,
        project_name: projectName,
        user_name: user?.name ?? undefined,
        user_email: user?.email ?? undefined,
        ...(findingContext
          ? {
              finding_id: findingContext.finding_id,
              finding_text: findingContext.finding_text,
              finding_designator: findingContext.designator,
              finding_mpn: findingContext.mpn,
              finding_status: findingContext.status,
            }
          : {}),
      });
      setSuccess(true);
      onSubmitted?.();
      setTimeout(() => handleOpenChange(false), 1200);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit feedback");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Send Feedback</DialogTitle>
          <DialogDescription>
            {findingContext
              ? "Let us know what's wrong with this finding."
              : "Report a bug, suggest a feature, or give us feedback."}
          </DialogDescription>
        </DialogHeader>

        {success ? (
          <div className="py-6 text-center text-sm text-emerald-600 dark:text-emerald-400">
            Feedback submitted. Thank you!
          </div>
        ) : (
          <div className="space-y-4">
            {/* Finding context card */}
            {findingContext && (
              <div className="rounded-lg border border-border bg-muted/30 p-3 space-y-1.5">
                <div className="flex items-center gap-2">
                  <Badge
                    variant="outline"
                    className={`text-xs font-medium ${STATUS_STYLES[findingContext.status] ?? ""}`}
                  >
                    {findingContext.status}
                  </Badge>
                  <span className="font-mono text-xs text-muted-foreground">
                    {findingContext.finding_id}
                  </span>
                </div>
                <p className="text-sm leading-snug">{findingContext.finding_text}</p>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-mono">{findingContext.designator}</span>
                  <span className="font-mono">{findingContext.mpn}</span>
                </div>
              </div>
            )}

            {/* Message */}
            <Textarea
              placeholder={
                findingContext
                  ? "What's wrong with this finding? Is it incorrect, misleading, or missing context?"
                  : "Tell us what's on your mind..."
              }
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              className="min-h-[100px]"
            />

            {error && (
              <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>
            )}
          </div>
        )}

        {!success && (
          <DialogFooter>
            <Button
              onClick={handleSubmit}
              disabled={submitting || !message.trim()}
            >
              {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Submit
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
