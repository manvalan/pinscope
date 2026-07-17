import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import type { Finding, FindingStatus } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function groupBy<T>(items: T[], key: (item: T) => string): Record<string, T[]> {
  const result: Record<string, T[]> = {};
  for (const item of items) {
    const k = key(item);
    (result[k] ??= []).push(item);
  }
  return result;
}

const STATUS_ORDER: Record<FindingStatus, number> = { ERROR: 0, WARNING: 1, INFO: 2 };

export function sortFindings(findings: Finding[]): Finding[] {
  return [...findings].sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status]);
}

export function getFindingKey(finding: Finding, index: number): string {
  return finding.finding_id ?? `${finding.designator}-idx-${index}`;
}

export function subtypeLabel(subtype: string | null): string {
  if (!subtype) return "";
  const parts = subtype.split(".");
  return parts[parts.length - 1]
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
