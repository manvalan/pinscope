"use client";

import { useState } from "react";
import { Upload, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { uploadPcb } from "@/lib/api";

export function PcbUploadButton({
  projectId,
  onUploaded,
}: {
  projectId: string;
  onUploaded?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="space-y-1">
      <label className="inline-flex">
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          nativeButton={false}
          render={<span />}
        >
          {busy ? (
            <Loader2 className="h-4 w-4 mr-1 animate-spin" />
          ) : (
            <Upload className="h-4 w-4 mr-1" />
          )}
          {busy ? "Uploading…" : "Upload .kicad_pcb"}
        </Button>
        <input
          type="file"
          accept=".kicad_pcb"
          className="hidden"
          disabled={busy}
          onChange={async (e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (!file) return;
            setBusy(true);
            setError(null);
            try {
              await uploadPcb(projectId, file);
              onUploaded?.();
            } catch (err) {
              setError(err instanceof Error ? err.message : "Upload failed");
            } finally {
              setBusy(false);
            }
          }}
        />
      </label>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
