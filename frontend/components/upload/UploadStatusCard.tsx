import { AlertTriangle, CheckCircle2, FileSpreadsheet, Loader2, RefreshCw, RotateCcw, XCircle } from "lucide-react";

import { Button, Card } from "@/components/ui/controls";
import { formatDateTime } from "@/lib/format";
import type { UploadDetail } from "@/types";

interface Props {
  upload: UploadDetail | undefined;
  validating: boolean;
  validationError: string | null;
  onReupload: () => void;
  onRetryFailed: () => void;
  retrying: boolean;
  actionError: string | null;
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "success" | "danger" | "warning" }) {
  const color = tone === "success" ? "text-emerald-600" : tone === "danger" ? "text-rose-600" : tone === "warning" ? "text-amber-600" : "text-ink";
  return (
    <div className="flex-1 px-4 py-3 first:pl-0">
      <p className="text-xs text-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</p>
    </div>
  );
}

export function UploadStatusCard({ upload, validating, validationError, onReupload, onRetryFailed, retrying, actionError }: Props) {
  if (validating) {
    return (
      <Card className="flex w-full flex-col justify-center p-6 lg:w-[440px]">
        <div className="flex items-center gap-3">
          <Loader2 className="animate-spin text-accent" size={22} />
          <div>
            <p className="font-semibold text-ink">Validating file…</p>
            <p className="text-sm text-muted">Checking columns and links before analysis starts.</p>
          </div>
        </div>
      </Card>
    );
  }

  if (validationError) {
    return (
      <Card className="flex w-full flex-col justify-center border-rose-200 p-6 lg:w-[440px]">
        <div className="flex items-start gap-3">
          <XCircle className="mt-0.5 shrink-0 text-rose-600" size={22} />
          <div>
            <p className="font-semibold text-ink">File could not be processed</p>
            <p className="mt-1 text-sm text-rose-700">{validationError}</p>
          </div>
        </div>
      </Card>
    );
  }

  if (!upload) {
    return (
      <Card className="flex w-full flex-col justify-center p-6 lg:w-[440px]">
        <div className="flex items-center gap-3 text-muted">
          <FileSpreadsheet size={22} />
          <p className="text-sm">No file processed yet. Results will appear here after upload.</p>
        </div>
      </Card>
    );
  }

  const processing = upload.status === "processing";
  const total = upload.total_rows;
  const percent = total ? Math.round((upload.processed_rows / total) * 100) : 0;
  const allFailed = !processing && total > 0 && upload.failed_rows === total;
  const withIssues = !processing && (upload.failed_rows > 0 || upload.partial_rows > 0 || upload.status === "failed");

  let icon = <CheckCircle2 className="text-emerald-600" size={24} />;
  let heading = "Analysis completed successfully";
  let container = "border-emerald-200 bg-emerald-50/40";
  if (processing) {
    icon = <Loader2 className="animate-spin text-accent" size={24} />;
    heading = "Analyzing creators…";
    container = "border-indigo-200 bg-accent-soft/40";
  } else if (allFailed || upload.status === "failed") {
    icon = <XCircle className="text-rose-600" size={24} />;
    heading = upload.status === "failed" ? "Processing stopped" : "No creators could be analyzed";
    container = "border-rose-200 bg-rose-50/40";
  } else if (withIssues) {
    icon = <AlertTriangle className="text-amber-600" size={24} />;
    heading = "Analysis completed with some issues";
    container = "border-amber-200 bg-amber-50/40";
  }

  return (
    <Card className={`w-full p-5 lg:w-[440px] ${container}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 shrink-0">{icon}</span>
          <div className="min-w-0">
            <p className="font-semibold text-ink">{heading}</p>
            <p className="truncate text-sm text-slate-600" title={upload.filename}>
              {upload.filename}
            </p>
            <p className="text-xs text-muted">{formatDateTime(upload.created_at)}</p>
          </div>
        </div>
        <Button variant="secondary" onClick={onReupload} disabled={processing} className="shrink-0">
          <RefreshCw size={15} />
          Re-upload
        </Button>
      </div>

      {processing && (
        <div className="mt-4">
          <div className="flex justify-between text-sm">
            <span className="font-medium text-ink">
              {upload.processed_rows} / {total} completed
            </span>
            <span className="text-muted">{percent}%</span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-indigo-100" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(percent, 3)}%` }} />
          </div>
        </div>
      )}

      {upload.error_message && <p className="mt-3 text-sm text-rose-700">{upload.error_message}</p>}
      {actionError && <p className="mt-3 text-sm text-rose-700">{actionError}</p>}

      <div className="mt-4 flex divide-x divide-line rounded-lg border border-line bg-white px-4">
        <Stat label="Total Channels" value={total} />
        <Stat label="Successful" value={upload.successful_rows} tone="success" />
        {upload.partial_rows > 0 && <Stat label="Partial" value={upload.partial_rows} tone="warning" />}
        <Stat label="Failed" value={upload.failed_rows} tone={upload.failed_rows ? "danger" : undefined} />
      </div>
      {!processing && upload.failed_rows + upload.partial_rows > 0 && (
        <Button variant="soft" className="mt-3 w-full" onClick={onRetryFailed} disabled={retrying}>
          {retrying ? <Loader2 size={15} className="animate-spin" /> : <RotateCcw size={15} />}
          Retry failed &amp; partial ({upload.failed_rows + upload.partial_rows})
        </Button>
      )}
      {upload.duplicate_rows > 0 && (
        <p className="mt-2 text-xs text-muted">
          {upload.duplicate_rows} duplicate row{upload.duplicate_rows === 1 ? "" : "s"} removed (same platform + channel).
        </p>
      )}
    </Card>
  );
}
