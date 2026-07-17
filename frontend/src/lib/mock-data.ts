import type { Project, PipelineStep } from "./types";

export const PROJECTS: Project[] = [
  {
    id: "simple_project",
    name: "TI MSP Tutorial Board",
    created: "2026-04-09T00:00:00Z",
    status: "complete",
    summary: { total: 8, ERROR: 2, WARNING: 4, INFO: 2 },
    hasNetlist: true,
    hasBom: true,
    datasheetCount: 3,
  },
];

export function createPipelineSteps(): PipelineStep[] {
  return [
    {
      title: "IC Datasheet Extraction",
      description: "Parse component datasheets and extract constraints",
      status: "pending",
      substeps: [
        { key: "SPX3819M5-L-3-3/TR", label: "SPX3819M5-L-3-3/TR (U1)", status: "pending" },
        { key: "CH340E", label: "CH340E (U2)", status: "pending" },
        { key: "MSPM0G3507SPTR", label: "MSPM0G3507SPTR (U3)", status: "pending" },
      ],
    },
    {
      title: "Passive Pattern Extraction",
      description: "Resolve passive component values from MPN patterns",
      status: "pending",
      substeps: [
        { key: "samsung", label: "Samsung capacitors", status: "pending" },
        { key: "uniroyal", label: "Uniroyal resistors", status: "pending" },
      ],
    },
    {
      title: "Build Design Graph",
      description: "Parse BOM and netlist into structured graph",
      status: "pending",
      substeps: [
        { key: "parse-bom", label: "Parse BOM (17 components)", status: "pending" },
        { key: "parse-netlist", label: "Parse netlist (36 nets)", status: "pending" },
        { key: "enrich", label: "Enrich with datasheet data", status: "pending" },
      ],
    },
    {
      title: "Review Design",
      description: "Review each IC against its datasheet",
      status: "pending",
      substeps: [
        { key: "U1", label: "Review U1 — LDO", status: "pending" },
        { key: "U2", label: "Review U2 — USB Bridge", status: "pending" },
        { key: "U3", label: "Review U3 — MCU", status: "pending" },
      ],
    },
  ];
}
