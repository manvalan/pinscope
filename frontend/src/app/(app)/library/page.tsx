"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Cpu,
  Library,
  Loader2,
  FileText,
  Zap,
  ExternalLink,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  fetchLibrary,
  fetchLibraryDatasheetUrl,
  type LibraryCatalog,
} from "@/lib/api";

export default function LibraryPage() {
  const [data, setData] = useState<LibraryCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [opening, setOpening] = useState<string | null>(null);

  useEffect(() => {
    fetchLibrary()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load library"))
      .finally(() => setLoading(false));
  }, []);

  const lf = filter.trim().toLowerCase();
  const ics = useMemo(
    () =>
      (data?.ics ?? []).filter(
        (c) =>
          !lf ||
          c.mpn.toLowerCase().includes(lf) ||
          c.subtype.toLowerCase().includes(lf),
      ),
    [data, lf],
  );
  const passives = useMemo(
    () =>
      (data?.passives ?? []).filter(
        (c) =>
          !lf ||
          c.mpn.toLowerCase().includes(lf) ||
          c.subtype.toLowerCase().includes(lf) ||
          c.description.toLowerCase().includes(lf),
      ),
    [data, lf],
  );
  const simple = useMemo(
    () =>
      (data?.simple ?? []).filter(
        (c) =>
          !lf ||
          c.mpn.toLowerCase().includes(lf) ||
          c.subtype.toLowerCase().includes(lf) ||
          c.specs_type.toLowerCase().includes(lf),
      ),
    [data, lf],
  );
  const datasheets = useMemo(
    () =>
      (data?.datasheets ?? []).filter(
        (c) => !lf || c.mpn.toLowerCase().includes(lf),
      ),
    [data, lf],
  );

  async function openDatasheet(mpn: string) {
    setOpening(mpn);
    try {
      const url = await fetchLibraryDatasheetUrl(mpn);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
    } finally {
      setOpening(null);
    }
  }

  if (loading) {
    return (
      <div className="flex-1 p-6 max-w-5xl mx-auto w-full space-y-4">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-4 w-96" />
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-12 rounded-lg" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 p-6 max-w-5xl mx-auto w-full">
        <p className="text-sm text-destructive">{error}</p>
      </div>
    );
  }

  const empty =
    !data ||
    (data.ics.length === 0 &&
      data.passives.length === 0 &&
      data.simple.length === 0 &&
      data.datasheets.length === 0);

  return (
    <div className="flex-1 p-6 max-w-5xl mx-auto w-full space-y-6">
      <div>
        <h1 className="text-lg font-semibold">Component library</h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
          Datasheets, pin tables, and passive specs are stored once and reused
          on every later project. A second board with the same CH340E will not
          re-download the PDF or re-extract the pin table.
        </p>
      </div>

      {empty ? (
        <div className="rounded-lg border border-border bg-card p-12 text-center space-y-3">
          <Library className="h-8 w-8 text-muted-foreground mx-auto" />
          <p className="text-sm font-medium">Library is empty</p>
          <p className="text-sm text-muted-foreground max-w-md mx-auto">
            Create a project and run a review. Fetched datasheets land here
            immediately; pin tables and passive patterns arrive after the first
            extraction.
          </p>
        </div>
      ) : (
        <>
          <div className="flex flex-col sm:flex-row sm:items-center gap-3">
            <div className="flex flex-wrap items-center gap-4 text-sm">
              <span className="flex items-center gap-1.5">
                <Cpu className="h-4 w-4 text-blue-600 dark:text-blue-400" />
                <span className="font-medium">{data.ics.length}</span>
                <span className="text-muted-foreground">ICs</span>
              </span>
              <span className="flex items-center gap-1.5">
                <Zap className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                <span className="font-medium">{data.passives.length}</span>
                <span className="text-muted-foreground">passive series</span>
              </span>
              <span className="flex items-center gap-1.5">
                <Zap className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                <span className="font-medium">{data.simple.length}</span>
                <span className="text-muted-foreground">discrete</span>
              </span>
              <span className="flex items-center gap-1.5">
                <FileText className="h-4 w-4 text-muted-foreground" />
                <span className="font-medium">{data.datasheets.length}</span>
                <span className="text-muted-foreground">datasheets</span>
              </span>
            </div>
            <Input
              placeholder="Filter by MPN or type…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="sm:ml-auto sm:w-64"
            />
          </div>

          <Tabs defaultValue="ics">
            <TabsList>
              <TabsTrigger value="ics">ICs ({ics.length})</TabsTrigger>
              <TabsTrigger value="passives">
                Passives ({passives.length})
              </TabsTrigger>
              <TabsTrigger value="simple">
                Discrete ({simple.length})
              </TabsTrigger>
              <TabsTrigger value="datasheets">
                Datasheets ({datasheets.length})
              </TabsTrigger>
            </TabsList>

            <TabsContent value="ics" className="pt-4">
              {ics.length === 0 ? (
                <EmptyFilter label="ICs" />
              ) : (
                <div className="rounded-lg border border-border overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-muted/50 text-muted-foreground">
                        <th className="text-left px-3 py-2 font-medium">MPN</th>
                        <th className="text-left px-3 py-2 font-medium">Type</th>
                        <th className="text-center px-3 py-2 font-medium">Pins</th>
                        <th className="text-left px-3 py-2 font-medium">Cached</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {ics.map((ic) => (
                        <tr key={ic.mpn} className="hover:bg-muted/30">
                          <td className="px-3 py-2 font-mono text-xs">{ic.mpn}</td>
                          <td className="px-3 py-2">
                            {ic.subtype ? (
                              <Badge variant="secondary">{ic.subtype}</Badge>
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-center">{ic.pin_count}</td>
                          <td className="px-3 py-2">
                            <div className="flex flex-wrap gap-1">
                              <Badge variant="outline">pin table</Badge>
                              {ic.has_datasheet && (
                                <DatasheetLink
                                  mpn={ic.mpn}
                                  opening={opening}
                                  onOpen={openDatasheet}
                                />
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </TabsContent>

            <TabsContent value="passives" className="pt-4">
              {passives.length === 0 ? (
                <EmptyFilter label="passive series" />
              ) : (
                <div className="rounded-lg border border-border overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-muted/50 text-muted-foreground">
                        <th className="text-left px-3 py-2 font-medium">Series</th>
                        <th className="text-left px-3 py-2 font-medium">Type</th>
                        <th className="text-left px-3 py-2 font-medium">
                          Description
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {passives.map((p) => (
                        <tr key={p.mpn} className="hover:bg-muted/30">
                          <td className="px-3 py-2 font-mono text-xs">{p.mpn}</td>
                          <td className="px-3 py-2">
                            {p.subtype ? (
                              <Badge variant="secondary">{p.subtype}</Badge>
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">
                            {p.description || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </TabsContent>

            <TabsContent value="simple" className="pt-4">
              {simple.length === 0 ? (
                <EmptyFilter label="discrete parts" />
              ) : (
                <div className="rounded-lg border border-border overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-muted/50 text-muted-foreground">
                        <th className="text-left px-3 py-2 font-medium">MPN</th>
                        <th className="text-left px-3 py-2 font-medium">Kind</th>
                        <th className="text-center px-3 py-2 font-medium">
                          Params
                        </th>
                        <th className="text-left px-3 py-2 font-medium">PDF</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {simple.map((s) => (
                        <tr key={s.mpn} className="hover:bg-muted/30">
                          <td className="px-3 py-2 font-mono text-xs">{s.mpn}</td>
                          <td className="px-3 py-2">
                            <Badge variant="secondary">
                              {s.subtype || s.specs_type || "discrete"}
                            </Badge>
                          </td>
                          <td className="px-3 py-2 text-center">{s.param_count}</td>
                          <td className="px-3 py-2">
                            {s.has_datasheet ? (
                              <DatasheetLink
                                mpn={s.mpn}
                                opening={opening}
                                onOpen={openDatasheet}
                              />
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </TabsContent>

            <TabsContent value="datasheets" className="pt-4">
              {datasheets.length === 0 ? (
                <EmptyFilter label="datasheets" />
              ) : (
                <div className="rounded-lg border border-border overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-muted/50 text-muted-foreground">
                        <th className="text-left px-3 py-2 font-medium">MPN</th>
                        <th className="text-left px-3 py-2 font-medium">Status</th>
                        <th className="text-left px-3 py-2 font-medium">PDF</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {datasheets.map((d) => (
                        <tr key={d.mpn} className="hover:bg-muted/30">
                          <td className="px-3 py-2 font-mono text-xs">{d.mpn}</td>
                          <td className="px-3 py-2">
                            {d.has_extraction ? (
                              <Badge variant="outline">pin table ready</Badge>
                            ) : d.has_model ? (
                              <Badge variant="outline">specs ready</Badge>
                            ) : (
                              <span className="text-xs text-muted-foreground">
                                PDF saved — extract on next review
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            <DatasheetLink
                              mpn={d.mpn}
                              opening={opening}
                              onOpen={openDatasheet}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}

function EmptyFilter({ label }: { label: string }) {
  return (
    <p className="text-sm text-muted-foreground py-8 text-center">
      No {label} match this filter.
    </p>
  );
}

function DatasheetLink({
  mpn,
  opening,
  onOpen,
}: {
  mpn: string;
  opening: string | null;
  onOpen: (mpn: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(mpn)}
      className="inline-flex items-center gap-1 text-xs text-blue-600 dark:text-blue-400 hover:underline"
    >
      {opening === mpn ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : (
        <ExternalLink className="h-3 w-3" />
      )}
      PDF
    </button>
  );
}
