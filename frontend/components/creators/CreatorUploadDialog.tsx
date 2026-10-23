"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, CheckCircle2, Loader2, Upload, XCircle } from "lucide-react";

import { Button } from "@/components/ui/controls";
import { Dialog } from "@/components/ui/Dialog";
import { FileDropzone } from "@/components/ui/FileDropzone";
import { useApi } from "@/hooks/useApi";
import { api } from "@/lib/api";
import type { UploadDetail } from "@/types";

/** Upload a creator spreadsheet from the Creator List (same pipeline as before: only new creators are fetched). */
export function CreatorUploadDialog({ open, onClose, onUploaded }: { open: boolean; onClose: () => void; onUploaded: () => void }) {
  return (
    <Dialog
      open={open}
      title="Upload creators"
      description="Excel or CSV with Channel Name and Channel Link (YouTube or Instagram)."
      onClose={onClose}
      size="lg"
    >
      {open && <UploadFlow onClose={onClose} onUploaded={onUploaded} />}
    </Dialog>
  );
}

function UploadFlow({ onClose, onUploaded }: { onClose: () => void; onUploaded: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadId, setUploadId] = useState<string | null>(null);

  async function send() {
    if (!file) return;
    setSending(true);
    setError(null);
    try {
      const created = await api.uploadFile(file);
      setUploadId(created.id);
      onUploaded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setSending(false);
    }
  }

  if (uploadId) return <UploadProgress uploadId={uploadId} onClose={onClose} onUploaded={onUploaded} />;

  return (
    <div className="space-y-4 pb-2">
      <FileDropzone
        file={file}
        onChange={(next) => {
          setFile(next);
          setError(null);
        }}
        disabled={sending}
        hint={
          <>
            Columns: <strong>Channel Name</strong> and <strong>Channel Link</strong> (also accepted: Creator Name / Creator Link /
            URL). Creators already in the list reuse their stored data - only new creators are fetched and analysed. Duplicate
            rows are skipped.
          </>
        }
      />
      {error && (
        <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button onClick={onClose} disabled={sending}>
          Cancel
        </Button>
        <Button variant="primary" onClick={send} disabled={!file || sending}>
          {sending ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
          {sending ? "Uploading & checking…" : "Upload"}
        </Button>
      </div>
    </div>
  );
}

/** Live progress of one creator upload, then its summary. */
function UploadProgress({ uploadId, onClose, onUploaded }: { uploadId: string; onClose: () => void; onUploaded: () => void }) {
  const { data: upload, error } = useApi(`creator-upload:${uploadId}`, () => api.getUpload(uploadId), {
    refreshMs: (d: UploadDetail | undefined) => (!d || d.status === "processing" ? 2000 : false),
  });
  const processing = !upload || upload.status === "processing";
  useEffect(() => {
    if (!processing) onUploaded(); // refresh the table with the final results
  }, [processing, onUploaded]);

  if (error) return <p className="pb-2 text-sm text-rose-600">{error}</p>;
  if (!upload)
    return (
      <div className="flex justify-center py-10">
        <Loader2 className="animate-spin text-slate-400" />
      </div>
    );

  const valid = upload.items.filter((i) => i.platform === "youtube" || i.platform === "instagram");
  const counts = {
    rows: upload.total_rows + upload.duplicate_rows,
    analysed: valid.filter((i) => !i.from_cache).length,
    existing: valid.filter((i) => i.from_cache).length,
    duplicates: upload.duplicate_rows,
    invalid: upload.items.length - valid.length,
    failed: valid.filter((i) => !i.from_cache && i.status === "Failed").length,
    partial: valid.filter((i) => !i.from_cache && i.status === "Partial").length,
  };
  const total = upload.total_rows;
  const percent = total ? Math.round((upload.processed_rows / total) * 100) : 100;
  const allFailed = !processing && counts.analysed + counts.existing === 0;
  const withIssues = counts.failed + counts.partial + counts.invalid > 0 || upload.status === "failed";

  let tone = "border-emerald-200 bg-emerald-50/50";
  let icon = <CheckCircle2 size={20} className="text-emerald-600" />;
  let heading = "Upload complete";
  if (processing) {
    tone = "border-indigo-200 bg-accent-soft/50";
    icon = <Loader2 size={20} className="animate-spin text-accent" />;
    heading = `Analysing new creators… ${upload.processed_rows} / ${total}`;
  } else if (allFailed || upload.status === "failed") {
    tone = "border-rose-200 bg-rose-50/50";
    icon = <XCircle size={20} className="text-rose-600" />;
    heading = upload.status === "failed" ? "Processing stopped" : "No creators could be imported";
  } else if (withIssues) {
    tone = "border-amber-200 bg-amber-50/50";
    icon = <AlertTriangle size={20} className="text-amber-600" />;
    heading = "Upload complete, with some issues";
  }

  return (
    <div className="space-y-4 pb-2">
      <div className={`rounded-lg border px-4 py-3 ${tone}`}>
        <p className="flex items-center gap-2 font-semibold text-ink" role="status">
          {icon}
          {heading}
        </p>
        <p className="mt-0.5 truncate text-sm text-slate-600" title={upload.filename}>
          {upload.filename}
        </p>
        {processing && (
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-indigo-100" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(percent, 3)}%` }} />
          </div>
        )}
        {upload.error_message && <p className="mt-2 text-sm text-rose-700">{upload.error_message}</p>}
      </div>

      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line text-center sm:grid-cols-3">
        {[
          ["Rows in file", counts.rows, "text-ink"],
          ["New (analysed)", counts.analysed, "text-emerald-700"],
          ["Existing (data reused)", counts.existing, "text-slate-700"],
          ["Duplicates skipped", counts.duplicates, "text-slate-700"],
          ["Invalid links", counts.invalid, counts.invalid ? "text-rose-700" : "text-slate-700"],
          ["Failed / partial", counts.failed + counts.partial, counts.failed ? "text-rose-700" : counts.partial ? "text-amber-700" : "text-slate-700"],
        ].map(([label, value, color]) => (
          <div key={label as string} className="bg-white px-3 py-3">
            <p className="text-xs text-muted">{label}</p>
            <p className={`text-xl font-semibold tabular-nums ${color}`}>{value as number}</p>
          </div>
        ))}
      </div>
      <p className="text-xs text-muted">
        Existing creators were not fetched again. New creators are fetched from YouTube / Instagram and analysed by the AI
        {processing ? " now - you can close this window, the work continues." : "."}
      </p>
      <div className="flex flex-wrap justify-end gap-2">
        <Link
          href={`/creators?upload=${upload.id}`}
          onClick={onClose}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line px-3.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          View this upload&apos;s creators <ArrowRight size={14} />
        </Link>
        <Button variant="primary" onClick={onClose}>
          {processing ? "Close" : "Done"}
        </Button>
      </div>
    </div>
  );
}
