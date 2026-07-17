"use client";

import { useState } from "react";
import { ChevronRight, CircleCheck, Check } from "lucide-react";
import { Checkbox } from "@base-ui/react/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "./status-badge";
import type { Finding } from "@/lib/types";
import { cn, sortFindings } from "@/lib/utils";

interface ReviewedSectionProps {
  findings: Finding[];
  findingKeys: Map<Finding, string>;
  onToggleReviewed: (key: string) => void;
  onViewReference: (finding: Finding) => void;
}

export function ReviewedSection({ findings, findingKeys, onToggleReviewed, onViewReference }: ReviewedSectionProps) {
  const [open, setOpen] = useState(false);
  const sorted = sortFindings(findings);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-2 w-full py-3 group">
        <ChevronRight
          className={cn("h-4 w-4 text-emerald-600 dark:text-emerald-400 transition-transform", open && "rotate-90")}
        />
        <CircleCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
        <span className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
          Reviewed ({findings.length})
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="space-y-2 pl-7 pb-4">
          {sorted.map((f) => {
            const key = findingKeys.get(f);
            return (
              <div
                key={key}
                className="flex items-center gap-3 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3 border-l-4 border-l-emerald-500"
              >
                <Checkbox.Root
                  checked={true}
                  onCheckedChange={() => key && onToggleReviewed(key)}
                  aria-label="Unmark as reviewed"
                  className="h-5 w-5 shrink-0 rounded border border-emerald-500/30 bg-emerald-500 flex items-center justify-center cursor-pointer transition-colors hover:bg-emerald-600"
                >
                  <Checkbox.Indicator>
                    <Check className="h-3.5 w-3.5 text-white" />
                  </Checkbox.Indicator>
                </Checkbox.Root>
                <StatusBadge status={f.status} />
                <Badge variant="secondary" className="font-mono text-xs shrink-0">
                  {f.designator}
                </Badge>
                <p className="text-sm text-emerald-700/70 dark:text-emerald-300/70 truncate min-w-0 flex-1">
                  {f.finding}
                </p>
              </div>
            );
          })}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
