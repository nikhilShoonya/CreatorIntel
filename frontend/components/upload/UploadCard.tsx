"use client";

import { useRef, useState, type DragEvent } from "react";
import { Loader2, Upload } from "lucide-react";

import { Button, Card } from "@/components/ui/controls";

const ACCEPT = ".xlsx,.xls,.csv";
const MAX_MB = 10;

function ExcelIcon() {
  return (
    <svg width="52" height="52" viewBox="0 0 48 48" aria-hidden="true">
      <rect x="14" y="6" width="28" height="36" rx="3" fill="#21a366" />
      <rect x="28" y="6" width="14" height="36" rx="0" fill="#33c481" opacity=".55" />
      <path d="M28 15h14M28 24h14M28 33h14" stroke="#fff" strokeOpacity=".6" strokeWidth="1.5" />
      <rect x="6" y="13" width="22" height="22" rx="3" fill="#107c41" />
      <path d="M11.5 18.5l4.5 5.5-4.5 5.5M22.5 18.5l-4.5 5.5 4.5 5.5" stroke="#fff" strokeWidth="2.6" fill="none" strokeLinecap="round" />
    </svg>
  );
}

export function validateClientFile(file: File): string | null {
  const ext = file.name.toLowerCase().slice(file.name.lastIndexOf("."));
  if (![".xlsx", ".xls", ".csv"].includes(ext)) return "Unsupported file type. Upload a .xlsx, .xls or .csv file.";
  if (file.size === 0) return "The selected file is empty.";
  if (file.size > MAX_MB * 1024 * 1024) return `File is larger than the ${MAX_MB} MB limit.`;
  return null;
}

export function UploadCard({ onFile, busy }: { onFile: (file: File) => void; busy: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function pick(files: FileList | null) {
    const file = files?.[0];
    if (file) onFile(file);
    if (inputRef.current) inputRef.current.value = "";
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (!busy) pick(event.dataTransfer.files);
  }

  return (
    <Card className="flex-1 p-2">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex h-full flex-col gap-6 rounded-lg border border-dashed p-5 transition-colors md:flex-row md:items-center ${
          dragging ? "border-accent bg-accent-soft" : "border-slate-200"
        }`}
      >
        <div className="flex flex-1 items-start gap-4">
          <ExcelIcon />
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-ink">Upload Excel File</h2>
            <p className="mt-1 text-sm text-muted">
              Upload a file containing <span className="font-medium text-slate-700">Channel Name</span> and{" "}
              <span className="font-medium text-slate-700">Channel Link</span> — or drop it here.
            </p>
            <Button variant="primary" className="mt-4 h-10 px-5" onClick={() => inputRef.current?.click()} disabled={busy}>
              {busy ? <Loader2 size={17} className="animate-spin" /> : <Upload size={17} />}
              {busy ? "Validating file…" : "Choose Excel File"}
            </Button>
            <input ref={inputRef} type="file" accept={ACCEPT} className="hidden" onChange={(event) => pick(event.target.files)} />
          </div>
        </div>
        <div className="border-line md:border-l md:pl-6">
          <p className="text-sm font-medium text-ink">Supported format</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted marker:text-slate-300">
            <li>.xlsx, .xls, .csv</li>
            <li>Max size: {MAX_MB} MB</li>
            <li>Columns: Channel Name, Channel Link</li>
            <li>YouTube &amp; Instagram links</li>
          </ul>
        </div>
      </div>
    </Card>
  );
}
