"use client";

import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { PdfViewerPanel } from "./pdf-viewer-panel";

interface PdfViewerSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  mpn: string | null;
  initialPage: number;
  highlightQuote?: string;
}

export function PdfViewerSheet({ open, onOpenChange, projectId, mpn, initialPage, highlightQuote }: PdfViewerSheetProps) {
  if (!mpn) return null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="sm:max-w-none p-0 flex flex-col"
        style={{ maxWidth: "none", width: "fit-content" }}
      >
        <SheetTitle className="sr-only">{mpn}</SheetTitle>
        {open && (
          <PdfViewerPanel
            projectId={projectId}
            mpn={mpn}
            initialPage={initialPage}
            highlightQuote={highlightQuote}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}
