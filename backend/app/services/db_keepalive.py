"""Keep the database connection pool warm (and a serverless database such as Neon awake) during working hours."""

import asyncio
import logging
from datetime import datetime, time

from app.config.settings import Settings
from app.models.db import ping_database, warm_pool
from app.utils.logging import log_event
from app.video_performance.timeutil import parse_hhmm, tracking_tz

logger = logging.getLogger("creatorintel.db_keepalive")

PING_EVERY_SECONDS = 240  # below Neon's default 5-minute auto-suspend


def _window(value: str) -> tuple[time, time] | None:
    if not value or "-" not in value:
        return None
    start, end = value.split("-", 1)
    return parse_hhmm(start, time(9, 0)), parse_hhmm(end, time(21, 0))


def in_window(value: str, now: datetime | None = None) -> bool:
    window = _window(value)
    if window is None:
        return False
    current = (now or datetime.now(tracking_tz())).astimezone(tracking_tz()).time()
    start, end = window
    return start <= current <= end if start <= end else current >= start or current <= end


class DatabaseKeepAlive:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self.settings.database_url.startswith("sqlite") or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="db-keepalive")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        try:
            opened = await asyncio.to_thread(warm_pool)
            log_event(logger, logging.INFO, "db_pool_warmed", connections=opened)
        except Exception as exc:
            log_event(logger, logging.WARNING, "db_pool_warm_failed", reason=type(exc).__name__)
        while True:
            await asyncio.sleep(PING_EVERY_SECONDS)
            if not in_window(self.settings.db_keep_warm_hours):
                continue
            try:
                await asyncio.to_thread(ping_database)
            except Exception as exc:  # the next request reconnects anyway
                log_event(logger, logging.WARNING, "db_keepalive_failed", reason=type(exc).__name__)
