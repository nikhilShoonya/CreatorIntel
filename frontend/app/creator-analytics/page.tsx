"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, ArrowUpRight, CalendarDays, CheckCircle2, ChevronDown, Loader2, Sparkles, Users } from "lucide-react";

import { ExportMenu } from "@/components/creators/ExportMenu";
import { SentimentBadge, StatusBadge } from "@/components/ui/Badges";
import { Card, EmptyState, ErrorBanner } from "@/components/ui/controls";
import { CreatorAvatar } from "@/components/ui/CreatorAvatar";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { Donut, MultiTrendChart } from "@/components/video-performance/charts";
import { SENTIMENT_COLORS } from "@/components/video-performance/badges";
import { useApi } from "@/hooks/useApi";
import { useStoredValue } from "@/hooks/useStoredValue";
import { api } from "@/lib/api";
import { formatCompact, formatDateTime, formatNumber, NA, platformLabel } from "@/lib/format";
import type { CountItem, CreatorDashboard, DashboardCreator } from "@/types";

const RANGES = [
  { days: 7, label: "Last 7 days" },
  { days: 14, label: "Last 14 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
  { days: 0, label: "All time" },
];
const RANGE_KEY = "creatorintel:ca-range";

const STATUS_COLORS: Record<string, string> = {
  Completed: "#22c55e",
  Partial: "#f59e0b",
  Pending: "#fbbf24",
  Processing: "#6366f1",
  Failed: "#f87171",
};
const PLATFORM_COLORS: Record<string, string> = { youtube: "#ef1d36", instagram: "#ec4899", invalid: "#94a3b8", unsupported: "#cbd5e1" };
const SENTIMENT_DONUT: Record<string, string> = { Positive: "#22c55e", Neutral: SENTIMENT_COLORS.Neutral, Negative: "#f43f5e", Unknown: "#cbd5e1" };
const LANGUAGE_COLORS = ["#6366f1", "#38bdf8", "#22c55e", "#f97316"];
const NOT_ANALYSED_COLOR = "#e2e8f0";
const OTHERS = "Others";

/** Creator List address with the dashboard item's filter applied (same stored values, so counts match). */
function listHref(filter: string, value: string | null | undefined): string | undefined {
  return value ? `/creators?${filter}=${encodeURIComponent(value)}` : undefined;
}

const pct = (n: number, total: number) => (total ? Math.round((n / total) * 100) : 0);

export default function CreatorAnalyticsDashboard() {
  const [storedRange, setRange] = useStoredValue(RANGE_KEY, "30");
  const days = RANGES.some((r) => r.days === Number(storedRange)) ? Number(storedRange) : 30;
  const [exportError, setExportError] = useState<string | null>(null);
  const { data, error, reload } = useApi(`creator-dashboard:${days}`, () => api.creatorDashboard(days), {
    keepPrevious: true,
    // follow creators being analysed right now; otherwise only reload on demand ("Refresh data")
    refreshMs: (d: CreatorDashboard | undefined) => (d && d.in_progress > 0 ? 5000 : false),
  });

  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Creator Analytics Dashboard</h1>
          <p className="text-sm text-muted">
            Overview of all creators analysed. Track growth, platform distribution, content focus and sentiment.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <RangeSelect value={days} onChange={(value) => setRange(String(value))} />
          <ExportMenu filters={{}} variant="primary" disabled={!data || data.total === 0} onError={setExportError} />
        </div>
      </div>

      {exportError && <ErrorBanner message={exportError} onRetry={() => setExportError(null)} />}
      {error && <ErrorBanner message={error} onRetry={reload} />}
      {!data && !error && <DashboardSkeleton />}

      {data && data.total === 0 && (
        <Card>
          <EmptyState
            icon={<Users size={22} />}
            title="No creators yet"
            description="Upload an Excel or CSV list of creators. Their YouTube / Instagram data and AI analysis then appear here."
            action={
              <Link href="/" className="inline-flex h-9 items-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-white hover:bg-accent-strong">
                Upload creators <ArrowRight size={15} />
              </Link>
            }
          />
        </Card>
      )}

      {data && data.total > 0 && <DashboardBody data={data} days={days} onRange={(value) => setRange(String(value))} />}
    </div>
  );
}

function RangeSelect({ value, onChange, compact = false }: { value: number; onChange: (value: number) => void; compact?: boolean }) {
  return (
    <label className="relative inline-flex items-center">
      {!compact && <CalendarDays size={16} className="pointer-events-none absolute left-3 text-slate-500" />}
      <select
        aria-label="Period"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className={`cursor-pointer appearance-none rounded-lg border border-line bg-white pr-9 font-medium text-slate-700 outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-indigo-100 ${
          compact ? "h-9 pl-3 text-xs" : "h-10 min-w-[170px] pl-9 text-sm"
        }`}
      >
        {RANGES.map((range) => (
          <option key={range.days} value={range.days}>
            {range.label}
          </option>
        ))}
      </select>
      <ChevronDown size={15} className="pointer-events-none absolute right-3 text-slate-500" />
    </label>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="Loading dashboard">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="h-[118px] animate-pulse rounded-xl border border-line bg-white" />
        ))}
      </div>
      <div className="grid gap-5 xl:grid-cols-12">
        <div className="h-80 animate-pulse rounded-xl border border-line bg-white xl:col-span-7" />
        <div className="h-80 animate-pulse rounded-xl border border-line bg-white xl:col-span-5" />
      </div>
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="h-64 animate-pulse rounded-xl border border-line bg-white" />
        ))}
      </div>
    </div>
  );
}

function DashboardBody({ data, days, onRange }: { data: CreatorDashboard; days: number; onRange: (days: number) => void }) {
  return (
    <>
      <Notices data={data} />
      <KpiRow data={data} days={days} />
      <div className="grid gap-5 xl:grid-cols-12">
        <GrowthCard data={data} days={days} onRange={onRange} />
        <PlatformCard data={data} days={days} />
      </div>
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        <GenreCard data={data} />
        <LanguageCard items={data.by_language} />
        <DistributionDonut
          title="Sentiment Distribution"
          hint="Overall creator sentiment"
          items={data.by_sentiment}
          colors={SENTIMENT_DONUT}
          filter="sentiment"
        />
        <DistributionDonut title="Analysis Status" hint="Current analysis status" items={data.by_status} colors={STATUS_COLORS} filter="status" />
      </div>
      <RecentTable creators={data.recently_analysed} />
      {data.attention.length > 0 && <AttentionCard data={data} />}
    </>
  );
}

function Notices({ data }: { data: CreatorDashboard }) {
  if (!data.in_progress && !data.ai_pending) return null;
  return (
    <div className="space-y-2">
      {data.in_progress > 0 && (
        <div role="status" className="flex items-center gap-2.5 rounded-xl border border-indigo-200 bg-accent-soft px-4 py-3 text-sm text-accent">
          <Loader2 size={16} className="shrink-0 animate-spin" />
          {data.in_progress} creator{data.in_progress === 1 ? " is" : "s are"} being analysed right now - this page updates automatically.
        </div>
      )}
      {data.ai_pending > 0 && (
        <div role="status" className="flex items-center gap-2.5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <Sparkles size={16} className="shrink-0" />
          AI analysis is waiting for Groq quota for {data.ai_pending} creator{data.ai_pending === 1 ? "" : "s"}. It runs automatically - nothing to do.
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ building blocks
function SectionTitle({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        {hint && <p className="text-xs text-muted">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

function ItemLabel({ item, filter, children }: { item: CountItem; filter: string; children: ReactNode }) {
  const href = item.label === OTHERS ? undefined : listHref(filter, item.value);
  return href ? (
    <Link href={href} className="hover:text-accent hover:underline" title="Show these creators in the Creator List">
      {children}
    </Link>
  ) : (
    <span title={item.value === null && item.label !== OTHERS ? "No AI result yet (not analysed, failed, or waiting for AI)" : undefined}>
      {children}
    </span>
  );
}

function Legend({ items, color, total, filter }: { items: CountItem[]; color: (item: CountItem) => string; total: number; filter: string }) {
  return (
    <ul className="min-w-[148px] flex-1 space-y-2.5 text-[13px]">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color(item) }} />
          <span className="min-w-0 flex-1 truncate text-slate-700" title={item.label}>
            <ItemLabel item={item} filter={filter}>
              {item.label}
            </ItemLabel>
          </span>
          <span className="w-6 text-right font-semibold tabular-nums text-ink">{formatNumber(item.count)}</span>
          <span className="w-9 text-right text-xs tabular-nums text-muted">{pct(item.count, total)}%</span>
        </li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------ KPIs
function KpiRow({ data, days }: { data: CreatorDashboard; days: number }) {
  const count = (items: CountItem[], value: string) => items.find((i) => i.value === value)?.count ?? 0;
  const completed = count(data.by_status, "Completed");
  const notDone = data.total - completed;
  const added = (n: number) => (days ? `+${formatNumber(n)} in last ${days} days` : "All time");
  const doneParts = [
    count(data.by_status, "Failed") && `${count(data.by_status, "Failed")} failed`,
    count(data.by_status, "Partial") && `${count(data.by_status, "Partial")} partial`,
    data.in_progress && `${data.in_progress} pending`,
  ].filter(Boolean);

  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
      <Kpi
        label="Total Creators"
        value={data.total}
        icon={<Users size={20} />}
        iconTone="bg-indigo-100/70 text-indigo-600"
        card="bg-white"
        footer={<Delta value={data.added_in_range} text={added(data.added_in_range)} />}
        href="/creators"
      />
      <Kpi
        label="YouTube Creators"
        value={count(data.by_platform, "youtube")}
        icon={<PlatformIcon platform="youtube" size={22} />}
        iconTone="bg-white"
        card="bg-gradient-to-br from-red-50/80 to-white"
        footer={<Delta value={count(data.added_by_platform, "youtube")} text={added(count(data.added_by_platform, "youtube"))} />}
        href={listHref("platform", "youtube")}
      />
      <Kpi
        label="Instagram Creators"
        value={count(data.by_platform, "instagram")}
        icon={<PlatformIcon platform="instagram" size={22} />}
        iconTone="bg-white"
        card="bg-gradient-to-br from-pink-50/80 to-white"
        footer={<Delta value={count(data.added_by_platform, "instagram")} text={added(count(data.added_by_platform, "instagram"))} />}
        href={listHref("platform", "instagram")}
      />
      <Kpi
        label="Analyzed Successfully"
        value={completed}
        icon={<CheckCircle2 size={20} />}
        iconTone="bg-emerald-500 text-white"
        card="bg-gradient-to-br from-emerald-50/80 to-white"
        footer={<Progress value={pct(completed, data.total)} color="bg-emerald-500" tone="text-emerald-700" />}
        href={listHref("status", "Completed")}
      />
      <Kpi
        label="Pending / Failed"
        value={notDone}
        icon={<AlertTriangle size={20} />}
        iconTone="bg-amber-400 text-white"
        card="bg-gradient-to-br from-amber-50/80 to-white"
        footer={
          <div>
            <Progress value={pct(notDone, data.total)} color="bg-amber-400" tone="text-amber-700" />
            {doneParts.length > 0 && <p className="mt-1 truncate text-[11px] text-muted">{doneParts.join(" · ")}</p>}
          </div>
        }
        href={notDone ? "#needs-attention" : undefined}
      />
    </div>
  );
}

function Kpi({
  label,
  value,
  icon,
  iconTone,
  card,
  footer,
  href,
}: {
  label: string;
  value: number;
  icon: ReactNode;
  iconTone: string;
  card: string;
  footer: ReactNode;
  href?: string;
}) {
  const body = (
    <div className="flex items-start gap-3.5">
      <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl shadow-xs ${iconTone}`}>{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] text-slate-600" title={label}>
          {label}
        </p>
        <p className="mt-0.5 text-3xl font-semibold tabular-nums tracking-tight text-ink">{formatNumber(value)}</p>
        <div className="mt-2">{footer}</div>
      </div>
    </div>
  );
  const className = `block rounded-xl border border-line p-4 shadow-[0_1px_2px_rgba(16,24,40,0.04)] ${card}`;
  return href ? (
    <Link href={href} className={`${className} transition-shadow hover:shadow-md`} title={`${label}: open in the Creator List`}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

function Delta({ value, text }: { value: number; text: string }) {
  return (
    <p className={`flex items-center gap-1 truncate text-xs ${value > 0 ? "text-emerald-600" : "text-muted"}`} title={text}>
      {value > 0 && <ArrowUpRight size={13} className="shrink-0" />}
      {text}
    </p>
  );
}

function Progress({ value, color, tone }: { value: number; color: string; tone: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className={`w-9 text-xs font-medium tabular-nums ${tone}`}>{value}%</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200/70">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${value}%` }} />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ growth + platform
function GrowthCard({ data, days, onRange }: { data: CreatorDashboard; days: number; onRange: (days: number) => void }) {
  const format = (day: string, options: Intl.DateTimeFormatOptions) => new Date(`${day}T00:00:00`).toLocaleDateString("en-IN", options);
  const labels = data.growth.map((p) => format(p.day, { day: "2-digit", month: "short" }));
  const fullLabels = data.growth.map(
    (p) => `${format(p.day, { weekday: "short", day: "numeric", month: "short", year: "numeric" })}${p.added ? ` · +${p.added} added` : ""}`,
  );
  return (
    <Card className="p-5 xl:col-span-7">
      <SectionTitle
        title="Creator Growth Trend"
        hint="Creators added over time, and how many of them are now analysed successfully"
        action={<RangeSelect value={days} onChange={onRange} compact />}
      />
      <div className="mt-3">
        {data.growth.length === 0 ? (
          <p className="py-16 text-center text-sm text-muted">No creators were added in this period.</p>
        ) : (
          <MultiTrendChart
            labels={labels}
            fullLabels={fullLabels}
            series={[
              { label: "Total Creators", color: "#7c3aed", values: data.growth.map((p) => p.total) },
              { label: "Successfully Analyzed", color: "#38bdf8", values: data.growth.map((p) => p.analysed) },
            ]}
          />
        )}
      </div>
    </Card>
  );
}

function PlatformCard({ data, days }: { data: CreatorDashboard; days: number }) {
  const [scope, setScope] = useState<"all" | "range">("all");
  const inRange = scope === "range" && days > 0;
  const items = inRange ? data.added_by_platform : data.by_platform;
  const total = items.reduce((sum, i) => sum + i.count, 0);
  // YouTube and Instagram always listed; invalid / unsupported links grouped as "Others"
  const known = ["youtube", "instagram"].map((value) => items.find((i) => i.value === value) ?? { value, label: platformLabel(value), count: 0 });
  const others = items.filter((i) => i.value !== "youtube" && i.value !== "instagram").reduce((sum, i) => sum + i.count, 0);
  const rows: CountItem[] = [...known, { value: null, label: OTHERS, count: others }];
  const color = (item: CountItem) => (item.value ? PLATFORM_COLORS[item.value] : "#94a3b8");
  return (
    <Card className="p-5 xl:col-span-5">
      <SectionTitle
        title="Creators by Platform"
        hint="Distribution across social platforms"
        action={
          <label className="relative inline-flex items-center">
            <select
              aria-label="Platform period"
              value={inRange ? "range" : "all"}
              onChange={(event) => setScope(event.target.value as "all" | "range")}
              disabled={days === 0}
              className="h-9 cursor-pointer appearance-none rounded-lg border border-line bg-white pl-3 pr-8 text-xs font-medium text-slate-700 outline-none disabled:cursor-default focus-visible:border-accent"
            >
              <option value="all">All Time</option>
              {days > 0 && <option value="range">Added in last {days} days</option>}
            </select>
            <ChevronDown size={14} className="pointer-events-none absolute right-2.5 text-slate-500" />
          </label>
        }
      />
      <div className="mt-5 flex flex-wrap items-center gap-x-10 gap-y-5">
        <Donut segments={rows.map((i) => ({ label: i.label, value: i.count, color: color(i) }))} center={formatNumber(total)} caption="Creators" size={164} />
        <ul className="min-w-[200px] flex-1 divide-y divide-line text-sm">
          {rows.map((item) => (
            <li key={item.label} className="flex items-center gap-3 py-2.5">
              <span className="h-3 w-3 shrink-0 rounded" style={{ background: color(item) }} />
              <span className="flex-1 text-slate-700" title={item.label === OTHERS ? "Invalid or unsupported channel links" : undefined}>
                <ItemLabel item={item} filter="platform">
                  {item.label}
                </ItemLabel>
              </span>
              <span className="w-10 text-right font-semibold tabular-nums text-ink">{formatNumber(item.count)}</span>
              <span className="w-12 text-right text-xs tabular-nums text-muted">{pct(item.count, total)}%</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="mt-3 text-[11px] text-muted">Creator Analytics supports YouTube and Instagram channels.</p>
    </Card>
  );
}

// ------------------------------------------------------------------ distributions
const GENRE_LIMITS = [
  { value: 5, label: "Top 5" },
  { value: 10, label: "Top 10" },
  { value: 0, label: "All" },
];

function GenreCard({ data }: { data: CreatorDashboard }) {
  const [limit, setLimit] = useState(5);
  const genres = data.by_genre.filter((i) => i.value);
  const shown = limit ? genres.slice(0, limit) : genres;
  const max = Math.max(1, ...shown.map((i) => i.count));
  const notAnalysed = data.by_genre.find((i) => i.value === null)?.count ?? 0;
  return (
    <Card className="p-5">
      <SectionTitle
        title="Creators by Genre"
        hint={data.genre_needs_review ? `Top content genres · ${data.genre_needs_review} flagged for review` : "Top content genres"}
        action={
          <label className="relative inline-flex items-center">
            <select
              aria-label="Genres shown"
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value))}
              className="h-9 cursor-pointer appearance-none rounded-lg border border-line bg-white pl-3 pr-8 text-xs font-medium text-slate-700 outline-none focus-visible:border-accent"
            >
              {GENRE_LIMITS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <ChevronDown size={14} className="pointer-events-none absolute right-2.5 text-slate-500" />
          </label>
        }
      />
      {shown.length === 0 ? (
        <p className="mt-6 text-sm text-muted">No genres classified yet.</p>
      ) : (
        <ul className="mt-5 space-y-3">
          {shown.map((item, index) => (
            <li key={item.label} className="grid grid-cols-[minmax(0,9.5rem)_1fr] items-center gap-3 text-[13px]">
              <span className="truncate text-slate-700" title={item.label}>
                <ItemLabel item={item} filter="genre">
                  {item.label}
                </ItemLabel>
              </span>
              <span className="flex items-center gap-2">
                <span
                  className="h-4 rounded"
                  style={{ width: `${Math.max(6, (item.count / max) * 85)}%`, background: "#6366f1", opacity: 1 - Math.min(index, 4) * 0.15 }}
                />
                <span className="text-xs tabular-nums text-slate-600">{item.count}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {notAnalysed > 0 && <p className="mt-4 text-[11px] text-muted">{notAnalysed} not analysed yet</p>}
    </Card>
  );
}

function LanguageCard({ items }: { items: CountItem[] }) {
  const languages = items.filter((i) => i.value);
  const top = languages.slice(0, 3);
  const rest = languages.slice(3).reduce((sum, i) => sum + i.count, 0);
  const notAnalysed = items.find((i) => i.value === null);
  const rows: CountItem[] = [...top, ...(languages.length > 3 ? [{ value: null, label: OTHERS, count: rest }] : [])];
  const color = (item: CountItem) => (item.label === OTHERS ? LANGUAGE_COLORS[3] : LANGUAGE_COLORS[top.indexOf(item)] ?? "#94a3b8");
  const total = items.reduce((sum, i) => sum + i.count, 0);
  return (
    <Card className="p-5">
      <SectionTitle title="Creators by Language" hint="Content language distribution" />
      <DonutBody
        rows={[...rows, ...(notAnalysed ? [notAnalysed] : [])]}
        color={(item) => (item.value === null && item.label !== OTHERS ? NOT_ANALYSED_COLOR : color(item))}
        total={total}
        filter="language"
      />
    </Card>
  );
}

function DistributionDonut({
  title,
  hint,
  items,
  colors,
  filter,
}: {
  title: string;
  hint: string;
  items: CountItem[];
  colors: Record<string, string>;
  filter: string;
}) {
  const total = items.reduce((sum, i) => sum + i.count, 0);
  return (
    <Card className="p-5">
      <SectionTitle title={title} hint={hint} />
      <DonutBody rows={items} color={(item) => (item.value ? colors[item.value] ?? "#94a3b8" : NOT_ANALYSED_COLOR)} total={total} filter={filter} />
    </Card>
  );
}

function DonutBody({ rows, color, total, filter }: { rows: CountItem[]; color: (item: CountItem) => string; total: number; filter: string }) {
  if (total === 0) return <p className="mt-6 text-sm text-muted">No data yet.</p>;
  return (
    <div className="mt-5 flex flex-wrap items-center gap-4">
      <Donut segments={rows.map((i) => ({ label: i.label, value: i.count, color: color(i) }))} center={formatNumber(total)} caption="Creators" size={108} />
      <Legend items={rows} color={color} total={total} filter={filter} />
    </div>
  );
}

// ------------------------------------------------------------------ tables
const th = "whitespace-nowrap px-3 py-2.5 text-left text-xs font-semibold text-slate-600";
const td = "whitespace-nowrap px-3 py-2.5";

function Pill({ children, tone }: { children: ReactNode; tone: string }) {
  return <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-medium ${tone}`}>{children}</span>;
}

function RecentTable({ creators }: { creators: DashboardCreator[] }) {
  return (
    <Card className="p-5">
      <SectionTitle
        title="Recently Analyzed Creators"
        hint="Latest creators analysed by the AI"
        action={
          <Link href="/creators" className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent-soft px-3 text-xs font-medium text-accent hover:bg-indigo-100">
            View All Creators <ArrowRight size={14} />
          </Link>
        }
      />
      {creators.length === 0 ? (
        <p className="mt-6 text-sm text-muted">No creator has been analysed yet.</p>
      ) : (
        <div className="table-scroll -mx-5 mt-4 overflow-x-auto">
          <table className="w-full min-w-[960px] text-sm">
            <thead className="border-y border-line bg-slate-50/70">
              <tr>
                <th className={`${th} pl-5`}>#</th>
                <th className={th}>Channel Name</th>
                <th className={th}>Platform</th>
                <th className={th}>Genre</th>
                <th className={th}>Language</th>
                <th className={`${th} text-right`}>Subscribers / Followers</th>
                <th className={th}>Sentiment</th>
                <th className={th}>Status</th>
                <th className={`${th} pr-5`}>Last Analyzed</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {creators.map((creator, index) => (
                <tr key={creator.id} className="hover:bg-slate-50/60">
                  <td className={`${td} pl-5 text-muted`}>{index + 1}</td>
                  <td className={td}>
                    <Link
                      href={`/creators?q=${encodeURIComponent(creator.channel_name)}`}
                      className="flex max-w-[240px] items-center gap-2.5 font-medium text-ink hover:text-accent"
                      title="Open in the Creator List"
                    >
                      <CreatorAvatar name={creator.channel_name} platform={creator.platform} src={creator.profile_picture_url} size={30} />
                      <span className="truncate">{creator.channel_name}</span>
                    </Link>
                  </td>
                  <td className={td}>
                    <span className="inline-flex items-center gap-1.5 text-slate-700">
                      <PlatformIcon platform={creator.platform} size={16} />
                      {platformLabel(creator.platform)}
                    </span>
                  </td>
                  <td className={td}>{creator.genre ? <Pill tone="bg-indigo-50 text-indigo-700">{creator.genre}</Pill> : <span className="text-slate-400">{NA}</span>}</td>
                  <td className={td}>{creator.language ? <Pill tone="bg-sky-50 text-sky-700">{creator.language}</Pill> : <span className="text-slate-400">{NA}</span>}</td>
                  <td className={`${td} text-right tabular-nums text-ink`}>{formatCompact(creator.audience_count)}</td>
                  <td className={td}>
                    <SentimentBadge sentiment={creator.sentiment} />
                  </td>
                  <td className={td}>
                    <StatusBadge status={creator.status} title={creator.error_message} />
                  </td>
                  <td className={`${td} pr-5 text-slate-600`}>{creator.analyzed_at ? formatDateTime(creator.analyzed_at) : NA}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function AttentionCard({ data }: { data: CreatorDashboard }) {
  const more = data.needs_attention - data.attention.length;
  return (
    <Card className="p-5">
      <div id="needs-attention" className="scroll-mt-20" />
      <SectionTitle
        title="Needs Attention"
        hint="Failed or partial - open a creator in the Creator List to retry or fix its link"
        action={
          <span className="flex gap-2">
            <Link href={listHref("status", "Failed") ?? "/creators"} className="inline-flex h-9 items-center rounded-lg border border-line px-3 text-xs font-medium text-slate-700 hover:bg-slate-50">
              View failed
            </Link>
            <Link href={listHref("status", "Partial") ?? "/creators"} className="inline-flex h-9 items-center rounded-lg border border-line px-3 text-xs font-medium text-slate-700 hover:bg-slate-50">
              View partial
            </Link>
          </span>
        }
      />
      <ul className="mt-4 divide-y divide-line">
        {data.attention.map((creator) => (
          <li key={creator.id} className="flex items-center gap-3 py-2.5">
            <CreatorAvatar name={creator.channel_name} platform={creator.platform} src={creator.profile_picture_url} size={32} badge />
            <div className="min-w-0 flex-1">
              <Link href={`/creators?q=${encodeURIComponent(creator.channel_name)}`} className="block truncate text-sm font-medium text-ink hover:text-accent">
                {creator.channel_name}
              </Link>
              <p className="truncate text-xs text-muted" title={creator.error_message ?? undefined}>
                {creator.error_message || "Some data is missing"}
              </p>
            </div>
            <StatusBadge status={creator.status} title={creator.error_message} />
          </li>
        ))}
      </ul>
      {more > 0 && <p className="mt-2 text-xs text-muted">+{more} more - use the buttons above.</p>}
    </Card>
  );
}
