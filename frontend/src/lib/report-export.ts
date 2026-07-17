import * as XLSX from "xlsx";

import type { DesignGraph, Finding, ValidationReport } from "./types";
import { sortFindings } from "./utils";

const HEADER = [
  "Designator",
  "MPN",
  "ID",
  "Severity",
  "Title",
  "Description",
  "Recommendation",
  "Source",
];

// Column widths (in characters), aligned with HEADER order.
const COL_WIDTHS = [12, 20, 12, 10, 44, 60, 50, 24];

// Fold the datasheet reference + page number into a single cell, mirroring the
// finding card's reference button + "Automated check" tag logic.
function formatSource(f: Finding): string {
  if (f.source && f.source !== "review") return "Automated check";
  const ds = f.source_designator || f.designator;
  return `${ds} datasheet` + (f.source_page ? ` p.${f.source_page}` : "");
}

function slugify(name: string): string {
  return name.replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "report";
}

/**
 * Build and download the validation report as an .xlsx workbook (client-side).
 * One "Findings" sheet, one row per finding, sorted ERROR -> WARNING -> INFO.
 */
export function exportReportToExcel(
  report: ValidationReport,
  graph: DesignGraph,
  projectName?: string,
): void {
  const rows = sortFindings(report.findings).map((f) => [
    f.designator,
    f.mpn || graph.components[f.designator]?.mpn || "",
    f.finding_id ?? "",
    f.status,
    f.finding,
    f.why ?? "",
    f.recommendation ?? "",
    formatSource(f),
  ]);

  const ws = XLSX.utils.aoa_to_sheet([HEADER, ...rows]);
  ws["!cols"] = COL_WIDTHS.map((wch) => ({ wch }));

  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Findings");

  const filename = `${slugify(projectName || report.project || "report")}-findings.xlsx`;
  XLSX.writeFile(wb, filename);
}
