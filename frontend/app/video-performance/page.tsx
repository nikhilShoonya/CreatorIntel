"use client";

import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  ChevronDown,
  Copy,
  ExternalLink,
  Eye,
  Film,
  Heart,
  History,
  Loader2,
  MoreVertical,
  Play,
  Radar,
  RefreshCw,
  Sparkles,
  TrendingUp,
  Trophy,
  Users,
  VideoOff,
  Zap,
} from "lucide-react";

import { Card, EmptyState, ErrorBanner } from "@/components/ui/controls";
import { Menu } from "@/components/ui/Menu";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { SENTIMENT_COLORS, VpSentimentBadge } from "@/components/video-performance/badges";
import { Donut, MiniBars, MiniLine, TrendChart, type TrendDatum } from "@/components/video-performance/charts";
import { HistoryDialog } from "@/components/video-performance/dialogs";
import { formatChecked, formatSigned } from "@/components/video-performance/format";
import { useApi } from "@/hooks/useApi";
import { useStoredValue } from "@/hooks/useStoredValue";
import { formatCompact, formatDateTime, formatNumber, formatPercent, platformLabel, safeHref } from "@/lib/format";
import { vpApi } from "@/lib/vpApi";
import type { SentimentCounts, VpDashboard, VpJob, VpPlatform, VpTrendPoint, VpVideo } from "@/types/videoPerformance";

const RANGES = [7, 14, 30, 90];
const RANGE_KEY = "creatorintel:vp-range";
const JOB_LABELS: Record<VpJob["job_type"], string> = {
  creator_discovery: "New Video Discovery",
  metrics_refresh: "Daily View Refresh",
};
const PREVIEW_ROWS = 5;

type Notify = (message: string) => void;

export default function VideoPerformanceDashboard() {
  const [storedRange, setRange] = useStoredValue(RANGE_KEY, "7");
  const days = RANGES.includes(Number(storedRange)) ? Number(storedRange) : 7;
  const [token, setToken] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const { data, error, reload } = useApi(`vp-dashboard:${days}:${token}`, () => vpApi.dashboard(days), {
    keepPrevious: true,
    refreshMs: (d: VpDashboard | undefined) => (d?.jobs.some((j) => j.running) ? 4000 : false),
  });

  const notify: Notify = (message) => {
    setNotice(message);
    window.setTimeout(() => setNotice(null), 4000);
  };

  async function runJob(job: VpJob["job_type"]) {
    try {
      await vpApi.runJob(job);
      notify(`${JOB_LABELS[job]} started`);
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not start the job");
    }
    setToken((n) => n + 1);
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Video Performance</h1>
          <p className="text-sm text-muted">Track, analyze and get insights on your YouTube, Instagram &amp; Facebook videos</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <RangeSelect value={days} onChange={(value) => setRange(String(value))} />
          {data?.jobs.map((job) => <JobButton key={job.job_type} job={job} timezone={data.timezone} onRun={runJob} />)}
        </div>
      </div>

      {notice && (
        <div role="status" className="rounded-lg border border-indigo-200 bg-accent-soft px-4 py-2.5 text-sm text-accent">
          {notice}
        </div>
      )}
      {error && <ErrorBanner message={error} onRetry={reload} />}
      {!data && !error && <DashboardSkeleton />}

      {data && data.total_videos === 0 && (
        <Card>
          <EmptyState
            icon={<Film size={22} />}
            title="Nothing tracked yet"
            description="Add videos or track a creator in the Tracking Library. Views are then checked automatically every day."
            action={
              <Link href="/video-performance/library" className="inline-flex h-9 items-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-white hover:bg-accent-strong">
                Open Tracking Library <ArrowRight size={15} />
              </Link>
            }
          />
        </Card>
      )}

      {data && data.total_videos > 0 && <DashboardBody data={data} notify={notify} />}
    </div>
  );
}

// ------------------------------------------------------------------ header
function RangeSelect({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  return (
    <label className="relative inline-flex items-center">
      <CalendarDays size={16} className="pointer-events-none absolute left-3 text-slate-500" />
      <select
        aria-label="Date range"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-11 cursor-pointer appearance-none rounded-xl border border-line bg-white pl-9 pr-9 text-sm font-medium text-slate-700 shadow-xs outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-indigo-100"
      >
        {RANGES.map((range) => (
          <option key={range} value={range}>
            Last {range} days
          </option>
        ))}
      </select>
      <ChevronDown size={15} className="pointer-events-none absolute right-3 text-slate-500" />
    </label>
  );
}

function JobButton({ job, timezone, onRun }: { job: VpJob; timezone: string; onRun: (job: VpJob["job_type"]) => void }) {
  const discovery = job.job_type === "creator_discovery";
  const Icon = discovery ? Play : RefreshCw;
  const last = job.last_finished_at ? formatChecked(job.last_finished_at) : "never";
  const next = job.next_run_at ? formatDateTime(job.next_run_at) : "scheduler off";
  
  let progressText = "";
  if (job.running && typeof job.progress_done === "number" && typeof job.progress_total === "number" && job.progress_total > 0) {
    const percent = Math.round((job.progress_done / job.progress_total) * 100);
    progressText = ` (${percent}%)`;
  }

  return (
    <button
      type="button"
      onClick={() => onRun(job.job_type)}
      disabled={job.running}
      title={`Last run: ${last}${job.last_status === "failed" ? " (failed)" : ""} · Next: ${next} (${timezone})`}
      className={`flex min-w-[196px] items-center gap-3 rounded-xl border px-4 py-2 text-left transition-colors disabled:cursor-wait ${
        discovery ? "border-indigo-200 bg-indigo-50 hover:bg-indigo-100/70" : "border-emerald-200 bg-emerald-50 hover:bg-emerald-100/70"
      }`}
    >
      <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${discovery ? "text-indigo-600" : "text-emerald-600"}`}>
        {job.running ? <Loader2 size={20} className="animate-spin" /> : <Icon size={20} />}
      </span>
      <span className="leading-tight">
        <span className={`block text-sm font-semibold ${discovery ? "text-indigo-700" : "text-emerald-700"}`}>{JOB_LABELS[job.job_type]}</span>
        <span className={`block text-xs ${discovery ? "text-indigo-600/80" : "text-emerald-700/80"}`}>
          {job.running ? `Running now…${progressText}` : `Run now · last ${last}${job.last_status === "failed" ? " (failed)" : ""}`}
        </span>
      </span>
    </button>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        {Array.from({ length: 7 }, (_, i) => (
          <div key={i} className="h-[120px] animate-pulse rounded-xl border border-line bg-white" />
        ))}
      </div>
      <div className="grid gap-5 xl:grid-cols-12">
        <div className="h-80 animate-pulse rounded-xl border border-line bg-white xl:col-span-7" />
        <div className="h-80 animate-pulse rounded-xl border border-line bg-white xl:col-span-5" />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ body
function DashboardBody({ data, notify }: { data: VpDashboard; notify: Notify }) {
  const [historyId, setHistoryId] = useState<number | null>(null);
  const actions: RowActions = {
    history: setHistoryId,
    copy: async (url) => {
      try {
        await navigator.clipboard.writeText(url);
        notify("Video link copied");
      } catch {
        notify("Could not copy the link");
      }
    },
  };

  return (
    <>
      {data.videos_down > 0 && <VideosDownAlert count={data.videos_down} />}
      <KpiRow data={data} />

      <div className="grid gap-5 xl:grid-cols-12">
        <ViewsTrendCard trend={data.trend} days={data.range_days} />
        <SentimentOverviewCard data={data} />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <RankingCard
          title="Top Performing Videos"
          hint="Videos with the highest views"
          icon={<Trophy size={18} />}
          tone="bg-amber-50 text-amber-600"
          videos={data.top_performing}
          trends={data.video_trends}
          showViews
          viewAll="/video-performance/library?sort=current_views"
          actions={actions}
        />
        <RankingCard
          title="Highest Engagement Rate"
          hint="Based on (likes + comments) / views"
          icon={<Heart size={18} />}
          tone="bg-rose-50 text-rose-600"
          videos={data.highest_engagement}
          trends={data.video_trends}
          viewAll="/video-performance/library?sort=engagement_rate"
          actions={actions}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-12">
        <RecentSentimentCard items={data.recent_sentiment} actions={actions} />
        <LatestDetectedCard videos={data.latest_detected} actions={actions} />
      </div>

      <HistoryDialog videoId={historyId} onClose={() => setHistoryId(null)} />
    </>
  );
}

function VideosDownAlert({ count }: { count: number }) {
  return (
    <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      <VideoOff size={18} className="shrink-0" />
      <p className="flex-1">
        <span className="font-semibold">
          {count} video{count === 1 ? " is" : "s are"} down.
        </span>{" "}
        The platform no longer returns {count === 1 ? "it" : "them"} (deleted, made private or a wrong link). Views recorded earlier
        are kept, and the daily check keeps looking in case {count === 1 ? "it comes" : "they come"} back.
      </p>
      <Link
        href={`/video-performance/library?status=${encodeURIComponent("Video Down")}`}
        className="inline-flex h-8 items-center rounded-lg border border-red-200 bg-white px-3 text-xs font-medium text-red-700 hover:bg-red-100/60"
      >
        View down videos
      </Link>
    </div>
  );
}

// ------------------------------------------------------------------ KPIs
function rangeLabel(days: number) {
  return days === 7 ? "this week" : `in ${days} days`;
}

function Kpi({
  label,
  value,
  icon,
  tone,
  sub,
  trend,
  spark,
  sparkColor,
  sparkLabel,
}: {
  label: string;
  value: string;
  icon: ReactNode;
  tone: string;
  sub: ReactNode;
  trend?: "up" | "down" | "flat";
  spark: (number | null)[];
  sparkColor: string;
  sparkLabel: string;
}) {
  const subTone = trend === "up" ? "text-emerald-600" : trend === "down" ? "text-rose-600" : "text-muted";
  return (
    <Card className="flex flex-col p-4">
      <div className="flex items-center gap-2.5">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${tone}`}>{icon}</span>
        <p className="text-xs font-medium leading-tight text-slate-600">{label}</p>
      </div>
      <div className="mt-3 flex items-end justify-between gap-2">
        <p className="text-2xl font-semibold tabular-nums tracking-tight text-ink">{value}</p>
        <MiniBars values={spark} color={sparkColor} label={sparkLabel} />
      </div>
      <p className={`mt-1 flex items-center gap-0.5 text-xs ${subTone}`} title={typeof sub === "string" ? sub : undefined}>
        {trend === "up" && <ArrowUpRight size={13} className="shrink-0" />}
        {trend === "down" && <ArrowDownRight size={13} className="shrink-0" />}
        <span className="truncate">{sub}</span>
      </p>
    </Card>
  );
}

function direction(value: number | null | undefined): "up" | "down" | "flat" {
  if (!value) return "flat";
  return value > 0 ? "up" : "down";
}

function KpiRow({ data }: { data: VpDashboard }) {
  const trend = data.trend;
  const tracked = (p: VpTrendPoint) => p.videos > 0;
  const series = (pick: (p: VpTrendPoint) => number | null) => trend.map((p) => (tracked(p) ? pick(p) : null));
  const today = trend[trend.length - 1];
  const yesterday = trend[trend.length - 2];
  const share = (n: number) => (data.total_videos ? `${formatPercent((n / data.total_videos) * 100)} of total` : "N/A");
  const newInRange = trend.reduce((sum, p) => sum + p.new_videos, 0);
  const label = rangeLabel(data.range_days);
  const growth = data.views_growth_pct;

  return (
    <div className={`grid grid-cols-2 gap-3 md:grid-cols-4 ${data.facebook_videos > 0 ? "xl:grid-cols-8" : "xl:grid-cols-7"}`}>
      <Kpi
        label="Total Tracked Videos"
        value={formatNumber(data.total_videos)}
        icon={<Film size={18} />}
        tone="bg-indigo-50 text-indigo-600"
        trend={direction(data.videos_added_in_range)}
        sub={`${formatSigned(data.videos_added_in_range)} ${label}`}
        spark={series((p) => p.videos)}
        sparkColor="#6366f1"
        sparkLabel="Tracked videos per day"
      />
      <Kpi
        label="YouTube Videos"
        value={formatNumber(data.youtube_videos)}
        icon={<PlatformIcon platform="youtube" size={18} />}
        tone="bg-red-50"
        sub={share(data.youtube_videos)}
        spark={series((p) => p.youtube_videos)}
        sparkColor="#ef4444"
        sparkLabel="YouTube videos tracked per day"
      />
      <Kpi
        label="Instagram Videos"
        value={formatNumber(data.instagram_videos)}
        icon={<PlatformIcon platform="instagram" size={18} />}
        tone="bg-pink-50"
        sub={share(data.instagram_videos)}
        spark={series((p) => p.instagram_videos)}
        sparkColor="#ec4899"
        sparkLabel="Instagram videos tracked per day"
      />
      {data.facebook_videos > 0 && (
        <Kpi
          label="Facebook Videos"
          value={formatNumber(data.facebook_videos)}
          icon={<PlatformIcon platform="facebook" size={18} />}
          tone="bg-blue-50"
          sub={share(data.facebook_videos)}
          spark={series((p) => p.facebook_videos)}
          sparkColor="#1877f2"
          sparkLabel="Facebook videos tracked per day"
        />
      )}
      <Kpi
        label="Total Current Views"
        value={formatCompact(data.total_current_views)}
        icon={<Eye size={18} />}
        tone="bg-sky-50 text-sky-600"
        trend={direction(growth ?? data.views_gained_in_range)}
        sub={growth !== null ? `${growth > 0 ? "+" : ""}${growth.toFixed(1)}% ${label}` : `${formatSigned(data.views_gained_in_range)} views ${label}`}
        spark={series((p) => p.total_views)}
        sparkColor="#3b82f6"
        sparkLabel="Total views per day"
      />
      <Kpi
        label="Views Gained Today"
        value={formatSigned(today?.views_gained ?? 0)}
        icon={<TrendingUp size={18} />}
        tone="bg-emerald-50 text-emerald-600"
        sub={`vs ${formatSigned(yesterday?.views_gained ?? 0)} yesterday`}
        spark={series((p) => p.views_gained)}
        sparkColor="#10b981"
        sparkLabel="Views gained per day"
      />
      <Kpi
        label="New Videos Detected"
        value={formatNumber(newInRange)}
        icon={<Radar size={18} />}
        tone="bg-orange-50 text-orange-600"
        sub={`Last ${data.range_days} days · ${today?.new_videos ?? 0} today`}
        spark={trend.map((p) => p.new_videos)}
        sparkColor="#f97316"
        sparkLabel="New videos detected per day"
      />
      <Kpi
        label="Active Trackings"
        value={formatNumber(data.active_trackings)}
        icon={<Users size={18} />}
        tone="bg-violet-50 text-violet-600"
        sub={`${data.active_creators} creator${data.active_creators === 1 ? "" : "s"} watched`}
        spark={series((p) => p.videos_checked)}
        sparkColor="#8b5cf6"
        sparkLabel="Videos checked per day"
      />
    </div>
  );
}

// ------------------------------------------------------------------ trend
type TrendMetric = "total" | "gained" | "engagement";
const METRICS: { id: TrendMetric; label: string }[] = [
  { id: "total", label: "Total Views" },
  { id: "gained", label: "Views Gained" },
  { id: "engagement", label: "Engagement" },
];

function dayLabel(day: string, options: Intl.DateTimeFormatOptions) {
  return new Date(`${day}T00:00:00`).toLocaleDateString("en-IN", options);
}

function ViewsTrendCard({ trend, days }: { trend: VpTrendPoint[]; days: number }) {
  const [metric, setMetric] = useState<TrendMetric>("total");
  const firstTracked = trend.find((p) => p.videos > 0);
  const data: TrendDatum[] = trend.map((p) => {
    const tracked = p.videos > 0;
    const value = !tracked ? null : metric === "total" ? p.total_views : metric === "gained" ? p.views_gained : p.engagement_rate;
    return { label: dayLabel(p.day, { day: "numeric", month: "short" }), fullLabel: dayLabel(p.day, { day: "numeric", month: "short", year: "numeric" }), value };
  });
  const hint = {
    total: "Total views across all tracked videos",
    gained: "Views gained per day (from the daily checks)",
    engagement: "(likes + comments) / views across tracked videos",
  }[metric];
  const format = metric === "engagement" ? (v: number) => formatPercent(v) : (v: number) => formatCompact(v);
  const change = (value: number, previous: number): string | null => {
    if (metric === "engagement") return `${value - previous >= 0 ? "+" : ""}${(value - previous).toFixed(2)} pts`;
    if (metric === "gained") return `${formatSigned(value - previous)} vs previous day`;
    if (!previous) return null;
    const pct = ((value - previous) / previous) * 100;
    return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
  };
  const startedInRange = firstTracked && firstTracked.day !== trend[0]?.day;

  return (
    <Card className="p-5 xl:col-span-6 2xl:col-span-7">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <SectionTitle icon={<TrendingUp size={18} />} tone="bg-indigo-50 text-indigo-600" title="Views Trend" hint={hint} />
        <div className="inline-flex rounded-lg border border-line bg-slate-50 p-0.5" role="tablist" aria-label="Trend metric">
          {METRICS.map((m) => (
            <button
              key={m.id}
              type="button"
              role="tab"
              aria-selected={metric === m.id}
              onClick={() => setMetric(m.id)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                metric === m.id ? "bg-accent text-white shadow-sm" : "text-slate-600 hover:text-ink"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-4">
        <TrendChart data={data} format={format} change={change} color={metric === "gained" ? "#059669" : metric === "engagement" ? "#db2777" : "#4f46e5"} />
      </div>
      {startedInRange && (
        <p className="mt-2 text-xs text-muted">
          Tracking started on {dayLabel(firstTracked.day, { day: "numeric", month: "short" })}, so earlier days in the last {days} days have no data. The trend fills in with each daily check.
        </p>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ sentiment
const SENTIMENT_ROWS = [
  { key: "positive", label: "Positive", color: SENTIMENT_COLORS.Positive },
  { key: "neutral", label: "Neutral", color: SENTIMENT_COLORS.Neutral },
  { key: "negative", label: "Negative", color: SENTIMENT_COLORS.Negative },
] as const;

function PlatformSelect({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) {
  return (
    <label className="relative inline-flex items-center">
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-8 cursor-pointer appearance-none rounded-lg border border-line bg-white pl-3 pr-8 text-xs font-medium text-slate-700 outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-indigo-100"
      >
        <option value="">All Platforms</option>
        <option value="youtube">YouTube</option>
        <option value="instagram">Instagram</option>
        <option value="facebook">Facebook</option>
      </select>
      <ChevronDown size={14} className="pointer-events-none absolute right-2.5 text-slate-500" />
    </label>
  );
}

function SentimentOverviewCard({ data }: { data: VpDashboard }) {
  const [platform, setPlatform] = useState("");
  const counts: SentimentCounts = platform
    ? data.sentiment_by_platform.find((g) => g.name === platform)?.counts ?? { positive: 0, neutral: 0, negative: 0, not_analyzed: 0 }
    : data.sentiment_overall;
  const analysed = counts.positive + counts.neutral + counts.negative;

  return (
    <Card className="p-5 xl:col-span-6 2xl:col-span-5">
      <div className="flex items-start justify-between gap-3">
        <SectionTitle icon={<Sparkles size={18} />} tone="bg-rose-50 text-rose-500" title="Content Sentiment Overview" hint="AI analysis of titles and captions" />
        <PlatformSelect value={platform} onChange={setPlatform} label="Sentiment platform" />
      </div>
      <div className="mt-5 flex flex-wrap items-center gap-x-8 gap-y-6">
        <div className="flex shrink-0 items-center gap-5">
          <Donut segments={SENTIMENT_ROWS.map((row) => ({ label: row.label, value: counts[row.key], color: row.color }))} center={formatNumber(analysed)} caption="Analysed" size={136} />
          <ul className="space-y-2 whitespace-nowrap text-sm">
            {SENTIMENT_ROWS.map((row) => (
              <li key={row.key} className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: row.color }} />
                <span className="w-[86px] text-slate-600">{row.label}</span>
                <span className="font-semibold tabular-nums text-ink">{counts[row.key]}</span>
                <span className="text-xs tabular-nums text-muted">({analysed ? Math.round((counts[row.key] / analysed) * 100) : 0}%)</span>
              </li>
            ))}
            <li className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-slate-200" />
              <span className="w-[86px] text-slate-600">Not analysed</span>
              <span className="font-semibold tabular-nums text-ink">{counts.not_analyzed}</span>
            </li>
          </ul>
        </div>
        <div className="min-w-[220px] flex-1">
          <p className="text-sm font-semibold text-ink">By Platform</p>
          <div className="mt-3 space-y-4">
            {data.sentiment_by_platform.length === 0 && <p className="text-sm text-muted">No videos yet.</p>}
            {data.sentiment_by_platform.map((group) => (
              <PlatformSentiment key={group.name} platform={group.name as VpPlatform} counts={group.counts} total={group.total} />
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}

function PlatformSentiment({ platform, counts, total }: { platform: VpPlatform; counts: SentimentCounts; total: number }) {
  const pct = (n: number) => (total ? Math.round((n / total) * 100) : 0);
  return (
    <div>
      <p className="flex items-center gap-2 text-sm font-medium text-ink">
        <PlatformIcon platform={platform} size={18} />
        {platformLabel(platform)}
        <span className="text-xs font-normal text-muted">
          ({total} video{total === 1 ? "" : "s"})
        </span>
      </p>
      <div className="mt-2 flex h-2.5 overflow-hidden rounded-full bg-slate-100" role="img" aria-label={`${platformLabel(platform)} sentiment`}>
        {SENTIMENT_ROWS.map((row) =>
          counts[row.key] ? <div key={row.key} style={{ width: `${pct(counts[row.key])}%`, background: row.color }} title={`${row.label}: ${counts[row.key]}`} /> : null,
        )}
      </div>
      <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted">
        {SENTIMENT_ROWS.map((row) => (
          <span key={row.key} className="inline-flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: row.color }} />
            {pct(counts[row.key])}% {row.label}
          </span>
        ))}
        {counts.not_analyzed > 0 && <span>{counts.not_analyzed} not analysed</span>}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ tables
interface RowActions {
  history: (videoId: number) => void;
  copy: (url: string) => void;
}

function SectionTitle({ icon, tone, title, hint }: { icon: ReactNode; tone: string; title: string; hint?: string }) {
  return (
    <div className="flex items-start gap-3">
      <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${tone}`}>{icon}</span>
      <div>
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        {hint && <p className="text-xs text-muted">{hint}</p>}
      </div>
    </div>
  );
}

function ViewAllLink({ href }: { href: string }) {
  return (
    <Link href={href} className="inline-flex h-8 items-center rounded-lg border border-line px-3 text-xs font-medium text-slate-700 hover:bg-slate-50">
      View All
    </Link>
  );
}

function ViewAllToggle({ expanded, onToggle }: { expanded: boolean; onToggle: () => void }) {
  return (
    <button type="button" onClick={onToggle} className="inline-flex h-8 items-center rounded-lg border border-line px-3 text-xs font-medium text-slate-700 hover:bg-slate-50">
      {expanded ? "Show less" : "View All"}
    </button>
  );
}

function Thumbnail({ url, platform, title }: { url: string | null; platform: VpPlatform; title: string }) {
  const [failed, setFailed] = useState(false);
  if (!url || failed) {
    return (
      <span
        className={`flex h-8 w-14 shrink-0 items-center justify-center rounded-md ${platform === "instagram" ? "bg-gradient-to-br from-amber-100 via-pink-100 to-violet-100" : "bg-slate-100"}`}
        aria-hidden="true"
      >
        <PlatformIcon platform={platform} size={16} />
      </span>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- remote YouTube thumbnail, no optimisation needed
    <img src={url} alt="" title={title} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} className="h-8 w-14 shrink-0 rounded-md bg-slate-100 object-cover" />
  );
}

function VideoCell({
  title,
  url,
  platform,
  thumbnail,
  down = false,
}: {
  title: string;
  url: string;
  platform: VpPlatform;
  thumbnail: string | null;
  down?: boolean;
}) {
  const href = safeHref(url);
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <Thumbnail url={thumbnail} platform={platform} title={title} />
      <span className="shrink-0">
        <PlatformIcon platform={platform} size={15} />
      </span>
      <div className="min-w-0">
        {href ? (
          <a href={href} target="_blank" rel="noopener noreferrer" title={title} className="line-clamp-2 text-[13px] font-medium leading-snug text-slate-800 hover:text-accent">
            {title}
          </a>
        ) : (
          <span className="line-clamp-2 text-[13px] font-medium leading-snug text-slate-800">{title}</span>
        )}
        {down && (
          <span
            className="mt-0.5 inline-flex items-center gap-1 rounded bg-red-50 px-1.5 py-px text-[11px] font-medium text-red-700 ring-1 ring-inset ring-red-200"
            title="Deleted, private or removed on the platform. Showing the last known numbers."
          >
            <VideoOff size={11} aria-hidden="true" /> Video Down
          </span>
        )}
      </div>
    </div>
  );
}

function CreatorCell({ name, platform }: { name: string | null; platform: VpPlatform }) {
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-sm text-slate-600">
      <span className="shrink-0">
        <PlatformIcon platform={platform} size={14} />
      </span>
      <span className="truncate">{name ?? "Unknown creator"}</span>
    </span>
  );
}

function RowMenu({ title, url, videoId, actions }: { title: string; url: string; videoId: number; actions: RowActions }) {
  const href = safeHref(url);
  return (
    <Menu
      label={`Actions for ${title}`}
      width={180}
      trigger={(props) => (
        <button type="button" {...props} className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-ink" aria-label={`More actions for ${title}`}>
          <MoreVertical size={16} />
        </button>
      )}
      items={[
        { label: "Open video", icon: <ExternalLink size={15} />, disabled: !href, onSelect: () => href && window.open(href, "_blank", "noopener,noreferrer") },
        { label: "View history", icon: <History size={15} />, onSelect: () => actions.history(videoId) },
        { label: "Copy link", icon: <Copy size={15} />, disabled: !href, onSelect: () => href && actions.copy(href) },
      ]}
    />
  );
}

const th = "px-2 py-2 text-left text-xs font-medium text-slate-500";

function RankingCard({
  title,
  hint,
  icon,
  tone,
  videos,
  trends,
  showViews = false,
  viewAll,
  actions,
}: {
  title: string;
  hint: string;
  icon: ReactNode;
  tone: string;
  videos: VpVideo[];
  trends: VpDashboard["video_trends"];
  showViews?: boolean;
  viewAll: string;
  actions: RowActions;
}) {
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <SectionTitle icon={icon} tone={tone} title={title} hint={hint} />
        <ViewAllLink href={viewAll} />
      </div>
      {videos.length === 0 ? (
        <p className="mt-6 text-sm text-muted">No data yet. Rankings appear after the first daily check.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[600px] table-fixed">
            <thead>
              <tr className="border-b border-line">
                <th className={`${th} w-8`}>#</th>
                <th className={th}>Video</th>
                <th className={`${th} w-[22%]`}>Creator</th>
                {showViews && <th className={`${th} w-16 text-right`}>Views</th>}
                <th className={`${th} w-[92px] text-right`}>Engagement</th>
                <th className={`${th} w-[72px] text-center`}>Trend</th>
                <th className="w-8" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {videos.map((video, index) => (
                <tr key={video.id} className="border-b border-line last:border-0">
                  <td className="px-2 py-2.5 text-sm tabular-nums text-slate-500">{index + 1}</td>
                  <td className="px-2 py-2.5">
                    <VideoCell title={video.display_title} url={video.video_url} platform={video.platform} thumbnail={video.thumbnail_url} down={video.status === "Video Down"} />
                  </td>
                  <td className="px-2 py-2.5">
                    <CreatorCell name={video.creator_name} platform={video.platform} />
                  </td>
                  {showViews && <td className="px-2 py-2.5 text-right text-sm font-semibold tabular-nums text-ink">{formatCompact(video.current_views)}</td>}
                  <td className={`px-2 py-2.5 text-right text-sm tabular-nums ${showViews ? "text-slate-700" : "font-semibold text-ink"}`}>{formatPercent(video.engagement_rate)}</td>
                  <td className="px-2 py-2.5 text-center">
                    <MiniLine values={trends[String(video.id)] ?? []} label={`Daily views gained for ${video.display_title}`} />
                  </td>
                  <td className="py-2.5 text-right">
                    <RowMenu title={video.display_title} url={video.video_url} videoId={video.id} actions={actions} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function RecentSentimentCard({ items, actions }: { items: VpDashboard["recent_sentiment"]; actions: RowActions }) {
  const [platform, setPlatform] = useState("");
  const [expanded, setExpanded] = useState(false);
  const filtered = useMemo(() => items.filter((item) => !platform || item.platform === platform), [items, platform]);
  const shown = expanded ? filtered : filtered.slice(0, PREVIEW_ROWS);
  return (
    <Card className="p-5 xl:col-span-7">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <SectionTitle icon={<Zap size={18} />} tone="bg-orange-50 text-orange-500" title="Recent Sentiment Analysis" hint="Latest analysed videos with AI sentiment" />
        <div className="flex items-center gap-2">
          <PlatformSelect value={platform} onChange={setPlatform} label="Recent sentiment platform" />
          {filtered.length > PREVIEW_ROWS && <ViewAllToggle expanded={expanded} onToggle={() => setExpanded((v) => !v)} />}
        </div>
      </div>
      {shown.length === 0 ? (
        <p className="mt-6 text-sm text-muted">No videos analysed yet.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[620px] table-fixed">
            <thead>
              <tr className="border-b border-line">
                <th className={th}>Video</th>
                <th className={`${th} w-[20%]`}>Creator</th>
                <th className={`${th} w-[72px] text-center`}>Platform</th>
                <th className={`${th} w-28`}>Sentiment</th>
                <th className={`${th} w-[150px]`}>Analysed At</th>
                <th className="w-8" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {shown.map((item) => (
                <tr key={item.video_id} className="border-b border-line last:border-0">
                  <td className="px-2 py-2.5">
                    <VideoCell title={item.display_title} url={item.video_url} platform={item.platform} thumbnail={item.thumbnail_url} />
                  </td>
                  <td className="truncate px-2 py-2.5 text-sm text-slate-600">{item.creator_name ?? "Unknown creator"}</td>
                  <td className="px-2 py-2.5">
                    <span className="flex justify-center" title={platformLabel(item.platform)}>
                      <PlatformIcon platform={item.platform} size={16} />
                    </span>
                  </td>
                  <td className="px-2 py-2.5">
                    <VpSentimentBadge sentiment={item.sentiment} confidence={item.confidence} />
                  </td>
                  <td className="whitespace-nowrap px-2 py-2.5 text-sm text-slate-600">{formatChecked(item.analyzed_at)}</td>
                  <td className="py-2.5 text-right">
                    <RowMenu title={item.display_title} url={item.video_url} videoId={item.video_id} actions={actions} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function LatestDetectedCard({ videos, actions }: { videos: VpVideo[]; actions: RowActions }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? videos : videos.slice(0, PREVIEW_ROWS);
  return (
    <Card className="flex flex-col p-5 xl:col-span-5">
      <div className="flex items-start justify-between gap-3">
        <SectionTitle icon={<Activity size={18} />} tone="bg-violet-50 text-violet-600" title="Latest Newly Detected Videos" hint="Automatically found from tracked creators" />
        {videos.length > PREVIEW_ROWS && <ViewAllToggle expanded={expanded} onToggle={() => setExpanded((v) => !v)} />}
      </div>
      {videos.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center px-6 py-10 text-center">
          <span className="flex h-14 w-14 items-center justify-center rounded-full bg-violet-50 text-violet-500">
            <Radar size={26} />
          </span>
          <p className="mt-3 text-sm font-semibold text-ink">No new videos detected</p>
          <p className="mt-1 max-w-xs text-xs text-muted">New videos will appear here when the daily discovery finds new uploads from your tracked creators.</p>
        </div>
      ) : (
        <ul className="mt-3 divide-y divide-line">
          {shown.map((video) => (
            <li key={video.id} className="flex items-center gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <VideoCell title={video.display_title} url={video.video_url} platform={video.platform} thumbnail={video.thumbnail_url} down={video.status === "Video Down"} />
                <p className="mt-0.5 truncate pl-[87px] text-xs text-muted">
                  {video.creator_name ?? "Unknown creator"} · found {formatChecked(video.discovered_at)}
                </p>
              </div>
              <span className="whitespace-nowrap text-sm font-semibold tabular-nums text-ink">{formatCompact(video.current_views)}</span>
              <RowMenu title={video.display_title} url={video.video_url} videoId={video.id} actions={actions} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
