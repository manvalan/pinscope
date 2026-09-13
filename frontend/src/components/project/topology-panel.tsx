"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { fetchPlacementPlan } from "@/lib/api";
import type { PlacementPlan, PlacementIcGroup, RoleHint } from "@/lib/types";
import { LayoutGrid, Zap, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

const ROLE_STYLES: Record<RoleHint | "ic", string> = {
  ic: "bg-sky-500/15 text-sky-700 dark:text-sky-300 border-sky-500/30",
  decoupling: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30",
  bulk: "bg-teal-500/15 text-teal-700 dark:text-teal-300 border-teal-500/30",
  load_cap: "bg-violet-500/15 text-violet-700 dark:text-violet-300 border-violet-500/30",
  crystal: "bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-300 border-fuchsia-500/30",
  filter: "bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30",
  pullup: "bg-orange-500/15 text-orange-700 dark:text-orange-300 border-orange-500/30",
  series: "bg-rose-500/15 text-rose-700 dark:text-rose-300 border-rose-500/30",
  divider: "bg-pink-500/15 text-pink-700 dark:text-pink-300 border-pink-500/30",
  bridge: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300 border-indigo-500/30",
  other: "bg-muted text-muted-foreground border-border",
};

function RoleBadge({ role, label }: { role: RoleHint | "ic"; label: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] font-mono tabular-nums",
        ROLE_STYLES[role] ?? ROLE_STYLES.other,
      )}
    >
      {label}
      {role !== "ic" && (
        <span className="ml-1 opacity-70 font-sans">{role}</span>
      )}
    </span>
  );
}

function GroupBlock({ group, emphasize }: { group: PlacementIcGroup; emphasize?: boolean }) {
  return (
    <div
      className={cn(
        "rounded-lg border p-3 space-y-2",
        emphasize
          ? "border-sky-500/40 bg-sky-500/[0.06]"
          : "border-border bg-muted/20",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <RoleBadge role="ic" label={group.ref} />
        {group.mpn && (
          <span className="text-xs text-muted-foreground truncate">{group.mpn}</span>
        )}
        {group.component_subtype && (
          <Badge variant="outline" className="text-[10px]">
            {group.component_subtype}
          </Badge>
        )}
      </div>
      {group.satellites.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {group.satellites.map((s) => (
            <RoleBadge
              key={s.ref}
              role={(s.role_hint as RoleHint) || "other"}
              label={s.ref}
            />
          ))}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">No satellite parts classified</p>
      )}
    </div>
  );
}

function EmptyPlan({ projectId }: { projectId: string }) {
  return (
    <Card>
      <CardContent className="py-10 text-center space-y-3">
        <p className="text-sm text-muted-foreground">
          No topology plan yet. Run analysis (graph build writes{" "}
          <code className="text-xs">functional_groups.json</code>) or build a
          placement plan.
        </p>
        <Link href={`/project/${projectId}/placement`}>
          <Button size="sm" variant="outline">
            <LayoutGrid className="h-4 w-4 mr-1" />
            Build placement plan
          </Button>
        </Link>
      </CardContent>
    </Card>
  );
}

function DomainsView({ plan }: { plan: PlacementPlan }) {
  const byRef = useMemo(
    () => Object.fromEntries(plan.groups.map((g) => [g.ref, g])),
    [plan.groups],
  );

  if (plan.domains.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No power domains detected.</p>
    );
  }

  return (
    <div className="space-y-4">
      {plan.domains.map((dom) => {
        const groups = dom.ic_refs
          .map((r) => byRef[r])
          .filter(Boolean) as PlacementIcGroup[];
        return (
          <Card key={dom.domain_id}>
            <CardHeader className="pb-2">
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle className="text-sm font-semibold">{dom.domain_id}</CardTitle>
                <Badge variant="secondary" className="text-[10px]">
                  {groups.length} IC group{groups.length === 1 ? "" : "s"}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                Power nets:{" "}
                {dom.power_nets.length
                  ? dom.power_nets.map((n) => (
                      <code key={n} className="mr-1.5 font-mono text-[11px]">
                        {n}
                      </code>
                    ))
                  : "—"}
              </p>
              {dom.assemble_order.length > 0 && (
                <p className="text-[11px] text-muted-foreground mt-1 font-mono">
                  Assemble: {dom.assemble_order.join(" → ")}
                </p>
              )}
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
                Functional groups
              </p>
              {groups.map((g) => (
                <GroupBlock key={g.ref} group={g} emphasize />
              ))}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

type RailRow = {
  net: string;
  domainIds: string[];
  groups: PlacementIcGroup[];
};

function RailsView({ plan }: { plan: PlacementPlan }) {
  const rails = useMemo(() => {
    const byRef = Object.fromEntries(plan.groups.map((g) => [g.ref, g]));
    const map = new Map<string, { domainIds: Set<string>; groupRefs: Set<string> }>();

    for (const dom of plan.domains) {
      for (const net of dom.power_nets) {
        let entry = map.get(net);
        if (!entry) {
          entry = { domainIds: new Set(), groupRefs: new Set() };
          map.set(net, entry);
        }
        entry.domainIds.add(dom.domain_id);
        for (const iref of dom.ic_refs) entry.groupRefs.add(iref);
      }
    }

    // Also attach groups that list the rail on their nets / satellites
    for (const g of plan.groups) {
      for (const net of g.nets ?? []) {
        const entry = map.get(net);
        if (entry) entry.groupRefs.add(g.ref);
      }
      for (const s of g.satellites) {
        for (const net of s.nets ?? []) {
          const entry = map.get(net);
          if (entry) entry.groupRefs.add(g.ref);
        }
      }
    }

    const rows: RailRow[] = [...map.entries()]
      .map(([net, v]) => ({
        net,
        domainIds: [...v.domainIds].sort(),
        groups: [...v.groupRefs]
          .map((r) => byRef[r])
          .filter(Boolean)
          .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99) || a.ref.localeCompare(b.ref)),
      }))
      .sort((a, b) => a.net.localeCompare(b.net));

    return rows;
  }, [plan]);

  if (rails.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No power rails in the topology plan.</p>
    );
  }

  return (
    <div className="space-y-4">
      {rails.map((rail) => (
        <Card key={rail.net}>
          <CardHeader className="pb-2">
            <div className="flex flex-wrap items-center gap-2">
              <Zap className="h-4 w-4 text-amber-600 dark:text-amber-400" />
              <CardTitle className="text-sm font-mono">{rail.net}</CardTitle>
              <Badge variant="secondary" className="text-[10px]">
                {rail.groups.length} group{rail.groups.length === 1 ? "" : "s"}
              </Badge>
            </div>
            {rail.domainIds.length > 0 && (
              <p className="text-xs text-muted-foreground mt-1">
                Domain{rail.domainIds.length === 1 ? "" : "s"}:{" "}
                {rail.domainIds.join(", ")}
              </p>
            )}
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
              Functional groups on this rail
            </p>
            {rail.groups.map((g) => {
              const onRailSats = g.satellites.filter(
                (s) => (s.nets || []).includes(rail.net),
              );
              const highlight: PlacementIcGroup = {
                ...g,
                satellites:
                  onRailSats.length > 0
                    ? onRailSats
                    : g.satellites.filter((s) =>
                        ["decoupling", "bulk"].includes(s.role_hint || ""),
                      ),
              };
              return <GroupBlock key={g.ref} group={highlight} emphasize />;
            })}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export function TopologyPanel({
  projectId,
  mode,
}: {
  projectId: string;
  mode: "domains" | "rails";
}) {
  const [plan, setPlan] = useState<PlacementPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setMissing(false);
    fetchPlacementPlan(projectId)
      .then((p) => {
        if (!cancelled) {
          setPlan(p);
          setMissing(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPlan(null);
          setMissing(true);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground py-8">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading topology…
      </div>
    );
  }

  if (missing || !plan) {
    return <EmptyPlan projectId={projectId} />;
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold">
          {mode === "domains" ? "Functional domains" : "Power rails"}
        </h2>
        <p className="text-xs text-muted-foreground mt-0.5">
          {mode === "domains"
            ? "Power-net islands with IC functional groups and satellite roles (routing-first, no mm)."
            : "Each supply rail with the functional groups that hang off it."}
        </p>
      </div>
      {mode === "domains" ? <DomainsView plan={plan} /> : <RailsView plan={plan} />}
    </div>
  );
}
