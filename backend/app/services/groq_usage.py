"""Groq AI usage for the Settings page: today's usage of the active key, key states and pending AI work.

Never returns API keys - keys are identified by their slot (#1, #2, #3) only.
"""

import time
from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import exists, func, or_, select

from app.config.settings import Settings
from app.models.db import ReadSessionLocal
from app.services.api_usage import ApiUsageOut, groq_counts
from app.services.llm_client import LLMClient


class GroqKeyStatus(BaseModel):
    slot: int
    state: Literal["active", "standby", "unusable", "daily_limit"]
    message: str
    until: datetime | None = None


class GroqUsage(BaseModel):
    configured: bool
    model: str
    active_slot: int | None
    tokens_today: int
    tokens_per_day: int
    percent: float
    requests_today: int
    requests_per_day: int
    requests_source: Literal["groq", "counted"]
    daily_limit_reached: bool
    resets_at: datetime | None
    keys: list[GroqKeyStatus]
    pending_creators: int
    pending_videos: int
    note: str


class AllUsageOut(ApiUsageOut):
    groq: GroqUsage


_PENDING_TTL_SECONDS = 60
_pending_cache: tuple[float, tuple[int, int]] | None = None


def _pending_counts() -> tuple[int, int]:
    """(creators, videos) waiting for AI - cached for a minute (the Settings page polls every 30 s)."""
    global _pending_cache
    now = time.monotonic()
    if _pending_cache and now - _pending_cache[0] < _PENDING_TTL_SECONDS:
        return _pending_cache[1]
    _pending_cache = (now, _count_pending())
    return _pending_cache[1]


def _count_pending() -> tuple[int, int]:
    from app.services.orchestrator import EnrichmentOrchestrator
    from app.video_performance.models import VideoStatus, VtSentiment, VtVideo

    creators = len(EnrichmentOrchestrator._pending_ai_creators(500))
    with ReadSessionLocal() as db:
        videos = db.scalar(
            select(func.count(VtVideo.id)).where(
                VtVideo.status.in_(VideoStatus.DAILY),
                or_(VtVideo.title.is_not(None), VtVideo.caption.is_not(None)),
                ~exists().where(VtSentiment.video_id == VtVideo.id),
            )
        ) or 0
    return creators, int(videos)


def groq_usage(settings: Settings) -> GroqUsage:
    client = LLMClient(settings)
    states = client.key_states()
    active = next((s for s in states if s["state"] in ("active", "daily_limit")), None)
    tokens = calls = 0
    reported = None
    if active is not None:
        tokens, calls, reported = groq_counts(active["key"])
    limit_reached = active is not None and active["state"] == "daily_limit"
    if reported:
        requests_today, requests_per_day, source = reported["limit"] - reported["remaining"], reported["limit"], "groq"
    else:
        requests_today, requests_per_day, source = calls, settings.groq_requests_per_day, "counted"
    pending_creators, pending_videos = _pending_counts() if settings.ai_configured else (0, 0)
    return GroqUsage(
        configured=settings.ai_configured,
        model=settings.groq_model,
        active_slot=active["slot"] if active else None,
        tokens_today=tokens,
        tokens_per_day=settings.groq_tokens_per_day,
        percent=100.0 if limit_reached else round(min(tokens / settings.groq_tokens_per_day * 100, 100), 1),
        requests_today=max(requests_today, 0),
        requests_per_day=requests_per_day,
        requests_source=source,
        daily_limit_reached=limit_reached,
        resets_at=active["until"] if limit_reached else None,
        keys=[GroqKeyStatus(**{k: v for k, v in s.items() if k != "key"}) for s in states],
        pending_creators=pending_creators,
        pending_videos=pending_videos,
        note="Tokens counted by CreatorIntel per UTC day for the active key; other apps using the same key are not included.",
    )
