import { ExternalLink } from "lucide-react";

import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { formatCompact, formatPercent, platformLabel, safeHref } from "@/lib/format";
import type { Creator } from "@/types";

export const NA_CELL = <span className="text-slate-400">N/A</span>;

export function PlatformCell({ platform }: { platform: Creator["platform"] }) {
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap text-slate-700">
      <PlatformIcon platform={platform} />
      {platformLabel(platform)}
    </span>
  );
}

export function ChannelLinkCell({ creator }: { creator: Creator }) {
  const supported = creator.platform === "youtube" || creator.platform === "instagram";
  const href = supported ? safeHref(creator.channel_url) : undefined;
  const label = creator.channel_display_url ?? creator.channel_url;
  if (!href) {
    return (
      <span className="block max-w-[210px] truncate text-slate-400" title={creator.channel_url}>
        {label || "N/A"}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="block max-w-[210px] truncate text-accent underline decoration-indigo-200 underline-offset-2 hover:decoration-accent"
      title={creator.channel_url}
    >
      {label}
    </a>
  );
}

export function TopVideoCell({ creator }: { creator: Creator }) {
  const href = safeHref(creator.top_video_url);
  if (!href || creator.top_video_views === null) return NA_CELL;
  const title = creator.top_video_title || (creator.platform === "instagram" ? "Untitled reel" : "Untitled video");
  return (
    <div className="max-w-[240px]">
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="group inline-flex max-w-full items-center gap-1 font-medium text-slate-800 hover:text-accent"
        title={creator.top_video_title ?? undefined}
      >
        <span className="truncate">{title}</span>
        <ExternalLink size={12} className="shrink-0 text-slate-300 group-hover:text-accent" aria-hidden="true" />
      </a>
      <div className="text-xs text-muted">{formatCompact(creator.top_video_views)} views</div>
    </div>
  );
}

export function EngagementCell({ creator }: { creator: Creator }) {
  if (creator.engagement_rate === null) return NA_CELL;
  return (
    <div title={creator.engagement_rate_basis === "followers" ? "(likes + comments) / followers" : "(likes + comments) / views"}>
      <span className="font-medium text-slate-800">{formatPercent(creator.engagement_rate)}</span>
      {creator.engagement_rate_basis === "followers" && <div className="text-[11px] text-muted">by followers</div>}
    </div>
  );
}

export function GenreCell({ creator }: { creator: Creator }) {
  if (!creator.genre) return NA_CELL;
  return (
    <div className="max-w-[170px]">
      <span className="text-slate-700">{creator.genre}</span>
      {creator.genre_needs_review && (
        <span className="ml-1.5 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-700 ring-1 ring-amber-200">
          Review
        </span>
      )}
    </div>
  );
}
