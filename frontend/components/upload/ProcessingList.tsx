import { AlertCircle, CheckCircle2, Clock3, Loader2 } from "lucide-react";

import { Card } from "@/components/ui/controls";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import type { UploadDetail, UploadItem } from "@/types";

function StatusIcon({ item }: { item: UploadItem }) {
  switch (item.status) {
    case "Completed":
      return <CheckCircle2 size={16} className="text-emerald-600" aria-label="Completed" />;
    case "Partial":
      return <AlertCircle size={16} className="text-amber-500" aria-label="Partial" />;
    case "Failed":
      return <AlertCircle size={16} className="text-rose-500" aria-label="Failed" />;
    case "Processing":
      return <Loader2 size={16} className="animate-spin text-accent" aria-label="Processing" />;
    default:
      return <Clock3 size={16} className="text-slate-400" aria-label="Pending" />;
  }
}

/** Live per-creator progress shown while an upload is being processed. */
export function ProcessingList({ upload }: { upload: UploadDetail }) {
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink">
          Processing {upload.processed_rows} / {upload.total_rows}
        </h3>
        <span className="text-xs text-muted">Updates automatically</span>
      </div>
      <ul className="grid max-h-56 grid-cols-1 gap-x-6 gap-y-1 overflow-y-auto sm:grid-cols-2 xl:grid-cols-3">
        {upload.items.map((item) => (
          <li key={item.creator_id} className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm" title={item.error_message ?? undefined}>
            <StatusIcon item={item} />
            <PlatformIcon platform={item.platform} size={15} />
            <span className="truncate text-slate-700">{item.channel_name}</span>
            <span className={`ml-auto shrink-0 text-xs ${item.status === "Processing" ? "text-accent" : "text-muted"}`}>
              {item.from_cache ? "Cached" : item.status === "Processing" ? "Processing…" : item.status}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
