export type VpPlatform = "youtube" | "instagram";

export type VpStatus = "Pending" | "Processing" | "Tracking" | "Paused" | "Completed" | "Partial" | "Failed" | "Unsupported";

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
  source: "upload" | "manual" | "discovered";
  current_views: number | null;
  previous_views: number | null;
  views_gained: number | null;
  growth_pct: number | null;
  likes: number | null;
  comments: number | null;
  engagement_rate: number | null;
  engagement_basis: string | null;
  sentiment: "Positive" | "Neutral" | "Negative" | null;
  sentiment_confidence: number | null;
  status: VpStatus;
  status_reason: string | null;
  last_checked_at: string | null;
  published_at: string | null;
  discovered_at: string | null;
  created_at: string;
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
  captured_at: string;
  views: number | null;
  likes: number | null;
  comments: number | null;
  engagement_rate: number | null;
  views_change: number | null;
}

export interface VpHistory {
  video: VpVideo;
  snapshots: VpSnapshot[];
}

export interface VpCreator {
  id: number;
  creator_name: string;
  platform: VpPlatform;
  channel_url: string;
  username: string | null;
  platform_name: string | null;
  enabled: boolean;
  status: "Pending" | "Active" | "Paused" | "Failed" | "Unsupported";
  status_reason: string | null;
  tracking_since: string;
  last_discovery_at: string | null;
  videos_discovered: number;
  video_count: number;
  created_at: string;
}

export interface VpImportRow {
  row: number;
  creator_name: string | null;
  platform: VpPlatform | null;
  video_url: string | null;
  status: "added" | "already_tracked" | "duplicate" | "invalid";
  message: string | null;
}

export interface VpUploadResult {
  upload: { id: string; filename: string; total_rows: number; added: number; already_tracked: number; duplicates: number; invalid: number; created_at: string };
  rows: VpImportRow[];
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
}

export interface VpDashboard {
  total_videos: number;
  youtube_videos: number;
  instagram_videos: number;
  total_current_views: number;
  views_gained_today: number;
  videos_checked_today: number;
  new_videos_7d: number;
  new_videos_today: number;
  active_trackings: number;
  active_creators: number;
  sentiment_overall: SentimentCounts;
  sentiment_by_platform: GroupSentiment[];
  sentiment_by_creator: GroupSentiment[];
  recent_sentiment: {
    video_id: number;
    display_title: string;
    video_url: string;
    platform: VpPlatform;
    creator_name: string | null;
    sentiment: string | null;
    confidence: number | null;
    analyzed_at: string;
  }[];
  top_performing: VpVideo[];
  fastest_growing: VpVideo[];
  highest_engagement: VpVideo[];
  latest_detected: VpVideo[];
  jobs: VpJob[];
  timezone: string;
}

export interface VpFilters {
  q?: string;
  platform?: string;
  creator?: string;
  status?: string;
  sort_by?: VpSortKey;
  sort_dir?: "asc" | "desc";
}
