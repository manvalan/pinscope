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

function withRelativePath(file: File, rel: string): File {
  if ((file as File & { webkitRelativePath?: string }).webkitRelativePath === rel) {
    return file;
  }
  try {
    Object.defineProperty(file, "webkitRelativePath", {
      value: rel,
      configurable: true,
    });
  } catch {
    /* ignore — some File objects are sealed */
  }
  return file;
}

function readDirEntries(
  reader: FileSystemDirectoryReader,
): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    const acc: FileSystemEntry[] = [];
    const pump = () => {
      reader.readEntries((chunk) => {
        if (chunk.length === 0) resolve(acc);
        else {
          acc.push(...chunk);
          pump();
        }
      }, reject);
    };
    pump();
  });
}

async function filesFromEntry(
  entry: FileSystemEntry,
  prefix: string,
): Promise<File[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => {
      (entry as FileSystemFileEntry).file(resolve, reject);
    });
    return [withRelativePath(file, prefix + file.name)];
  }
  if (entry.isDirectory) {
    const skip = /(-backups|\.pretty|3dmodels|__macosx)$/i.test(entry.name);
    if (skip) return [];
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    const children = await readDirEntries(reader);
    const nested: File[] = [];
    for (const child of children) {
      nested.push(...(await filesFromEntry(child, prefix + entry.name + "/")));
    }
    return nested;
  }
  return [];
}

async function filesFromDrop(e: React.DragEvent): Promise<File[]> {
  const items = e.dataTransfer?.items;
  if (items && items.length > 0) {
    const out: File[] = [];
    for (const item of Array.from(items)) {
      const entry = item.webkitGetAsEntry?.();
      if (entry) out.push(...(await filesFromEntry(entry, "")));
      else {
        const f = item.getAsFile();
        if (f) out.push(f);
      }
    }
    if (out.length > 0) return out;
  }
  return Array.from(e.dataTransfer?.files ?? []);
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
      void filesFromDrop(e).then((dropped) => {
        onFilesChange(multiple ? [...files, ...dropped] : dropped.slice(0, 1));
      });
    },
    [files, multiple, onFilesChange],
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const selected = Array.from(e.target.files ?? []);
      onFilesChange(multiple ? [...files, ...selected] : selected.slice(0, 1));
    },
    [files, multiple, onFilesChange],
  );

  const hasFiles = files.length > 0 || (preloaded && preloaded.length > 0);

  return (
    <label
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-6 cursor-pointer transition-colors",
        dragOver ? "border-blue-500 bg-blue-500/5" : "border-border hover:border-foreground/20",
        hasFiles && "border-emerald-500/40 bg-emerald-500/5",
      )}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
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
          {files.length <= 2 ? (
            files.map((f) => {
              const rel =
                (f as File & { webkitRelativePath?: string }).webkitRelativePath ||
                f.name;
              return (
                <span key={rel} className="font-mono block">{rel.split("/").pop()}</span>
              );
            })
          ) : (
            <span>{files.length} files</span>
          )}
        </div>
      )}
      <input
        type="file"
        accept={accept}
        multiple={multiple}
        className="hidden"
        onChange={handleChange}
      />
    </label>
  );
}
