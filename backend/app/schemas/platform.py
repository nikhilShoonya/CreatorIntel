"""Normalised data returned by the platform collectors (YouTube / Instagram)."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ContentItem:
    id: str
    url: str | None
    published_at: datetime | None
    title: str | None = None  # YouTube title or a caption-derived snippet for Instagram
    text: str | None = None  # description / caption
    is_video: bool = False  # YouTube video or Instagram Reel/video
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    tags: list[str] = field(default_factory=list)
    is_live_or_upcoming: bool = False
    duration_seconds: int | None = None  # YouTube only


@dataclass
class ChannelProfile:
    platform: str
    platform_id: str
    display_name: str | None
    bio: str | None
    audience_count: int | None  # subscribers (YouTube) or followers (Instagram)
    account_access: str  # e.g. "public_channel", "professional_account"
    website: str | None = None
    profile_picture_url: str | None = None  # avatar URL from the platform API
    items: list[ContentItem] = field(default_factory=list)
    source: str = ""  # e.g. "youtube_data_api_v3"
    # Set when the input link was not a channel link (e.g. a video) and the channel was resolved by the API
    resolved_channel_url: str | None = None
    notes: list[str] = field(default_factory=list)


class CollectionError(Exception):
    """A platform collector could not return data for a creator."""

    # Codes
    CONFIG_MISSING = "config_missing"
    NOT_FOUND = "not_found"
    NOT_ACCESSIBLE = "not_accessible"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    AUTH_ERROR = "auth_error"
    API_ERROR = "api_error"

    def __init__(self, code: str, message: str, *, account_access: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.account_access = account_access
