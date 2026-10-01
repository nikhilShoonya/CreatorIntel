"use client";

import Link from "next/link";
import { AlertTriangle, CheckCircle2, X, XCircle } from "lucide-react";

import { useApi } from "@/hooks/useApi";
import { useStoredValue } from "@/hooks/useStoredValue";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { ApiUsage, InstagramTokenStatus } from "@/types";

const PROBLEM: InstagramTokenStatus["state"][] = ["expired", "invalid", "wrong_type", "expiring"];
const DISMISS_KEY = "creatorintel:token-banner-dismissed";

function signature(status: InstagramTokenStatus) {
  return `${status.state}:${status.expires_at ?? ""}:${status.message}`;
}

/** Slim app-wide warning when the Instagram (Meta) token is expired, invalid or about to expire. */
export function TokenBanner() {
  const { data } = useApi("ig-token", ({ force }) => api.instagramToken(force));
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
  // "Refresh data" in the top bar re-checks the token with Meta (force = true).
  const { data, error } = useApi("ig-token-settings", ({ force }) => api.instagramToken(force), { keepPrevious: true });
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
      </div>
    </div>
  );
}

let usageRequest: Promise<ApiUsage> | null = null;

/** One request shared by both usage cards (they poll together). */
function loadUsage() {
  usageRequest ??= api.apiUsage().finally(() => {
    usageRequest = null;
  });
  return usageRequest;
}

function useUsage() {
  return useApi("api-usage", loadUsage, { refreshMs: 30000, keepPrevious: true });
}

function UsageBar({ label, percent, full, valueNow, valueMax }: {
  label: string;
  percent: number;
  full?: boolean;
  valueNow: number;
  valueMax: number;
}) {
  const bar = full || percent >= 90 ? "bg-rose-500" : percent >= 70 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div
      className="mt-2 h-2.5 overflow-hidden rounded-full bg-slate-100"
      role="meter"
      aria-label={label}
      aria-valuenow={valueNow}
      aria-valuemin={0}
      aria-valuemax={valueMax}
    >
      <div className={`h-full rounded-full transition-[width] ${bar}`} style={{ width: `${Math.max(percent, valueNow ? 1 : 0)}%` }} />
    </div>
  );
}

function UsageShell({ title, aside, children }: { title: string; aside: string; children: React.ReactNode }) {
  return (
    <div className="px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium text-ink">{title}</p>
        <p className="text-xs text-muted">{aside}</p>
      </div>
      {children}
    </div>
  );
}

const units = (n: number) => n.toLocaleString("en-US");

/** YouTube Data API units used today across all configured keys (counted by this app). */
export function YouTubeQuotaCard() {
  const { data, error } = useUsage();
  if (error) return <p className="px-5 py-4 text-sm text-rose-600">{error}</p>;
  if (!data) return <div className="mx-5 my-4 h-10 animate-pulse rounded bg-slate-100" />;
  const yt = data.youtube;
  const reset = new Date(yt.resets_at).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
  const allExhausted = yt.key_count > 0 && yt.quota_exceeded_keys >= yt.key_count;
  return (
    <UsageShell title="YouTube quota used today" aside={`Resets daily at ${reset} (midnight Pacific time)`}>
      <p className="mt-3 flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
        <span className="tabular-nums text-slate-700">
          <span className="font-semibold text-ink">{units(yt.total_units)}</span> / {units(yt.total_limit)} units
          <span className="ml-1.5 text-muted">({yt.percent}%)</span>
        </span>
        <span className="text-xs text-muted">
          {yt.key_count} key{yt.key_count === 1 ? "" : "s"} × {units(yt.daily_limit_per_key)} units
        </span>
      </p>
      <UsageBar label="YouTube quota used today" percent={yt.percent} full={allExhausted} valueNow={yt.total_units} valueMax={yt.total_limit} />
      {yt.quota_exceeded_keys > 0 && (
        <p className={`mt-2 text-xs font-medium ${allExhausted ? "text-rose-600" : "text-amber-700"}`}>
          {allExhausted
            ? "All keys are out of quota. YouTube data will be N/A until the reset."
            : `${yt.quota_exceeded_keys} of ${yt.key_count} keys ran out of quota; the remaining keys are being used.`}
        </p>
      )}
      <p className="mt-2 text-xs text-muted">{yt.note} Keys are used one after another automatically.</p>
    </UsageShell>
  );
}

/** Instagram Graph API calls today and Meta's own report of its hourly rate limit. */
export function InstagramUsageCard() {
  const { data, error } = useUsage();
  if (error) return <p className="px-5 py-4 text-sm text-rose-600">{error}</p>;
  if (!data) return <div className="mx-5 my-4 h-10 animate-pulse rounded bg-slate-100" />;
  const ig = data.instagram;
  const blocked = (ig.regain_access_minutes ?? 0) > 0;
  return (
    <UsageShell title="Instagram API usage" aside="Meta's limit is a rolling one-hour window">
      <p className="mt-3 flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
        <span className="tabular-nums text-slate-700">
          <span className="font-semibold text-ink">{ig.percent}%</span> of Meta&apos;s hourly limit used
        </span>
        <span className="text-xs tabular-nums text-muted">
          {units(ig.calls_today)} call{ig.calls_today === 1 ? "" : "s"} today
        </span>
      </p>
      <UsageBar label="Instagram hourly limit used" percent={Math.min(ig.percent, 100)} full={blocked} valueNow={ig.percent} valueMax={100} />
      {blocked && (
        <p className="mt-2 text-xs font-medium text-rose-600">
          Meta is throttling requests. Access returns in about {ig.regain_access_minutes} min.
        </p>
      )}
      <p className="mt-2 text-xs text-muted">
        {ig.observed_at
          ? ig.stale
            ? `No Instagram calls in the last hour (last at ${formatDateTime(ig.observed_at)}), so the hourly usage has reset.`
            : `Reported by Meta at ${formatDateTime(ig.observed_at)}.`
          : "No usage reported by Meta yet. It appears after the next Instagram request."}
      </p>
    </UsageShell>
  );
}
