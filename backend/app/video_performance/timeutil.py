from datetime import datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config.settings import get_settings


def tracking_tz() -> tzinfo:
    try:
        return ZoneInfo(get_settings().video_tracking_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def local_day_start_utc(now: datetime | None = None) -> datetime:
    """Start of 'today' in the tracking timezone, expressed in UTC."""
    tz = tracking_tz()
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    return datetime.combine(local.date(), time.min, tzinfo=tz).astimezone(timezone.utc)


def parse_hhmm(value: str, default: time) -> time:
    try:
        hours, minutes = value.strip().split(":")
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return default


def next_run_utc(at: time, now: datetime | None = None) -> datetime:
    """Next occurrence of local wall-clock time `at`, in UTC."""
    tz = tracking_tz()
    local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    candidate = datetime.combine(local_now.date(), at, tzinfo=tz)
    if candidate <= local_now:
        candidate = datetime.combine(local_now.date() + timedelta(days=1), at, tzinfo=tz)
    return candidate.astimezone(timezone.utc)


def slot_start_utc(at: time, now: datetime | None = None) -> datetime:
    """The most recent local occurrence of wall-clock time `at` at or before `now`, in UTC.

    A daily job belongs to this "slot": it is due when it has not completed since the slot started
    (e.g. since today 06:30, or since yesterday 06:30 when it is earlier than 06:30 now).
    """
    tz = tracking_tz()
    local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    candidate = datetime.combine(local_now.date(), at, tzinfo=tz)
    if candidate > local_now:
        candidate = datetime.combine(local_now.date() - timedelta(days=1), at, tzinfo=tz)
    return candidate.astimezone(timezone.utc)


def job_times(settings) -> dict[str, time]:
    """Configured daily time of each Video Performance job."""
    return {
        "creator_discovery": parse_hhmm(settings.video_tracking_discovery_time, time(6, 0)),
        "metrics_refresh": parse_hhmm(settings.video_tracking_refresh_time, time(6, 30)),
    }

