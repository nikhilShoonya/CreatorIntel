export type VpPlatform = "youtube" | "instagram" | "facebook";

export type VpStatus = "Pending" | "Processing" | "Tracking" | "Paused" | "Completed" | "Partial" | "Failed" | "Unsupported" | "Video Down";

export type VpSortKey = "current_views" | "views_gained" | "growth_pct" | "engagement_rate" | "last_checked_at" | "created_at";

export interface VpVideo {
  id: number;
  platform: VpPlatform;
  video_identifier: string;
  video_url: string;
  title: string | null;
  display_title: string;
  creator_name: string | null;
  creator_id: number | null;
  owner_username: string | null;
  instagram_media_kind?: "reel" | "post" | "tv" | null;
  metrics_source?: string | null;
  view_metric?: string | null;
  source_metrics?: Record<string, string | number | null> | null;
  /** Facebook only. Plays are not views, and reactions are not likes. */
  plays: number | null;
  reactions: number | null;
  shares: number | null;
  content_type: string | null;
  profile_followers: number | null;
  profile_page_likes: number | null;
  views_precision: string | null;
  metric_status: VpMetricStatus | null;
  /** Current Views came from public shorthand such as 3.3M: not an exact count. */
  views_approximate: boolean;
  views_gained_approximate: boolean;
  source: "upload" | "manual" | "discovered";
  current_views: number | null;
  previous_views: number | null;
  views_gained: number | null;
  growth_pct: number | null;
  likes: number | null;
  comments: number | null;
  engagement_rate: number | null;
  engagement_basis: string | null;
  thumbnail_url: string | null;
  sentiment: "Positive" | "Neutral" | "Negative" | null;
  sentiment_confidence: number | null;
  status: VpStatus;
  status_reason: string | null;
  last_checked_at: string | null;
  published_at: string | null;
  discovered_at: string | null;
  created_at: string;
}

export interface VpMetricStatus {
  content?: Record<string, { status: string | null; precision: string | null; source: string | null }>;
  profile?: Record<string, { status: string | null; precision: string | null; source: string | null }>;
  previous_views_precision?: string | null;
}

export interface VpVideoList {
  items: VpVideo[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  status_counts: Partial<Record<VpStatus, number>>;
}

export interface VpSnapshot {
  metrics_source?: string | null;
  view_metric?: string | null;
  source_metrics?: Record<string, string | number | null> | null;
  plays: number | null;
  reactions: number | null;
  shares: number | null;
  views_approximate: boolean;
  captured_at: string;
  views: number | null;
  likes: number | null;
  comments: number | null;
  engagement_rate: number | null;
  views_change: number | null;
  /** An older, lower count the API returned right after a newer one - ignored in every calculation. */
  outdated: boolean;
}

export interface VpHistory {
  video: VpVideo;
  snapshots: VpSnapshot[];
}

export interface VpImportRow {
  row: number;
  creator_name: string | null;
  platform: VpPlatform | null;
  video_url: string | null;
  status: "added" | "already_tracked" | "duplicate" | "invalid";
  message: string | null;
}

export interface VpUpload {
  id: string;
  filename: string;
  total_rows: number;
  added: number;
  already_tracked: number;
  duplicates: number;
  invalid: number;
  created_at: string;
  file_deleted_at: string | null;
  file_delete_after: string | null;
  /** The original uploaded file can still be downloaded. */
  file_available: boolean;
  /** Instagram / Facebook videos this file added, and how many still wait for Meta's hourly limit. */
  instagram_total: number;
  instagram_waiting: number;
  upload_type: "file" | "links";
  /** Batches of pasted links only: where their unique videos stand now. */
  videos_total: number;
  videos_processed: number;
  videos_failed: number;
  videos_in_progress: number;
  processing_status: "processing" | "completed" | "partial" | "failed" | null;
}

export interface VpLinkPreview {
  detected: number;
  valid: number;
  duplicates: number;
  invalid: number;
  /** Valid, unique links per detected platform. */
  platforms: Record<string, number>;
  invalid_links: { link: string; message: string }[];
}

export interface VpUploadResult {
  upload: VpUpload;
  rows: VpImportRow[];
}

export interface VpUploadRow {
  row_number: number;
  creator_name: string | null;
  platform: string | null;
  video_link: string | null;
  username: string | null;
  status: "added" | "already_tracked" | "duplicate" | "invalid";
  message: string | null;
  video_id: number | null;
  video_status: VpStatus | null;
  video_status_reason: string | null;
  /** Batches of pasted links only: what became of this link. */
  outcome: "processing" | "processed" | "failed" | "duplicate" | "invalid" | "removed" | null;
}

export interface VpPlatformViews {
  platform: VpPlatform;
  total_views: number;
  videos: number;
  /** The rest have no usable view count yet and add nothing to the total. */
  videos_with_views: number;
  /** Counted with a rounded public figure, so the total is approximate. */
  videos_rounded: number;
}

export interface VpUploadPlatformTotals {
  platforms: VpPlatformViews[];
  /** Videos still being checked, so the totals may still change. */
  in_progress: number;
}

export interface SentimentCounts {
  positive: number;
  neutral: number;
  negative: number;
  not_analyzed: number;
}

export interface GroupSentiment {
  name: string;
  platform: VpPlatform | null;
  counts: SentimentCounts;
  total: number;
}

export interface VpJob {
  job_type: "creator_discovery" | "metrics_refresh";
  running: boolean;
  next_run_at: string | null;
  last_status: string | null;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_message: string | null;
  progress_done?: number | null;
  progress_total?: number | null;
}

export interface VpDashboard {
  total_videos: number;
  youtube_videos: number;
  instagram_videos: number;
  facebook_videos: number;
  total_current_views: number;
  views_gained_today: number;
  videos_checked_today: number;
  new_videos_7d: number;
  new_videos_today: number;
  active_trackings: number;
  active_creators: number;
  /** Deleted, private or otherwise unavailable on the platform. */
  videos_down: number;
  sentiment_overall: SentimentCounts;
  sentiment_by_platform: GroupSentiment[];
  recent_sentiment: {
    video_id: number;
    display_title: string;
    video_url: string;
    platform: VpPlatform;
    creator_name: string | null;
    sentiment: string | null;
    confidence: number | null;
    analyzed_at: string;
    thumbnail_url: string | null;
  }[];
  top_performing: VpVideo[];
  highest_engagement: VpVideo[];
  latest_detected: VpVideo[];
  jobs: VpJob[];
  timezone: string;
  range_days: number;
  trend: VpTrendPoint[];
  videos_added_in_range: number;
  views_gained_in_range: number;
  /** Growth of the videos that were already tracked before the range; null when none were. */
  views_growth_pct: number | null;
  /** Daily views gained for the listed videos (null = not tracked yet that day). */
  video_trends: Record<string, (number | null)[]>;
}

export interface VpTrendPoint {
  day: string;
  total_views: number;
  views_gained: number;
  engagement_rate: number | null;
  videos: number;
  youtube_videos: number;
  instagram_videos: number;
  facebook_videos: number;
  new_videos: number;
  videos_checked: number;
}

export interface VpFilters {
  /** Only the videos of one uploaded file (Uploads tab -> View data). */
  upload_id?: string;
  q?: string;
  platform?: string;
  creator?: string;
  status?: string;
  sort_by?: VpSortKey;
  sort_dir?: "asc" | "desc";
}
