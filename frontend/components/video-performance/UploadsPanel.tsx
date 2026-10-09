"use client";

import { useState } from "react";
import { FileSpreadsheet, FileText } from "lucide-react";

import { EmptyState, ErrorBanner } from "@/components/ui/controls";
import { Dialog } from "@/components/ui/Dialog";
import { FileStatusText } from "@/components/ui/FileRowsDialog";
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
            <FileStatusText deletedAt={upload.file_deleted_at} deleteAfter={upload.file_delete_after} />
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

/** Uploaded video lists of the Video Performance module, with their saved rows. */
export function UploadsPanel({ refreshToken, onToast, onChanged }: { refreshToken: number; onToast: Toast; onChanged: () => void }) {
  const { data, error, reload } = useApi(`vp-uploads:${refreshToken}`, () => vpApi.uploads(), { keepPrevious: true });
  const [viewing, setViewing] = useState<VpUpload | null>(null);

  if (error) return <div className="px-5 pb-5"><ErrorBanner message={error} onRetry={reload} /></div>;
  if (data && data.length === 0)
    return <EmptyState icon={<FileSpreadsheet size={22} />} title="No uploads yet" description="Video lists you upload are listed here with their rows." />;

  return (
    <>
      <div className="table-scroll overflow-x-auto border-t border-line">
        <table className="w-full min-w-[900px] text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold text-slate-600">
            <tr>
              {["File", "Uploaded", "Rows", "Added", "Already tracked", "Duplicates", "Invalid", "Original file", ""].map((h) => (
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
                    <td className="border-b border-line px-4 py-3 tabular-nums text-emerald-700">{upload.added}</td>
                    <td className="border-b border-line px-4 py-3 tabular-nums text-slate-600">{upload.already_tracked}</td>
                    <td className="border-b border-line px-4 py-3 tabular-nums text-slate-600">{upload.duplicates}</td>
                    <td className="border-b border-line px-4 py-3 tabular-nums text-rose-700">{upload.invalid}</td>
                    <td className="border-b border-line px-4 py-3">
                      <FileStatusText deletedAt={upload.file_deleted_at} deleteAfter={upload.file_delete_after} />
                    </td>
                    <td className="border-b border-line px-4 py-3 text-right">
                      <button
                        type="button"
                        onClick={() => setViewing(upload)}
                        className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-accent hover:bg-accent-soft"
                      >
                        <FileText size={15} /> View data
                      </button>
                    </td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>
      <UploadVideosDialog upload={viewing} onClose={() => setViewing(null)} onToast={onToast} onChanged={onChanged} />
    </>
  );
}
