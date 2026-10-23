export type Platform = "youtube" | "instagram" | "invalid" | "unsupported";

export type CreatorStatus = "Pending" | "Processing" | "Completed" | "Partial" | "Failed";

export type UploadStatus = "validating" | "processing" | "completed" | "failed";

export type SortKey = "audience" | "average_views" | "engagement_rate" | "genre" | "sentiment" | "data_fetched_at";

export type SortDir = "asc" | "desc";

export interface Creator {
  id: number;
  channel_name: string;
  platform: Platform;
  channel_url: string;
  channel_display_url: string | null;
  normalized_identifier: string;
  profile_picture_url: string | null;
  followers_count: number | null;
  subscriber_count: number | null;
  audience_count: number | null;
  average_views: number | null;
  average_views_sample_count: number | null;
  average_views_long: number | null;
  average_views_long_count: number | null;
  average_views_short: number | null;
  average_views_short_count: number | null;
  top_video_title: string | null;
  top_video_url: string | null;
  top_video_views: number | null;
  engagement_rate: number | null;
  engagement_rate_basis: "views" | "followers" | null;
  genre: string | null;
  sub_genre: string | null;
  genre_needs_review: boolean;
  language: string | null;
  sentiment: string | null;
  status: CreatorStatus;
  error_message: string | null;
  data_fetched_at: string | null;
  updated_at: string;
}

export interface CreatorDetail extends Creator {
  identifier_type: string | null;
  platform_id: string | null;
  platform_display_name: string | null;
  account_access: string | null;
  median_views: number | null;
  engagement_sample_count: number | null;
  secondary_language: string | null;
  sentiment_score: number | null;
  genre_confidence: number | null;
  language_confidence: number | null;
  sentiment_confidence: number | null;
  evidence_topics: string[] | null;
  issues: string[] | null;
  provenance: Record<string, string> | null;
  analyzed_at: string | null;
  created_at: string;
}

export interface CreatorListResponse {
  items: Creator[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

/** One bar of a dashboard distribution; `value` is the Creator List filter value (null = not analysed). */
export interface CountItem {
  value: string | null;
  label: string;
  count: number;
}

export interface DashboardCreator extends Creator {
  analyzed_at: string | null;
}

/** Creator Analytics dashboard - aggregated from all stored creators (no API / AI calls). */
export interface CreatorDashboard {
  total: number;
  analysed: number;
  needs_attention: number;
  in_progress: number;
  genre_needs_review: number;
  ai_pending: number;
  average_engagement_rate: number | null;
  engagement_sample: number;
  by_platform: CountItem[];
  by_status: CountItem[];
  by_genre: CountItem[];
  by_language: CountItem[];
  by_sentiment: CountItem[];
  recently_analysed: DashboardCreator[];
  attention: DashboardCreator[];
  last_updated_at: string | null;
  /** Selected period (0 = all time): creators added in it and growth over it. */
  range_days: number;
  added_in_range: number;
  added_by_platform: CountItem[];
  growth: GrowthPoint[];
  timezone: string;
}

export interface GrowthPoint {
  day: string; // local date YYYY-MM-DD
  total: number; // creators added up to this day
  analysed: number; // of those, currently analysed successfully
  added: number; // added on this day
}

/** Meta (Instagram / Facebook) hourly call budget and the work waiting for it. */
export interface MetaQueue {
  calls_last_hour: number;
  hourly_budget: number;
  reported_percent: number | null;
  stop_percent: number;
  paused: boolean;
  reason: string | null;
  resume_at: string | null;
  waiting_videos: number;
  waiting_instagram_videos: number;
  waiting_creators: number;
}

export interface Facets {
  platforms: string[];
  genres: string[];
  languages: string[];
  sentiments: string[];
  statuses: string[];
}

export interface CreatorFilters {
  q?: string;
  platform?: string;
  genre?: string;
  language?: string;
  sentiment?: string;
  status?: string;
  upload_id?: string;
  sort_by?: SortKey;
  sort_dir?: SortDir;
}

export interface UploadSummary {
  id: string;
  filename: string;
  total_rows: number;
  successful_rows: number;
  partial_rows: number;
  failed_rows: number;
  duplicate_rows: number;
  completed_rows: number;
  processed_rows: number;
  status: UploadStatus;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  /** Original file retention: the rows stay in the database after the file is removed. */
  file_deleted_at: string | null;
  file_delete_after: string | null;
}

export interface UploadRow {
  row_number: number;
  channel_name: string | null;
  channel_link: string | null;
  outcome: "queued" | "cached" | "duplicate" | "invalid";
  message: string | null;
}

export interface UploadRows {
  upload: UploadSummary;
  rows: UploadRow[];
}

export interface YouTubeKeyUsage {
  label: string;
  fingerprint: string;
  units: number;
  calls: number;
  limit: number;
  percent: number;
  quota_exceeded: boolean;
}

export interface YouTubeQuota {
  day: string;
  resets_at: string;
  daily_limit_per_key: number;
  key_count: number;
  total_units: number;
  total_limit: number;
  percent: number;
  quota_exceeded_keys: number;
  keys: YouTubeKeyUsage[];
  note: string;
}

/** Meta-reported percentages of its rolling one-hour limit (null = not reported). */
export interface MetaUsageMetrics {
  call_count: number | null;
  total_cputime: number | null;
  total_time: number | null;
}

export interface InstagramUsage {
  calls_today: number;
  facebook_calls_today: number;
  percent: number;
  metrics: MetaUsageMetrics;
  safety_stop_percent: number;
  resume_at: string | null;
  observed_at: string | null;
  stale: boolean;
  regain_access_minutes: number | null;
  note: string;
}

export interface GroqKeyStatus {
  slot: number;
  state: "active" | "standby" | "unusable" | "daily_limit";
  message: string;
  until: string | null;
}

export interface GroqUsage {
  configured: boolean;
  model: string;
  active_slot: number | null;
  tokens_today: number;
  tokens_per_day: number;
  percent: number;
  requests_today: number;
  requests_per_day: number;
  requests_source: "groq" | "counted";
  daily_limit_reached: boolean;
  resets_at: string | null;
  keys: GroqKeyStatus[];
  pending_creators: number;
  pending_videos: number;
  note: string;
}

export interface ApiUsage {
  youtube: YouTubeQuota;
  instagram: InstagramUsage;
  groq: GroqUsage;
}

export interface ApifyUsage {
  enabled: boolean;
  configured: boolean;
  actor_id: string;
  month: string;
  limit: number;
  used_units: number;
  remaining: number;
  reserved_units: number;
  returned_items: number;
  active_runs: number;
  note: string;
  recent_runs: Array<{ id: string; remote_run_id: string | null; status: string; started_at: string; finished_at: string | null; message: string | null }>;
}

export interface InstagramTokenStatus {
  state: "ok" | "expiring" | "expired" | "invalid" | "missing" | "wrong_type" | "unknown" | "error";
  message: string;
  expires_at: string | null;
  days_left: number | null;
  never_expires: boolean;
  missing_permissions: string[];
  checked_at: string;
}

export interface UploadItem {
  creator_id: number;
  position: number;
  source_row: number;
  channel_name: string;
  platform: Platform;
  status: CreatorStatus;
  error_message: string | null;
  from_cache: boolean;
}

export interface UploadDetail extends UploadSummary {
  items: UploadItem[];
}

export interface ConfigStatus {
  youtube_configured: boolean;
  instagram_configured: boolean;
  facebook_configured: boolean;
  ai_configured: boolean;
  ai_model: string;
  meta_api_version: string;
  average_views_sample_size: number;
  recent_content_fetch_limit: number;
  cache_ttl_hours: number;
  max_upload_size_mb: number;
  database: string;
  youtube_key_count: number;
  warnings: string[];
}
