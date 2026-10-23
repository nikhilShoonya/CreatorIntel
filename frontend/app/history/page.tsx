"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, Clapperboard, FileText, History, Loader2, RotateCcw, Search, Trash2, Users } from "lucide-react";

import { Card, EmptyState, ErrorBanner } from "@/components/ui/controls";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { FileRowsDialog, OutcomePill } from "@/components/ui/FileRowsDialog";
import { UploadVideosDialog } from "@/components/video-performance/dialogs";
import { UploadsPanel } from "@/components/video-performance/UploadsPanel";
import { useApi } from "@/hooks/useApi";
import { useUpload, useUploads } from "@/hooks/useData";
import { useStoredValue } from "@/hooks/useStoredValue";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { UploadRow, UploadSummary } from "@/types";

const OUTCOMES: Record<UploadRow["outcome"], { label: string; tone: "green" | "gray" | "amber" | "red" }> = {
  queued: { label: "Analysed", tone: "green" },
  cached: { label: "Existing data reused", tone: "gray" },
  duplicate: { label: "Duplicate", tone: "amber" },
  invalid: { label: "Invalid link", tone: "red" },
};

/** The saved rows of one upload (works after the original file was deleted). */
function UploadFileData({ upload, onClose }: { upload: UploadSummary | null; onClose: () => void }) {
  const { data, error } = useApi(upload ? `upload-rows:${upload.id}` : null, () => api.uploadRows(upload!.id));
  return (
    <FileRowsDialog<UploadRow>
      open={upload !== null}
      onClose={onClose}
      filename={upload?.filename}
      uploadedAt={upload?.created_at}
      fileDeletedAt={data?.upload.file_deleted_at ?? upload?.file_deleted_at}
      rows={data?.rows}
      error={error}
      rowKey={(row) => row.row_number}
      onDownload={(format) => api.downloadUploadRows(upload!.id, format)}
      columns={[
        { label: "Row", render: (r) => r.row_number, className: "w-14 tabular-nums text-muted" },
        { label: "Channel Name", render: (r) => r.channel_name ?? "-" },
        { label: "Channel Link", render: (r) => <span className="break-all text-slate-600">{r.channel_link || "-"}</span>, className: "min-w-[240px]" },
        { label: "Result", render: (r) => <OutcomePill {...OUTCOMES[r.outcome]} /> },
        { label: "Notes", render: (r) => <span className="text-muted">{r.message ?? ""}</span> },
      ]}
    />
  );
}

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

/**
 * "Retry failed & partial (N)" for one creator upload. Counts exactly what the backend re-queues (the same rule the
 * retired Upload & Analyze page used): Failed / Partial creators with a YouTube or Instagram link - invalid links are
 * also Failed but cannot be retried, so the upload's own items are read instead of its failed_rows total.
 */
function RetryFailedAction({ upload, busy, disabled, onRetry }: { upload: UploadSummary; busy: boolean; disabled: boolean; onRetry: (upload: UploadSummary) => void }) {
  const { data: detail } = useUpload(upload.id);
  const retryable = (detail?.items ?? []).filter(
    (item) => (item.status === "Failed" || item.status === "Partial") && (item.platform === "youtube" || item.platform === "instagram"),
  ).length;
  if (!detail || detail.status === "processing" || retryable === 0) return null;
  return (
    <button
      type="button"
      onClick={() => onRetry(upload)}
      disabled={disabled}
      title="Fetch and analyse this upload's failed and partial creators again"
      className="inline-flex items-center gap-1 whitespace-nowrap font-medium text-amber-700 hover:underline disabled:cursor-not-allowed disabled:opacity-50 disabled:no-underline"
    >
      {busy ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
      {busy ? "Retrying…" : `Retry failed & partial (${retryable})`}
    </button>
  );
}

type HistoryTab = "creators" | "videos";
const TAB_KEY = "creatorintel:history-tab";

export default function HistoryPage() {
  const [storedTab, setTab] = useStoredValue(TAB_KEY, "creators");
  const tab: HistoryTab = storedTab === "videos" ? "videos" : "creators";
  const [query, setQuery] = useState("");

  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Upload History</h1>
          <p className="text-sm text-muted">Every file uploaded to Creator Analytics and Video Tracking, with its saved rows.</p>
        </div>
        <label className="relative w-full sm:w-72">
          <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by file name…"
            aria-label="Search uploads by file name"
            maxLength={200}
            className="h-10 w-full rounded-lg border border-line bg-white pl-9 pr-3 text-sm outline-none placeholder:text-slate-400 focus:border-accent focus:ring-2 focus:ring-indigo-100"
          />
        </label>
      </div>

      <div className="inline-flex rounded-lg border border-line bg-white p-1" role="tablist" aria-label="History source">
        {(
          [
            { id: "creators", label: "Creator Analytics History", Icon: Users },
            { id: "videos", label: "Video Tracking History", Icon: Clapperboard },
          ] as const
        ).map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`history-tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`history-panel-${id}`}
            onClick={() => setTab(id)}
            className={`inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === id ? "bg-accent-soft text-accent" : "text-slate-600 hover:text-ink"
            }`}
          >
            <Icon size={15} aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`history-panel-${tab}`} aria-labelledby={`history-tab-${tab}`}>
        {tab === "creators" ? <CreatorHistory query={query} /> : <VideoHistory query={query} />}
      </div>
    </div>
  );
}

/** Video Performance uploads (same data and actions as Tracking Library > Uploads). */
function VideoHistory({ query }: { query: string }) {
  const [token, setToken] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [toast, setToast] = useState<{ tone: "success" | "error"; message: string } | null>(null);

  function showToast(tone: "success" | "error", message: string) {
    setToast({ tone, message });
    window.setTimeout(() => setToast(null), 4000);
  }

  return (
    <div className="space-y-3">
      {toast && (
        <div
          role="status"
          className={`rounded-lg border px-4 py-3 text-sm ${
            toast.tone === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-700"
          }`}
        >
          {toast.message}
        </div>
      )}
      <Card className="overflow-hidden">
        <UploadsPanel
          refreshToken={token}
          onToast={showToast}
          onChanged={() => setToken((n) => n + 1)}
          onUpload={() => setUploading(true)}
          query={query}
        />
      </Card>
      <UploadVideosDialog open={uploading} onClose={() => setUploading(false)} onDone={() => setToken((n) => n + 1)} />
    </div>
  );
}

/** Creator Analytics uploads. */
function CreatorHistory({ query }: { query: string }) {
  const { data: allUploads, error, reload } = useUploads();
  const term = query.trim().toLowerCase();
  const data = allUploads && term ? allUploads.filter((u) => u.filename.toLowerCase().includes(term)) : allUploads;
  const [toDelete, setToDelete] = useState<UploadSummary | null>(null);
  const [alsoCreators, setAlsoCreators] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [viewing, setViewing] = useState<UploadSummary | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [retryError, setRetryError] = useState<string | null>(null);

  // One retry request at a time; the upload then shows as Processing (the list polls until it finishes).
  async function retryUpload(upload: UploadSummary) {
    if (retryingId) return;
    setRetryingId(upload.id);
    setRetryError(null);
    try {
      await api.retryFailed(upload.id);
      setNotice(`Retrying the failed and partial creators of ${upload.filename} - they are processed in the background.`);
      window.setTimeout(() => setNotice(null), 4000);
    } catch (err) {
      setRetryError(err instanceof Error ? err.message : "Retry failed");
      window.setTimeout(() => setRetryError(null), 8000);
    } finally {
      setRetryingId(null);
      reload();
    }
  }

  async function confirmDelete() {
    if (!toDelete) return;
    const result = await api.deleteUpload(toDelete.id, alsoCreators);
    setNotice(result.message);
    window.setTimeout(() => setNotice(null), 4000);
    reload();
  }

  return (
    <div className="space-y-3">
      {error && <ErrorBanner message={error} onRetry={reload} />}
      {retryError && <ErrorBanner message={retryError} />}
      {notice && (
        <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          {notice}
        </div>
      )}
      <Card className="overflow-hidden">
        {allUploads && allUploads.length === 0 ? (
          <EmptyState
            icon={<History size={22} />}
            title="No creator uploads yet"
            description="Creator spreadsheets uploaded with Creator List > Upload Creators are listed here."
            action={
              <Link href="/creators" className="inline-flex h-9 items-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-white hover:bg-accent-strong">
                Open Creator List <ArrowRight size={15} />
              </Link>
            }
          />
        ) : data && data.length === 0 ? (
          <EmptyState icon={<Search size={22} />} title="No matching uploads" description={`No creator file name contains "${query.trim()}".`} />
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
                            {upload.status !== "processing" && upload.failed_rows + upload.partial_rows > 0 && (
                              <RetryFailedAction
                                upload={upload}
                                busy={retryingId === upload.id}
                                disabled={retryingId !== null}
                                onRetry={retryUpload}
                              />
                            )}
                            <button
                              type="button"
                              onClick={() => setViewing(upload)}
                              title="View the uploaded file's rows"
                              aria-label={`View file data of ${upload.filename}`}
                              className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-ink"
                            >
                              <FileText size={16} />
                            </button>
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

      <UploadFileData upload={viewing} onClose={() => setViewing(null)} />

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
