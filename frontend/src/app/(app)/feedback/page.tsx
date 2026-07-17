"use client";

import { useEffect, useState } from "react";
import { MessageSquareWarning } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchMyFeedback, type FeedbackTicket } from "@/lib/api";

const STATUS_STYLES: Record<string, string> = {
  open: "bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30",
  acknowledged: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30",
  resolved: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30",
};

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function FeedbackPage() {
  const [tickets, setTickets] = useState<FeedbackTicket[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    fetchMyFeedback()
      .then(setTickets)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex-1 p-6 max-w-4xl mx-auto w-full space-y-4">
        <Skeleton className="h-8 w-48" />
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-16 rounded-lg" />
        ))}
      </div>
    );
  }

  return (
    <div className="flex-1 p-6 max-w-4xl mx-auto w-full space-y-6">
      <div>
        <h1 className="text-lg font-semibold">My Feedback</h1>
        <p className="text-sm text-muted-foreground">
          {tickets.length} ticket{tickets.length !== 1 ? "s" : ""} submitted
        </p>
      </div>

      {tickets.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-12 text-center space-y-3">
          <MessageSquareWarning className="h-8 w-8 text-muted-foreground mx-auto" />
          <p className="text-sm text-muted-foreground">
            No feedback submitted yet. Use the Feedback button in the sidebar or the flag icon on a finding to submit.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {tickets.map((t) => (
            <button
              key={t.ticket_id}
              type="button"
              onClick={() => setExpanded(expanded === t.ticket_id ? null : t.ticket_id)}
              className="w-full text-left rounded-lg border border-border bg-card p-4 hover:bg-accent/30 transition-colors"
            >
              <div className="flex items-center gap-3">
                <Badge variant="outline" className={`text-xs shrink-0 ${STATUS_STYLES[t.status] ?? ""}`}>
                  {t.status}
                </Badge>
                {t.finding_id && (
                  <span className="font-mono text-xs text-muted-foreground shrink-0">
                    {t.finding_id}
                  </span>
                )}
                {t.project_name && (
                  <span className="text-xs text-muted-foreground truncate">
                    {t.project_name}
                  </span>
                )}
                <span className="ml-auto text-xs text-muted-foreground shrink-0">
                  {formatRelativeTime(t.created_at)}
                </span>
              </div>
              <p className={`text-sm text-muted-foreground mt-2 ${expanded === t.ticket_id ? "" : "line-clamp-2"}`}>
                {t.message}
              </p>
              {expanded === t.ticket_id && t.finding_text && (
                <div className="mt-3 rounded border border-border/60 bg-muted/30 p-3 space-y-1">
                  <p className="text-xs text-muted-foreground">Reported finding:</p>
                  <p className="text-sm">{t.finding_text}</p>
                  <div className="flex gap-2 text-xs text-muted-foreground">
                    {t.finding_designator && <span className="font-mono">{t.finding_designator}</span>}
                    {t.finding_mpn && <span className="font-mono">{t.finding_mpn}</span>}
                  </div>
                </div>
              )}
              {expanded === t.ticket_id && t.admin_notes && (
                <div className="mt-3 rounded border border-emerald-500/20 bg-emerald-500/5 p-3">
                  <p className="text-xs text-emerald-600 dark:text-emerald-400 mb-1">Pinscope Team:</p>
                  <p className="text-sm text-muted-foreground">{t.admin_notes}</p>
                </div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
