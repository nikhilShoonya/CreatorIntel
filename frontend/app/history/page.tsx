"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, History, Loader2, Trash2 } from "lucide-react";

import { Card, EmptyState, ErrorBanner } from "@/components/ui/controls";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { useUploads } from "@/hooks/useData";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { UploadSummary } from "@/types";

function UploadStatusPill({ upload }: { upload: UploadSummary }) {
  if (upload.status === "processing")
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md bg-indigo-50 px-2 py-1 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-200">
        <Loader2 size={12} className="animate-spin" />
        Processing {upload.processed_rows}/{upload.total_rows}
      </span>
    );
  const failed = upload.status === "failed";
  const issues = upload.failed_rows > 0 || upload.partial_rows > 0;
  const cls = failed
    ? "bg-rose-50 text-rose-700 ring-rose-200"
    : issues
      ? "bg-amber-50 text-amber-700 ring-amber-200"
      : "bg-emerald-50 text-emerald-700 ring-emerald-200";
  return (
    <span className={`inline-flex rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset ${cls}`}>
      {failed ? "Stopped" : issues ? "Completed with issues" : "Completed"}
    </span>
  );
}

export default function HistoryPage() {
  const { data, error, reload } = useUploads();
  const [toDelete, setToDelete] = useState<UploadSummary | null>(null);
  const [alsoCreators, setAlsoCreators] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function confirmDelete() {
    if (!toDelete) return;
    const result = await api.deleteUpload(toDelete.id, alsoCreators);
    setNotice(result.message);
    window.setTimeout(() => setNotice(null), 4000);
    reload();
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Upload History</h1>
        <p className="text-sm text-muted">Previously processed files. Open one to see its creators.</p>
      </div>
      {error && <ErrorBanner message={error} onRetry={reload} />}
      {notice && (
        <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          {notice}
        </div>
      )}
      <Card className="overflow-hidden">
        {data && data.length === 0 ? (
          <EmptyState icon={<History size={22} />} title="No uploads yet" description="Files you upload on the Upload & Analyze page will be listed here." />
        ) : (
          <div className="table-scroll overflow-x-auto">
            <table className="w-full min-w-[900px] text-sm">
              <thead className="bg-slate-50 text-left text-xs font-semibold text-slate-600">
                <tr>
                  {["File", "Uploaded", "Total", "Successful", "Partial", "Failed", "Duplicates", "Status", ""].map((label) => (
                    <th key={label} className="whitespace-nowrap border-b border-line px-4 py-3">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {!data
                  ? Array.from({ length: 3 }, (_, i) => (
                      <tr key={i}>
                        <td colSpan={9} className="border-b border-line px-4 py-4">
                          <div className="h-4 animate-pulse rounded bg-slate-100" />
                        </td>
                      </tr>
                    ))
                  : data.map((upload) => (
                      <tr key={upload.id} className="hover:bg-slate-50">
                        <td className="max-w-[280px] truncate border-b border-line px-4 py-3 font-medium text-ink" title={upload.filename}>
                          {upload.filename}
                        </td>
                        <td className="whitespace-nowrap border-b border-line px-4 py-3 text-slate-600">{formatDateTime(upload.created_at)}</td>
                        <td className="border-b border-line px-4 py-3 tabular-nums">{upload.total_rows}</td>
                        <td className="border-b border-line px-4 py-3 tabular-nums text-emerald-700">{upload.successful_rows}</td>
                        <td className="border-b border-line px-4 py-3 tabular-nums text-amber-700">{upload.partial_rows}</td>
                        <td className="border-b border-line px-4 py-3 tabular-nums text-rose-700">{upload.failed_rows}</td>
                        <td className="border-b border-line px-4 py-3 tabular-nums text-slate-500">{upload.duplicate_rows}</td>
                        <td className="border-b border-line px-4 py-3">
                          <UploadStatusPill upload={upload} />
                        </td>
                        <td className="border-b border-line px-4 py-3">
                          <div className="flex items-center justify-end gap-3">
                            <Link href={`/creators?upload=${upload.id}`} className="inline-flex items-center gap-1 whitespace-nowrap font-medium text-accent hover:underline">
                              View creators
                              <ArrowRight size={14} />
                            </Link>
                            <button
                              type="button"
                              onClick={() => {
                                setAlsoCreators(false);
                                setToDelete(upload);
                              }}
                              disabled={upload.status === "processing"}
                              title={upload.status === "processing" ? "Wait until processing has finished" : "Delete upload"}
                              aria-label={`Delete upload ${upload.filename}`}
                              className="rounded-md p-1.5 text-slate-400 hover:bg-rose-50 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              <Trash2 size={16} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={toDelete !== null}
        title="Delete upload?"
        message={
          <>
            <span className="font-medium text-ink">{toDelete?.filename}</span> will be removed from history.
          </>
        }
        confirmLabel="Delete upload"
        onConfirm={confirmDelete}
        onClose={() => setToDelete(null)}
      >
        <label className="mt-4 flex cursor-pointer items-start gap-2.5 rounded-lg border border-line p-3 text-sm">
          <input
            type="checkbox"
            checked={alsoCreators}
            onChange={(event) => setAlsoCreators(event.target.checked)}
            className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]"
          />
          <span>
            <span className="font-medium text-ink">Also delete its creators</span>
            <span className="block text-muted">Creators that also appear in another upload are kept.</span>
          </span>
        </label>
      </ConfirmDialog>
    </div>
  );
}
