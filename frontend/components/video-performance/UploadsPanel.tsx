"use client";

import { useState } from "react";
import { FileSpreadsheet, FileText } from "lucide-react";

import { EmptyState, ErrorBanner } from "@/components/ui/controls";
import { FileRowsDialog, FileStatusText, OutcomePill } from "@/components/ui/FileRowsDialog";
import { useApi } from "@/hooks/useApi";
import { formatDateTime, platformLabel } from "@/lib/format";
import { vpApi } from "@/lib/vpApi";
import type { VpUpload, VpUploadRow } from "@/types/videoPerformance";

const RESULTS: Record<VpUploadRow["status"], { label: string; tone: "green" | "gray" | "amber" | "red" }> = {
  added: { label: "Added", tone: "green" },
  already_tracked: { label: "Already tracked", tone: "gray" },
  duplicate: { label: "Duplicate", tone: "amber" },
  invalid: { label: "Invalid", tone: "red" },
};

function UploadFileData({ upload, onClose }: { upload: VpUpload | null; onClose: () => void }) {
  const { data, error } = useApi(upload ? `vp-upload-rows:${upload.id}` : null, () => vpApi.uploadRows(upload!.id));
  return (
    <FileRowsDialog<VpUploadRow>
      open={upload !== null}
      onClose={onClose}
      filename={upload?.filename}
      uploadedAt={upload?.created_at}
      fileDeletedAt={data?.upload.file_deleted_at ?? upload?.file_deleted_at}
      fileDeleteAfter={data?.upload.file_delete_after ?? upload?.file_delete_after}
      rows={data?.rows}
      error={error}
      rowKey={(row) => row.row_number}
      onDownload={(format) => vpApi.downloadUploadRows(upload!.id, format)}
      columns={[
        { label: "Row", render: (r) => r.row_number, className: "w-14 tabular-nums text-muted" },
        { label: "Creator", render: (r) => r.creator_name ?? "-" },
        { label: "Platform", render: (r) => (r.platform ? platformLabel(r.platform) : "-") },
        { label: "Video Link", render: (r) => <span className="break-all text-slate-600">{r.video_link || "-"}</span>, className: "min-w-[240px]" },
        { label: "Username", render: (r) => (r.username ? `@${r.username}` : "") },
        { label: "Result", render: (r) => <OutcomePill {...RESULTS[r.status]} /> },
        { label: "Notes", render: (r) => <span className="text-muted">{r.message ?? ""}</span> },
      ]}
    />
  );
}

/** Uploaded video lists of the Video Performance module, with their saved rows. */
export function UploadsPanel({ refreshToken }: { refreshToken: number }) {
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
      <UploadFileData upload={viewing} onClose={() => setViewing(null)} />
    </>
  );
}
