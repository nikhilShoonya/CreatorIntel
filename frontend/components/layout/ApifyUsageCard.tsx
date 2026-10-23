"use client";

import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { formatNumber } from "@/lib/format";

import { RowPlaceholder, UsageBar, UsageRow } from "./TokenHealth";

/** Apify's local job / usage state (never calls Apify, never returns credentials). */
export function useApifyUsage() {
  return useApi("apify-usage", api.apifyUsage, { refreshMs: 15000, keepPrevious: true });
}

/** Apify reel results used this month, against the app's own monthly limit. */
export function ApifyUsageCard() {
  const { data, error, reload } = useApifyUsage();
  if (error) return <RowPlaceholder error={error} onRetry={reload} />;
  if (!data) return <RowPlaceholder />;
  if (!data.configured) return null; // shown as "Not set up" under Connections
  const percent = data.limit ? Math.min(Math.round((data.used_units / data.limit) * 100), 100) : 100;
  const runs = data.recent_runs.filter((run) => run.message).slice(0, 3);
  return (
    <UsageRow
      title="Apify (Instagram reels)"
      value={<><span className="font-semibold text-ink">{formatNumber(data.used_units)}</span> / {formatNumber(data.limit)} reels this month</>}
      bar={<UsageBar label="Apify monthly results used" percent={percent} full={data.remaining === 0} valueNow={data.used_units} valueMax={data.limit} />}
      status={
        data.remaining === 0 ? (
          <p className="font-medium text-amber-700">Monthly limit reached - new Apify runs are blocked.</p>
        ) : (
          <p className="text-muted">
            {formatNumber(data.remaining)} left · {data.active_runs} run{data.active_runs === 1 ? "" : "s"} in progress · {data.month} (UTC)
          </p>
        )
      }
      details={
        <>
          <p>
            {formatNumber(data.reserved_units)} reserved · {formatNumber(data.returned_items)} results returned this month.
          </p>
          <p>{data.note}</p>
          {runs.map((run) => (
            <p key={run.id} className="break-words">
              <span className="font-medium text-slate-700">{run.status}:</span> {run.message} · Job {run.id}
              {run.remote_run_id ? ` · Actor run ${run.remote_run_id}` : ""}
            </p>
          ))}
        </>
      }
    />
  );
}
