"use client";

import { useState } from "react";
import Link from "next/link";
import { RotateCcw, Trash2, Users } from "lucide-react";
import { useOptionalUser } from "@/hooks/use-optional-auth";
import { Badge } from "@/components/ui/badge";
import { deleteProject } from "@/lib/api";
import type { Project } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useReviewedCount } from "@/hooks/use-reviewed-count";

const STATUS_STYLES: Record<string, string> = {
  draft: "bg-muted text-muted-foreground",
  running: "bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30",
  complete: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30",
  error: "bg-rose-500/15 text-rose-600 dark:text-rose-400 border-rose-500/30",
  cancelled: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30",
};

export function ProjectsTable({
  projects,
  onDeleted,
  onRerun,
}: {
  projects: Project[];
  onDeleted?: (id: string) => void;
  onRerun?: (project: Project) => void;
}) {
  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr>
            <th className="text-left font-medium px-3 py-2">Name</th>
            <th className="text-left font-medium px-3 py-2 w-28">Status</th>
            <th className="text-left font-medium px-3 py-2 w-56">Findings</th>
            <th className="text-left font-medium px-3 py-2 w-32">Created</th>
            <th className="w-16 px-3 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {projects.map((p) => (
            <ProjectRow
              key={p.id}
              project={p}
              onDeleted={() => onDeleted?.(p.id)}
              onRerun={onRerun}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProjectRow({
  project,
  onDeleted,
  onRerun,
}: {
  project: Project;
  onDeleted?: () => void;
  onRerun?: (project: Project) => void;
}) {
  const { user } = useOptionalUser();
  const [deleting, setDeleting] = useState(false);
  const { summary } = project;
  const total = summary?.total ?? 0;
  const isShared = project.userId != null && user?.id != null && project.userId !== user.id;
  const checkedCount = useReviewedCount(project.id);
  const isCancelled = project.status === "cancelled";
  const isDraft = project.status === "draft";
  const opensModalOnClick = isDraft && !isShared && onRerun != null;

  async function handleDelete(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm(`Delete "${project.name}"?`)) return;
    setDeleting(true);
    try {
      await deleteProject(project.id);
      onDeleted?.();
    } catch {
      setDeleting(false);
    }
  }

  function handleRerun(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    onRerun?.(project);
  }

  const inFlight =
    project.status === "running"
    || project.status === "paused_insufficient_credits"
    || project.status === "paused_by_user";
  const href = inFlight
    ? `/project/${project.id}/progress`
    : `/project/${project.id}/report`;

  const nameCell = (
    <div className="flex items-center gap-1.5 min-w-0">
      <span className="truncate font-medium">{project.name}</span>
      {isShared && (
        <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-blue-600 dark:text-blue-400 border-blue-500/40 shrink-0">
          <Users className="h-3 w-3 mr-0.5" />
          Shared
        </Badge>
      )}
    </div>
  );

  const wrappedName = isCancelled ? (
    nameCell
  ) : opensModalOnClick ? (
    <button type="button" onClick={() => onRerun?.(project)} className="text-left w-full hover:underline">
      {nameCell}
    </button>
  ) : (
    <Link href={href} className="block hover:underline">
      {nameCell}
    </Link>
  );

  return (
    <tr
      className={cn(
        "border-t border-border transition-colors",
        isCancelled ? "opacity-70" : "hover:bg-muted/30",
      )}
    >
      <td className="px-3 py-2 max-w-0">{wrappedName}</td>
      <td className="px-3 py-2">
        <Badge variant="outline" className={cn("text-xs capitalize", STATUS_STYLES[project.status])}>
          {project.status}
        </Badge>
      </td>
      <td className="px-3 py-2">
        {summary && total > 0 ? (
          <div className="flex items-center gap-3 text-xs font-mono tabular-nums">
            <span className="text-rose-600 dark:text-rose-400">{summary.ERROR ?? 0} err</span>
            <span className="text-amber-600 dark:text-amber-400">{summary.WARNING ?? 0} warn</span>
            <span className="text-blue-600 dark:text-blue-400">{summary.INFO ?? 0} info</span>
            {checkedCount > 0 && (
              <span className="text-emerald-600 dark:text-emerald-400">{checkedCount} ✓</span>
            )}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">No report yet</span>
        )}
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground tabular-nums">
        {new Date(project.created).toLocaleDateString()}
      </td>
      <td className="px-3 py-2">
        <div className="flex items-center justify-end gap-0.5">
          {isCancelled && !isShared && onRerun && (
            <button
              onClick={handleRerun}
              title="Rerun project"
              className="p-1 rounded-md text-muted-foreground hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-emerald-500/10 transition-colors"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
          )}
          {!isShared && (
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="p-1 rounded-md text-muted-foreground hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-500/10 transition-colors disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}
