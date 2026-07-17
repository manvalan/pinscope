import { Cpu, ArrowLeft } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import Markdown from "react-markdown";

function extractText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (typeof node === "object" && "props" in node) {
    return extractText((node as { props: { children?: ReactNode } }).props.children);
  }
  return "";
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

/**
 * Shared markdown renderer used by the marketing-style pages (privacy, terms,
 * file-guide). Exported so callers like the file-guide page can reuse the same
 * typography when they render markdown alongside custom components (tabs).
 */
export function MarkdownContent({ children }: { children: string }) {
  return (
    <Markdown
      components={{
        h1: ({ children }) => (
          <h1 className="text-3xl font-bold tracking-tight mb-2">{children}</h1>
        ),
        h2: ({ children }) => (
          <h2
            id={slugify(extractText(children))}
            className="text-xl font-semibold mt-10 mb-4 border-b border-border/50 pb-2 scroll-mt-24"
          >
            {children}
          </h2>
        ),
        h3: ({ children }) => (
          <h3
            id={slugify(extractText(children))}
            className="text-base font-semibold mt-6 mb-2 scroll-mt-24"
          >
            {children}
          </h3>
        ),
        p: ({ children }) => (
          <p className="text-sm text-muted-foreground leading-relaxed mb-4">{children}</p>
        ),
        ul: ({ children }) => (
          <ul className="text-sm text-muted-foreground leading-relaxed mb-4 list-disc pl-6 space-y-1">{children}</ul>
        ),
        li: ({ children }) => (
          <li>{children}</li>
        ),
        strong: ({ children }) => (
          <strong className="text-foreground font-medium">{children}</strong>
        ),
        a: ({ href, children }) => (
          <a href={href} className="text-blue-500 hover:underline">{children}</a>
        ),
        em: ({ children }) => (
          <em className="text-muted-foreground">{children}</em>
        ),
        ol: ({ children }) => (
          <ol className="text-sm text-muted-foreground leading-relaxed mb-4 list-decimal pl-6 space-y-1">{children}</ol>
        ),
        code: ({ className, children, ...props }) => {
          const text = extractText(children);
          const isBlock = text.includes("\n") || Boolean(className);
          if (isBlock) {
            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          }
          return (
            <code className="px-1 py-0.5 rounded bg-muted text-foreground text-[13px] font-mono">
              {children}
            </code>
          );
        },
        pre: ({ children }) => (
          <pre className="mb-4 p-3 rounded-md border border-border/60 bg-muted/40 text-[12px] font-mono leading-relaxed overflow-x-auto">
            {children}
          </pre>
        ),
      }}
    >
      {children}
    </Markdown>
  );
}

/**
 * Marketing-page shell with header, footer, and a single markdown body.
 * Used by /privacy and /terms. File-guide uses its own shell so it can
 * interleave tabs with markdown.
 */
export function LegalPageShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Header */}
      <header className="border-b border-border/50">
        <div className="mx-auto max-w-3xl px-6 py-6 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors">
            <ArrowLeft className="h-4 w-4" />
            Back to home
          </Link>
          <Link href="/" className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-blue-500" />
            <span className="text-sm font-medium">Pinscope</span>
          </Link>
        </div>
      </header>

      {/* Content */}
      <main className="mx-auto max-w-3xl px-6 py-12">
        {children}
      </main>

      {/* Footer */}
      <footer className="border-t border-border/50">
        <div className="mx-auto max-w-3xl px-6 py-8 flex items-center justify-between text-xs text-muted-foreground">
          <div className="flex items-center gap-4">
            <Link href="/changelog" className="hover:text-foreground transition-colors">Changelog</Link>
            <Link href="/privacy" className="hover:text-foreground transition-colors">Privacy Policy</Link>
            <Link href="/terms" className="hover:text-foreground transition-colors">Terms of Service</Link>
          </div>
          <span>&copy; {new Date().getFullYear()} Faradworks</span>
        </div>
      </footer>
    </div>
  );
}

export function LegalPage({ content }: { content: string }) {
  return (
    <LegalPageShell>
      <MarkdownContent>{content}</MarkdownContent>
    </LegalPageShell>
  );
}
