"use client";

import { useState, type FormEvent } from "react";
import { Download, FileSpreadsheet, FileText, Loader2, MoreVertical, Pencil, RefreshCw, Trash2, Upload } from "lucide-react";

import { Button, EmptyState, ErrorBanner } from "@/components/ui/controls";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { FileStatusText } from "@/components/ui/FileRowsDialog";
import { Menu } from "@/components/ui/Menu";
import { useApi } from "@/hooks/useApi";
import { formatDateTime } from "@/lib/format";
import { vpApi } from "@/lib/vpApi";
import type { VpUpload } from "@/types/videoPerformance";
import { VideoTable } from "./VideoTable";

type Toast = (tone: "success" | "error", message: string) => void;

/** "View data": the tracking table for exactly the videos in one uploaded file. */
function UploadVideosDialog({
  upload,
  onClose,
  onToast,
  onChanged,
}: {
  upload: VpUpload | null;
  onClose: () => void;
  onToast: Toast;
  onChanged: () => void;
}) {
  const [refreshToken, setRefreshToken] = useState(0);
  const [showInvalid, setShowInvalid] = useState(false);
  const tracked = useApi(upload ? `vp-upload-total:${upload.id}:${refreshToken}` : null, () => vpApi.videos({ upload_id: upload!.id }, 1, 10), {
    keepPrevious: true,
  });
  const rows = useApi(upload && showInvalid ? `vp-upload-rows:${upload.id}` : null, () => vpApi.uploadRows(upload!.id));
  const invalidRows = rows.data?.rows.filter((r) => r.status === "invalid") ?? [];

  const inFile = upload ? upload.added + upload.already_tracked : 0;
  const removed = tracked.data ? Math.max(inFile - tracked.data.total, 0) : 0;

  return (
    <Dialog open={upload !== null} title="File data" description={upload?.filename} onClose={onClose} size="xl">
      {upload && (
        <div className="space-y-3 pb-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-slate-600">
            <span className="inline-flex items-center gap-1.5">
              <FileSpreadsheet size={15} className="text-emerald-600" /> Uploaded {formatDateTime(upload.created_at)}
            </span>
            <FileStatusText deletedAt={upload.file_deleted_at} />
          </div>
          <p className="text-sm text-slate-600">
            <span className="font-semibold text-ink">{inFile}</span> video{inFile === 1 ? "" : "s"} in this file
            <span className="text-muted">
              {" "}
              ({upload.added} newly added, {upload.already_tracked} already tracked) · {upload.total_rows} rows
              {upload.duplicates > 0 && ` · ${upload.duplicates} duplicate row${upload.duplicates === 1 ? "" : "s"}`}
            </span>
            {upload.invalid > 0 && (
              <>
                {" · "}
                <button type="button" onClick={() => setShowInvalid((v) => !v)} className="font-medium text-rose-700 underline-offset-2 hover:underline">
                  {upload.invalid} invalid row{upload.invalid === 1 ? "" : "s"} (not tracked) {showInvalid ? "▲" : "▼"}
                </button>
              </>
            )}
          </p>
          {removed > 0 && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {removed} video{removed === 1 ? " was" : "s were"} removed from tracking after this upload, so {removed === 1 ? "it is" : "they are"} not shown.
            </p>
          )}
          {showInvalid && (
            <div className="overflow-hidden rounded-lg border border-rose-100">
              {rows.error && <p className="px-3 py-2 text-sm text-rose-600">{rows.error}</p>}
              {!rows.data && !rows.error && <div className="h-16 animate-pulse bg-rose-50/50" />}
              {rows.data && (
                <table className="w-full text-sm">
                  <thead className="bg-rose-50 text-left text-xs font-semibold text-rose-800">
                    <tr>
                      <th className="w-16 px-3 py-2">Row</th>
                      <th className="px-3 py-2">Video link in the file</th>
                      <th className="px-3 py-2">Why it was not tracked</th>
                    </tr>
                  </thead>
                  <tbody>
                    {invalidRows.map((r) => (
                      <tr key={r.row_number} className="border-t border-rose-100">
                        <td className="px-3 py-2 tabular-nums text-muted">{r.row_number}</td>
                        <td className="break-all px-3 py-2 text-slate-700">{r.video_link || "(empty)"}</td>
                        <td className="px-3 py-2 text-slate-600">{r.message ?? "Invalid link"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
          <div className="-mx-6 border-b border-line">
            <VideoTable
              key={upload.id}
              uploadId={upload.id}
              refreshToken={refreshToken}
              onToast={onToast}
              onChanged={() => {
                setRefreshToken((n) => n + 1);
                onChanged();
              }}
            />
          </div>
        </div>
      )}
    </Dialog>
  );
}

/** Rename an uploaded file (the name shown here and used for its downloads). */
function RenameUploadDialog({ upload, onClose, onSaved }: { upload: VpUpload | null; onClose: () => void; onSaved: (message: string) => void }) {
  const [name, setName] = useState(upload?.filename ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!upload) return;
    setBusy(true);
    setError(null);
    try {
      await vpApi.renameUpload(upload.id, name.trim());
      onSaved("Upload renamed");
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not rename the upload");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={upload !== null} title="Rename upload" onClose={() => !busy && onClose()} size="sm">
      <form onSubmit={submit} className="space-y-4">
        <label className="block">
          <span className="text-sm font-medium text-ink">File name</span>
          <input
            className="mt-1.5 h-10 w-full rounded-lg border border-line px-3 text-sm outline-none focus:border-accent focus:ring-2 focus:ring-indigo-100"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={200}
            required
            autoFocus
          />
        </label>
        {error && <p className="text-sm text-rose-600">{error}</p>}
        <div className="flex justify-end gap-2 pb-2">
          <Button type="button" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={busy || !name.trim() || name.trim() === upload?.filename}>
            {busy && <Loader2 size={15} className="animate-spin" />} Save
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Uploaded video lists of the Video Performance module: view, rename, re-check, download and delete. */
export function UploadsPanel({
  refreshToken,
  onToast,
  onChanged,
  onUpload,
}: {
  refreshToken: number;
  onToast: Toast;
  onChanged: () => void;
  onUpload: () => void;
}) {
  const { data, error, reload } = useApi(`vp-uploads:${refreshToken}`, () => vpApi.uploads(), { keepPrevious: true });
  const [viewing, setViewing] = useState<VpUpload | null>(null);
  const [renaming, setRenaming] = useState<VpUpload | null>(null);
  const [deleting, setDeleting] = useState<VpUpload | null>(null);
  const [alsoVideos, setAlsoVideos] = useState(false);

  async function act(action: () => Promise<{ message: string } | void>, done?: string) {
    try {
      const result = await action();
      onToast("success", (result && result.message) || done || "Done");
      onChanged();
    } catch (err) {
      onToast("error", err instanceof Error ? err.message : "Something went wrong");
    }
  }

  async function download(action: () => Promise<void>) {
    try {
      await action();
    } catch (err) {
      onToast("error", err instanceof Error ? err.message : "Download failed");
    }
  }

  if (error) return <div className="px-5 pb-5"><ErrorBanner message={error} onRetry={reload} /></div>;
  if (data && data.length === 0)
    return (
      <EmptyState
        icon={<FileSpreadsheet size={22} />}
        title="No uploads yet"
        description="Video lists you upload are listed here with their rows."
        action={
          <Button variant="primary" onClick={onUpload}>
            <Upload size={16} /> Upload Excel
          </Button>
        }
      />
    );

  return (
    <>
      <div className="table-scroll overflow-x-auto border-t border-line">
        <table className="w-full min-w-[900px] text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold text-slate-600">
            <tr>
              {["File", "Uploaded", "Rows", "Added", "Already tracked", "Duplicates", "Invalid", ""].map((h) => (
                <th key={h} className="whitespace-nowrap border-b border-line px-4 py-3">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {!data
              ? Array.from({ length: 3 }, (_, i) => (
                  <tr key={i}>
                    <td colSpan={8} className="border-b border-line px-4 py-4">
                      <div className="h-4 animate-pulse rounded bg-slate-100" />
                    </td>
                  </tr>
                ))
              : data.map((upload) => (
                  <tr key={upload.id} className="hover:bg-slate-50">
                    <td className="max-w-[280px] border-b border-line px-4 py-3" title={upload.filename}>
                      <span className="block truncate font-medium text-ink">{upload.filename}</span>
                      <FileStatusText deletedAt={upload.file_deleted_at} />
                    </td>
                    <td className="whitespace-nowrap border-b border-line px-4 py-3 text-slate-600">{formatDateTime(upload.created_at)}</td>
                    <td className="border-b border-line px-4 py-3 tabular-nums">{upload.total_rows}</td>
                    <td className="border-b border-line px-4 py-3 tabular-nums text-emerald-700">{upload.added}</td>
                    <td className="border-b border-line px-4 py-3 tabular-nums text-slate-600">{upload.already_tracked}</td>
                    <td className="border-b border-line px-4 py-3 tabular-nums text-slate-600">{upload.duplicates}</td>
                    <td className="border-b border-line px-4 py-3 tabular-nums text-rose-700">{upload.invalid}</td>
                    <td className="border-b border-line px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => setViewing(upload)}
                          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-accent hover:bg-accent-soft"
                        >
                          <FileText size={15} /> View data
                        </button>
                        <Menu
                          label={`Actions for ${upload.filename}`}
                          width={230}
                          trigger={(props) => (
                            <button
                              type="button"
                              {...props}
                              className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-ink"
                              aria-label={`More actions for ${upload.filename}`}
                            >
                              <MoreVertical size={16} />
                            </button>
                          )}
                          items={[
                            { label: "Rename", icon: <Pencil size={15} />, onSelect: () => setRenaming(upload) },
                            {
                              label: "Re-check all videos now",
                              icon: <RefreshCw size={15} />,
                              onSelect: () => act(() => vpApi.refreshUpload(upload.id)),
                            },
                            {
                              label: "Download original file",
                              icon: <Download size={15} />,
                              disabled: !upload.file_available,
                              hint: upload.file_available ? undefined : "No longer stored - download the rows",
                              onSelect: () => download(() => vpApi.downloadUploadFile(upload)),
                            },
                            {
                              label: "Download rows (Excel)",
                              icon: <FileSpreadsheet size={15} />,
                              onSelect: () => download(() => vpApi.downloadUploadRows(upload, "excel")),
                            },
                            {
                              label: "Delete upload",
                              icon: <Trash2 size={15} />,
                              danger: true,
                              onSelect: () => {
                                setAlsoVideos(false);
                                setDeleting(upload);
                              },
                            },
                          ]}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      {/* keyed by upload so "show invalid rows" and other dialog state start fresh for each file */}
      <UploadVideosDialog key={viewing?.id ?? "none"} upload={viewing} onClose={() => setViewing(null)} onToast={onToast} onChanged={onChanged} />
      <RenameUploadDialog
        key={`rename:${renaming?.id ?? "none"}`}
        upload={renaming}
        onClose={() => setRenaming(null)}
        onSaved={(message) => {
          onToast("success", message);
          onChanged();
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        title="Delete upload?"
        message={
          <>
            <span className="font-medium text-ink">{deleting?.filename}</span> and its saved rows will be removed.{" "}
            {alsoVideos ? "" : "Its videos keep being tracked."}
          </>
        }
        confirmLabel="Delete upload"
        onConfirm={async () => {
          if (!deleting) return;
          const result = await vpApi.deleteUpload(deleting.id, alsoVideos);
          onToast("success", result.message);
          onChanged();
        }}
        onClose={() => setDeleting(null)}
      >
        <label className="mt-4 flex cursor-pointer items-start gap-2.5 rounded-lg border border-line p-3 text-sm">
          <input
            type="checkbox"
            checked={alsoVideos}
            onChange={(event) => setAlsoVideos(event.target.checked)}
            className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]"
          />
          <span>
            <span className="font-medium text-ink">Also delete the {deleting?.added ?? 0} video{deleting?.added === 1 ? "" : "s"} this file added</span>
            <span className="block text-muted">
              Removes them and their view history. Videos that were already tracked before this file are kept.
            </span>
          </span>
        </label>
      </ConfirmDialog>
    </>
  );
}
