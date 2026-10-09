"""Application settings loaded from environment variables / backend/.env."""

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- External APIs -------------------------------------------------
    # One key, or several separated by commas (the next one is used when a key
    # is invalid or its daily quota is exhausted).
    youtube_api_key: str = ""
    youtube_daily_quota: int = Field(default=10000, ge=1)  # units per key per day (Google's default)

    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_access_token: str = ""
    meta_api_version: str = "v21.0"
    # Instagram Professional account ID used as the "viewer" for Business Discovery.
    # Optional: discovered automatically from the access token's Facebook Pages when empty.
    meta_ig_business_account_id: str = ""
    # Facebook Page whose videos/reels Video Performance can track. META_ACCESS_TOKEN must be a System User
    # token with this Page assigned; the Page access token is derived from it at runtime (never stored).
    meta_facebook_page_id: str = ""

    # Groq (OpenAI-compatible Chat Completions API). The default model supports
    # strict JSON-schema structured output.
    groq_api_key: str = ""  # single key (used when GROQ_API_KEY_1..3 are not set)
    # Separately authorised keys, used in order. The next key is used ONLY when the active key itself is
    # unusable (invalid/revoked key, restricted account) - never to get around a rate or daily limit.
    groq_api_key_1: str = ""
    groq_api_key_2: str = ""
    groq_api_key_3: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    # Optional extra models, used in order when the main model's daily limit is reached. Must support strict
    # JSON-schema output. Comma-separated; empty (default) = only GROQ_MODEL is used.
    groq_fallback_models: str = ""
    # Free-plan per-minute limits of the models above; requests are paced below them.
    groq_requests_per_minute: int = Field(default=30, ge=1)
    groq_tokens_per_minute: int = Field(default=8000, ge=500)
    # Free-plan daily limits, shown in Settings (Groq reports remaining requests itself once it has been called).
    groq_tokens_per_day: int = Field(default=200_000, ge=1)
    groq_requests_per_day: int = Field(default=1_000, ge=1)
    # Video Performance: videos whose sentiment is rated in one AI request.
    ai_sentiment_batch_size: int = Field(default=10, ge=1, le=25)
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # --- Persistence ---------------------------------------------------
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'creatorintel.db').as_posix()}"
    db_pool_size: int = Field(default=10, ge=1, le=30)
    # Keep a remote database (e.g. Neon, which suspends after ~5 idle minutes) awake during these local
    # hours so the first page load is not slowed by a cold start. "" disables. Uses VIDEO_TRACKING_TIMEZONE.
    db_keep_warm_hours: str = "09:00-21:00"
    upload_dir: Path = BACKEND_DIR / "uploads"

    # --- Processing ----------------------------------------------------
    average_views_sample_size: int = Field(default=10, ge=1, le=50)
    recent_content_fetch_limit: int = Field(default=20, ge=5, le=50)
    max_concurrent_creators: int = Field(default=4, ge=1, le=16)
    cache_ttl_hours: int = Field(default=24, ge=0)
    max_upload_size_mb: int = Field(default=10, ge=1, le=50)
    max_rows_per_upload: int = Field(default=1000, ge=1)
    # Uploaded files are deleted after this many days; their rows stay in the database (0 = keep files forever)
    upload_file_retention_days: int = Field(default=30, ge=0)
    housekeeping_interval_hours: int = Field(default=6, ge=1, le=168)

    # --- HTTP behaviour -------------------------------------------------
    http_timeout_seconds: float = 20.0
    http_max_retries: int = Field(default=3, ge=0, le=6)

    # --- AI classification ---------------------------------------------
    ai_timeout_seconds: float = 60.0
    ai_max_retries: int = Field(default=2, ge=0, le=4)
    ai_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # --- Video Performance (independent module) ------------------------
    video_tracking_scheduler_enabled: bool = True
    video_tracking_timezone: str = "Asia/Kolkata"
    # Video Down videos are re-checked daily for this many days after they were last seen, then only on Retry (0 = always).
    video_tracking_down_recheck_days: int = Field(default=14, ge=0)
    video_tracking_refresh_time: str = "06:30"  # daily metric refresh (HH:MM, local to the timezone above)
    video_tracking_discovery_time: str = "06:00"  # daily new-video discovery for tracked creators
    video_tracking_max_days: int = Field(default=0, ge=0)  # stop tracking a video after N days (0 = never)
    video_tracking_instagram_scan_pages: int = Field(default=4, ge=1, le=10)  # 50 media per page

    # --- Web -------------------------------------------------------------
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    log_level: str = "INFO"
    log_dir: Path = BACKEND_DIR / "logs"  # daily rotating log files
    log_retention_days: int = Field(default=14, ge=1, le=365)

    @field_validator("database_url")
    @classmethod
    def _default_database_url(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            return f"sqlite:///{(BACKEND_DIR / 'creatorintel.db').as_posix()}"
        # Use the psycopg (v3) driver for plain PostgreSQL URLs (e.g. Neon, Supabase, RDS).
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @field_validator("meta_api_version")
    @classmethod
    def _default_meta_version(cls, value: str) -> str:
        value = (value or "v21.0").strip()
        return value if value.startswith("v") else f"v{value}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def youtube_api_keys(self) -> list[str]:
        keys = [k.strip().strip("\"'") for k in re.split(r"[,;\s]+", self.youtube_api_key or "")]
        return list(dict.fromkeys(k for k in keys if k))

    @property
    def youtube_configured(self) -> bool:
        return bool(self.youtube_api_keys)

    @property
    def groq_api_keys(self) -> list[str]:
        numbered = [k.strip() for k in (self.groq_api_key_1, self.groq_api_key_2, self.groq_api_key_3) if k.strip()]
        keys = numbered or ([self.groq_api_key.strip()] if self.groq_api_key.strip() else [])
        return list(dict.fromkeys(keys))

    @property
    def groq_models(self) -> list[str]:
        models = [self.groq_model, *(m.strip() for m in self.groq_fallback_models.split(","))]
        return [m for m in dict.fromkeys(models) if m]

    @property
    def facebook_configured(self) -> bool:
        return bool(self.meta_access_token.strip() and self.meta_facebook_page_id.strip())

    @property
    def instagram_configured(self) -> bool:
        return bool(self.meta_access_token)

    @property
    def instagram_token_problem(self) -> str | None:
        """Business Discovery only works with Facebook Login tokens (EAA...), not Instagram Login tokens (IG...)."""
        token = self.meta_access_token.strip()
        if token.startswith("IG"):
            return (
                "META_ACCESS_TOKEN is an Instagram Login token (starts with 'IG'). Business Discovery needs a "
                "Facebook Login user token (starts with 'EAA') from a Facebook Page linked to your Instagram "
                "Business account - see README section 4"
            )
        return None

    @property
    def ai_configured(self) -> bool:
        return bool(self.groq_api_keys)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
