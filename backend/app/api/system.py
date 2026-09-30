from fastapi import APIRouter

from app.config.settings import get_settings
from app.schemas.api import ConfigStatus

router = APIRouter(prefix="/api", tags=["system"])


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
