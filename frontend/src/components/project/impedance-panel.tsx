"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  analyzeImpedanceNets,
  computeImpedance,
  designAntenna,
  fetchAntennaReport,
  fetchImpedanceNets,
} from "@/lib/api";
import { PcbUploadButton } from "@/components/project/pcb-upload";
import type {
  AntennaReport,
  ImpedanceKind,
  ImpedanceNetsReport,
  ImpedanceStackupResult,
  ImpedanceTraceResult,
} from "@/lib/types";

function num(v: string): number {
  return Number.parseFloat(v);
}

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}

export function ImpedancePanel({
  projectId,
  hasPcb,
  onPcbUploaded,
}: {
  projectId: string;
  hasPcb: boolean;
  onPcbUploaded?: () => void;
}) {
  const [kind, setKind] = useState<ImpedanceKind>("microstrip");
  const [h, setH] = useState("0.20");
  const [er, setEr] = useState("4.5");
  const [t, setT] = useState("0.035");
  const [w, setW] = useState("0.35");
  const [s, setS] = useState("0.20");
  const [targetZ, setTargetZ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [trace, setTrace] = useState<ImpedanceTraceResult | null>(null);
  const [stackup, setStackup] = useState<ImpedanceStackupResult | null>(null);
  const [boardNets, setBoardNets] = useState<ImpedanceNetsReport | null>(null);
  const [extraNets, setExtraNets] = useState("");
  const [antenna, setAntenna] = useState<AntennaReport | null>(null);
  const [f0, setF0] = useState("2440");
  const [antBusy, setAntBusy] = useState(false);
  const [antError, setAntError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchImpedanceNets(projectId)
      .then((r) => {
        if (!cancelled) setBoardNets(r);
      })
      .catch(() => {
        if (!cancelled) setBoardNets(null);
      });
    fetchAntennaReport(projectId)
      .then((r) => {
        if (!cancelled) setAntenna(r);
      })
      .catch(() => {
        if (!cancelled) setAntenna(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  async function runTrace() {
    setBusy(true);
    setError(null);
    try {
      const tz = targetZ.trim() === "" ? null : num(targetZ);
      const result = (await computeImpedance({
        mode: "trace",
        kind,
        h: num(h),
        er: num(er),
        t: num(t),
        w: tz != null ? null : num(w),
        s: kind === "microstrip" || kind === "stripline" ? null : num(s),
        target_z: tz,
      })) as ImpedanceTraceResult;
      setTrace(result);
      if (result.w_mm != null) setW(result.w_mm.toFixed(4));
    } catch (e) {
      setTrace(null);
      setError(e instanceof Error ? e.message : "Compute failed");
    } finally {
      setBusy(false);
    }
  }

  async function runStackup() {
    setBusy(true);
    setError(null);
    try {
      const result = (await computeImpedance({
        mode: "stackup",
        h: num(h),
        er: num(er),
        t: num(t),
        s: num(s),
      })) as ImpedanceStackupResult;
      setStackup(result);
    } catch (e) {
      setStackup(null);
      setError(e instanceof Error ? e.message : "Stackup failed");
    } finally {
      setBusy(false);
    }
  }

  function downloadDru() {
    if (!stackup?.kicad_dru) return;
    const blob = new Blob([stackup.kicad_dru], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "pinscope.kicad_dru";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function runSpecifiedNets() {
    const names = extraNets
      .split(/[\s,]+/)
      .map((n) => n.trim())
      .filter(Boolean);
    if (names.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      setBoardNets(await analyzeImpedanceNets(projectId, names));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Net analysis failed");
    } finally {
      setBusy(false);
    }
  }

  async function runAntennaDesign() {
    setAntBusy(true);
    setAntError(null);
    try {
      const f0n = f0.trim() === "" ? null : num(f0);
      const report = await designAntenna(projectId, {
        f0_mhz: f0n != null && !Number.isNaN(f0n) ? f0n : null,
        target_z_ohm: 50,
        h: num(h),
        er: num(er),
        t: num(t),
      });
      setAntenna(report);
    } catch (e) {
      setAntError(e instanceof Error ? e.message : "Antenna design failed");
    } finally {
      setAntBusy(false);
    }
  }

  function copyRecipe() {
    if (!antenna?.design) return;
    void navigator.clipboard.writeText(JSON.stringify(antenna.design, null, 2));
  }

  const needsGap = kind === "cpw" || kind === "diff";
  const design = antenna?.design;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Verify antenna / RF feed</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {antenna?.marker_help ??
              "Matching from IC ANT/RF pins toward ANT* / ANT_FEED. Feed Z0 when PCB nets were analyzed."}
          </p>
          {antenna && antenna.verify.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No RF ports detected on IC pins (ANT/RF…). Modules with an internal
              antenna may show nothing here — use Progetta with an ANT* marker.
            </p>
          )}
          {antenna && antenna.verify.length > 0 && (
            <div className="space-y-2">
              {antenna.verify.map((row) => (
                <div
                  key={`${row.ic_ref}-${row.pin}-${row.net}`}
                  className="rounded-lg border p-3 text-sm space-y-1"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono font-medium">
                      {row.ic_ref}.{row.pin}
                    </span>
                    <code className="text-xs">{row.net}</code>
                    <Badge variant="outline">{row.topology}</Badge>
                    <Badge
                      variant={row.status === "warning" ? "destructive" : "secondary"}
                    >
                      {row.status}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">{row.detail}</p>
                  {row.parts.length > 0 && (
                    <p className="text-xs text-muted-foreground">
                      Parts: {row.parts.join(", ")}
                    </p>
                  )}
                  {(row.feed_z0 != null || row.feed_length_mm != null) && (
                    <p className="text-xs tabular-nums">
                      Feed Z0 {fmt(row.feed_z0)} Ω · {fmt(row.feed_length_mm)} mm
                      {row.marker_ref ? ` · marker ${row.marker_ref}` : ""}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Progetta antenna (ricetta)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Mark the feed join in KiCad (<code className="text-xs">ANT*</code>{" "}
            footprint or net <code className="text-xs">ANT_FEED</code>). Optional
            zone net <code className="text-xs">antenna</code>. Auto-draw in the
            zone comes later — this returns w / Z0 / length to draw by hand.
          </p>
          {!hasPcb && (
            <PcbUploadButton projectId={projectId} onUploaded={onPcbUploaded} />
          )}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <label className="space-y-1">
              <Label>f0 (MHz)</Label>
              <Input value={f0} onChange={(e) => setF0(e.target.value)} placeholder="2440" />
            </label>
            <label className="space-y-1">
              <Label>h (mm)</Label>
              <Input value={h} onChange={(e) => setH(e.target.value)} />
            </label>
            <label className="space-y-1">
              <Label>εr</Label>
              <Input value={er} onChange={(e) => setEr(e.target.value)} />
            </label>
            <label className="space-y-1">
              <Label>t (mm)</Label>
              <Input value={t} onChange={(e) => setT(e.target.value)} />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={runAntennaDesign} disabled={antBusy}>
              {antBusy ? "Computing…" : "Compute recipe"}
            </Button>
            <Button
              variant="outline"
              onClick={copyRecipe}
              disabled={!design || design.status !== "ready"}
            >
              Copy JSON
            </Button>
          </div>
          {antError && <p className="text-sm text-destructive">{antError}</p>}
          {design && (
            <div className="rounded-lg border p-3 text-sm space-y-2">
              <div className="flex flex-wrap gap-2 items-center">
                <Badge variant="secondary">{design.status}</Badge>
                <span className="text-muted-foreground text-xs">{design.detail}</span>
              </div>
              {design.feed_line && (
                <p className="tabular-nums">
                  Feed microstrip @ {design.feed_line.target_z_ohm} Ω → w ={" "}
                  {fmt(design.feed_line.w_mm, 4)} mm (h={fmt(design.feed_line.h_mm)},
                  εr={fmt(design.feed_line.er)})
                </p>
              )}
              {design.radiator?.length_mm_suggest != null && (
                <p className="tabular-nums text-xs text-muted-foreground">
                  λ/4 suggest ≈ {fmt(design.radiator.length_mm_suggest)} mm at{" "}
                  {fmt(design.radiator.f0_mhz, 0)} MHz — {design.radiator.note}
                </p>
              )}
              {design.zone && (
                <p className="text-xs text-muted-foreground">
                  Zone {design.zone.net} on {design.zone.layer}
                  {design.zone.bbox_mm
                    ? ` · bbox [${design.zone.bbox_mm.map((n) => n.toFixed(1)).join(", ")}]`
                    : ""}
                </p>
              )}
              {design.keepout_checklist.length > 0 && (
                <ul className="list-disc pl-4 text-xs text-muted-foreground space-y-0.5">
                  {design.keepout_checklist.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Impedance calculator</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            ImpedenceFinder (Hammerstad–Jensen). The calculator is advice only.
            With a `.kicad_pcb` and stackup, a pipeline run samples routed signal
            nets. CPWG is not implemented upstream.
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <label className="space-y-1">
              <Label>Kind</Label>
              <select
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm"
                value={kind}
                onChange={(e) => setKind(e.target.value as ImpedanceKind)}
              >
                <option value="microstrip">Microstrip</option>
                <option value="stripline">Stripline</option>
                <option value="diff">Coupled diff</option>
              </select>
            </label>
            <label className="space-y-1">
              <Label>h (mm)</Label>
              <Input value={h} onChange={(e) => setH(e.target.value)} />
            </label>
            <label className="space-y-1">
              <Label>εr</Label>
              <Input value={er} onChange={(e) => setEr(e.target.value)} />
            </label>
            <label className="space-y-1">
              <Label>t (mm)</Label>
              <Input value={t} onChange={(e) => setT(e.target.value)} />
            </label>
            <label className="space-y-1">
              <Label>w (mm)</Label>
              <Input value={w} onChange={(e) => setW(e.target.value)} />
            </label>
            <label className="space-y-1">
              <Label>s gap (mm)</Label>
              <Input
                value={s}
                onChange={(e) => setS(e.target.value)}
                disabled={!needsGap && !stackup}
              />
            </label>
            <label className="space-y-1 col-span-2 sm:col-span-1">
              <Label>Target Z0 (optional)</Label>
              <Input
                value={targetZ}
                onChange={(e) => setTargetZ(e.target.value)}
                placeholder="solve w"
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={runTrace} disabled={busy}>
              Compute Z
            </Button>
            <Button variant="outline" onClick={runStackup} disabled={busy}>
              Stackup 50 / 90 / 100
            </Button>
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          {trace && (
            <p className="text-sm tabular-nums">
              {trace.z0 != null && <>Z0 = {fmt(trace.z0)} Ω</>}
              {trace.zdiff != null && (
                <>
                  {" "}
                  Zdiff = {fmt(trace.zdiff)} Ω (odd {fmt(trace.zodd)}, even{" "}
                  {fmt(trace.zeven)})
                </>
              )}
              {trace.w_mm != null && <> · w = {fmt(trace.w_mm, 4)} mm</>}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">PCB net Z0 (ImpedenceFinder)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {!hasPcb && (
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">
                Upload a `.kicad_pcb`, then re-run the pipeline. Power/ground
                nets are skipped; signal traces with stackup εr/h are sampled.
              </p>
              <PcbUploadButton projectId={projectId} onUploaded={onPcbUploaded} />
            </div>
          )}
          {hasPcb && boardNets?.skipped && (
            <p className="text-sm text-muted-foreground">{boardNets.skipped}</p>
          )}
          {boardNets && boardNets.nets.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="py-1">Net</th>
                  <th>Z0 avg Ω</th>
                  <th>min–max</th>
                  <th>mm</th>
                  <th>topology</th>
                </tr>
              </thead>
              <tbody>
                {boardNets.nets.map((row) => (
                  <tr key={row.net_name} className="border-t border-border">
                    <td className="py-1">{row.net_name}</td>
                    <td className="tabular-nums">
                      {row.error ?? fmt(row.z0_avg_ohms)}
                    </td>
                    <td className="tabular-nums">
                      {row.z0_min_ohms != null
                        ? `${fmt(row.z0_min_ohms)}–${fmt(row.z0_max_ohms)}`
                        : "—"}
                    </td>
                    <td className="tabular-nums">{fmt(row.length_mm, 2)}</td>
                    <td>{(row.topologies || []).join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {hasPcb && (
            <div className="flex flex-wrap items-end gap-2">
              <label className="min-w-40 flex-1 space-y-1">
                <Label>Extra nets</Label>
                <Input
                  value={extraNets}
                  onChange={(e) => setExtraNets(e.target.value)}
                  placeholder="/USB.D+ /USB.D-"
                />
              </label>
              <Button
                variant="outline"
                onClick={runSpecifiedNets}
                disabled={busy}
              >
                Analyze named nets
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {stackup && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Suggested widths (apply in KiCad)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-1 text-sm tabular-nums">
              {Object.entries(stackup.targets).map(([k, v]) => (
                <div key={k}>
                  {k}: w={fmt(v.w_mm, 4)} mm
                  {v.z0 != null && <> · Z0={fmt(v.z0)}</>}
                  {v.zdiff != null && <> · Zdiff={fmt(v.zdiff)}</>}
                </div>
              ))}
            </div>
            <Button variant="outline" size="sm" onClick={downloadDru}>
              Download .kicad_dru advice
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
