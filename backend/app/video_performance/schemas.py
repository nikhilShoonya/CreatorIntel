"""API schemas for Video Performance."""

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, computed_field

from app.utils.text import caption_title
from app.utils.time import as_utc

UTC = Annotated[datetime, AfterValidator(as_utc)]
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_thumbnail(platform: str, video_identifier: str | None) -> str | None:
    """YouTube's public thumbnail for a video ID (Instagram has no stable public thumbnail URL)."""
    if platform == "youtube" and _YOUTUBE_ID.match(video_identifier or ""):
        return f"https://i.ytimg.com/vi/{video_identifier}/mqdefault.jpg"
    return None


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: str
    video_identifier: str
    video_url: str
    title: str | None
    creator_name: str | None
    creator_id: int | None
    owner_username: str | None
    source: str
    current_views: int | None
    previous_views: int | None
    views_gained: int | None
    growth_pct: float | None
    likes: int | None
    comments: int | None
    engagement_rate: float | None
    engagement_basis: str | None
    sentiment: str | None
    sentiment_confidence: float | None
    status: str
    status_reason: str | None
    last_checked_at: UTC | None
    published_at: UTC | None
    discovered_at: UTC | None
    created_at: UTC
    caption: str | None = Field(default=None, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def thumbnail_url(self) -> str | None:
        return youtube_thumbnail(self.platform, self.video_identifier)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_title(self) -> str:
        return self.title or caption_title(self.caption, 90) or (
            f"Reel {self.video_identifier}" if self.platform == "instagram" else f"Video {self.video_identifier}"
        )


class VideoDetailOut(VideoOut):
    caption_text: str | None = None
    channel_id: str | None
    upload_id: str | None
    tracking_started_at: UTC


class VideoListOut(BaseModel):
    items: list[VideoOut]
    total: int
    page: int
    page_size: int
    total_pages: int
    status_counts: dict[str, int]


class SnapshotOut(BaseModel):
    captured_at: UTC
    views: int | None
    likes: int | None
    comments: int | None
    engagement_rate: float | None
    views_change: int | None


class HistoryOut(BaseModel):
    video: VideoOut
    snapshots: list[SnapshotOut]


class CreatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    creator_name: str
    platform: str
    channel_url: str
    username: str | None
    platform_name: str | None
    enabled: bool
    status: str
    status_reason: str | None
    tracking_since: UTC
    last_discovery_at: UTC | None
    videos_discovered: int
    video_count: int = 0
    created_at: UTC


class UploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    total_rows: int
    added: int
    already_tracked: int
    duplicates: int
    invalid: int
    created_at: UTC
    # Original file retention: rows stay in the database after the file is removed.
    file_deleted_at: UTC | None = None
    file_delete_after: UTC | None = None


class UploadRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    creator_name: str | None
    platform: str | None
    video_link: str | None
    username: str | None
    status: str
    message: str | None


class UploadRowsOut(BaseModel):
    upload: UploadOut
    rows: list[UploadRowOut]


class ImportRowOut(BaseModel):
    row: int
    creator_name: str | None
    platform: str | None
    video_url: str | None
    status: str
    message: str | None


class UploadResultOut(BaseModel):
    upload: UploadOut
    rows: list[ImportRowOut]


class Facets(BaseModel):
    creators: list[str]
    statuses: list[str]


# ------------------------------------------------------------------ inputs
class VideoCreateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    video_url: str = Field(min_length=1, max_length=2048)
    creator_name: str | None = Field(default=None, max_length=300)
    instagram_username: str | None = Field(default=None, max_length=200)


class VideoUpdateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    creator_name: str | None = Field(default=None, max_length=300)
    video_url: str | None = Field(default=None, min_length=1, max_length=2048)
    instagram_username: str | None = Field(default=None, max_length=200)
    tracking_status: Literal["tracking", "paused"] | None = None


class CreatorCreateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    channel_url: str = Field(min_length=1, max_length=2048)
    creator_name: str | None = Field(default=None, max_length=300)
    backfill: int = Field(default=0, ge=0, le=10, description="Also track this many of the latest videos now")


class CreatorUpdateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    creator_name: str | None = Field(default=None, min_length=1, max_length=300)
    enabled: bool | None = None


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)
    action: Literal["pause", "resume", "refresh", "delete"]


class ActionOut(BaseModel):
    affected: int
    message: str


# --------------------------------------------------------------- dashboard
class SentimentCounts(BaseModel):
    positive: int = 0
    neutral: int = 0
    negative: int = 0
    not_analyzed: int = 0


class GroupSentiment(BaseModel):
    name: str
    platform: str | None = None
    counts: SentimentCounts
    total: int


class RecentSentiment(BaseModel):
    video_id: int
    display_title: str
    video_url: str
    platform: str
    creator_name: str | None
    sentiment: str | None
    confidence: float | None
    analyzed_at: UTC
    thumbnail_url: str | None = None


class JobInfo(BaseModel):
    job_type: str
    running: bool
    next_run_at: UTC | None
    last_status: str | None = None
    last_started_at: UTC | None = None
    last_finished_at: UTC | None = None
    last_message: str | None = None
    # Live progress while running (videos for the refresh job, creators for discovery)
    progress_done: int | None = None
    progress_total: int | None = None


class TrendPoint(BaseModel):
    """One local day (tracking timezone). Each video's views carry forward from its latest check."""

    day: date
    total_views: int
    views_gained: int
    engagement_rate: float | None
    videos: int
    youtube_videos: int
    instagram_videos: int
    facebook_videos: int = 0
    new_videos: int  # newly detected on tracked creators
    videos_checked: int


class DashboardOut(BaseModel):
    total_videos: int
    youtube_videos: int
    instagram_videos: int
    facebook_videos: int = 0
    total_current_views: int
    views_gained_today: int
    videos_checked_today: int
    new_videos_7d: int
    new_videos_today: int
    active_trackings: int
    active_creators: int
    videos_down: int = 0  # deleted, private or otherwise unavailable on the platform
    sentiment_overall: SentimentCounts
    sentiment_by_platform: list[GroupSentiment]
    recent_sentiment: list[RecentSentiment]
    top_performing: list[VideoOut]
    highest_engagement: list[VideoOut]
    latest_detected: list[VideoOut]
    jobs: list[JobInfo]
    timezone: str
    range_days: int = 7
    trend: list[TrendPoint] = []
    videos_added_in_range: int = 0
    views_gained_in_range: int = 0
    views_growth_pct: float | None = None  # growth of the videos already tracked before the range
    video_trends: dict[int, list[int | None]] = {}  # daily views gained of the listed videos
