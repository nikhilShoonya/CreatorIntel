from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.config.settings import get_settings
from app.schemas.api import ConfigStatus, UTCDateTime
from app.services import meta_budget
from app.services.api_usage import YouTubeQuota, instagram_usage, youtube_quota
from app.services.groq_usage import AllUsageOut, groq_usage
from app.services.meta_token import InstagramTokenStatus, instagram_token_status

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/config/apify-usage")
def apify_usage() -> dict:
    """Local job/usage state only. Does not call Apify and never returns credentials."""
    from app.video_performance.apify_repository import usage

    return usage(get_settings())


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/config/status", response_model=ConfigStatus)
def config_status() -> ConfigStatus:
    """Which integrations are configured (booleans only - never secret values)."""
    settings = get_settings()
    return ConfigStatus(
        youtube_configured=settings.youtube_configured,
        instagram_configured=settings.instagram_configured,
        facebook_configured=settings.facebook_configured,
        ai_configured=settings.ai_configured,
        ai_model=settings.groq_model,
        meta_api_version=settings.meta_api_version,
        average_views_sample_size=settings.average_views_sample_size,
        recent_content_fetch_limit=settings.recent_content_fetch_limit,
        cache_ttl_hours=settings.cache_ttl_hours,
        max_upload_size_mb=settings.max_upload_size_mb,
        database="SQLite" if settings.database_url.startswith("sqlite") else "PostgreSQL",
        youtube_key_count=len(settings.youtube_api_keys),
        warnings=[w for w in (settings.instagram_token_problem,) if w],
    )


@router.get("/config/api-usage", response_model=AllUsageOut)
def api_usage_today() -> AllUsageOut:
    """YouTube quota, Instagram (Meta) rate-limit usage and Groq AI usage. Never returns keys or tokens."""
    settings = get_settings()
    return AllUsageOut(youtube=youtube_quota(settings), instagram=instagram_usage(), groq=groq_usage(settings))


class MetaQueueOut(BaseModel):
    calls_last_hour: int
    hourly_budget: int
    reported_percent: float | None  # Meta's own usage report (last hour), when known
    stop_percent: float
    paused: bool  # no Meta call can be made right now
    reason: str | None
    resume_at: UTCDateTime | None  # when Meta calls continue (None = now)
    waiting_videos: int  # Instagram / Facebook videos waiting for Meta's hourly limit
    waiting_instagram_videos: int
    waiting_creators: int  # Instagram creators waiting for Meta's hourly limit


@router.get("/config/meta-queue", response_model=MetaQueueOut)
def meta_queue() -> MetaQueueOut:
    """Meta (Instagram / Facebook) hourly call budget and the work waiting for it - read-only, no Meta call."""
    from app.agents.instagram_agent import META_WAIT
    from app.models.db import ReadSessionLocal
    from app.models.entities import Creator, CreatorStatus
    from app.video_performance.models import VideoStatus, VtVideo

    with ReadSessionLocal() as db:
        videos = dict(db.execute(
            select(VtVideo.platform, func.count())
            .where(VtVideo.status == VideoStatus.PENDING, VtVideo.status_reason.like(f"{META_WAIT}%"))
            .group_by(VtVideo.platform)
        ).all())
        creators = db.scalar(
            select(func.count()).select_from(Creator)
            .where(Creator.status == CreatorStatus.PENDING, Creator.error_message.like(f"{META_WAIT}%"))
        ) or 0
    return MetaQueueOut(
        **meta_budget.status(),
        waiting_videos=sum(videos.values()),
        waiting_instagram_videos=videos.get("instagram", 0),
        waiting_creators=creators,
    )


@router.get("/config/youtube-quota", response_model=YouTubeQuota)
def youtube_quota_today() -> YouTubeQuota:
    """YouTube Data API units used today per key (as counted by this app)."""
    return youtube_quota(get_settings())


@router.get("/config/instagram-token", response_model=InstagramTokenStatus)
async def instagram_token(refresh: bool = False) -> InstagramTokenStatus:
    """Validity / expiry of the configured Meta token (cached a few minutes; never returns the token)."""
    return await instagram_token_status(get_settings(), refresh=refresh)

