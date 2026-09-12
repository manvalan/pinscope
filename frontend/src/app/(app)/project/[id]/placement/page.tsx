"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PipelineStepper } from "@/components/progress/pipeline-stepper";
import { usePlacementProgress } from "@/hooks/use-placement-progress";
import {
  cancelPlacementPipeline,
  fetchPlacementPlan,
  fetchProject,
} from "@/lib/api";
import {
  ArrowLeft,
  CheckCircle2,
  Loader2,
  OctagonX,
  Ban,
} from "lucide-react";

type Plan = Awaited<ReturnType<typeof fetchPlacementPlan>>;

export default function PlacementPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const [projectName, setProjectName] = useState("");
  const [placementStatus, setPlacementStatus] = useState<string>("draft");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const alreadyDone = placementStatus === "complete";
  const { steps, done, cancelled, error, summary, started } = usePlacementProgress(
    id,
    !alreadyDone && placementStatus !== "draft",
  );

  useEffect(() => {
    fetchProject(id)
      .then((p) => {
        setProjectName(p.name);
        setPlacementStatus(p.placementStatus ?? "draft");
      })
      .catch(() => {});
  }, [id]);

  useEffect(() => {
    if (!alreadyDone && !done) return;
    fetchPlacementPlan(id)
      .then(setPlan)
      .catch(() => setPlan(null));
  }, [id, alreadyDone, done]);

  const handleCancel = async () => {
    setCancelling(true);
    try {
      await cancelPlacementPipeline(id);
    } catch {
      // may already be finished
    } finally {
      setCancelling(false);
    }
  };

  const finished = alreadyDone || done;
  const isRunning = !finished && !cancelled && !error;
  const isQueued = isRunning && !started && !alreadyDone;

  return (
    <div className="flex-1 p-6 max-w-3xl mx-auto w-full space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">Placement plan</h1>
          <p className="text-sm text-muted-foreground">
            {projectName ? `${projectName} · ` : ""}
            Routing-first topology (no millimetres)
          </p>
        </div>
        <Link href={`/project/${id}`}>
          <Button size="sm" variant="outline">
            <ArrowLeft className="h-4 w-4 mr-1" />
            Project
          </Button>
        </Link>
      </div>

      {isQueued && (
        <div className="flex items-center gap-3 p-4 rounded-lg border border-blue-500/30 bg-blue-500/5">
          <Loader2 className="h-5 w-5 text-blue-600 animate-spin" />
          <p className="text-sm">Queued — starting placement worker…</p>
        </div>
      )}

      {isRunning && !isQueued && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Progress</CardTitle>
          </CardHeader>
          <CardContent>
            <PipelineStepper steps={steps} />
            <div className="mt-4">
              <Button
                size="sm"
                variant="outline"
                disabled={cancelling}
                onClick={handleCancel}
              >
                <OctagonX className="h-4 w-4 mr-1" />
                {cancelling ? "Cancelling…" : "Cancel"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {cancelled && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Ban className="h-4 w-4" />
          Placement cancelled
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">Placement failed</p>
          <p className="mt-1 text-muted-foreground">{error}</p>
          <Button
            size="sm"
            className="mt-3"
            variant="outline"
            onClick={() => router.push(`/project/${id}`)}
          >
            Back to project
          </Button>
        </div>
      )}

      {finished && !error && !cancelled && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-4 w-4" />
            Placement plan ready
            {summary && (
              <span className="text-muted-foreground">
                · {summary.domains ?? plan?.domains.length ?? "?"} domains,{" "}
                {summary.groups ?? plan?.groups.length ?? "?"} IC groups
              </span>
            )}
          </div>

          {plan && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Topology</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4 text-sm">
                {plan.domains.map((d) => (
                  <div key={d.domain_id} className="space-y-1">
                    <p className="font-medium">{d.domain_id}</p>
                    <p className="text-xs text-muted-foreground">
                      Power: {d.power_nets.join(", ") || "—"}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      Assemble: {d.assemble_order.join(" → ") || "—"}
                    </p>
                  </div>
                ))}
                {plan.groups.length > 0 && (
                  <div className="border-t pt-3 space-y-2">
                    <p className="font-medium">IC groups</p>
                    {plan.groups.map((g) => (
                      <div key={g.ref} className="text-xs text-muted-foreground">
                        <span className="text-foreground font-medium">{g.ref}</span>
                        {g.mpn ? ` · ${g.mpn}` : ""}
                        {g.component_subtype ? ` · ${g.component_subtype}` : ""}
                        {g.satellites.length > 0 && (
                          <span>
                            {" "}
                            — satellites:{" "}
                            {g.satellites
                              .map((s) => `${s.ref}(${s.role_hint ?? "other"})`)
                              .join(", ")}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
