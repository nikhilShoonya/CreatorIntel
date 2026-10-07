"""Response models for the REST API."""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, computed_field

from app.utils.time import as_utc
from app.utils.url_parser import short_display_url

# SQLite returns naive datetimes; serialise everything as explicit UTC.
UTCDateTime = Annotated[datetime, AfterValidator(as_utc)]


class CreatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_name: str
    platform: str
    channel_url: str
    normalized_identifier: str
    profile_picture_url: str | None = None

    followers_count: int | None
    subscriber_count: int | None
    average_views: float | None
    average_views_sample_count: int | None
    average_views_long: float | None = None
    average_views_long_count: int | None = None
    average_views_short: float | None = None
    average_views_short_count: int | None = None

    top_video_title: str | None
    top_video_url: str | None
    top_video_views: int | None

    engagement_rate: float | None
    engagement_rate_basis: str | None

    genre: str | None
    sub_genre: str | None
    genre_needs_review: bool
    language: str | None
    sentiment: str | None

    status: str
    error_message: str | None
    updated_at: UTCDateTime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def audience_count(self) -> int | None:
        return self.subscriber_count if self.platform == "youtube" else self.followers_count

    @computed_field  # type: ignore[prop-decorator]
    @property
    def channel_display_url(self) -> str | None:
        if self.platform in ("youtube", "instagram"):
            return short_display_url(self.channel_url)
        return self.channel_url[:80]


class CreatorDetailOut(CreatorOut):
    identifier_type: str | None
    platform_id: str | None
    platform_display_name: str | None
    account_access: str | None
    median_views: float | None
    engagement_sample_count: int | None
    secondary_language: str | None
    sentiment_score: float | None
    genre_confidence: float | None
    language_confidence: float | None
    sentiment_confidence: float | None
    evidence_topics: list[str] | None
    issues: list[str] | None
    provenance: dict[str, str] | None
    data_fetched_at: UTCDateTime | None
    analyzed_at: UTCDateTime | None
    created_at: UTCDateTime


class CreatorListResponse(BaseModel):
    items: list[CreatorOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class FacetsResponse(BaseModel):
    platforms: list[str]
    genres: list[str]
    languages: list[str]
    sentiments: list[str]
    statuses: list[str]


class UploadItemOut(BaseModel):
    creator_id: int
    position: int
    source_row: int
    channel_name: str
    platform: str
    status: str
    error_message: str | None
    from_cache: bool


class UploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    total_rows: int
    successful_rows: int
    partial_rows: int
    failed_rows: int
    duplicate_rows: int
    completed_rows: int = 0
    processed_rows: int = 0
    status: str
    error_message: str | None
    created_at: UTCDateTime
    completed_at: UTCDateTime | None
    # Original file retention: rows are kept in the database after the file is removed.
    file_deleted_at: UTCDateTime | None = None
    file_delete_after: UTCDateTime | None = None


class UploadRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    channel_name: str | None
    channel_link: str | None
    outcome: str
    message: str | None


class UploadRowsOut(BaseModel):
    upload: UploadOut
    rows: list[UploadRowOut]


class UploadDetailOut(UploadOut):
    items: list[UploadItemOut]


class ActionResponse(BaseModel):
    creator_id: int
    status: str
    message: str


class ConfigStatus(BaseModel):
    youtube_configured: bool
    instagram_configured: bool
    ai_configured: bool
    ai_model: str
    meta_api_version: str
    average_views_sample_size: int
    recent_content_fetch_limit: int
    cache_ttl_hours: int
    max_upload_size_mb: int
    database: str
    youtube_key_count: int
    warnings: list[str]


# ----------------------------------------------------------------- CRUD inputs
class CreatorCreateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    channel_name: str = Field(min_length=1, max_length=300)
    channel_link: str = Field(min_length=1, max_length=2048)
    upload_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class CreatorUpdateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    channel_name: str | None = Field(default=None, min_length=1, max_length=300)
    channel_link: str | None = Field(default=None, min_length=1, max_length=2048)


class BulkDeleteIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)


class DeleteResult(BaseModel):
    deleted: int
    message: str
