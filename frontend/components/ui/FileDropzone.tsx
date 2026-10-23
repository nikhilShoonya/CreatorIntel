"use client";

import { useId, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from "react";
import { FileSpreadsheet, RefreshCw, UploadCloud, X } from "lucide-react";

export const SPREADSHEET_ACCEPT = ".xlsx,.xls,.csv";
export const MAX_UPLOAD_MB = 10;

/** Client-side check before uploading (the backend validates again). */
export function validateSpreadsheet(file: File): string | null {
  const dot = file.name.lastIndexOf(".");
  const ext = dot >= 0 ? file.name.toLowerCase().slice(dot) : "";
  if (![".xlsx", ".xls", ".csv"].includes(ext)) return `"${file.name}" is not a spreadsheet. Upload a .xlsx, .xls or .csv file.`;
  if (file.size === 0) return "The selected file is empty.";
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `The file is larger than the ${MAX_UPLOAD_MB} MB limit.`;
  return null;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Drop zone for one spreadsheet: drag & drop or click / Enter / Space to browse. Shows the chosen file with its
 * size and Replace / Remove, and the validation error of a rejected file.
 */
export function FileDropzone({
  file,
  onChange,
  disabled = false,
  hint,
}: {
  file: File | null;
  onChange: (file: File | null) => void;
  disabled?: boolean;
  hint?: ReactNode;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hintId = useId();
  const errorId = useId();

  function choose(files: FileList | null) {
    const picked = files?.[0];
    if (inputRef.current) inputRef.current.value = "";
    if (!picked) return;
    const problem = validateSpreadsheet(picked);
    setError(problem);
    onChange(problem ? null : picked);
  }

  function browse() {
    if (!disabled) inputRef.current?.click();
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (!disabled) choose(event.dataTransfer.files);
  }

  function onKey(event: KeyboardEvent) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      browse();
    }
  }

  return (
    <div className="space-y-2">
      {file ? (
        <div className="flex items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50/50 px-4 py-3">
          <FileSpreadsheet size={28} className="shrink-0 text-emerald-600" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-ink" title={file.name}>
              {file.name}
            </p>
            <p className="text-xs text-muted">{formatFileSize(file.size)}</p>
          </div>
          <button
            type="button"
            onClick={browse}
            disabled={disabled}
            className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-slate-700 hover:bg-white disabled:opacity-50"
          >
            <RefreshCw size={13} /> Replace
          </button>
          <button
            type="button"
            onClick={() => {
              setError(null);
              onChange(null);
            }}
            disabled={disabled}
            aria-label={`Remove ${file.name}`}
            className="rounded-md p-1.5 text-slate-500 hover:bg-white hover:text-rose-600 disabled:opacity-50"
          >
            <X size={16} />
          </button>
        </div>
      ) : (
        <div
          role="button"
          tabIndex={disabled ? -1 : 0}
          aria-disabled={disabled}
          aria-describedby={`${hintId} ${error ? errorId : ""}`}
          onClick={browse}
          onKeyDown={onKey}
          onDragOver={(event) => {
            event.preventDefault();
            if (!disabled) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed px-4 py-9 text-center outline-none transition-colors focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-indigo-100 ${
            dragging ? "border-accent bg-accent-soft" : error ? "border-rose-300 bg-rose-50/40" : "border-slate-300 hover:border-accent hover:bg-accent-soft/40"
          } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
        >
          <UploadCloud size={30} className={dragging ? "text-accent" : "text-slate-400"} aria-hidden="true" />
          <p className="text-sm font-medium text-ink">{dragging ? "Drop the file to select it" : "Drag & drop your file here"}</p>
          <p className="text-sm text-muted">
            or <span className="font-medium text-accent">browse</span> to choose one
          </p>
          <p id={hintId} className="text-xs text-muted">
            .xlsx, .xls or .csv · max {MAX_UPLOAD_MB} MB
          </p>
        </div>
      )}
      <input ref={inputRef} type="file" accept={SPREADSHEET_ACCEPT} className="hidden" onChange={(event) => choose(event.target.files)} />
      {error && (
        <p id={errorId} role="alert" className="text-sm text-rose-600">
          {error}
        </p>
      )}
      {hint && <div className="text-xs text-muted">{hint}</div>}
    </div>
  );
}
