"use client";

import { useMemo } from "react";
import { LegalPageShell, MarkdownContent } from "@/components/legal/legal-page";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/**
 * The h2 heading that introduces the per-tool export instructions. The body
 * of this section is split on `### Tool Name` and rendered as tabs; anything
 * before or after the section renders as plain markdown.
 */
const EXPORT_HEADING = "## Exporting from your EDA tool";

type EdaSection = {
  /** Display name from the `### …` line, e.g. "KiCad". */
  name: string;
  /** Body markdown after the `### …` line (no h3 heading itself). */
  body: string;
};

type ParsedGuide = {
  before: string;
  tabs: EdaSection[];
  after: string;
};

/**
 * Splits the markdown into (pre-export markdown, EDA tab sections, post-export markdown).
 * If the document doesn't follow the expected shape, falls back to rendering everything
 * as a single markdown block — the page degrades gracefully if content/file-guide.md
 * gets restructured.
 */
function parseGuide(content: string): ParsedGuide {
  const exportIdx = content.indexOf(`\n${EXPORT_HEADING}\n`);
  if (exportIdx === -1) {
    return { before: content, tabs: [], after: "" };
  }
  const before = content.slice(0, exportIdx).trimEnd();
  const afterHeading = content.slice(exportIdx + EXPORT_HEADING.length + 2); // skip heading + leading newline

  // The export section ends at the next h2 heading.
  const nextH2 = afterHeading.search(/\n## [^\n]/);
  const exportBody = nextH2 === -1 ? afterHeading : afterHeading.slice(0, nextH2);
  const after = nextH2 === -1 ? "" : afterHeading.slice(nextH2).trimStart();

  // Split on `### ` at line start to get one entry per tool.
  const parts = exportBody.split(/\n### /);
  // First part is whatever sits between `## Exporting…` and the first `### Tool` (usually empty).
  const intro = parts.shift()?.trim() ?? "";
  const tabs: EdaSection[] = parts.map((chunk) => {
    const newlineIdx = chunk.indexOf("\n");
    const name = (newlineIdx === -1 ? chunk : chunk.slice(0, newlineIdx)).trim();
    const body = newlineIdx === -1 ? "" : chunk.slice(newlineIdx + 1).trim();
    return { name, body };
  });

  // If there was intro prose, prepend it to the `before` block so it still renders.
  return {
    before: intro ? `${before}\n\n${EXPORT_HEADING}\n\n${intro}` : `${before}\n\n${EXPORT_HEADING}`,
    tabs,
    after,
  };
}

/** Slug for the tab value (stable identifier independent of label changes). */
function tabSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function FileGuidePage({ content }: { content: string }) {
  const guide = useMemo(() => parseGuide(content), [content]);

  if (guide.tabs.length === 0) {
    return (
      <LegalPageShell>
        <MarkdownContent>{content}</MarkdownContent>
      </LegalPageShell>
    );
  }

  const defaultValue = tabSlug(guide.tabs[0].name);

  return (
    <LegalPageShell>
      <MarkdownContent>{guide.before}</MarkdownContent>

      <Tabs defaultValue={defaultValue} className="mb-10">
        <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1 bg-transparent p-0 mb-4 border-b border-border/50 rounded-none">
          {guide.tabs.map((tab) => (
            <TabsTrigger
              key={tabSlug(tab.name)}
              value={tabSlug(tab.name)}
              className="rounded-md px-3 py-1.5 text-sm font-medium data-active:bg-muted data-active:text-foreground"
            >
              {tab.name}
            </TabsTrigger>
          ))}
        </TabsList>
        {guide.tabs.map((tab) => (
          <TabsContent
            key={tabSlug(tab.name)}
            value={tabSlug(tab.name)}
            className="pt-2"
          >
            <MarkdownContent>{tab.body}</MarkdownContent>
          </TabsContent>
        ))}
      </Tabs>

      {guide.after && <MarkdownContent>{guide.after}</MarkdownContent>}
    </LegalPageShell>
  );
}
