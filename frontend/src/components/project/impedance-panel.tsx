"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { computeImpedance } from "@/lib/api";
import type {
  ImpedanceKind,
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

export function ImpedancePanel() {
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

  const needsGap = kind === "cpw" || kind === "diff";

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Impedance calculator</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            IPC-2141 / Hammerstad–Jensen from ImpedenceFinder (same as KiCad
            pcb_calculator). Advice only — this tab does not sample the PCB
            and does not emit findings. CPWG is not implemented.
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

      {stackup && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Suggested widths (apply in KiCad)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="py-1">Target</th>
                  <th>w mm</th>
                  <th>s mm</th>
                  <th>Z</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(stackup.targets).map(([key, row]) => (
                  <tr key={key} className="border-t border-border">
                    <td className="py-1">{key}</td>
                    <td className="tabular-nums">{fmt(row.w_mm, 4)}</td>
                    <td className="tabular-nums">{fmt(row.s_mm, 4)}</td>
                    <td className="tabular-nums">
                      {row.z0 != null ? `${fmt(row.z0)} Ω` : `${fmt(row.zdiff)} Ω diff`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Button variant="outline" onClick={downloadDru}>
              Download pinscope.kicad_dru
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
