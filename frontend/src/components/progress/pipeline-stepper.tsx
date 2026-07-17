"use client";

import { Check, Circle, Loader2 } from "lucide-react";
import type { PipelineStep } from "@/lib/types";
import { cn } from "@/lib/utils";

function StepIcon({ status }: { status: string }) {
  if (status === "complete")
    return (
      <div className="h-7 w-7 rounded-full bg-emerald-500/20 flex items-center justify-center">
        <Check className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
      </div>
    );
  if (status === "running")
    return (
      <div className="h-7 w-7 rounded-full bg-blue-500/20 flex items-center justify-center">
        <Loader2 className="h-4 w-4 text-blue-600 dark:text-blue-400 animate-spin" />
      </div>
    );
  return (
    <div className="h-7 w-7 rounded-full bg-muted flex items-center justify-center">
      <Circle className="h-3 w-3 text-muted-foreground" />
    </div>
  );
}

interface PipelineStepperProps {
  steps: PipelineStep[];
}

export function PipelineStepper({ steps }: PipelineStepperProps) {
  return (
    <div className="space-y-0">
      {steps.map((step, i) => (
        <div key={i} className="relative flex gap-4">
          {/* Vertical line */}
          {i < steps.length - 1 && (
            <div
              className={cn(
                "absolute left-[13px] top-9 w-px bottom-0",
                step.status === "complete" ? "bg-emerald-500/40" : "bg-border"
              )}
            />
          )}
          <div className="pt-1">
            <StepIcon status={step.status} />
          </div>
          <div className="flex-1 pb-8">
            <p className="text-sm font-semibold leading-7">
              {step.title}
              {step.status === "running" && step.totalNew != null && step.totalNew > 0 && (
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  ({step.substeps.filter((s) => s.status === "complete" && !s.cached).length}
                  /{step.totalNew})
                </span>
              )}
            </p>
            <p className="text-xs text-muted-foreground mb-2">{step.description}</p>
            <div className="space-y-1">
              {step.substeps.map((sub) => (
                <div key={sub.key} className="flex items-center gap-2 text-xs">
                  {sub.status === "complete" && (
                    <Check className="h-3 w-3 text-emerald-600 dark:text-emerald-400 shrink-0" />
                  )}
                  {sub.status === "running" && (
                    <Loader2 className="h-3 w-3 text-blue-600 dark:text-blue-400 animate-spin shrink-0" />
                  )}
                  {sub.status === "pending" && (
                    <Circle className="h-3 w-3 text-muted-foreground shrink-0" />
                  )}
                  <span
                    className={cn(
                      "text-muted-foreground",
                      sub.status === "complete" && "text-foreground",
                      sub.status === "running" && "text-blue-600 dark:text-blue-400"
                    )}
                  >
                    {sub.label}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
