"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import dynamic from "next/dynamic";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ChevronLeft, ChevronRight, XIcon } from "lucide-react";
import { fetchDatasheetUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { DocumentProps, PageProps } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

const Document = dynamic(
  () => import("react-pdf").then((mod) => {
    // Bundle the worker same-origin (version-matched to the installed
    // pdfjs-dist). A CDN URL breaks on http://localhost — a protocol-relative
    // //unpkg.com resolves to http://unpkg.com there, which the CSP
    // (worker-src 'self' blob:) blocks; it only "worked" on the https deploy.
    mod.pdfjs.GlobalWorkerOptions.workerSrc = new URL(
      "pdfjs-dist/build/pdf.worker.min.mjs",
      import.meta.url,
    ).toString();
    return mod.Document;
  }),
  { ssr: false }
) as React.ComponentType<DocumentProps>;

const Page = dynamic(
  () => import("react-pdf").then((mod) => mod.Page),
  { ssr: false }
) as React.ComponentType<PageProps>;

interface PdfViewerPanelProps {
  projectId: string;
  mpn: string;
  initialPage: number;
  highlightQuote?: string;
  onClose?: () => void;
  className?: string;
}

const HIGHLIGHT_CLASS = "pinscope-quote-hl";

// U+00B5 MICRO SIGN and U+03BC GREEK SMALL MU render identically but the
// model and the PDF often disagree on which one — fold them together.
function foldChars(s: string): string {
  return s.replace(/µ/g, "μ");
}

// Collapse whitespace + soft hyphens + line-break hyphenation, lowercase.
function normalizeQuote(s: string): string {
  return foldChars(s)
    .replace(/­/g, "")
    .replace(/-\s+/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

// Per-span normalizer — keeps internal spacing so concat offsets stay aligned.
function normalizePiece(s: string): string {
  return foldChars(s).replace(/­/g, "").replace(/\s+/g, " ").toLowerCase();
}

function clearHighlights(root: HTMLElement): void {
  root.querySelectorAll<HTMLElement>("." + HIGHLIGHT_CLASS).forEach((el) => {
    el.classList.remove(HIGHLIGHT_CLASS);
    el.style.backgroundColor = "";
  });
}

// Locate `quote` in the rendered text layer and highlight the overlapping
// spans. Returns true on a match. Misses (figure/table evidence or extraction
// mismatch) are silent — the page is already scrolled into view.
function highlightQuoteInLayer(root: HTMLElement, quote: string): boolean {
  const layer = root.querySelector(".react-pdf__Page__textContent");
  clearHighlights(root);
  if (!layer) return false;

  // pdf.js v5 nests text spans inside zero-height `span.markedContent`
  // wrappers. Select only the leaf text spans (role="presentation", the same
  // mapping react-pdf uses) to avoid double-counting text and painting the
  // highlight onto an invisible height:0 wrapper.
  const spans = Array.from(
    layer.querySelectorAll<HTMLElement>('span[role="presentation"]'),
  );
  let concat = "";
  const ranges: { span: HTMLElement; start: number; end: number }[] = [];
  for (const span of spans) {
    const piece = normalizePiece(span.textContent ?? "");
    if (!piece) continue;
    if (concat && !concat.endsWith(" ") && !piece.startsWith(" ")) concat += " ";
    const start = concat.length;
    concat += piece;
    ranges.push({ span, start, end: concat.length });
  }

  const target = normalizeQuote(quote);
  if (target.length < 4) return false;

  let idx = concat.indexOf(target);
  let matchLen = target.length;
  if (idx === -1) {
    // The model injects ellipses, column labels (MIN/TYP/MAX), or reordered
    // cells when quoting tables, so an exact/prefix match fails. Fall back to
    // the longest contiguous run of quote words that appears verbatim.
    const tokens = target.split(" ").filter(Boolean);
    const MIN_TOKENS = 4;
    search: for (let len = tokens.length; len >= MIN_TOKENS; len--) {
      for (let s = 0; s + len <= tokens.length; s++) {
        const at = concat.indexOf(tokens.slice(s, s + len).join(" "));
        if (at !== -1) {
          idx = at;
          matchLen = tokens.slice(s, s + len).join(" ").length;
          break search;
        }
      }
    }
    if (idx === -1) return false;
  }

  const matchEnd = idx + matchLen;
  let first: HTMLElement | null = null;
  for (const r of ranges) {
    if (r.end > idx && r.start < matchEnd) {
      r.span.classList.add(HIGHLIGHT_CLASS);
      r.span.style.backgroundColor = "rgba(245, 158, 11, 0.4)";
      if (!first) first = r.span;
    }
  }
  if (!first) return false;
  first.scrollIntoView({ block: "center", behavior: "smooth" });
  return true;
}

export function PdfViewerPanel({ projectId, mpn, initialPage, highlightQuote, onClose, className }: PdfViewerPanelProps) {
  const [numPages, setNumPages] = useState<number>(0);
  const [pageNumber, setPageNumber] = useState(initialPage);
  const [pageInput, setPageInput] = useState(String(initialPage));
  const [containerHeight, setContainerHeight] = useState<number>(0);
  const [pageAspect, setPageAspect] = useState<number>(8.5 / 11);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const obsRef = useRef<ResizeObserver | null>(null);
  const scrollElRef = useRef<HTMLDivElement | null>(null);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPdfError(null);
    fetchDatasheetUrl(projectId, mpn).then((url) => {
      if (cancelled) {
        if (url) URL.revokeObjectURL(url);
        return;
      }
      if (!url) {
        setPdfError("Datasheet not found");
        return;
      }
      setPdfUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return url; });
    });
    return () => { cancelled = true; };
  }, [projectId, mpn]);

  // Revoke the blob URL on unmount (the sheet wrapper unmounts the panel on
  // close; the effect above only revokes when replacing the URL).
  useEffect(() => { urlRef.current = pdfUrl; }, [pdfUrl]);
  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
  }, []);

  // Navigate when a different finding on the same datasheet is selected —
  // onDocumentLoadSuccess only fires on document (mpn) change.
  useEffect(() => {
    setPageNumber(initialPage);
    setPageInput(String(initialPage));
  }, [initialPage]);

  const containerRef = useCallback((node: HTMLDivElement | null) => {
    if (obsRef.current) {
      obsRef.current.disconnect();
      obsRef.current = null;
    }
    scrollElRef.current = node;
    if (node) {
      const obs = new ResizeObserver(([entry]) => {
        setContainerHeight(entry.contentRect.height);
      });
      obs.observe(node);
      obsRef.current = obs;
    }
  }, []);

  const applyHighlight = useCallback(() => {
    const root = scrollElRef.current;
    if (!root) return;
    if (!highlightQuote) {
      clearHighlights(root);
      return;
    }
    highlightQuoteInLayer(root, highlightQuote);
  }, [highlightQuote]);

  // Re-run when the quote changes but the text layer is already mounted
  // (same mpn + page, different finding). rAF lets pending DOM settle.
  useEffect(() => {
    const id = requestAnimationFrame(applyHighlight);
    return () => cancelAnimationFrame(id);
  }, [applyHighlight, pageNumber, pdfUrl]);

  const onDocumentLoadSuccess = useCallback(({ numPages: n }: { numPages: number }) => {
    setNumPages(n);
    setPageNumber(initialPage);
    setPageInput(String(initialPage));
  }, [initialPage]);

  const goToPage = (n: number) => {
    const clamped = Math.max(1, Math.min(n, numPages));
    setPageNumber(clamped);
    setPageInput(String(clamped));
  };

  return (
    <div
      className={cn("flex h-full max-w-full flex-col", className)}
      style={{
        width: containerHeight > 0
          ? `min(95vw, ${containerHeight * pageAspect}px)`
          : `min(95vw, calc((100vh - 80px) * ${pageAspect}))`,
      }}
    >
      <div className="flex flex-col gap-0.5 px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center">
          <h2 className="text-sm font-mono font-medium">{mpn}</h2>
          {onClose && (
            <Button
              variant="ghost"
              size="icon-sm"
              className="ml-auto"
              onClick={onClose}
              aria-label="Close datasheet"
            >
              <XIcon />
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => goToPage(pageNumber - 1)}
            disabled={pageNumber <= 1}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <div className="flex items-center gap-1 text-xs text-muted-foreground">
            <span>Page</span>
            <Input
              className="h-7 w-12 text-xs text-center px-1"
              value={pageInput}
              onChange={(e) => setPageInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") goToPage(Number(pageInput));
              }}
              onBlur={() => goToPage(Number(pageInput))}
            />
            <span>of {numPages}</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => goToPage(pageNumber + 1)}
            disabled={pageNumber >= numPages}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div ref={containerRef} className="flex-1 overflow-auto bg-muted/50 flex justify-center">
        {pdfError ? (
          <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
            {pdfError}
          </div>
        ) : containerHeight > 0 && pdfUrl ? (
          <Document
            file={pdfUrl}
            onLoadSuccess={onDocumentLoadSuccess}
            onLoadError={() => setPdfError("Failed to load PDF")}
            loading={
              <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
                Loading PDF...
              </div>
            }
          >
            <Page
              pageNumber={pageNumber}
              height={containerHeight}
              onRenderTextLayerSuccess={applyHighlight}
              onLoadSuccess={(page: { originalWidth: number; originalHeight: number }) => {
                if (page.originalHeight > 0) {
                  setPageAspect(page.originalWidth / page.originalHeight);
                }
              }}
              loading={
                <div style={{ height: containerHeight, width: containerHeight * pageAspect }} className="bg-card animate-pulse rounded" />
              }
            />
          </Document>
        ) : null}
      </div>
    </div>
  );
}
