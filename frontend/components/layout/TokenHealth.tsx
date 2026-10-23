"use client";

import Link from "next/link";
import { AlertTriangle, X } from "lucide-react";
import type { ReactNode } from "react";

import { useApi } from "@/hooks/useApi";
import { useStoredValue } from "@/hooks/useStoredValue";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { ApiUsage, GroqKeyStatus, InstagramTokenStatus, MetaUsageMetrics } from "@/types";

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

/** The Meta token check for Settings (`force` re-checks with Meta when all data on screen is reloaded). */
export function useInstagramToken(enabled: boolean) {
  return useApi(enabled ? "ig-token-settings" : null, ({ force }) => api.instagramToken(force), { keepPrevious: true });
}

/** One short line about the Meta token, e.g. "Token valid · 45 days left". */
export function tokenSummary(token: InstagramTokenStatus): string {
  if (token.state === "ok") return token.never_expires ? "Token valid · never expires" : token.days_left !== null ? `Token valid · ${token.days_left} days left` : "Token valid";
  if (token.state === "expiring") return `Token expires in ${token.days_left ?? "a few"} days`;
  return token.message;
}

let usageRequest: Promise<ApiUsage> | null = null;

/** One request shared by all usage rows (they poll together). */
function loadUsage() {
  usageRequest ??= api.apiUsage().finally(() => {
    usageRequest = null;
  });
  return usageRequest;
}

function useUsage() {
  return useApi("api-usage", loadUsage, { refreshMs: 30000, keepPrevious: true });
}

export function UsageBar({ label, percent, full, valueNow, valueMax }: {
  label: string;
  percent: number;
  full?: boolean;
  valueNow: number;
  valueMax: number;
}) {
  const bar = full || percent >= 90 ? "bg-rose-500" : percent >= 70 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div
      className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100"
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

/** One usage line: name, headline number, bar, an optional status line (problems stay visible) and collapsed details. */
export function UsageRow({ title, value, bar, status, details }: {
  title: string;
  value: ReactNode;
  bar?: ReactNode;
  status?: ReactNode;
  details?: ReactNode;
}) {
  return (
    <div className="flex flex-col px-5 py-4">
      <p className="text-sm font-medium text-muted">{title}</p>
      <p className="mt-1 text-sm tabular-nums text-slate-600 [&>span:first-child]:text-xl">{value}</p>
      {bar}
      {status && <div className="mt-2 text-xs">{status}</div>}
      {details && (
        <details className="mt-1.5">
          <summary className="w-fit cursor-pointer select-none text-xs font-medium text-muted hover:text-ink">Details</summary>
          <div className="mt-2 space-y-1.5 rounded-lg bg-slate-50 px-3 py-2.5 text-xs text-slate-600">{details}</div>
        </details>
      )}
    </div>
  );
}

/** Loading skeleton, or the load error (with an optional retry). */
export function RowPlaceholder({ error, onRetry }: { error?: string; onRetry?: () => void }) {
  if (error)
    return (
      <p className="px-5 py-4 text-sm text-rose-600">
        {error}
        {onRetry && (
          <button type="button" onClick={onRetry} className="ml-2 font-medium underline underline-offset-2">
            Retry
          </button>
        )}
      </p>
    );
  return (
    <div className="px-5 py-4">
      <div className="h-12 animate-pulse rounded bg-slate-100" />
    </div>
  );
}

const units = (n: number) => n.toLocaleString("en-US");
const clockTime = (value: string) => new Date(value).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });

/** YouTube Data API units used today across all configured keys (counted by this app). */
export function YouTubeQuotaCard() {
  const { data, error } = useUsage();
  if (!data) return <RowPlaceholder error={error} />;
  const yt = data.youtube;
  const allExhausted = yt.key_count > 0 && yt.quota_exceeded_keys >= yt.key_count;
  return (
    <UsageRow
      title="YouTube"
      value={<><span className="font-semibold text-ink">{units(yt.total_units)}</span> / {units(yt.total_limit)} units today</>}
      bar={<UsageBar label="YouTube quota used today" percent={yt.percent} full={allExhausted} valueNow={yt.total_units} valueMax={yt.total_limit} />}
      status={
        yt.quota_exceeded_keys > 0 ? (
          <p className={`font-medium ${allExhausted ? "text-rose-600" : "text-amber-700"}`}>
            {allExhausted
              ? "All keys are out of quota. YouTube data is N/A until the reset."
              : `${yt.quota_exceeded_keys} of ${yt.key_count} keys are out of quota; the others are being used.`}
          </p>
        ) : (
          <p className="text-muted">Resets at {clockTime(yt.resets_at)} (midnight Pacific time)</p>
        )
      }
      details={
        <>
          <p>
            {yt.key_count} key{yt.key_count === 1 ? "" : "s"} × {units(yt.daily_limit_per_key)} units a day ({yt.percent}% used). Keys are used one
            after another automatically.
          </p>
          <p>{yt.note}</p>
        </>
      }
    />
  );
}

const META_METRICS: { key: keyof MetaUsageMetrics; label: string }[] = [
  { key: "call_count", label: "Call count" },
  { key: "total_cputime", label: "CPU time" },
  { key: "total_time", label: "Total time" },
];

/** Meta (Instagram + Facebook) hourly usage as reported by Meta, plus calls made today. */
export function InstagramUsageCard() {
  const { data, error } = useUsage();
  if (!data) return <RowPlaceholder error={error} />;
  const ig = data.instagram;
  const blocked = (ig.regain_access_minutes ?? 0) > 0;
  const metrics = ig.metrics ?? { call_count: null, total_cputime: null, total_time: null };
  const reported = !ig.stale && META_METRICS.some(({ key }) => metrics[key] !== null);
  return (
    <UsageRow
      title="Meta (Instagram + Facebook)"
      value={<><span className="font-semibold text-ink">{ig.percent}%</span> of the hourly limit</>}
      bar={<UsageBar label="Highest Meta-reported usage" percent={Math.min(ig.percent, 100)} full={blocked} valueNow={ig.percent} valueMax={100} />}
      status={
        blocked ? (
          <p className="font-medium text-rose-600">Meta is throttling requests. Access returns in about {ig.regain_access_minutes} min.</p>
        ) : ig.resume_at ? (
          <p className="font-medium text-amber-700">
            Paused by CreatorIntel&apos;s {ig.safety_stop_percent}% safety stop (not a Meta refusal) - continues after {clockTime(ig.resume_at)}.
          </p>
        ) : (
          <p className="text-muted">
            {units(ig.calls_today)} Instagram · {units(ig.facebook_calls_today)} Facebook call{ig.calls_today + ig.facebook_calls_today === 1 ? "" : "s"} today
          </p>
        )
      }
      details={
        <>
          {reported && (
            <dl className="grid grid-cols-3 gap-2 tabular-nums">
              {META_METRICS.map(({ key, label }) => (
                <div key={key} className="rounded bg-white px-2 py-1 ring-1 ring-line">
                  <dt className="text-muted">{label}</dt>
                  <dd className="font-medium text-ink">{metrics[key] === null ? "N/A" : `${metrics[key]}%`}</dd>
                </div>
              ))}
            </dl>
          )}
          <p>
            Meta limits call count, CPU time and total time over a rolling hour; the bar shows the highest. CreatorIntel pauses Meta
            calls at {ig.safety_stop_percent}%.
          </p>
          <p>
            {ig.observed_at
              ? ig.stale
                ? `No Instagram calls in the last hour (last at ${formatDateTime(ig.observed_at)}), so the hourly usage has reset.`
                : `Reported by Meta at ${formatDateTime(ig.observed_at)}.`
              : "No usage reported by Meta yet. It appears after the next Instagram request."}
          </p>
        </>
      }
    />
  );
}

const KEY_STATE: Record<GroqKeyStatus["state"], { label: string; cls: string }> = {
  active: { label: "Active", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  standby: { label: "Standby", cls: "bg-slate-100 text-slate-600 ring-slate-200" },
  unusable: { label: "Not working", cls: "bg-rose-50 text-rose-700 ring-rose-200" },
  daily_limit: { label: "Daily limit reached", cls: "bg-amber-50 text-amber-800 ring-amber-200" },
};

/** Groq AI tokens used today by the active key, the state of every configured key, and AI work waiting for quota. */
export function GroqUsageCard() {
  const { data, error } = useUsage();
  if (!data) return <RowPlaceholder error={error ? `Could not load Groq usage: ${error}` : undefined} />;
  const g = data.groq;
  if (!g.configured) return null; // shown as "Not set up" under Connections
  const unusable = g.keys.filter((k) => k.state === "unusable");
  const noKey = g.active_slot === null;
  const firstRetry = unusable.map((k) => k.until).filter((u): u is string => Boolean(u)).sort()[0];
  const pending = g.pending_creators + g.pending_videos;
  return (
    <UsageRow
      title="Groq AI"
      value={<><span className="font-semibold text-ink">{units(g.tokens_today)}</span> / {units(g.tokens_per_day)} tokens today</>}
      bar={<UsageBar label="Groq tokens used today" percent={g.percent} full={g.daily_limit_reached || noKey} valueNow={g.tokens_today} valueMax={g.tokens_per_day} />}
      status={
        <>
          {noKey && (
            <p role="alert" className="font-medium text-rose-600">
              No Groq key is working ({unusable.map((k) => `#${k.slot}`).join(", ")}). AI work waits and retries automatically
              {firstRetry ? ` from ${clockTime(firstRetry)}` : ""}. Check the keys in backend/.env.
            </p>
          )}
          {g.daily_limit_reached && g.resets_at && (
            <p role="alert" className="font-medium text-amber-700">
              Daily limit reached on key #{g.active_slot}. AI work continues automatically after about {clockTime(g.resets_at)}.
            </p>
          )}
          {pending > 0 && (
            <p className="mt-1 text-amber-700">
              Waiting for AI: {g.pending_creators > 0 && `${g.pending_creators} creator${g.pending_creators === 1 ? "" : "s"}`}
              {g.pending_creators > 0 && g.pending_videos > 0 && " and "}
              {g.pending_videos > 0 && `${g.pending_videos} video${g.pending_videos === 1 ? "" : "s"}`} - analysed automatically, nothing to do.
            </p>
          )}
          {!noKey && !g.daily_limit_reached && pending === 0 && (
            <p className="text-muted">
              Key #{g.active_slot} active · {units(g.requests_today)} / {units(g.requests_per_day)} requests
            </p>
          )}
        </>
      }
      details={
        <>
          <ul className="space-y-1">
            {g.keys.map((key) => (
              <li key={key.slot} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="w-12 font-medium text-slate-700">Key #{key.slot}</span>
                <span className={`inline-flex rounded-md px-1.5 py-0.5 font-medium ring-1 ring-inset ${KEY_STATE[key.state].cls}`}>
                  {KEY_STATE[key.state].label}
                </span>
                {key.state !== "active" && (
                  <span className="text-muted">
                    {key.message}
                    {key.until && (key.state === "unusable" ? ` · tried again at ${clockTime(key.until)}` : ` · resets about ${clockTime(key.until)}`)}
                  </span>
                )}
              </li>
            ))}
          </ul>
          <p>
            {g.model} · counted per UTC day ({g.percent}% used){g.requests_source === "groq" ? " · requests as reported by Groq" : ""}. Other keys are
            never used to get around a daily limit (Groq terms).
          </p>
          <p>{g.note} Keys are never shown - only their slot number.</p>
        </>
      }
    />
  );
}
