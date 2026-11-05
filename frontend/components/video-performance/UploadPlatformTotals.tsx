"use client";

import { Card, ErrorBanner } from "@/components/ui/controls";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { useApi } from "@/hooks/useApi";
import { formatNumber, platformLabel } from "@/lib/format";
import { vpApi } from "@/lib/vpApi";
import type { VpPlatform, VpPlatformViews, VpUploadPlatformTotals } from "@/types/videoPerformance";

const PLATFORMS: VpPlatform[] = ["youtube", "instagram", "facebook"];
const REFRESH_WHILE_CHECKING_MS = 5000;

function videoCount(count: number): string {
  return `${count.toLocaleString("en-US")} video${count === 1 ? "" : "s"}`;
}

/** How much of the platform the total covers, so a partial sum is never mistaken for a complete one. */
function coverage({ videos, videos_with_views: withViews }: VpPlatformViews): string {
  if (videos === 0) return "No videos in this file";
  if (withViews === videos) return videoCount(videos);
  if (withViews === 0) return `${videoCount(videos)}, no view counts yet`;
  return `${withViews.toLocaleString("en-US")} of ${videoCount(videos)} counted`;
}

function roundedNote(rounded: number): string {
  return rounded > 0 ? ` · ${rounded.toLocaleString("en-US")} rounded` : "";
}

function PlatformTotalCard({ platform, totals }: { platform: VpPlatform; totals: VpPlatformViews | undefined }) {
  const hasNoCounts = totals !== undefined && totals.videos > 0 && totals.videos_with_views === 0;
  return (
    <Card className="flex items-center gap-3 px-4 py-2.5">
      <span className="shrink-0">
        <PlatformIcon platform={platform} size={24} />
      </span>
      <div className="min-w-0">
        <p className="truncate text-xs font-medium text-slate-600">{platformLabel(platform)} – Total Views</p>
        {totals ? (
          <p className="flex flex-wrap items-baseline gap-x-2">
            <span className={`text-xl font-semibold tabular-nums ${hasNoCounts ? "text-slate-400" : "text-ink"}`}>
              {hasNoCounts ? "N/A" : `${totals.videos_rounded > 0 ? "≈" : ""}${formatNumber(totals.total_views)}`}
            </span>
            <span className="text-xs text-muted">
              {coverage(totals)}
              {roundedNote(totals.videos_rounded)}
            </span>
          </p>
        ) : (
          <div className="mt-1 h-6 w-28 animate-pulse rounded bg-slate-100" />
        )}
      </div>
    </Card>
  );
}

/** Total views per platform across every video of one uploaded file, whatever page or filter the table is on. */
export function UploadPlatformTotals({ uploadId, refreshToken }: { uploadId: string; refreshToken: number }) {
  const { data, error, reload } = useApi(`vp-upload-platform-totals:${uploadId}:${refreshToken}`, () => vpApi.uploadPlatformTotals(uploadId), {
    keepPrevious: true,
    refreshMs: (d: VpUploadPlatformTotals | undefined) => (d?.in_progress ? REFRESH_WHILE_CHECKING_MS : false),
  });

  if (error && !data) return <ErrorBanner message={error} onRetry={reload} />;

  return (
    <div role="group" aria-label="Total views per platform in this file" className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {PLATFORMS.map((platform) => (
        <PlatformTotalCard key={platform} platform={platform} totals={data?.platforms.find((p) => p.platform === platform)} />
      ))}
    </div>
  );
}
