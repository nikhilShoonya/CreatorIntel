"""YouTube Data API quota usage, counted by this app.

Every request costs quota units (channels/playlistItems/videos.list = 1 unit each, including failed
requests and retries). Google resets the daily quota at midnight Pacific Time. Counts are kept in memory
and written to the database every few seconds, so recording a call never adds a database round trip.
API keys are identified by a short hash; the key itself is never stored.
"""

import asyncio
import hashlib
import json
import logging
import threading
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.config.settings import Settings, get_settings
from app.models.db import ReadSessionLocal, session_scope
from app.models.usage import ApiRateStatus, ApiUsage
from app.utils.logging import log_event
from app.utils.time import as_utc, utcnow

logger = logging.getLogger("creatorintel.api_usage")

YOUTUBE = "youtube"
INSTAGRAM = "instagram"
GROQ = "groq"
FACEBOOK = "facebook"
META_WINDOW_MINUTES = 60  # Meta's platform rate limit is a rolling one-hour window
QUOTA_TZ = ZoneInfo("America/Los_Angeles")  # YouTube quota resets at midnight Pacific Time
FLUSH_EVERY_SECONDS = 20

_pending: dict[tuple[str, str, str], list] = {}  # (day, service, fingerprint) -> [units, calls, exceeded_at]
_meta_latest: dict | None = None  # latest Meta usage reading not yet written to the database
_lock = threading.Lock()


def local_day(now: datetime | None = None) -> str:
    """Calendar day in the app's timezone (used for 'calls today' of services without a daily quota)."""
    try:
        tz = ZoneInfo(get_settings().video_tracking_timezone)
    except Exception:
        tz = timezone.utc
    return (now or datetime.now(timezone.utc)).astimezone(tz).date().isoformat()


def record_instagram_call() -> None:
    """Count one Instagram Graph API request (every attempt)."""
    key = (local_day(), INSTAGRAM, "app")
    with _lock:
        entry = _pending.setdefault(key, [0, 0, None])
        entry[0] += 1
        entry[1] += 1


def record_facebook_call() -> None:
    """Count one Facebook Graph API request (every attempt)."""
    key = (local_day(), FACEBOOK, "app")
    with _lock:
        entry = _pending.setdefault(key, [0, 0, None])
        entry[0] += 1
        entry[1] += 1


def _percentages(entry: dict) -> list[float]:
    return [float(entry.get(k) or 0) for k in ("call_count", "total_cputime", "total_time")]


def record_meta_usage(response) -> None:
    """Read Meta's rate-limit headers (X-App-Usage / X-Business-Use-Case-Usage) from a Graph API response."""
    global _meta_latest
    headers = response.headers
    raw_app, raw_buc = headers.get("x-app-usage"), headers.get("x-business-use-case-usage")
    if not raw_app and not raw_buc:
        return
    percents: list[float] = []
    regain = None
    details: dict = {}
    try:
        if raw_app:
            app_usage = json.loads(raw_app)
            details["app"] = {k: app_usage.get(k) for k in ("call_count", "total_cputime", "total_time")}
            percents += _percentages(app_usage)
        if raw_buc:
            for entries in json.loads(raw_buc).values():  # keyed by business object id - not stored
                for entry in entries or []:
                    percents += _percentages(entry)
                    details.setdefault("business_use_case", []).append(
                        {k: entry.get(k) for k in ("type", "call_count", "total_cputime", "total_time")}
                    )
                    wait = entry.get("estimated_time_to_regain_access")
                    if wait:
                        regain = max(regain or 0, int(wait))
    except (ValueError, TypeError, AttributeError):
        return
    with _lock:
        _meta_latest = {
            "percent": round(max(percents, default=0.0), 1), "details": details,
            "regain": regain, "observed_at": utcnow(),
        }


def groq_day(now: datetime | None = None) -> str:
    """Groq usage is counted per UTC day."""
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()


_groq_reported: dict[str, dict] = {}  # key fingerprint -> latest x-ratelimit-* reading from Groq


def record_groq_call(api_key: str, tokens: int, *, limit_reached: bool = False) -> None:
    """Count one Groq request (every attempt, including rate-limited ones) and the tokens it used."""
    key = (groq_day(), GROQ, key_fingerprint(api_key))
    with _lock:
        entry = _pending.setdefault(key, [0, 0, None])
        entry[0] += max(int(tokens or 0), 0)
        entry[1] += 1
        if limit_reached:
            entry[2] = utcnow()


def record_groq_headers(api_key: str, headers) -> None:
    """Keep Groq's own daily request counter (x-ratelimit-*-requests = requests per day)."""
    def number(name):
        try:
            return int(float(headers.get(name)))
        except (TypeError, ValueError):
            return None

    limit, remaining = number("x-ratelimit-limit-requests"), number("x-ratelimit-remaining-requests")
    if limit is None or remaining is None:
        return
    with _lock:
        _groq_reported[key_fingerprint(api_key)] = {"limit": limit, "remaining": remaining, "observed_at": utcnow()}


def groq_counts(api_key: str) -> tuple[int, int, dict | None]:
    """(tokens today, requests today, latest Groq-reported request counter) for one key."""
    fingerprint, day = key_fingerprint(api_key), groq_day()
    with ReadSessionLocal() as db:
        row = db.get(ApiUsage, (day, GROQ, fingerprint))
    with _lock:
        extra = _pending.get((day, GROQ, fingerprint), [0, 0, None])
        reported = dict(_groq_reported[fingerprint]) if fingerprint in _groq_reported else None
    tokens = (row.units if row else 0) + extra[0]
    calls = (row.calls if row else 0) + extra[1]
    if reported and groq_day(reported["observed_at"]) != day:
        reported = None  # yesterday's reading
    return tokens, calls, reported


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
    global _meta_latest
    with _lock:
        pending = dict(_pending)
        _pending.clear()
        meta, _meta_latest = _meta_latest, None
    if not pending and meta is None:
        return 0
    try:
        with session_scope() as db:
            if meta is not None:  # latest Meta rate-limit reading (one row, overwritten)
                status = db.get(ApiRateStatus, INSTAGRAM) or ApiRateStatus(service=INSTAGRAM)
                status.percent, status.details = meta["percent"], meta["details"]
                status.regain_access_minutes, status.observed_at = meta["regain"], meta["observed_at"]
                db.add(status)
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
            if meta is not None and _meta_latest is None:
                _meta_latest = meta
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
    key_count: int
    total_units: int
    total_limit: int  # all keys together (keys are used one after another)
    percent: float
    quota_exceeded_keys: int
    keys: list[KeyUsage]
    note: str


class InstagramUsage(BaseModel):
    calls_today: int
    facebook_calls_today: int = 0  # same Meta app limit
    percent: float  # share of Meta's rolling one-hour limit (as reported by Meta)
    observed_at: datetime | None
    stale: bool  # no Graph API call in the last hour, so the hourly usage has reset
    regain_access_minutes: int | None
    note: str


class ApiUsageOut(BaseModel):
    youtube: YouTubeQuota
    instagram: InstagramUsage


def instagram_usage() -> InstagramUsage:
    day = local_day()
    with ReadSessionLocal() as db:
        row = db.get(ApiUsage, (day, INSTAGRAM, "app"))
        fb_row = db.get(ApiUsage, (day, FACEBOOK, "app"))
        status = db.get(ApiRateStatus, INSTAGRAM)
    with _lock:
        pending_calls = _pending.get((day, INSTAGRAM, "app"), [0, 0, None])[1]
        fb_pending = _pending.get((day, FACEBOOK, "app"), [0, 0, None])[1]
        meta = dict(_meta_latest) if _meta_latest else None
    observed = meta["observed_at"] if meta else (as_utc(status.observed_at) if status else None)
    percent = meta["percent"] if meta else (status.percent if status else 0.0)
    regain = meta["regain"] if meta else (status.regain_access_minutes if status else None)
    stale = observed is None or utcnow() - observed > timedelta(minutes=META_WINDOW_MINUTES)
    return InstagramUsage(
        calls_today=(row.calls if row else 0) + pending_calls,
        facebook_calls_today=(fb_row.calls if fb_row else 0) + fb_pending,
        percent=0.0 if stale else percent,
        observed_at=observed,
        stale=stale,
        regain_access_minutes=None if stale else regain,
        note="Percent of Meta's rolling one-hour rate limit, as reported by Meta on each API response.",
    )


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
    total_units = sum(k.units for k in keys)
    total_limit = limit * len(keys)
    return YouTubeQuota(
        day=day,
        resets_at=next_reset(),
        daily_limit_per_key=limit,
        key_count=len(keys),
        total_units=total_units,
        total_limit=total_limit,
        percent=round(min(total_units / total_limit * 100, 100), 1) if total_limit else 0.0,
        quota_exceeded_keys=sum(1 for k in keys if k.quota_exceeded),
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


