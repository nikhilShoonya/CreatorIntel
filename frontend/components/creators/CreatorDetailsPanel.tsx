"use client";

import { useEffect, type ReactNode } from "react";
import { ExternalLink, Loader2, Pencil, RefreshCw, Sparkles, Trash2, X } from "lucide-react";

import { SentimentBadge, StatusBadge } from "@/components/ui/Badges";
import { Button, ErrorBanner } from "@/components/ui/controls";
import { CreatorAvatar, platformGradient } from "@/components/ui/CreatorAvatar";
import { PlatformIcon } from "@/components/ui/PlatformIcon";
import { useCreatorDetail } from "@/hooks/useData";
import {
  formatCompact,
  formatConfidence,
  formatDateTime,
  formatNumber,
  formatPercent,
  humanize,
  NA,
  platformLabel,
  safeHref,
} from "@/lib/format";
import type { CreatorDetail } from "@/types";
import { isBusy } from "./RowActions";

interface Props {
  creatorId: number | null;
  onClose: () => void;
  onAction: (kind: "retry" | "reanalyze", creatorId: number) => Promise<void>;
  onEdit: (creator: CreatorDetail) => void;
  onDelete: (creator: CreatorDetail) => void;
}

const ACCESS_LABELS: Record<string, string> = {
  public_channel: "Public YouTube channel",
  professional_account: "Instagram Professional account",
  consumer_or_unavailable: "Personal / private account (not available via official API)",
  api_unavailable: "API not configured",
  api_error: "API error",
  not_found: "Not found",
};

export function CreatorDetailsPanel({ creatorId, onClose, onAction, onEdit, onDelete }: Props) {
  const { data: creator, error, reload } = useCreatorDetail(creatorId);
  const open = creatorId !== null;

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    // Lock page scroll behind the modal
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  async function act(kind: "retry" | "reanalyze") {
    if (!creator) return;
    await onAction(kind, creator.id);
    reload();
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center p-4 sm:p-6" role="dialog" aria-modal="true" aria-label="Creator details">
      <button type="button" className="animate-fade-in absolute inset-0 bg-slate-900/40 backdrop-blur-[2px]" aria-label="Close details" onClick={onClose} />
      <div className="animate-modal-in relative flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl shadow-slate-900/20">
        <header className="flex items-start justify-between gap-4 border-b border-line px-6 py-5">
          {creator ? (
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <PlatformIcon platform={creator.platform} size={20} />
                <h2 className="truncate text-lg font-semibold text-ink">{creator.channel_name}</h2>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <StatusBadge status={creator.status} />
                <span className="text-xs text-muted">Last updated {formatDateTime(creator.updated_at)}</span>
              </div>
            </div>
          ) : (
            <div className="h-12" />
          )}
          <button type="button" onClick={onClose} className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100" aria-label="Close">
            <X size={18} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {error && <ErrorBanner message={error} onRetry={reload} />}
          {!creator && !error && (
            <div className="flex justify-center py-16">
              <Loader2 className="animate-spin text-slate-400" />
            </div>
          )}
          {creator && <DetailBody creator={creator} />}
        </div>

        {creator && (
          <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-slate-50/60 px-6 py-4">
            <button
              type="button"
              onClick={() => onDelete(creator)}
              className="mr-auto inline-flex h-9 items-center gap-2 rounded-lg px-3 text-sm font-medium text-rose-600 hover:bg-rose-50"
            >
              <Trash2 size={15} />
              Delete
            </button>
            <Button onClick={() => onEdit(creator)}>
              <Pencil size={15} />
              Edit
            </Button>
            {safeHref(creator.channel_url) && (creator.platform === "youtube" || creator.platform === "instagram") && (
              <a
                href={safeHref(creator.channel_url)}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-9 items-center gap-2 rounded-lg border border-line px-3.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                <ExternalLink size={15} />
                Open Channel
              </a>
            )}
            <Button onClick={() => act("reanalyze")} disabled={isBusy(creator) || creator.platform === "invalid" || creator.platform === "unsupported"}>
              <Sparkles size={15} />
              Re-analyze
            </Button>
            <Button variant="primary" onClick={() => act("retry")} disabled={isBusy(creator) || creator.platform === "invalid" || creator.platform === "unsupported"}>
              <RefreshCw size={15} />
              {creator.status === "Failed" || creator.status === "Partial" ? "Retry" : "Refresh Data"}
            </Button>
          </footer>
        )}
      </div>
    </div>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  youtube_data_api_v3: "YouTube Data API v3",
  instagram_graph_api_business_discovery: "Instagram Graph API",
};

/** "calculated_from_10_reels" -> "calculated from 10 reels"; known API sources get their product name. */
function sourceLabel(value: string | undefined): string | undefined {
  if (!value) return undefined;
  return SOURCE_LABELS[value] ?? value.replace(/_/g, " ");
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">{title}</h3>
      <dl className="divide-y divide-line rounded-lg border border-line">{children}</dl>
    </section>
  );
}

function Row({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3 px-4 py-2.5 text-sm">
      <dt className="text-muted">{label}</dt>
      <dd className="min-w-0 break-words text-ink">
        {children}
        {hint && <div className="text-xs text-muted">{hint}</div>}
      </dd>
    </div>
  );
}

function StatPill({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line bg-slate-50 px-2.5 py-0.5 text-xs font-medium text-slate-700">
      {children}
    </span>
  );
}

/** Creator Information as a profile card: platform cover, profile picture, name, link and quick stats. */
function ProfileCard({ creator }: { creator: CreatorDetail }) {
  const href = safeHref(creator.channel_url);
  const isPlatform = creator.platform === "youtube" || creator.platform === "instagram";
  const displayName = creator.platform_display_name || creator.channel_name;
  const audienceLabel = creator.platform === "youtube" ? "subscribers" : "followers";

  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Creator Information</h3>
      <div className="overflow-hidden rounded-lg border border-line">
        <div className="relative h-20" style={{ background: platformGradient(creator.platform) }} aria-hidden="true">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_85%_20%,rgba(255,255,255,0.35),transparent_55%)]" />
        </div>
        <div className="flex items-end gap-4 px-4">
          {creator.profile_picture_url ? (
            <a
              href={creator.profile_picture_url}
              target="_blank"
              rel="noopener noreferrer"
              className="-mt-10 shrink-0 rounded-full transition-opacity hover:opacity-90"
              title="View profile picture"
            >
              <CreatorAvatar
                name={displayName}
                platform={creator.platform}
                src={creator.profile_picture_url}
                size={84}
                badge
                className="rounded-full shadow-md ring-4 ring-white"
              />
            </a>
          ) : (
            <CreatorAvatar
              name={displayName}
              platform={creator.platform}
              src={creator.profile_picture_url}
              size={84}
              badge
              className="-mt-10 rounded-full shadow-md ring-4 ring-white"
            />
          )}
          <div className="min-w-0 flex-1 pb-1 pt-2">
            <p className="truncate text-base font-semibold text-ink" title={displayName}>
              {displayName}
            </p>
            {href && isPlatform ? (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="block truncate text-sm text-accent hover:underline"
              >
                {creator.channel_display_url}
              </a>
            ) : (
              <span className="block truncate text-sm text-slate-500">{creator.channel_url || NA}</span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5 px-4 pb-4 pt-3">
          {creator.audience_count !== null && (
            <StatPill>
              <span className="font-semibold text-ink">{formatCompact(creator.audience_count)}</span> {audienceLabel}
            </StatPill>
          )}
          {creator.average_views !== null && (
            <StatPill>
              <span className="font-semibold text-ink">{formatCompact(creator.average_views)}</span> avg views
            </StatPill>
          )}
          {creator.genre && <StatPill>{creator.genre}</StatPill>}
          {creator.language && <StatPill>{creator.language}</StatPill>}
        </div>
        <dl className="divide-y divide-line border-t border-line">
          {displayName !== creator.channel_name && <Row label="Channel name">{creator.channel_name}</Row>}
          <Row label="Platform">
            <span className="inline-flex items-center gap-1.5">
              <PlatformIcon platform={creator.platform} size={16} />
              {platformLabel(creator.platform)}
            </span>
          </Row>
          <Row label="Account access">
            {creator.account_access ? ACCESS_LABELS[creator.account_access] ?? humanize(creator.account_access) : NA}
          </Row>
          {creator.platform_id && (
            <Row label={creator.platform === "youtube" ? "Channel ID" : "Account ID"}>
              <span className="font-mono text-xs text-slate-600">{creator.platform_id}</span>
            </Row>
          )}
        </dl>
      </div>
    </section>
  );
}

function DetailBody({ creator }: { creator: CreatorDetail }) {
  const audienceLabel = creator.platform === "youtube" ? "Subscribers" : "Followers";
  const videoHref = safeHref(creator.top_video_url);
  const provenance = creator.provenance ?? {};

  return (
    <div className="space-y-5">
      {creator.issues && creator.issues.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <p className="mb-1 font-medium">Notes</p>
          <ul className="list-disc space-y-0.5 pl-5">
            {creator.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Two independent columns: each card keeps its natural height, so short cards leave no gaps. */}
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          <ProfileCard creator={creator} />

          <Section title="Content Analysis (AI)">
            <Row label="Genre">
              {creator.genre ?? NA}
              {creator.genre_needs_review && <span className="ml-2 text-xs font-medium text-amber-700">Needs review</span>}
            </Row>
            <Row label="Sub-genre">{creator.sub_genre ?? NA}</Row>
            <Row label="Language">{creator.language ?? NA}</Row>
            <Row label="Secondary language">{creator.secondary_language ?? NA}</Row>
            <Row label="Sentiment">
              <SentimentBadge sentiment={creator.sentiment} />
              {creator.sentiment_score !== null && <span className="ml-2 text-muted">score {creator.sentiment_score.toFixed(2)}</span>}
            </Row>
            <Row label="Analysis confidence">
              Genre {formatConfidence(creator.genre_confidence)} · Language {formatConfidence(creator.language_confidence)} · Sentiment{" "}
              {formatConfidence(creator.sentiment_confidence)}
            </Row>
            {creator.evidence_topics && creator.evidence_topics.length > 0 && (
              <Row label="Observed topics">
                <div className="flex flex-wrap gap-1.5">
                  {creator.evidence_topics.map((topic) => (
                    <span key={topic} className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                      {topic}
                    </span>
                  ))}
                </div>
              </Row>
            )}
          </Section>
        </div>

        <div className="space-y-5">
          <Section title="Audience & Performance">
            <Row label={audienceLabel} hint={provenance.audience_count && `Source: ${sourceLabel(provenance.audience_count)}`}>
              {creator.audience_count === null ? NA : `${formatCompact(creator.audience_count)} (${formatNumber(creator.audience_count)})`}
            </Row>
            <Row label="Average views" hint={provenance.average_views && `Source: ${sourceLabel(provenance.average_views)}`}>
              {formatCompact(creator.average_views)}
              {creator.median_views !== null && <span className="text-muted"> · median {formatCompact(creator.median_views)}</span>}
            </Row>
            {creator.platform === "youtube" && (creator.average_views_long !== null || creator.average_views_short !== null) && (
              <>
                <Row label="Long-form avg" hint="Videos longer than 3 minutes">
                  {creator.average_views_long === null
                    ? "No long-form videos in the sample"
                    : `${formatCompact(creator.average_views_long)} · ${creator.average_views_long_count} videos`}
                </Row>
                <Row label="Short-form avg" hint="Videos up to 3 minutes (includes Shorts)">
                  {creator.average_views_short === null
                    ? "No short-form videos in the sample"
                    : `${formatCompact(creator.average_views_short)} · ${creator.average_views_short_count} videos`}
                </Row>
              </>
            )}
            <Row label="Top performing video" hint={provenance.top_video && `Source: ${sourceLabel(provenance.top_video)}`}>
              {videoHref && creator.top_video_views !== null ? (
                <>
                  <a href={videoHref} target="_blank" rel="noopener noreferrer" className="font-medium text-accent hover:underline">
                    {creator.top_video_title || "Untitled"}
                  </a>
                  <div className="text-muted">{formatNumber(creator.top_video_views)} views</div>
                </>
              ) : (
                NA
              )}
            </Row>
            <Row label="Engagement rate" hint={provenance.engagement_rate && `Formula: ${sourceLabel(provenance.engagement_rate)}`}>
              {formatPercent(creator.engagement_rate, 2)}
              {creator.engagement_rate_basis && <span className="text-muted"> · based on {creator.engagement_rate_basis}</span>}
            </Row>
          </Section>

          <Section title="Processing">
            <Row label="Status">
              <StatusBadge status={creator.status} />
            </Row>
            {creator.error_message && <Row label="Message">{creator.error_message}</Row>}
            <Row label="Data source">{sourceLabel(provenance.audience_count) ?? (creator.data_fetched_at ? "Platform API" : NA)}</Row>
            <Row label="AI model">{provenance.genre?.replace("llm_analysis:", "").replace("groq:", "Groq · ") ?? NA}</Row>
            <Row label="Data fetched">{formatDateTime(creator.data_fetched_at)}</Row>
            <Row label="Analyzed">{formatDateTime(creator.analyzed_at)}</Row>
            <Row label="Last updated">{formatDateTime(creator.updated_at)}</Row>
          </Section>
        </div>
      </div>
    </div>
  );
}
