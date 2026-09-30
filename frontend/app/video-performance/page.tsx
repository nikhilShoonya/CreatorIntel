"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  CalendarClock,
  Eye,
  Film,
  Loader2,
  Radar,
  RefreshCw,
  Sparkles,
  TrendingUp,
} from "lucide-react";

import { Button, Card, EmptyState, ErrorBanner } from "@/components/ui/controls";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { VpSentimentBadge } from "@/components/video-performance/badges";
import { formatChecked, formatGrowth, formatSigned } from "@/components/video-performance/format";
import { SentimentBar } from "@/components/video-performance/SentimentBar";
import { useApi } from "@/hooks/useApi";
import { formatCompact, formatDateTime, formatNumber, formatPercent, platformLabel, safeHref } from "@/lib/format";
import { vpApi } from "@/lib/vpApi";
import type { VpDashboard, VpJob, VpVideo } from "@/types/videoPerformance";

const JOB_LABELS: Record<VpJob["job_type"], string> = {
  creator_discovery: "New-video discovery",
  metrics_refresh: "Daily view refresh",
};

export default function VideoPerformanceDashboard() {
  const [token, setToken] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const { data, error, reload } = useApi(`vp-dashboard:${token}`, () => vpApi.dashboard(), {
    keepPrevious: true,
    refreshMs: (d: VpDashboard | undefined) => (d?.jobs.some((j) => j.running) ? 4000 : false),
  });

  async function runJob(job: VpJob["job_type"]) {
    try {
      await vpApi.runJob(job);
      setNotice(`${JOB_LABELS[job]} started`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not start the job");
    }
    window.setTimeout(() => setNotice(null), 4000);
    setToken((n) => n + 1);
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-accent">Video Performance</p>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Dashboard</h1>
          <p className="text-sm text-muted">Daily view tracking and content sentiment for the videos in your Tracking Library.</p>
        </div>
        {data && <JobsStrip jobs={data.jobs} timezone={data.timezone} onRun={runJob} />}
      </div>

      {notice && <div className="rounded-lg border border-indigo-200 bg-accent-soft px-4 py-2.5 text-sm text-accent">{notice}</div>}
      {error && <ErrorBanner message={error} onRetry={reload} />}
      {!data && !error && <div className="h-64 animate-pulse rounded-xl border border-line bg-white" />}

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

      {data && data.total_videos > 0 && <DashboardBody data={data} />}
    </div>
  );
}

function JobsStrip({ jobs, timezone, onRun }: { jobs: VpJob[]; timezone: string; onRun: (job: VpJob["job_type"]) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {jobs.map((job) => (
        <div key={job.job_type} className="flex items-center gap-3 rounded-lg border border-line bg-white px-3 py-2 text-xs">
          <CalendarClock size={16} className="text-slate-400" />
          <div>
            <p className="font-medium text-ink">{JOB_LABELS[job.job_type]}</p>
            <p className="text-muted">
              {job.running
                ? "Running now…"
                : `Last: ${job.last_finished_at ? formatChecked(job.last_finished_at) : "never"}${job.last_status === "failed" ? " (failed)" : ""} · Next: ${
                    job.next_run_at ? formatDateTime(job.next_run_at) : "scheduler off"
                  }`}
            </p>
          </div>
          <Button
            variant="ghost"
            className="h-7 px-2 text-xs"
            onClick={() => onRun(job.job_type)}
            disabled={job.running}
            title={`Run now (scheduled daily, ${timezone})`}
          >
            {job.running ? <Loader2 size={13} className="animate-spin" /> : job.job_type === "metrics_refresh" ? <RefreshCw size={13} /> : <Radar size={13} />}
            Run now
          </Button>
        </div>
      ))}
    </div>
  );
}

function Kpi({ label, value, sub, icon }: { label: string; value: string; sub?: string; icon: ReactNode }) {
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium text-muted">{label}</p>
        <span className="text-slate-400">{icon}</span>
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums tracking-tight text-ink">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </Card>
  );
}

function DashboardBody({ data }: { data: VpDashboard }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Kpi label="Total Tracked Videos" value={formatNumber(data.total_videos)} icon={<Film size={16} />} />
        <Kpi label="YouTube Videos" value={formatNumber(data.youtube_videos)} icon={<PlatformIcon platform="youtube" size={16} />} />
        <Kpi label="Instagram Videos" value={formatNumber(data.instagram_videos)} icon={<PlatformIcon platform="instagram" size={16} />} />
        <Kpi label="Total Current Views" value={formatCompact(data.total_current_views)} sub={formatNumber(data.total_current_views)} icon={<Eye size={16} />} />
        <Kpi
          label="Views Gained Today"
          value={formatSigned(data.views_gained_today)}
          sub={`${data.videos_checked_today} video${data.videos_checked_today === 1 ? "" : "s"} checked today`}
          icon={<TrendingUp size={16} />}
        />
        <Kpi label="New Videos Detected" value={formatNumber(data.new_videos_7d)} sub={`Last 7 days · ${data.new_videos_today} today`} icon={<Radar size={16} />} />
        <Kpi label="Active Trackings" value={formatNumber(data.active_trackings)} sub={`${data.active_creators} creator${data.active_creators === 1 ? "" : "s"} watched`} icon={<Activity size={16} />} />
      </div>

      <div className="grid gap-5 xl:grid-cols-5">
        <Card className="p-5 xl:col-span-3">
          <SectionTitle icon={<Sparkles size={16} />} title="Content sentiment" hint="AI analysis of each tracked video's own title and caption" />
          <p className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wider text-slate-500">Overall</p>
          <SentimentBar counts={data.sentiment_overall} height={14} />

          {data.sentiment_by_platform.length > 0 && (
            <>
              <p className="mb-2 mt-6 text-xs font-semibold uppercase tracking-wider text-slate-500">By platform</p>
              <div className="space-y-4">
                {data.sentiment_by_platform.map((group) => (
                  <div key={group.name}>
                    <p className="mb-1.5 flex items-center gap-2 text-sm font-medium text-ink">
                      <PlatformIcon platform={group.name} size={15} /> {platformLabel(group.name)}
                      <span className="text-xs font-normal text-muted">
                        {group.total} video{group.total === 1 ? "" : "s"}
                      </span>
                    </p>
                    <SentimentBar counts={group.counts} />
                  </div>
                ))}
              </div>
            </>
          )}

          {data.sentiment_by_creator.length > 0 && (
            <>
              <p className="mb-2 mt-6 text-xs font-semibold uppercase tracking-wider text-slate-500">By creator</p>
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-slate-500">
                  <tr>
                    <th className="pb-2 font-medium">Creator</th>
                    <th className="pb-2 text-right font-medium">Videos</th>
                    <th className="w-[45%] pb-2 pl-4 font-medium">Sentiment</th>
                    <th className="pb-2 text-right font-medium">Pos / Neu / Neg</th>
                  </tr>
                </thead>
                <tbody>
                  {data.sentiment_by_creator.map((group) => (
                    <tr key={`${group.name}-${group.platform}`} className="border-t border-line">
                      <td className="py-2">
                        <span className="inline-flex max-w-[200px] items-center gap-2 truncate">
                          {group.platform && <PlatformIcon platform={group.platform} size={14} />}
                          {group.name}
                        </span>
                      </td>
                      <td className="py-2 text-right tabular-nums">{group.total}</td>
                      <td className="py-2 pl-4">
                        <SentimentBar counts={group.counts} showLegend={false} height={8} />
                      </td>
                      <td className="py-2 text-right tabular-nums text-slate-600">
                        {group.counts.positive} / {group.counts.neutral} / {group.counts.negative}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </Card>

        <Card className="p-5 xl:col-span-2">
          <SectionTitle icon={<Sparkles size={16} />} title="Recent sentiment" hint="Latest analysed videos" />
          {data.recent_sentiment.length === 0 ? (
            <p className="mt-4 text-sm text-muted">No videos analysed yet.</p>
          ) : (
            <ul className="mt-3 divide-y divide-line">
              {data.recent_sentiment.map((item) => (
                <li key={item.video_id} className="flex items-center gap-3 py-2.5">
                  <PlatformIcon platform={item.platform} size={16} />
                  <div className="min-w-0 flex-1">
                    <VideoLink url={item.video_url} title={item.display_title} />
                    <p className="text-xs text-muted">
                      {item.creator_name ?? "Unknown creator"} · {formatChecked(item.analyzed_at)}
                    </p>
                  </div>
                  <VpSentimentBadge sentiment={item.sentiment} confidence={item.confidence} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <RankedList title="Top performing videos" hint="Most current views" videos={data.top_performing} metric={(v) => `${formatCompact(v.current_views)} views`} />
        <RankedList
          title="Fastest growing videos"
          hint="Growth since the previous day's check"
          videos={data.fastest_growing}
          metric={(v) => `${formatGrowth(v.growth_pct)} · ${formatSigned(v.views_gained)}`}
          empty="Growth appears after the second daily check."
        />
        <RankedList title="Highest engagement" hint="(likes + comments) / views" videos={data.highest_engagement} metric={(v) => formatPercent(v.engagement_rate)} />
        <RankedList
          title="Latest newly detected videos"
          hint="Found automatically on tracked creators"
          videos={data.latest_detected}
          metric={(v) => formatChecked(v.discovered_at)}
          empty="Track a creator to detect new uploads automatically."
        />
      </div>
    </>
  );
}

function SectionTitle({ icon, title, hint }: { icon: ReactNode; title: string; hint?: string }) {
  return (
    <div>
      <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
        <span className="text-accent">{icon}</span>
        {title}
      </h2>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

function VideoLink({ url, title }: { url: string; title: string }) {
  const href = safeHref(url);
  return href ? (
    <a href={href} target="_blank" rel="noopener noreferrer" className="block truncate text-sm font-medium text-slate-800 hover:text-accent" title={title}>
      {title}
    </a>
  ) : (
    <span className="block truncate text-sm font-medium">{title}</span>
  );
}

function RankedList({
  title,
  hint,
  videos,
  metric,
  empty = "No data yet.",
}: {
  title: string;
  hint: string;
  videos: VpVideo[];
  metric: (v: VpVideo) => string;
  empty?: string;
}) {
  return (
    <Card className="p-5">
      <SectionTitle icon={<TrendingUp size={16} />} title={title} hint={hint} />
      {videos.length === 0 ? (
        <p className="mt-4 text-sm text-muted">{empty}</p>
      ) : (
        <ol className="mt-3 divide-y divide-line">
          {videos.map((video, index) => (
            <li key={video.id} className="flex items-center gap-3 py-2.5">
              <span className="w-5 text-right text-xs tabular-nums text-slate-400">{index + 1}</span>
              <PlatformIcon platform={video.platform} size={16} />
              <div className="min-w-0 flex-1">
                <VideoLink url={video.video_url} title={video.display_title} />
                <p className="truncate text-xs text-muted">{video.creator_name ?? "Unknown creator"}</p>
              </div>
              <span className="whitespace-nowrap text-sm font-semibold tabular-nums text-ink">{metric(video)}</span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
