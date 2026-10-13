"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Clock3,
  Heart,
  Languages,
  Loader2,
  PieChart,
  Sparkles,
  Tags,
  Users,
} from "lucide-react";

import { SentimentBadge, StatusBadge } from "@/components/ui/Badges";
import { Card, EmptyState, ErrorBanner } from "@/components/ui/controls";
import { CreatorAvatar } from "@/components/ui/CreatorAvatar";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { Donut } from "@/components/video-performance/charts";
import { SENTIMENT_COLORS } from "@/components/video-performance/badges";
import { formatChecked } from "@/components/video-performance/format";
import { useApi } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { formatCompact, formatDateTime, formatNumber, formatPercent, NA } from "@/lib/format";
import type { CountItem, CreatorDashboard, DashboardCreator } from "@/types";

const STATUS_COLORS: Record<string, string> = {
  Completed: "#059669",
  Partial: "#d97706",
  Failed: "#e11d48",
  Processing: "#4f46e5",
  Pending: "#94a3b8",
};
const PLATFORM_COLORS: Record<string, string> = { youtube: "#ef4444", instagram: "#d946ef", invalid: "#94a3b8", unsupported: "#cbd5e1" };
const SENTIMENT_DONUT: Record<string, string> = { ...SENTIMENT_COLORS, Unknown: "#cbd5e1" };
const NOT_ANALYSED_COLOR = "#e2e8f0";

/** Creator List address with the dashboard item's filter applied (same stored values, so counts match). */
function listHref(filter: string, value: string | null | undefined): string | undefined {
  return value ? `/creators?${filter}=${encodeURIComponent(value)}` : undefined;
}

export default function CreatorAnalyticsDashboard() {
  const { data, error, reload } = useApi("creator-dashboard", () => api.creatorDashboard(), {
    keepPrevious: true,
    // follow creators that are being analysed right now; otherwise only reload on demand ("Refresh data")
    refreshMs: (d: CreatorDashboard | undefined) => (d && d.in_progress > 0 ? 5000 : false),
  });

  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-accent">Creator Analytics</p>
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Dashboard</h1>
          <p className="text-sm text-muted">Overview of every creator analysed so far, from the stored YouTube, Instagram and AI results.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {data?.last_updated_at && <span className="text-xs text-muted">Last change {formatDateTime(data.last_updated_at)}</span>}
          <Link
            href="/creators"
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-line bg-white px-4 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Open Creator List <ArrowRight size={15} />
          </Link>
        </div>
      </div>

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

      {data && data.total > 0 && <DashboardBody data={data} />}
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="Loading dashboard">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="h-[112px] animate-pulse rounded-xl border border-line bg-white" />
        ))}
      </div>
      <div className="grid gap-5 xl:grid-cols-3">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="h-72 animate-pulse rounded-xl border border-line bg-white" />
        ))}
      </div>
    </div>
  );
}

function DashboardBody({ data }: { data: CreatorDashboard }) {
  const count = (items: CountItem[], value: string) => items.find((i) => i.value === value)?.count ?? 0;
  const share = (n: number) => `${formatPercent((n / data.total) * 100)} of creators`;
  const youtube = count(data.by_platform, "youtube");
  const instagram = count(data.by_platform, "instagram");
  const other = data.total - youtube - instagram;

  return (
    <>
      <Notices data={data} />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Total Creators" value={formatNumber(data.total)} icon={<Users size={18} />} tone="bg-indigo-50 text-indigo-600" sub="All stored creators" href="/creators" />
        <Kpi
          label="YouTube Creators"
          value={formatNumber(youtube)}
          icon={<PlatformIcon platform="youtube" size={18} />}
          tone="bg-red-50"
          sub={share(youtube)}
          href={listHref("platform", "youtube")}
        />
        <Kpi
          label="Instagram Creators"
          value={formatNumber(instagram)}
          icon={<PlatformIcon platform="instagram" size={18} />}
          tone="bg-fuchsia-50"
          sub={other > 0 ? `${share(instagram)} · ${other} invalid/unsupported link${other === 1 ? "" : "s"}` : share(instagram)}
          href={listHref("platform", "instagram")}
        />
        <Kpi
          label="Analysed"
          value={formatNumber(data.analysed)}
          icon={<CheckCircle2 size={18} />}
          tone="bg-emerald-50 text-emerald-600"
          sub={`${count(data.by_status, "Completed")} completed · ${count(data.by_status, "Partial")} partial`}
        />
        <Kpi
          label="Needs Attention"
          value={formatNumber(data.needs_attention)}
          icon={<AlertTriangle size={18} />}
          tone={data.needs_attention ? "bg-rose-50 text-rose-600" : "bg-slate-50 text-slate-500"}
          sub={`${count(data.by_status, "Failed")} failed · ${count(data.by_status, "Partial")} partial`}
          href={data.needs_attention ? "#needs-attention" : undefined}
        />
        <Kpi
          label="Avg. Engagement Rate"
          value={data.average_engagement_rate === null ? NA : formatPercent(data.average_engagement_rate, 2)}
          icon={<Heart size={18} />}
          tone="bg-rose-50 text-rose-500"
          sub={data.engagement_sample ? `Views-based, ${data.engagement_sample} creator${data.engagement_sample === 1 ? "" : "s"}` : "No engagement data yet"}
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <DonutCard
          title="Analysis Status"
          hint="Current state of every creator"
          icon={<PieChart size={18} />}
          tone="bg-indigo-50 text-indigo-600"
          items={data.by_status}
          colors={STATUS_COLORS}
          center={formatNumber(data.total)}
          caption="Creators"
          filter="status"
        />
        <DonutCard
          title="Platform Distribution"
          hint="Where each creator's channel link points"
          icon={<BarChart3 size={18} />}
          tone="bg-fuchsia-50 text-fuchsia-600"
          items={data.by_platform}
          colors={PLATFORM_COLORS}
          center={formatNumber(data.total)}
          caption="Creators"
          filter="platform"
        />
        <DonutCard
          title="Content Sentiment"
          hint="AI tone of each creator's recent content"
          icon={<Sparkles size={18} />}
          tone="bg-rose-50 text-rose-500"
          items={data.by_sentiment}
          colors={SENTIMENT_DONUT}
          center={formatNumber(data.by_sentiment.filter((i) => i.value).reduce((sum, i) => sum + i.count, 0))}
          caption="Analysed"
          filter="sentiment"
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <BarsCard
          title="Genre Distribution"
          hint={data.genre_needs_review ? `${data.genre_needs_review} genre${data.genre_needs_review === 1 ? "" : "s"} flagged for review` : "AI-classified genre"}
          icon={<Tags size={18} />}
          tone="bg-amber-50 text-amber-600"
          items={data.by_genre}
          color="#4f46e5"
          filter="genre"
        />
        <BarsCard
          title="Language Distribution"
          hint="Primary language of the content"
          icon={<Languages size={18} />}
          tone="bg-sky-50 text-sky-600"
          items={data.by_language}
          color="#0284c7"
          filter="language"
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <CreatorListCard
          title="Recently Analysed"
          hint="Latest AI analyses"
          icon={<Clock3 size={18} />}
          tone="bg-emerald-50 text-emerald-600"
          creators={data.recently_analysed}
          empty="No creator has been analysed yet."
          when={(c) => c.analyzed_at}
        />
        <div id="needs-attention" className="scroll-mt-20">
          <CreatorListCard
            title="Needs Attention"
            hint="Failed or partial - open a creator to retry"
            icon={<AlertTriangle size={18} />}
            tone="bg-rose-50 text-rose-600"
            creators={data.attention}
            empty="Nothing needs attention - every creator was analysed successfully."
            when={(c) => c.updated_at}
            showReason
            total={data.needs_attention}
          />
        </div>
      </div>
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

function Kpi({ label, value, icon, tone, sub, href }: { label: string; value: string; icon: ReactNode; tone: string; sub: string; href?: string }) {
  const body = (
    <>
      <div className="flex items-center gap-2.5">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${tone}`}>{icon}</span>
        <p className="text-xs font-medium leading-tight text-slate-600">{label}</p>
      </div>
      <p className="mt-3 text-2xl font-semibold tabular-nums tracking-tight text-ink">{value}</p>
      <p className="mt-1 truncate text-xs text-muted" title={sub}>
        {sub}
      </p>
    </>
  );
  return (
    <Card className="p-4">
      {href ? (
        <Link href={href} className="-m-4 block rounded-xl p-4 transition-colors hover:bg-slate-50" title={`${label}: open in the Creator List`}>
          {body}
        </Link>
      ) : (
        body
      )}
    </Card>
  );
}

function ItemLabel({ item, filter, children }: { item: CountItem; filter: string; children: ReactNode }) {
  const href = listHref(filter, item.value);
  return href ? (
    <Link href={href} className="hover:text-accent hover:underline" title={`Show these creators in the Creator List`}>
      {children}
    </Link>
  ) : (
    <span title={item.value === null ? "No AI result yet (not analysed, failed, or waiting for AI)" : undefined}>{children}</span>
  );
}

function DonutCard({
  title,
  hint,
  icon,
  tone,
  items,
  colors,
  center,
  caption,
  filter,
}: {
  title: string;
  hint: string;
  icon: ReactNode;
  tone: string;
  items: CountItem[];
  colors: Record<string, string>;
  center: string;
  caption: string;
  filter: string;
}) {
  const total = items.reduce((sum, i) => sum + i.count, 0);
  const color = (item: CountItem) => (item.value ? colors[item.value] ?? "#94a3b8" : NOT_ANALYSED_COLOR);
  const shown = items.filter((i) => i.value); // the "not analysed" part is listed but not drawn
  return (
    <Card className="p-5">
      <SectionTitle icon={icon} tone={tone} title={title} hint={hint} />
      {total === 0 ? (
        <p className="mt-6 text-sm text-muted">No data yet.</p>
      ) : (
        <div className="mt-5 flex flex-wrap items-center gap-6">
          <Donut segments={shown.map((i) => ({ label: i.label, value: i.count, color: color(i) }))} center={center} caption={caption} size={136} />
          <ul className="min-w-[160px] flex-1 space-y-2 text-sm">
            {items.map((item) => (
              <li key={item.label} className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color(item) }} />
                <span className="flex-1 truncate text-slate-600">
                  <ItemLabel item={item} filter={filter}>
                    {item.label}
                  </ItemLabel>
                </span>
                <span className="font-semibold tabular-nums text-ink">{formatNumber(item.count)}</span>
                <span className="w-10 text-right text-xs tabular-nums text-muted">{Math.round((item.count / total) * 100)}%</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function BarsCard({
  title,
  hint,
  icon,
  tone,
  items,
  color,
  filter,
}: {
  title: string;
  hint: string;
  icon: ReactNode;
  tone: string;
  items: CountItem[];
  color: string;
  filter: string;
}) {
  const max = Math.max(0, ...items.map((i) => i.count));
  const total = items.reduce((sum, i) => sum + i.count, 0);
  return (
    <Card className="p-5">
      <SectionTitle icon={icon} tone={tone} title={title} hint={hint} />
      {items.length === 0 ? (
        <p className="mt-6 text-sm text-muted">No YouTube or Instagram creators yet.</p>
      ) : (
        <ul className="mt-5 space-y-3">
          {items.map((item) => (
            <li key={item.label}>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="truncate text-slate-700">
                  <ItemLabel item={item} filter={filter}>
                    {item.label}
                  </ItemLabel>
                </span>
                <span className="shrink-0 tabular-nums">
                  <span className="font-semibold text-ink">{formatNumber(item.count)}</span>
                  <span className="ml-1.5 text-xs text-muted">{Math.round((item.count / total) * 100)}%</span>
                </span>
              </div>
              <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${max ? Math.max(2, (item.count / max) * 100) : 0}%`, background: item.value ? color : NOT_ANALYSED_COLOR }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function CreatorListCard({
  title,
  hint,
  icon,
  tone,
  creators,
  empty,
  when,
  showReason = false,
  total,
}: {
  title: string;
  hint: string;
  icon: ReactNode;
  tone: string;
  creators: DashboardCreator[];
  empty: string;
  when: (creator: DashboardCreator) => string | null;
  showReason?: boolean;
  total?: number;
}) {
  const more = total !== undefined && total > creators.length ? total - creators.length : 0;
  return (
    <Card className="flex h-full flex-col p-5">
      <SectionTitle icon={icon} tone={tone} title={title} hint={hint} />
      {creators.length === 0 ? (
        <p className="mt-6 text-sm text-muted">{empty}</p>
      ) : (
        <ul className="mt-4 divide-y divide-line">
          {creators.map((creator) => (
            <li key={creator.id} className="flex items-center gap-3 py-2.5">
              <CreatorAvatar name={creator.channel_name} platform={creator.platform} src={creator.profile_picture_url} size={36} badge />
              <div className="min-w-0 flex-1">
                <Link
                  href={`/creators?q=${encodeURIComponent(creator.channel_name)}`}
                  className="block truncate text-sm font-medium text-ink hover:text-accent"
                  title="Open in the Creator List"
                >
                  {creator.channel_name}
                </Link>
                <p className="truncate text-xs text-muted" title={showReason ? creator.error_message ?? undefined : undefined}>
                  {showReason
                    ? creator.error_message || "Some data is missing"
                    : [creator.genre, creator.language, creator.audience_count !== null ? `${formatCompact(creator.audience_count)} audience` : null]
                        .filter(Boolean)
                        .join(" · ") || NA}
                </p>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                {showReason ? <StatusBadge status={creator.status} title={creator.error_message} /> : <SentimentBadge sentiment={creator.sentiment} />}
                <span className="text-[11px] text-muted">{formatChecked(when(creator), true)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
      {more > 0 && (
        <p className="mt-3 text-xs text-muted">
          +{more} more in the Creator List:{" "}
          <Link href={listHref("status", "Failed") ?? "/creators"} className="font-medium text-accent hover:underline">
            Failed
          </Link>{" "}
          ·{" "}
          <Link href={listHref("status", "Partial") ?? "/creators"} className="font-medium text-accent hover:underline">
            Partial
          </Link>
        </p>
      )}
    </Card>
  );
}
