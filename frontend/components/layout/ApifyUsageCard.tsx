"use client";

import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { formatNumber } from "@/lib/format";
import { PlatformIcon } from "@/components/ui/PlatformIcon";

export function ApifyUsageCard() {
  const { data, error, reload } = useApi("apify-usage", api.apifyUsage, { refreshMs: 15000 });
  return (
    <div className="px-5 py-4">
      <div className="flex items-start gap-4">
        <PlatformIcon platform="instagram" size={22} />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-ink">Apify · Public Reel metrics</p>
          <p className="text-sm text-muted">Direct Reel links in Video Performance. Creator Analytics and posts keep their existing providers.</p>
          {data && !data.configured && <p className="mt-1 text-xs text-muted">Enable APIFY_INSTAGRAM_REEL_ENABLED and set APIFY_API_TOKEN in the backend environment, then restart.</p>}
        </div>
        <span className={`text-sm font-medium ${data?.configured ? "text-emerald-700" : "text-muted"}`}>
          {data ? data.configured ? "Configured" : "Disabled" : "Loading…"}
        </span>
      </div>
      {error && <button className="mt-2 text-sm text-rose-600" onClick={reload}>Usage unavailable · Retry</button>}
      {data && (
        <div className="mt-3 pl-9 text-sm">
          <p className="text-ink">{formatNumber(data.used_units)} / {formatNumber(data.limit)} result units · {data.month} (UTC)</p>
          <p className="text-muted">{formatNumber(data.reserved_units)} reserved · {data.active_runs} active runs · {formatNumber(data.returned_items)} returned items</p>
          {data.configured && data.remaining === 0 && <p className="mt-1 text-amber-700">Application usage limit reached. New runs are blocked.</p>}
          <p className="mt-1 text-xs text-muted">{data.note}</p>
          {data.recent_runs.filter((run) => run.message).slice(0, 3).map((run) => (
            <p key={run.id} className="mt-1 break-words text-xs text-muted">{run.status}: {run.message} · Job {run.id}{run.remote_run_id ? ` · Actor run ${run.remote_run_id}` : ""}</p>
          ))}
        </div>
      )}
    </div>
  );
}
