export type Platform = "youtube" | "instagram" | "invalid" | "unsupported";

export type CreatorStatus = "Pending" | "Processing" | "Completed" | "Partial" | "Failed";

export type UploadStatus = "validating" | "processing" | "completed" | "failed";

export type SortKey = "audience" | "average_views" | "engagement_rate" | "genre" | "sentiment";

export type SortDir = "asc" | "desc";

export interface Creator {
  id: number;
  channel_name: string;
  platform: Platform;
  channel_url: string;
  channel_display_url: string | null;
  normalized_identifier: string;
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
  data_fetched_at: string | null;
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

export interface InstagramUsage {
  calls_today: number;
  percent: number;
  observed_at: string | null;
  stale: boolean;
  regain_access_minutes: number | null;
  note: string;
}

export interface ApiUsage {
  youtube: YouTubeQuota;
  instagram: InstagramUsage;
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
