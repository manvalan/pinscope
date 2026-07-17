"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useState, useEffect } from "react";
import {
  LayoutDashboard,
  Cpu,
  Shield,
  ArrowLeft,
  ClipboardList,
  Settings,
  TableProperties,
  Loader2,
  Zap,
  ScrollText,
  MessageSquareWarning,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthApi } from "@/hooks/use-auth-api";
import { useOptionalUser } from "@/hooks/use-optional-auth";
import { fetchProject } from "@/lib/api";
import { SidebarCredits, SidebarUserButton } from "@/components/layout/sidebar-auth";
import { FeedbackDialog } from "@/components/feedback/feedback-dialog";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import type { Project } from "@/lib/types";
import { APP_VERSION } from "@/lib/version";

function useProjectFromPath(pathname: string): {
  projectId: string | null;
  project: Project | null;
} {
  const match = pathname.match(/^\/project\/([^/]+)/);
  const projectId = match ? match[1] : null;
  const [project, setProject] = useState<Project | null>(null);

  useEffect(() => {
    if (!projectId) {
      setProject(null);
      return;
    }
    fetchProject(projectId).then(setProject).catch(() => setProject(null));
  }, [projectId]);

  // Poll for status updates while pipeline is running
  useEffect(() => {
    if (!projectId || project?.status !== "running") return;
    const interval = setInterval(() => {
      fetchProject(projectId).then(setProject).catch(() => {});
    }, 3000);
    return () => clearInterval(interval);
  }, [projectId, project?.status]);

  return { projectId, project };
}

export function Sidebar() {
  const pathname = usePathname();
  const { user } = useOptionalUser();
  useAuthApi();
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  const { projectId, project } = useProjectFromPath(pathname);

  const isAdmin = user?.isAdmin ?? false;

  return (
    <aside className="w-56 shrink-0 border-r border-border bg-card flex flex-col min-h-0">
      <div className="px-4 py-4 border-b border-border">
        <Link href="/dashboard" className="flex items-center gap-2">
          <Cpu className="h-5 w-5 text-blue-500" />
          <span className="text-sm font-semibold tracking-tight">Pinscope</span>
        </Link>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto flex flex-col">
        {projectId ? (
          <ProjectNav pathname={pathname} projectId={projectId} project={project} isAdmin={isAdmin} />
        ) : (
          <DefaultNav pathname={pathname} isAdmin={isAdmin} />
        )}
      </div>

      <div className="border-t border-border">
        <SidebarCredits />
        <div className="px-2 py-1.5 border-b border-border/60">
          <button
            type="button"
            onClick={() => setFeedbackOpen(true)}
            className="flex items-center gap-2 w-full px-3 py-1.5 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
          >
            <MessageSquareWarning className="h-3.5 w-3.5" />
            Feedback
          </button>
        </div>
        <div className="px-4 py-3 flex items-center gap-2">
          <SidebarUserButton />
          <Link
            href="/changelog"
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            v{APP_VERSION}
          </Link>
          <ThemeToggle className="ml-auto" />
        </div>
      </div>
      <FeedbackDialog
        open={feedbackOpen}
        onOpenChange={setFeedbackOpen}
      />
    </aside>
  );
}

function DefaultNav({ pathname, isAdmin }: { pathname: string; isAdmin: boolean }) {
  return (
    <nav className="flex-1 px-2 py-3 space-y-0.5">
      <Link
        href="/dashboard"
        className={cn(
          "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
          pathname === "/dashboard"
            ? "bg-accent text-accent-foreground"
            : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
        )}
      >
        <LayoutDashboard className="h-4 w-4" />
        Projects
      </Link>
      <Link
        href="/feedback"
        className={cn(
          "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
          pathname === "/feedback"
            ? "bg-accent text-accent-foreground"
            : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
        )}
      >
        <MessageSquareWarning className="h-4 w-4" />
        My Feedback
      </Link>
      {isAdmin && (
        <Link
          href="/admin"
          className={cn(
            "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
            pathname === "/admin" || pathname.startsWith("/admin/")
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
          )}
        >
          <Shield className="h-4 w-4" />
          Admin
          <Shield className="h-3 w-3 ml-auto text-amber-600/60 dark:text-amber-500/60" />
        </Link>
      )}
    </nav>
  );
}

type NavItem =
  | { type: "route"; path: string; label: string; icon: typeof ClipboardList; adminOnly?: boolean }
  | { type: "tab"; tab: string; label: string; icon: typeof ClipboardList; adminOnly?: boolean };

const PROJECT_NAV_ITEMS: NavItem[] = [
  { type: "route", path: "/report", label: "Report", icon: ClipboardList },
  { type: "tab", tab: "bom", label: "BOM", icon: TableProperties },
  { type: "tab", tab: "derating", label: "Derating", icon: Zap },
  { type: "tab", tab: "logs", label: "Logs", icon: ScrollText, adminOnly: true },
  { type: "tab", tab: "settings", label: "Settings", icon: Settings },
];

function ProjectNav({
  pathname,
  projectId,
  project,
  isAdmin,
}: {
  pathname: string;
  projectId: string;
  project: Project | null;
  isAdmin: boolean;
}) {
  const searchParams = useSearchParams();
  const base = `/project/${projectId}`;
  const currentTab = searchParams.get("tab");
  const isRunning = project?.status === "running";
  const isOnProgress = pathname === `${base}/progress`;

  function isActive(item: NavItem): boolean {
    if (item.type === "route") {
      return pathname === `${base}${item.path}` && !currentTab;
    }
    return pathname === base && currentTab === item.tab;
  }

  function getHref(item: NavItem): string {
    if (item.type === "route") return `${base}${item.path}`;
    return `${base}?tab=${item.tab}`;
  }

  return (
    <nav className="flex-1 px-2 py-3 space-y-1">
      <Link
        href="/dashboard"
        className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
      >
        <ArrowLeft className="h-4 w-4" />
        Dashboard
      </Link>

      <div className="px-3 pt-3 pb-1">
        <p className="text-xs font-semibold text-foreground truncate">
          {project?.name ?? "Loading..."}
        </p>
      </div>

      <div className="space-y-0.5">
        {isRunning && (
          <Link
            href={`${base}/progress`}
            className={cn(
              "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
              isOnProgress
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
            )}
          >
            <Loader2 className="h-4 w-4 animate-spin" />
            Processing
          </Link>
        )}
        {PROJECT_NAV_ITEMS.filter((item) => !item.adminOnly || isAdmin).map((item) => {
          const active = isActive(item);
          const disabled = isRunning;
          return disabled ? (
            <span
              key={item.label}
              className="flex items-center gap-2 px-3 py-2 rounded-md text-sm text-muted-foreground/40 cursor-not-allowed"
            >
              <item.icon className="h-4 w-4" />
              {item.label}
              {item.adminOnly && <Shield className="h-3 w-3 ml-auto text-amber-600/60 dark:text-amber-500/60" />}
            </span>
          ) : (
            <Link
              key={item.label}
              href={getHref(item)}
              className={cn(
                "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
                active
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
              )}
            >
              <item.icon className="h-4 w-4" />
              {item.label}
              {item.adminOnly && <Shield className="h-3 w-3 ml-auto text-amber-600/60 dark:text-amber-500/60" />}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
