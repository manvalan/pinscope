"use client";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface ReportSummaryProps {
  summary: Record<string, number>;
  reviewedCount: number;
  creditsSpent?: number;
  totalCostUsd?: number | null;
}

const STAT_CONFIG = [
  { key: "ERROR", label: "Error", color: "text-rose-600 dark:text-rose-400", barColor: "bg-rose-500" },
  { key: "WARNING", label: "Warning", color: "text-amber-600 dark:text-amber-400", barColor: "bg-amber-500" },
  { key: "INFO", label: "Info", color: "text-blue-600 dark:text-blue-400", barColor: "bg-blue-500" },
  { key: "total", label: "Total", color: "text-foreground", barColor: "" },
];

export function ReportSummary({
  summary,
  reviewedCount,
  creditsSpent,
  totalCostUsd,
}: ReportSummaryProps) {
  const total = summary.total || 0;
  const showCredits = typeof creditsSpent === "number" && creditsSpent > 0;
  const showCost = typeof totalCostUsd === "number" && totalCostUsd > 0;
  const extraCols = (showCredits ? 1 : 0) + (showCost ? 1 : 0);

  return (
    <div className="space-y-4">
      <div
        className={cn(
          "grid gap-4",
          extraCols === 2 ? "grid-cols-7" : extraCols === 1 ? "grid-cols-6" : "grid-cols-5",
        )}
      >
        {STAT_CONFIG.map(({ key, label, color }) => (
          <Card key={key}>
            <CardContent className="pt-4 pb-4">
              <p className="text-sm text-muted-foreground">{label}</p>
              <p className={cn("text-3xl font-semibold font-mono tabular-nums", color)}>
                {summary[key] ?? 0}
              </p>
            </CardContent>
          </Card>
        ))}
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-sm text-muted-foreground">Checked</p>
            <p className="text-3xl font-semibold font-mono tabular-nums text-emerald-600 dark:text-emerald-400">
              {reviewedCount}
            </p>
          </CardContent>
        </Card>
        {showCost && (
          <Card>
            <CardContent className="pt-4 pb-4">
              <p className="text-sm text-muted-foreground">API cost</p>
              <p className="text-3xl font-semibold font-mono tabular-nums text-foreground">
                ${totalCostUsd!.toFixed(2)}
              </p>
            </CardContent>
          </Card>
        )}
        {showCredits && (
          <Card>
            <CardContent className="pt-4 pb-4">
              <p className="text-sm text-muted-foreground">Credits</p>
              <p className="text-3xl font-semibold font-mono tabular-nums text-amber-600 dark:text-amber-400">
                {creditsSpent!.toFixed(2)}
              </p>
            </CardContent>
          </Card>
        )}
      </div>
      {total > 0 && (
        <div className="flex h-2 rounded-full overflow-hidden bg-muted">
          {STAT_CONFIG.filter((s) => s.key !== "total" && (summary[s.key] ?? 0) > 0).map(
            ({ key, barColor }) => (
              <div
                key={key}
                className={cn("h-full", barColor)}
                style={{ width: `${((summary[key] ?? 0) / total) * 100}%` }}
              />
            )
          )}
        </div>
      )}
    </div>
  );
}
