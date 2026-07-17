"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { FindingStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Search } from "lucide-react";

interface ReportFiltersProps {
  statusFilters: Set<FindingStatus>;
  onToggleStatus: (status: FindingStatus) => void;
  componentFilter: string;
  onComponentChange: (value: string) => void;
  search: string;
  onSearchChange: (value: string) => void;
  designators: string[];
}

const STATUSES: { key: FindingStatus; label: string; activeClass: string }[] = [
  { key: "ERROR", label: "Error", activeClass: "bg-rose-500/20 text-rose-600 dark:text-rose-400 border-rose-500/40" },
  { key: "WARNING", label: "Warning", activeClass: "bg-amber-500/20 text-amber-600 dark:text-amber-400 border-amber-500/40" },
  { key: "INFO", label: "Info", activeClass: "bg-blue-500/20 text-blue-600 dark:text-blue-400 border-blue-500/40" },
];

export function ReportFilters({
  statusFilters,
  onToggleStatus,
  componentFilter,
  onComponentChange,
  search,
  onSearchChange,
  designators,
}: ReportFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="flex items-center gap-1.5">
        {STATUSES.map(({ key, label, activeClass }) => (
          <Button
            key={key}
            variant="outline"
            size="sm"
            className={cn(
              "h-8 text-xs",
              statusFilters.has(key) && activeClass
            )}
            onClick={() => onToggleStatus(key)}
          >
            {label}
          </Button>
        ))}
      </div>

      <Select value={componentFilter} onValueChange={(v) => onComponentChange(v ?? "all")}>
        <SelectTrigger className="w-[140px] h-8 text-xs">
          <SelectValue placeholder="All components" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All components</SelectItem>
          {designators.map((d) => (
            <SelectItem key={d} value={d}>
              <span className="font-mono">{d}</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <div className="relative flex-1 min-w-[200px]">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
        <Input
          placeholder="Search findings..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="h-8 pl-8 text-xs"
        />
      </div>
    </div>
  );
}
