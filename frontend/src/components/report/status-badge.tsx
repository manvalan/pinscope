import { Badge } from "@/components/ui/badge";
import type { FindingStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<FindingStatus, string> = {
  ERROR: "bg-rose-500/10 text-rose-600 border-rose-500/30 dark:bg-rose-500/15 dark:text-rose-400",
  WARNING: "bg-amber-500/10 text-amber-600 border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-400",
  INFO: "bg-blue-500/10 text-blue-600 border-blue-500/30 dark:bg-blue-500/15 dark:text-blue-400",
};

export function StatusBadge({ status }: { status: FindingStatus }) {
  return (
    <Badge variant="outline" className={cn("text-xs font-medium", STATUS_STYLES[status])}>
      {status}
    </Badge>
  );
}
