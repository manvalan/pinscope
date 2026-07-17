"use client";

import { useCallback, useState } from "react";
import { Upload, FileCheck } from "lucide-react";
import { cn } from "@/lib/utils";

interface FileUploadZoneProps {
  label: string;
  accept: string;
  multiple?: boolean;
  files: File[];
  onFilesChange: (files: File[]) => void;
  preloaded?: string[];
}

export function FileUploadZone({
  label,
  accept,
  multiple = false,
  files,
  onFilesChange,
  preloaded,
}: FileUploadZoneProps) {
  const [dragOver, setDragOver] = useState(false);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const dropped = Array.from(e.dataTransfer.files);
      onFilesChange(multiple ? [...files, ...dropped] : dropped.slice(0, 1));
    },
    [files, multiple, onFilesChange]
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const selected = Array.from(e.target.files ?? []);
      onFilesChange(multiple ? [...files, ...selected] : selected.slice(0, 1));
    },
    [files, multiple, onFilesChange]
  );

  const hasFiles = files.length > 0 || (preloaded && preloaded.length > 0);

  return (
    <label
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-6 cursor-pointer transition-colors",
        dragOver ? "border-blue-500 bg-blue-500/5" : "border-border hover:border-foreground/20",
        hasFiles && "border-emerald-500/40 bg-emerald-500/5"
      )}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      {hasFiles ? (
        <FileCheck className="h-6 w-6 text-emerald-600 dark:text-emerald-400" />
      ) : (
        <Upload className="h-6 w-6 text-muted-foreground" />
      )}
      <span className="text-sm font-medium">{label}</span>
      {preloaded && preloaded.length > 0 && (
        <div className="text-xs text-muted-foreground">
          {preloaded.map((f) => (
            <span key={f} className="font-mono block">{f}</span>
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div className="text-xs text-muted-foreground">
          {files.map((f) => (
            <span key={f.name} className="font-mono block">{f.name}</span>
          ))}
        </div>
      )}
      <input type="file" accept={accept} multiple={multiple} className="hidden" onChange={handleChange} />
    </label>
  );
}
