"use client";

import { useState, type ReactNode } from "react";
import { Download, FileCheck2, FileX2, Loader2 } from "lucide-react";

import { Button, ErrorBanner } from "./controls";
import { Dialog } from "./Dialog";
import { formatDateTime } from "@/lib/format";

export interface FileRowsColumn<Row> {
  label: string;
  render: (row: Row) => ReactNode;
  className?: string;
}

interface Props<Row> {
  open: boolean;
  onClose: () => void;
  filename?: string;
  uploadedAt?: string;
  fileDeletedAt?: string | null;
  fileDeleteAfter?: string | null;
  rows?: Row[];
  error?: string;
  columns: FileRowsColumn<Row>[];
  rowKey: (row: Row) => string | number;
  onDownload: (format: "excel" | "csv") => Promise<void>;
}

/** The rows of an uploaded file as saved in the database - available even after the file itself is deleted. */
export function FileRowsDialog<Row>({
  open,
  onClose,
  filename,
  uploadedAt,
  fileDeletedAt,
  fileDeleteAfter,
  rows,
  error,
  columns,
  rowKey,
  onDownload,
}: Props<Row>) {
  const [busy, setBusy] = useState<"excel" | "csv" | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  async function download(format: "excel" | "csv") {
    setBusy(format);
    setDownloadError(null);
    try {
      await onDownload(format);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Download failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Dialog open={open} title="File data" description={filename} onClose={onClose} size="lg">
      <div className="space-y-4 pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="flex items-center gap-2 text-sm text-slate-600">
            {fileDeletedAt ? (
              <>
                <FileX2 size={16} className="text-slate-400" />
                Original file removed on {formatDateTime(fileDeletedAt)} - all rows are saved below.
              </>
            ) : (
              <>
                <FileCheck2 size={16} className="text-emerald-600" />
                Uploaded {formatDateTime(uploadedAt)}
                {fileDeleteAfter && <span className="text-muted">· original file kept until {formatDateTime(fileDeleteAfter)}</span>}
              </>
            )}
          </p>
          <div className="flex gap-2">
            <Button onClick={() => download("excel")} disabled={busy !== null || !rows?.length}>
              {busy === "excel" ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
              Excel
            </Button>
            <Button onClick={() => download("csv")} disabled={busy !== null || !rows?.length}>
              {busy === "csv" ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
              CSV
            </Button>
          </div>
        </div>

        {(error || downloadError) && <ErrorBanner message={error ?? downloadError ?? ""} />}
        {!rows && !error && (
          <div className="flex justify-center py-10">
            <Loader2 className="animate-spin text-slate-400" />
          </div>
        )}
        {rows && rows.length === 0 && (
          <p className="rounded-lg bg-slate-50 px-4 py-6 text-center text-sm text-muted">No rows were saved for this file.</p>
        )}
        {rows && rows.length > 0 && (
          <div className="max-h-[55vh] overflow-auto rounded-lg border border-line">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-slate-50 text-left text-xs font-semibold text-slate-600">
                <tr>
                  {columns.map((column) => (
                    <th key={column.label} className={`whitespace-nowrap px-3 py-2.5 ${column.className ?? ""}`}>
                      {column.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={rowKey(row)} className="border-t border-line align-top">
                    {columns.map((column) => (
                      <td key={column.label} className={`px-3 py-2 ${column.className ?? ""}`}>
                        {column.render(row)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows && rows.length > 0 && <p className="text-xs text-muted">{rows.length} rows</p>}
      </div>
    </Dialog>
  );
}

const OUTCOME_STYLES: Record<string, string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  gray: "bg-slate-100 text-slate-600 ring-slate-200",
  amber: "bg-amber-50 text-amber-700 ring-amber-200",
  red: "bg-rose-50 text-rose-700 ring-rose-200",
};

export function OutcomePill({ label, tone }: { label: string; tone: keyof typeof OUTCOME_STYLES }) {
  return (
    <span className={`inline-flex whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${OUTCOME_STYLES[tone]}`}>
      {label}
    </span>
  );
}

/** "Kept until 30 Oct" / "Removed 1 Oct" for upload tables. */
export function FileStatusText({ deletedAt, deleteAfter }: { deletedAt: string | null; deleteAfter: string | null }) {
  if (deletedAt)
    return (
      <span className="whitespace-nowrap text-xs text-muted" title="Rows are saved in the database">
        Removed {new Date(deletedAt).toLocaleDateString("en-IN", { day: "numeric", month: "short" })} · data saved
      </span>
    );
  if (deleteAfter)
    return (
      <span className="whitespace-nowrap text-xs text-muted">
        Kept until {new Date(deleteAfter).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}
      </span>
    );
  return <span className="text-xs text-muted">Kept</span>;
}
