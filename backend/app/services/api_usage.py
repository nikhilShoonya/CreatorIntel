"""YouTube Data API quota usage, counted by this app.

Every request costs quota units (channels/playlistItems/videos.list = 1 unit each, including failed
requests and retries). Google resets the daily quota at midnight Pacific Time. Counts are kept in memory
and written to the database every few seconds, so recording a call never adds a database round trip.
API keys are identified by a short hash; the key itself is never stored.
"""

import asyncio
import hashlib
import logging
import threading
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.config.settings import Settings
from app.models.db import ReadSessionLocal, session_scope
from app.models.usage import ApiUsage
from app.utils.logging import log_event
from app.utils.time import utcnow

logger = logging.getLogger("creatorintel.api_usage")

YOUTUBE = "youtube"
QUOTA_TZ = ZoneInfo("America/Los_Angeles")  # YouTube quota resets at midnight Pacific Time
FLUSH_EVERY_SECONDS = 20

_pending: dict[tuple[str, str, str], list] = {}  # (day, service, fingerprint) -> [units, calls, exceeded_at]
_lock = threading.Lock()


def key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:10]


def quota_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(QUOTA_TZ).date().isoformat()


def next_reset(now: datetime | None = None) -> datetime:
    local = (now or datetime.now(timezone.utc)).astimezone(QUOTA_TZ)
    return datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=QUOTA_TZ).astimezone(timezone.utc)


def record_youtube_call(api_key: str, units: int = 1) -> None:
    """Count one YouTube API request (call this for every attempt, retries included)."""
    _add(YOUTUBE, api_key, units=units, calls=1)


def record_youtube_quota_exceeded(api_key: str) -> None:
    _add(YOUTUBE, api_key, units=0, calls=0, exceeded=True)


def _add(service: str, api_key: str, *, units: int, calls: int, exceeded: bool = False) -> None:
    key = (quota_day(), service, key_fingerprint(api_key))
    with _lock:
        entry = _pending.setdefault(key, [0, 0, None])
        entry[0] += units
        entry[1] += calls
        if exceeded:
            entry[2] = utcnow()


def flush() -> int:
    """Write pending counts to the database (atomic increments, safe across processes)."""
    with _lock:
        pending = dict(_pending)
        _pending.clear()
    if not pending:
        return 0
    try:
        with session_scope() as db:
            for (day, service, fingerprint), (units, calls, exceeded_at) in pending.items():
                where = (ApiUsage.day == day, ApiUsage.service == service, ApiUsage.key_fingerprint == fingerprint)
                values = {"units": ApiUsage.units + units, "calls": ApiUsage.calls + calls, "updated_at": utcnow()}
                if exceeded_at is not None:
                    values["quota_exceeded_at"] = exceeded_at
                if db.execute(update(ApiUsage).where(*where).values(**values)).rowcount == 0:
                    try:
                        with db.begin_nested():
                            db.add(ApiUsage(day=day, service=service, key_fingerprint=fingerprint, units=units,
                                            calls=calls, quota_exceeded_at=exceeded_at))
                    except IntegrityError:  # another process inserted the row first
                        db.execute(update(ApiUsage).where(*where).values(**values))
    except Exception:
        with _lock:  # keep the counts for the next attempt
            for key, (units, calls, exceeded_at) in pending.items():
                entry = _pending.setdefault(key, [0, 0, None])
                entry[0] += units
                entry[1] += calls
                entry[2] = entry[2] or exceeded_at
        raise
    return len(pending)


class KeyUsage(BaseModel):
    label: str
    fingerprint: str
    units: int
    calls: int
    limit: int
    percent: float
    quota_exceeded: bool


class YouTubeQuota(BaseModel):
    day: str
    resets_at: datetime
    daily_limit_per_key: int
    total_units: int
    keys: list[KeyUsage]
    note: str


def youtube_quota(settings: Settings) -> YouTubeQuota:
    day = quota_day()
    with ReadSessionLocal() as db:  # read-only: no transaction round trips
        rows = {
            r.key_fingerprint: r
            for r in db.scalars(select(ApiUsage).where(ApiUsage.day == day, ApiUsage.service == YOUTUBE))
        }
    with _lock:
        pending = {fp: v for (d, s, fp), v in _pending.items() if d == day and s == YOUTUBE}
    limit = settings.youtube_daily_quota
    keys = []
    for index, api_key in enumerate(settings.youtube_api_keys, start=1):
        fp = key_fingerprint(api_key)
        row = rows.get(fp)
        extra = pending.get(fp, [0, 0, None])
        units = (row.units if row else 0) + extra[0]
        calls = (row.calls if row else 0) + extra[1]
        exceeded = bool((row and row.quota_exceeded_at) or extra[2])
        keys.append(
            KeyUsage(
                label=f"Key {index}", fingerprint=fp, units=units, calls=calls, limit=limit,
                percent=round(min(units / limit * 100, 100), 1) if limit else 0.0, quota_exceeded=exceeded,
            )
        )
    return YouTubeQuota(
        day=day,
        resets_at=next_reset(),
        daily_limit_per_key=limit,
        total_units=sum(k.units for k in keys),
        keys=keys,
        note="Counted by CreatorIntel. Use of the same keys by other apps is not included.",
    )


class UsageFlusher:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="api-usage-flush")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        try:
            await asyncio.to_thread(flush)  # don't lose the last counts on shutdown
        except Exception:
            logger.exception("api_usage_final_flush_failed")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_EVERY_SECONDS)
            try:
                await asyncio.to_thread(flush)
            except Exception as exc:
                log_event(logger, logging.WARNING, "api_usage_flush_failed", reason=type(exc).__name__)
