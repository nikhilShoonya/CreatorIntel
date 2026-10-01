"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, X, XCircle } from "lucide-react";

import { Button } from "@/components/ui/controls";
import { useApi } from "@/hooks/useApi";
import { useStoredValue } from "@/hooks/useStoredValue";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { InstagramTokenStatus } from "@/types";

const PROBLEM: InstagramTokenStatus["state"][] = ["expired", "invalid", "wrong_type", "expiring"];
const DISMISS_KEY = "creatorintel:token-banner-dismissed";

function signature(status: InstagramTokenStatus) {
  return `${status.state}:${status.expires_at ?? ""}:${status.message}`;
}

/** Slim app-wide warning when the Instagram (Meta) token is expired, invalid or about to expire. */
export function TokenBanner() {
  const { data } = useApi("ig-token", () => api.instagramToken());
  const [dismissed, setDismissed] = useStoredValue(DISMISS_KEY, "");
  if (!data || !PROBLEM.includes(data.state) || dismissed === signature(data)) return null;
  const expiring = data.state === "expiring";
  return (
    <div
      role="alert"
      className={`mb-5 flex items-start gap-3 rounded-lg border px-4 py-3 text-sm ${
        expiring ? "border-amber-200 bg-amber-50 text-amber-900" : "border-rose-200 bg-rose-50 text-rose-800"
      }`}
    >
      <AlertTriangle size={18} className="mt-0.5 shrink-0" />
      <p className="flex-1">
        <span className="font-medium">{expiring ? "Instagram token expires soon. " : "Instagram data is unavailable. "}</span>
        {data.message}{" "}
        <Link href="/settings" className="font-medium underline underline-offset-2">
          Open Settings
        </Link>
      </p>
      <button type="button" onClick={() => setDismissed(signature(data))} className="rounded p-0.5 opacity-70 hover:opacity-100" aria-label="Dismiss">
        <X size={16} />
      </button>
    </div>
  );
}

/** Token details for the Settings page. */
export function TokenStatusCard() {
  const [refresh, setRefresh] = useState(0);
  const { data, error, loading } = useApi(`ig-token-settings:${refresh}`, () => api.instagramToken(refresh > 0), {
    keepPrevious: true,
  });
  const good = data?.state === "ok";
  const warn = data?.state === "expiring" || data?.state === "unknown";
  const Icon = good ? CheckCircle2 : warn ? AlertTriangle : XCircle;
  const tone = good ? "text-emerald-700" : warn ? "text-amber-700" : "text-rose-600";

  return (
    <div className="px-5 py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-medium text-ink">Meta access token</p>
          {error && <p className="text-sm text-rose-600">{error}</p>}
          {!data && !error && <p className="text-sm text-muted">Checking with Meta…</p>}
          {data && (
            <>
              <p className={`mt-0.5 flex items-start gap-1.5 text-sm ${tone}`}>
                <Icon size={16} className="mt-0.5 shrink-0" />
                <span>{data.message}</span>
              </p>
              {data.days_left !== null && (data.state === "ok" || data.state === "expiring") && (
                <span
                  className={`mt-2 inline-flex rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${
                    data.state === "expiring" ? "bg-amber-50 text-amber-800 ring-amber-200" : "bg-emerald-50 text-emerald-700 ring-emerald-200"
                  }`}
                >
                  {data.days_left} day{data.days_left === 1 ? "" : "s"} left
                </span>
              )}
              {data.never_expires && (
                <span className="mt-2 inline-flex rounded-md bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-700 ring-1 ring-inset ring-emerald-200">
                  Never expires
                </span>
              )}
              {data.expires_at && <p className="mt-1 text-xs text-muted">Expires {formatDateTime(data.expires_at)}</p>}
              {data.missing_permissions.length > 0 && (
                <p className="mt-1 text-xs text-amber-700">Missing permissions: {data.missing_permissions.join(", ")}</p>
              )}
              <p className="mt-1 text-xs text-muted">Last checked {formatDateTime(data.checked_at)}</p>
            </>
          )}
        </div>
        <Button onClick={() => setRefresh((n) => n + 1)} disabled={loading} className="shrink-0">
          {loading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
          Check again
        </Button>
      </div>
    </div>
  );
}

/** YouTube Data API units used today, per configured key (counted by this app). */
export function YouTubeQuotaCard() {
  const { data, error } = useApi("yt-quota", () => api.youtubeQuota(), { refreshMs: 30000, keepPrevious: true });
  if (error) return <p className="px-5 py-4 text-sm text-rose-600">{error}</p>;
  if (!data) return <div className="mx-5 my-4 h-10 animate-pulse rounded bg-slate-100" />;
  const reset = new Date(data.resets_at).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
  return (
    <div className="px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium text-ink">YouTube quota used today</p>
        <p className="text-xs text-muted">Resets daily at {reset} (midnight Pacific time)</p>
      </div>
      <ul className="mt-3 space-y-3">
        {data.keys.map((key) => {
          const level = key.quota_exceeded || key.percent >= 90 ? "high" : key.percent >= 70 ? "medium" : "low";
          const bar = level === "high" ? "bg-rose-500" : level === "medium" ? "bg-amber-500" : "bg-emerald-500";
          return (
            <li key={key.fingerprint}>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="font-medium text-slate-700">{key.label}</span>
                <span className="tabular-nums text-slate-600">
                  {key.units.toLocaleString("en-US")} / {key.limit.toLocaleString("en-US")} units
                  <span className="ml-1.5 text-muted">({key.percent}%)</span>
                  {key.quota_exceeded && <span className="ml-2 font-semibold text-rose-600">Quota exceeded</span>}
                </span>
              </div>
              <div
                className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-100"
                role="meter"
                aria-label={`${key.label} quota used`}
                aria-valuenow={key.units}
                aria-valuemin={0}
                aria-valuemax={key.limit}
              >
                <div className={`h-full rounded-full ${bar}`} style={{ width: `${Math.max(key.percent, key.units ? 1 : 0)}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs text-muted">
        {data.note} When one key runs out, the next key is used automatically.
      </p>
    </div>
  );
}

