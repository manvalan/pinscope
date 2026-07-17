import { Cpu, ArrowLeft } from "lucide-react";
import Link from "next/link";

type Tag = "New" | "Improved" | "Fixed";

interface ChangelogEntry {
  version: string;
  date: string;
  title?: string;
  description?: string;
  items: { tag: Tag; text: string }[];
}

function parseChangelog(md: string): ChangelogEntry[] {
  const lines = md.split("\n");
  const entries: ChangelogEntry[] = [];
  let current: ChangelogEntry | null = null;
  let descLines: string[] = [];

  const finalize = () => {
    if (!current) return;
    const desc = descLines.join(" ").trim();
    if (desc) current.description = desc;
    entries.push(current);
    current = null;
    descLines = [];
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (line.startsWith("## ")) {
      finalize();
      const parts = line.slice(3).split(" — ");
      current = {
        version: parts[0]?.trim() ?? "",
        date: parts[1]?.trim() ?? "",
        title: parts[2]?.trim() || undefined,
        items: [],
      };
    } else if (line.startsWith("- ") && current) {
      const text = line.slice(2);
      const m = text.match(/^\[(New|Improved|Fixed)\]\s*(.+)$/);
      if (m) {
        current.items.push({ tag: m[1] as Tag, text: m[2] });
      } else {
        current.items.push({ tag: "Improved", text });
      }
    } else if (current && current.items.length === 0 && line.trim() && !line.startsWith("#")) {
      descLines.push(line.trim());
    }
  }
  finalize();
  return entries;
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

const TAG_STYLES: Record<Tag, string> = {
  New: "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  Improved: "border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-400",
  Fixed: "border-orange-500/40 bg-orange-500/10 text-orange-600 dark:text-orange-400",
};

function TagPill({ tag }: { tag: Tag }) {
  return (
    <span
      className={`inline-flex h-5 w-[68px] flex-shrink-0 items-center justify-center rounded-full border px-2 text-[11px] font-medium ${TAG_STYLES[tag]}`}
    >
      {tag}
    </span>
  );
}

export function ChangelogTimeline({ content }: { content: string }) {
  const entries = parseChangelog(content);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border/50">
        <div className="mx-auto max-w-3xl px-6 py-6 flex items-center justify-between">
          <Link
            href="/"
            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to home
          </Link>
          <Link href="/" className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-blue-500" />
            <span className="text-sm font-medium">Pinscope</span>
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-12">
        <div className="mb-12">
          <h1 className="text-3xl font-bold tracking-tight mb-2">Changelog</h1>
          <p className="text-sm text-muted-foreground">What's new in Pinscope.</p>
        </div>

        <ol className="relative">
          {entries.map((entry, i) => {
            const isLast = i === entries.length - 1;
            return (
              <li key={entry.version} className="relative pl-10 pb-12 last:pb-0">
                <span
                  aria-hidden
                  className="absolute left-[5px] top-3 h-3 w-3 rounded-full border-2 border-border bg-background"
                />
                {!isLast && (
                  <span
                    aria-hidden
                    className="absolute left-[10px] top-7 bottom-0 w-px bg-border/60"
                  />
                )}
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-2">
                  <span>{formatDate(entry.date)}</span>
                  {entry.version && (
                    <>
                      <span aria-hidden className="text-muted-foreground/50">·</span>
                      <span className="font-mono text-xs">v{entry.version}</span>
                    </>
                  )}
                </div>
                {entry.title && (
                  <h2 className="text-2xl font-bold tracking-tight mb-2">
                    {entry.title}
                  </h2>
                )}
                {entry.description && (
                  <p className="text-sm text-muted-foreground leading-relaxed mb-5">
                    {entry.description}
                  </p>
                )}
                <ul className="space-y-2.5">
                  {entry.items.map((item, j) => (
                    <li key={j} className="flex items-start gap-3 text-sm">
                      <TagPill tag={item.tag} />
                      <span className="text-muted-foreground leading-relaxed pt-px">
                        {item.text}
                      </span>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ol>
      </main>

      <footer className="border-t border-border/50">
        <div className="mx-auto max-w-3xl px-6 py-8 flex items-center justify-between text-xs text-muted-foreground">
          <div className="flex items-center gap-4">
            <Link href="/changelog" className="hover:text-foreground transition-colors">
              Changelog
            </Link>
            <Link href="/privacy" className="hover:text-foreground transition-colors">
              Privacy Policy
            </Link>
            <Link href="/terms" className="hover:text-foreground transition-colors">
              Terms of Service
            </Link>
          </div>
          <span>&copy; {new Date().getFullYear()} Faradworks</span>
        </div>
      </footer>
    </div>
  );
}
