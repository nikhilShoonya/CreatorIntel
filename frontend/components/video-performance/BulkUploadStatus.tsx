"use client";

import { Loader2 } from "lucide-react";

import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { platformLabel } from "@/lib/format";
import type { VpUpload, VpUploadRow } from "@/types/videoPerformance";

const REFRESH_WHILE_PROCESSING_MS = 3000;
const REFRESH_WHILE_WAITING_FOR_META_MS = 60_000;

/** How often to re-read a batch: quickly while videos are being fetched, slowly when they only wait for Meta's hourly limit. */
export function batchRefreshMs(upload: VpUpload): number | false {
  if (!upload.videos_in_progress) return false;
  return upload.videos_in_progress > upload.instagram_waiting ? REFRESH_WHILE_PROCESSING_MS : REFRESH_WHILE_WAITING_FOR_META_MS;
}

export function BatchStatusPill({ upload }: { upload: VpUpload }) {
  if (upload.processing_status === "processing")
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-inset ring-indigo-200">
        <Loader2 size={12} className="animate-spin" />
        Processing {upload.videos_processed + upload.videos_failed}/{upload.videos_total}
      </span>
    );
  const [label, tone] =
    upload.processing_status === "failed"
      ? ["Failed", "bg-rose-50 text-rose-700 ring-rose-200"]
      : upload.processing_status === "partial"
        ? ["Completed with issues", "bg-amber-50 text-amber-700 ring-amber-200"]
        : ["Completed", "bg-emerald-50 text-emerald-700 ring-emerald-200"];
  return <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tone}`}>{label}</span>;
}

/** Bar and counts of one batch of pasted links. */
export function BatchSummary({ upload }: { upload: VpUpload }) {
  const finished = upload.videos_processed + upload.videos_failed;
  const percent = upload.videos_total ? Math.round((finished / upload.videos_total) * 100) : 100;
  const stats: [string, number, string][] = [
    ["Submitted", upload.total_rows, "text-slate-700"],
    ["Valid", upload.total_rows - upload.invalid - upload.duplicates, "text-slate-700"],
    ["Successful", upload.videos_processed, "text-emerald-700"],
    ["Failed", upload.videos_failed, upload.videos_failed ? "text-rose-700" : "text-slate-700"],
  ];
  return (
    <div className="space-y-3">
      <div>
        <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2 text-sm text-slate-600">
          <BatchStatusPill upload={upload} />
          <span className="tabular-nums">
            {finished} of {upload.videos_total} video{upload.videos_total === 1 ? "" : "s"} done
          </span>
        </div>
        <div
          role="progressbar"
          aria-label="Videos processed"
          aria-valuemin={0}
          aria-valuemax={upload.videos_total}
          aria-valuenow={finished}
          className="h-2 overflow-hidden rounded-full bg-slate-100"
        >
          <div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${percent}%` }} />
        </div>
      </div>
      <div className="grid grid-cols-2 divide-line rounded-lg border border-line text-center sm:grid-cols-4 sm:divide-x">
        {stats.map(([label, value, tone]) => (
          <div key={label} className="px-3 py-3">
            <p className="text-xs text-muted">{label}</p>
            <p className={`text-xl font-semibold tabular-nums ${tone}`}>{value}</p>
          </div>
        ))}
      </div>
      {(upload.invalid > 0 || upload.duplicates > 0 || upload.already_tracked > 0) && (
        <p className="text-xs text-muted">
          {[
            upload.invalid > 0 && `${upload.invalid} invalid link${upload.invalid === 1 ? "" : "s"} not tracked`,
            upload.duplicates > 0 && `${upload.duplicates} duplicate${upload.duplicates === 1 ? "" : "s"} skipped`,
            upload.already_tracked > 0 && `${upload.already_tracked} already tracked, so their stored data is shown and they are not fetched again`,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      )}
    </div>
  );
}

function problemText(row: VpUploadRow): string {
  if (row.outcome === "invalid") return row.message ?? "Invalid link";
  const status = row.video_status ?? "Failed";
  return row.video_status_reason ? `${status}: ${row.video_status_reason}` : status;
}

/** The links of a batch that gave no data, and why. */
export function BatchProblems({ rows }: { rows: VpUploadRow[] }) {
  const problems = rows.filter((row) => row.outcome === "invalid" || row.outcome === "failed");
  if (problems.length === 0) return null;
  return (
    <div className="max-h-56 overflow-y-auto rounded-lg border border-rose-100">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-rose-50 text-left text-xs font-semibold text-rose-800">
          <tr>
            <th className="w-12 px-3 py-2">#</th>
            <th className="px-3 py-2">Link</th>
            <th className="px-3 py-2">Why it has no data</th>
          </tr>
        </thead>
        <tbody>
          {problems.map((row) => (
            <tr key={row.row_number} className="border-t border-rose-100 align-top">
              <td className="px-3 py-2 tabular-nums text-muted">{row.row_number}</td>
              <td className="break-all px-3 py-2 text-slate-700">
                {row.outcome === "failed" && row.platform && (
                  <span className="mr-1.5 inline-block align-text-bottom" title={platformLabel(row.platform)}>
                    <PlatformIcon platform={row.platform} size={14} />
                  </span>
                )}
                {row.video_link || "(empty)"}
              </td>
              <td className="px-3 py-2 text-slate-600">{problemText(row)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
